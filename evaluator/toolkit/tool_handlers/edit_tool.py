"""
Edit Tool - Performs exact string replacements in files
"""
import os
from typing import Dict, Any
from toolkit.tool_handlers.base_tool_handler import BaseToolHandler, ToolState, ToolFailedError
from toolkit.tool_handlers.utils import generate_diff_data
from toolkit.tool_handlers.tool_categories import ToolName


class EditToolHandler(BaseToolHandler):
    """Tool for performing exact string replacements in files"""
    
    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = True
    
    @property
    def name(self) -> str:
        return ToolName.EDIT
    
    @property
    def display_result_type(self) -> str:
        return "edit_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to modify. Relative paths are resolved against the workspace working directory."
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace"
                },
                "new_string": {
                    "type": "string", 
                    "description": "The text to replace it with (must be different from old_string)"
                },
                "replace_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "Replace all occurences of old_string (default false)"
                }
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#"
        }
    
    async def execute_async(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False, update_file: bool = True) -> Dict[str, Any]:
        """Execute the tool"""
        # Resolve relative paths against working directory
        if not os.path.isabs(file_path):
            working_dir = self.tool_state.get_working_dir()
            file_path = os.path.abspath(os.path.join(working_dir, file_path))

        # Check if old_string and new_string are the same
        if old_string == new_string:
            raise ToolFailedError("Error: old_string and new_string cannot be the same")
        
        # Check if old_string is empty
        if not old_string:
            raise ToolFailedError("Error: old_string cannot be empty")
        
        # Check if file exists
        if not os.path.exists(file_path):
            raise ToolFailedError(f"Error: File not found: {file_path}")
        
        if not os.path.isfile(file_path):
            raise ToolFailedError(f"Error: Path is not a file: {file_path}")
        
        # Check if file has been read first and the read is still fresh
        if not self.is_file_read_fresh(file_path):
            if not self.is_file_read(file_path):
                raise ToolFailedError(f"Error: You must read the file's contents before editing: {file_path}")
            else:
                raise ToolFailedError(f"Error: The file {file_path} has been modified since it was last read. Please read the file again before editing.")
        
        try:
            # Read the file
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Backup the original content before any modifications (only if update_file=True)
            if update_file:
                self.backup_file_state(file_path, content, was_created=False)
            
            if not content and old_string:
                raise ToolFailedError(f"Error: File is empty, cannot find old_string: {old_string}")
            
            # Check if old_string exists in the file
            if old_string not in content:
                raise ToolFailedError(f"Error: old_string not found in file: {old_string}")
            
            # For non-replace_all operations, check uniqueness
            if not replace_all:
                occurrences = content.count(old_string)
                if occurrences > 1:
                    raise ToolFailedError(f"Error: old_string appears {occurrences} times in the file. Either provide a larger string with more surrounding context to make it unique or use replace_all=true to change every instance.")
                elif occurrences == 0:
                    raise ToolFailedError(f"Error: old_string not found in file: {old_string}")
            
            # Perform the replacement
            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)
            
            if update_file:
                # Write the modified content back
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                # Refresh the file read status
                self.mark_file_as_read(file_path)
            
            # Generate cat -n style output for modified area only
            cat_snippet = self._generate_modified_snippet(content, new_content, old_string)
            diff_data = self._generate_diff_data(content, new_content, file_path)

            # if replace_all:
            #     # CC can not prinit the modified content when replace_all is true
            #     agent_reuslt = f"The file {file_path} has been updated. All occurrences of '{old_string}' were successfully replaced with '{new_string}'. "
            # else:
            #     agent_reuslt = f"The file {file_path} has been updated. Here's the result of running `cat -n` on a snippet of the edited file:\n{cat_snippet}"

            rel_path = os.path.relpath(file_path, self.tool_state.get_working_dir())
            agent_reuslt = f"The file {rel_path} has been updated."

            return {
                'tool_success': True,
                'result': agent_reuslt,
                'display_result': diff_data
            }
            
        except PermissionError:
            raise ToolFailedError(f"Error: Permission denied accessing file: {file_path}")
        
        except UnicodeDecodeError:
            raise ToolFailedError(f"Error: File appears to be binary or has encoding issues: {file_path}")

        except Exception as e:
            raise ToolFailedError(f"Error editing file {file_path}: {str(e)}")
        
    def _generate_modified_snippet(self, original_content: str, new_content: str, old_string: str) -> str:
        """Generate cat -n style snippet showing only modified lines with 4 lines context before and after"""
        if not old_string:
            return ""
        
        # Find the first occurrence of the modification in the original content
        first_occurrence_pos = original_content.find(old_string)
        if first_occurrence_pos == -1:
            return ""
        
        # Calculate which line the modification starts at
        lines_before_modification = original_content[:first_occurrence_pos].count('\n')
        modification_start_line = lines_before_modification + 1  # 1-based line numbering
        
        # Calculate how many lines the old_string spans
        old_string_lines = old_string.count('\n') + 1
        modification_end_line = modification_start_line + old_string_lines - 1
        
        # Split the new content into lines
        new_lines = new_content.split('\n')
        
        # Calculate context range (4 lines before and after)
        context_before = 4
        context_after = 4
        
        start_line = max(1, modification_start_line - context_before)
        end_line = min(len(new_lines), modification_end_line + context_after)
        
        # Generate cat -n style output for the snippet
        cat_output = []
        for i in range(start_line - 1, end_line):  # Convert to 0-based indexing
            if i < len(new_lines):
                line_num = i + 1  # Convert back to 1-based for display
                spaces = " " * (6 - len(str(line_num)))
                cat_output.append(f"{spaces}{line_num}\t{new_lines[i]}")
        
        return '\n'.join(cat_output)
    
        
    def _generate_diff_data(self, old_content, new_content, filename):
        difftext = generate_diff_data(
            old_content=old_content,
            new_content=new_content)
        return {
            'old_file': filename,
            'new_file': filename,
            'diff': difftext
        }
