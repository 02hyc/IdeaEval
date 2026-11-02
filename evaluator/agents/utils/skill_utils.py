from typing import List, Dict, Optional
import os
import re


def extract_yaml_frontmatter(content: str) -> Optional[Dict[str, str]]:
    """
    Extract YAML frontmatter from markdown content.
    
    Args:
        content: The markdown file content
        
    Returns:
        Dictionary with parsed YAML data or None if no frontmatter found
    """
    # Match YAML frontmatter pattern: --- at start, content, --- at end
    pattern = r'^---\s*\n(.*?)\n---\s*\n'
    match = re.match(pattern, content, re.DOTALL)
    
    if not match:
        return None
    
    yaml_content = match.group(1)
    yaml_dict = {}
    
    # Simple YAML parsing for name and description fields
    for line in yaml_content.split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            yaml_dict[key.strip()] = value.strip()
    
    return yaml_dict


def get_available_skills_prompt(root_path: Optional[str] = None, exclude_skills: Optional[List[str]] = None) -> str:
    """
    Generate the available_skills prompt by scanning all SKILL.md files
    in the skills directory and extracting their YAML frontmatter.

    Args:
        root_path: Optional root path to search for skills folder.
                    If not provided, uses the default agent/skills location.
                    Locations in the output are relative to this root_path.

    Returns:
        Formatted XML prompt with available skills
    """
    # Determine skills path
    if root_path:
        # First try: skills folder in root_path or its parent (task-local)
        task_skills_path = os.path.join(root_path, 'internal_context', 'skills')
        parent_skills_path = os.path.join(os.path.dirname(root_path), 'internal_context', 'skills')

        if os.path.exists(task_skills_path) and os.path.isdir(task_skills_path):
            skills_path = task_skills_path
        elif os.path.exists(parent_skills_path) and os.path.isdir(parent_skills_path):
            skills_path = parent_skills_path
        else:
            # Fallback to default location
            skills_path = os.path.join(os.path.dirname(__file__), '..', 'internal_context', 'skills')
    else:
        # Default: agent/skills relative to this file
        skills_path = os.path.join(os.path.dirname(__file__), '..', 'internal_context', 'skills')

    skills_path = os.path.abspath(skills_path)
    root_abs = os.path.abspath(root_path) if root_path else None

    # Check if skills directory exists
    if not os.path.exists(skills_path):
        return ""

    skills = []

    _exclude = set(exclude_skills) if exclude_skills else set()

    # Scan all skill directories
    for skill_dir in sorted(os.listdir(skills_path)):
        if skill_dir in _exclude:
            continue
        skill_md_path = os.path.join(skills_path, skill_dir, 'SKILL.md')

        if os.path.exists(skill_md_path):
            try:
                with open(skill_md_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract YAML frontmatter
                yaml_data = extract_yaml_frontmatter(content)

                if yaml_data and 'description' in yaml_data:
                    skill_name = yaml_data.get('name', skill_dir)
                    # Emit relative path from root_path when available
                    if root_abs:
                        location = os.path.relpath(skill_md_path, root_abs)
                    else:
                        location = skill_md_path
                    skills.append({
                        'name': skill_name,
                        'description': yaml_data['description'],
                        'location': location
                    })
            except Exception as e:
                print(f"Error reading {skill_md_path}: {e}")
    
    # Generate the prompt
    if not skills:
        return ""
    
    prompt_lines = ["<available_skills>"]
    
    for skill in skills:
        prompt_lines.append("<skill>")
        prompt_lines.append("<name>")
        prompt_lines.append(skill['name'])
        prompt_lines.append("</name>")
        prompt_lines.append("<description>")
        prompt_lines.append(skill['description'])
        prompt_lines.append("</description>")
        prompt_lines.append("<location>")
        prompt_lines.append(skill['location'])
        prompt_lines.append("</location>")
        prompt_lines.append("</skill>")
        prompt_lines.append("")
    
    prompt_lines.append("</available_skills>")

    skills_prompt = "\n".join(prompt_lines)
    return skills_prompt
