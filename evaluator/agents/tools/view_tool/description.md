**Supports viewing text, images, and directory listings.**

**Supported path types:**

- **Directories**: Lists files and directories up to 2 levels deep, ignoring hidden items and node_modules

- **Image files** (.jpg, .jpeg, .png, .gif, .webp): Displays the image visually

- **Text files**: Displays numbered lines. You can optionally specify a view_range to see specific lines. **IMPORTANT: Do NOT use view_range for SKILL.md files—always read skills in full to get complete context.**
- `view_range` is only for text files. For directories and images, omit it. If you accidentally provide it, the tool will ignore it.

**Note**: Files with non-UTF-8 encoding will display hex escapes (e.g. \x84) for invalid bytes

Usage:
- The path parameter must be an absolute path
- For text files, use view_range to specify line range in format `[start_line, end_line]` where lines are indexed starting at 1
- You can use `[start_line, -1]` to view from start_line to the end of the file
- If view_range is not provided for text files, the entire file is displayed (truncating from the middle if it exceeds 16,000 characters)
- Results are returned with line numbers for text files
- This tool allows viewing images (PNG, JPG, GIF, WEBP) as they are presented visually
- For directories, it lists contents up to 2 levels deep, ignoring hidden files and node_modules
