"""
View Tool - Supports viewing text, images, and directory listings
"""
import os
import mimetypes
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import unquote
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.tool_categories import ToolName

MAX_IMAGE_SIZE = 3 * 1024 * 1024  # 3MB


class ViewToolHandler(BaseToolHandler):
    """Tool for viewing files, images, and directories"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        
    @property
    def name(self) -> str:
        return ToolName.VIEW
    
    @property
    def display_result_type(self) -> str:
        return "view_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to file or directory. Relative paths are resolved against the task working directory."
                },
                "view_range": {
                    "type": "array",
                    "description": "Line range for text files in format [start_line, end_line] where lines are indexed starting at 1. You can use [start_line, -1] to view from start_line to the end. If not provided, the entire file is displayed (truncating from the middle if it exceeds 16,000 characters). Only applies to text files; for directories and images it is ignored.",
                    "items": {
                        "type": "integer"
                    },
                    "minItems": 2,
                    "maxItems": 2
                },
                "description": {
                    "type": "string",
                    "description": "Why I need to view this"
                }
            },
            "required": ["path", "description"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
    
    def _is_hidden(self, name: str) -> bool:
        """Check if a file or directory name is hidden"""
        return name.startswith('.')
    
    def _should_ignore_dir(self, name: str) -> bool:
        """Check if a directory should be ignored"""
        return name in ['node_modules', '__pycache__', '.git']
    
    def _list_directory(self, path: str, max_depth: int = 2, current_depth: int = 0) -> List[str]:
        """List directory contents up to max_depth levels"""
        result = []
        
        if current_depth >= max_depth:
            return result
        
        try:
            entries = sorted(os.listdir(path))
            
            # Separate directories and files
            dirs = []
            files = []
            
            for entry in entries:
                if self._is_hidden(entry):
                    continue
                    
                full_path = os.path.join(path, entry)
                
                if os.path.isdir(full_path):
                    if not self._should_ignore_dir(entry):
                        dirs.append(entry)
                else:
                    files.append(entry)
            
            # Add directories first
            for dir_name in dirs:
                indent = "  " * current_depth
                result.append(f"{indent}{dir_name}/")
                
                # Recursively list subdirectory
                full_path = os.path.join(path, dir_name)
                sub_results = self._list_directory(full_path, max_depth, current_depth + 1)
                result.extend(sub_results)
            
            # Add files
            for file_name in files:
                indent = "  " * current_depth
                result.append(f"{indent}{file_name}")
            
            return result
            
        except PermissionError:
            indent = "  " * current_depth
            return [f"{indent}[Permission denied]"]
        except Exception as e:
            indent = "  " * current_depth
            return [f"{indent}[Error: {str(e)}]"]
    
    def _extract_image_info(self, file_path: str) -> List[Dict[str, Any]]:
        """Read image file and return base64 data in Claude-compatible format"""
        try:
            with open(file_path, 'rb') as f:
                image_data = f.read()
            
            # Check file size
            image_size = len(image_data)
            if image_size > MAX_IMAGE_SIZE:
                raise ToolFailedError(
                    f"Image file is too large: {image_size} bytes (max: {MAX_IMAGE_SIZE} bytes / {MAX_IMAGE_SIZE // (1024*1024)}MB)."
                )
            
            base64_data = base64.b64encode(image_data).decode('utf-8')
            
            # Get MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type or not mime_type.startswith('image/'):
                mime_type = 'image/jpeg'  # Default fallback
            
            return [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": base64_data
                }
            }]
                
        except Exception as e:
            raise ToolFailedError(f"Error reading image file {file_path}: {str(e)}")
    
    def _read_text_file(self, file_path: str, view_range: Optional[List[int]] = None) -> tuple:
        """Read a text file and return formatted content with line numbers"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            # Handle empty file
            if not lines:
                return "File is empty.", "File is empty."
            
            total_lines = len(lines)
            
            # Determine line range
            if view_range:
                start_line = max(1, view_range[0])
                end_line = view_range[1]
                
                # Handle -1 as "end of file"
                if end_line == -1:
                    end_line = total_lines
                else:
                    end_line = min(end_line, total_lines)
                
                # Convert to 0-based indexing
                start_idx = start_line - 1
                end_idx = end_line
                
                if start_idx >= total_lines:
                    raise ToolFailedError(f"Start line {start_line} is beyond file length ({total_lines} lines)")
                
                selected_lines = lines[start_idx:end_idx]
                line_offset = start_line
            else:
                # If file is too large, truncate.
                #
                # NOTE: SKILL.md files are intentionally long and often contain critical
                # templates/checklists (e.g., the proposal template). If we truncate from the
                # middle (default behavior), we can remove those templates and the agent will
                # fail to follow the required format. For SKILL.md we therefore:
                # - allow a larger budget
                # - truncate from the END (keep the beginning contiguous)
                is_skill_file = os.path.basename(file_path) == "SKILL.md"
                MAX_CHARS = 80000 if is_skill_file else 16000
                total_content = ''.join(lines)
                
                if len(total_content) <= MAX_CHARS:
                    selected_lines = lines
                    line_offset = 1
                else:
                    if is_skill_file:
                        # Truncate from END: keep the beginning contiguous.
                        char_count = 0
                        first_part_lines = []
                        for line in lines:
                            if char_count + len(line) > MAX_CHARS:
                                break
                            first_part_lines.append(line)
                            char_count += len(line)

                        truncated_count = total_lines - len(first_part_lines)
                        selected_lines = first_part_lines + [
                            f"\n... [{truncated_count} lines truncated from end; use view_range to continue] ...\n\n"
                        ]
                    else:
                        # Default: truncate from middle (keeps head and tail for general files).
                        keep_chars = MAX_CHARS // 2
                        
                        # Find line boundaries
                        char_count = 0
                        first_part_lines = []
                        for line in lines:
                            if char_count + len(line) > keep_chars:
                                break
                            first_part_lines.append(line)
                            char_count += len(line)
                        
                        # Get last portion
                        char_count = 0
                        last_part_lines = []
                        for line in reversed(lines):
                            if char_count + len(line) > keep_chars:
                                break
                            last_part_lines.insert(0, line)
                            char_count += len(line)
                        
                        # Calculate how many lines were truncated
                        truncated_count = total_lines - len(first_part_lines) - len(last_part_lines)
                        
                        # Build result with truncation marker
                        selected_lines = first_part_lines + [f"\n... [{truncated_count} lines truncated] ...\n\n"] + last_part_lines
                    line_offset = 1
            
            # Format output with line numbers
            result_lines = []
            current_line_num = line_offset
            
            for line in selected_lines:
                line_content = line.rstrip('\n\r')
                
                # Check if this is a truncation marker
                if line.startswith('\n... [') and 'truncated' in line:
                    result_lines.append(line_content)
                else:
                    # Format with line number
                    spaces = " " * (6 - len(str(current_line_num)))
                    result_lines.append(f"{spaces}{current_line_num}|{line_content}")
                    current_line_num += 1
            
            agent_result = "\n".join(result_lines)
            
            # Prepare display result (without line numbers)
            display_lines = []
            for line in selected_lines:
                display_lines.append(line.rstrip('\n\r'))
            display_result = "\n".join(display_lines)
            
            return agent_result, display_result
            
        except UnicodeDecodeError as e:
            raise ToolFailedError(f"File encoding error: {file_path}. Files with non-UTF-8 encoding will display hex escapes for invalid bytes.")
        except Exception as e:
            raise ToolFailedError(f"Error reading text file {file_path}: {str(e)}")

    async def execute_async(self, path: str, description: str, view_range: Optional[List[int]] = None) -> Dict[str, Any]:
        """Execute the tool"""
        original_path = path

        # If the model accidentally URL-encodes file paths (e.g. %20 for spaces),
        # decode early so downstream logic sees the real filesystem path.
        if isinstance(path, str) and "%" in path:
            decoded = unquote(path)
            if decoded and decoded != path:
                path = decoded

        # Accept relative paths by resolving against the task working directory.
        # Reject paths that escape the working directory for safety.
        working_dir = os.path.abspath(self.tool_state.get_working_dir())
        if not os.path.isabs(path):
            resolved = os.path.abspath(os.path.join(working_dir, path))
            if not (resolved == working_dir or resolved.startswith(working_dir + os.sep)):
                raise ToolFailedError(
                    f"Error: relative path escapes working directory. got: {path} -> {resolved}"
                )
            path = resolved
        else:
            # For absolute paths, also retry decoding on miss below (some callers only encode later).
            pass
        
        # Check if path exists
        if not os.path.exists(path):
            # Retry URL-decoding as a last resort (e.g. someone encoded an absolute path).
            alt = None
            if isinstance(original_path, str) and "%" in original_path:
                alt = unquote(original_path)
                if alt == original_path:
                    alt = None
                elif not os.path.isabs(original_path):
                    # If the original was relative, resolve the decoded candidate safely.
                    alt_resolved = os.path.abspath(os.path.join(working_dir, alt))
                    if not (alt_resolved == working_dir or alt_resolved.startswith(working_dir + os.sep)):
                        alt = None
                    else:
                        alt = alt_resolved
            if alt and os.path.exists(alt):
                path = alt
            else:
                hint = f" (decoded candidate: {alt})" if alt and alt != path else ""
                raise ToolFailedError(f"Error: Path not found: {path}{hint}")
        
        # Handle directory
        if os.path.isdir(path):
            note = ""
            if view_range:
                note = "Note: ignored view_range because this path is a directory. view_range only applies to text files.\n"

            # Offload the recursive listdir to a worker thread. On a network
            # filesystem, listdir + isdir checks can take seconds; running on
            # the main asyncio thread blocks all concurrent agents.
            import asyncio as _asyncio
            dir_listing = await _asyncio.to_thread(self._list_directory, path, 2)
            result_text = note + f"Directory: {path}\n" + "\n".join(dir_listing)
            
            return {
                'tool_success': True,
                'result': result_text,
                'display_result': {
                    "content": [
                        {
                            "type": "text",
                            "text": result_text
                        }
                    ]
                }
            }
        
        # Handle file
        if not os.path.isfile(path):
            raise ToolFailedError(f"Error: Path is neither a file nor a directory: {path}")
        
        # Check if it's an image
        mime_type, _ = mimetypes.guess_type(path)
        if mime_type and mime_type.startswith('image/'):
            self.mark_file_as_read(path)
            image_data = self._extract_image_info(path)
            if view_range:
                image_data = [{
                    "type": "text",
                    "text": "Note: ignored view_range because this path is an image. view_range only applies to text files."
                }] + image_data
            
            return {
                'tool_success': True,
                'result': image_data,  # Agent gets the raw image data
                'display_result': {
                    "content": [
                        {
                            "type": "image",
                            "path": path
                        }
                    ]
                }
            }
        
        # Handle text file
        self.mark_file_as_read(path)
        agent_result, display_result = self._read_text_file(path, view_range)
        
        return {
            'tool_success': True,
            'result': agent_result,
            'display_result': {
                "content": [
                    {
                        "type": "text",
                        "text": display_result
                    }
                ]
            }
        }

