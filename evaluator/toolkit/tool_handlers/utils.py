import asyncio
from difflib import unified_diff
from typing import List
import os
import platform
import subprocess
from datetime import datetime

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def convert_content_to_lines(content: str) -> List[str]:
    start = 0
    lines = []
    for i in range(len(content)):
        if content[i] == '\n':
            lines.append(content[start:i+1])
            start = i+1
    if start < len(content) or start == 0:
        # Handle the case where content doesn't end with newline
        remaining = content[start:]
        if remaining:
            lines.append(remaining)
    return lines

def generate_diff_data(old_content: str, new_content: str) -> str:
    old_lines = convert_content_to_lines(old_content)
    new_lines = convert_content_to_lines(new_content)

    result = list(unified_diff(old_lines, new_lines, 'Original', 'Current', lineterm=''))
    return '\n'.join(result)


async def get_system_status(working_dir: str) -> dict:
    """
    Get system status information for CodeAgent system prompt construction.
    
    Args:
        working_dir: Working directory path to analyze
        
    Returns:
        Dictionary containing success status and system status information
    """
    try:
        # Validate working directory parameter
        if not working_dir:
            return {
                "success": False,
                "error": 'Working directory is required'
            }
        
        # Expand user home shortcut
        if working_dir.startswith('~'):
            working_dir = os.path.expanduser(working_dir)
        
        # Convert to absolute path
        working_dir = os.path.abspath(working_dir.strip())
        
        # Validate working directory
        if not await asyncio.to_thread(os.path.exists, working_dir):
            return {
                "success": False,
                "error": f'Working directory does not exist: {working_dir}'
            }
        
        if not await asyncio.to_thread(os.path.isdir, working_dir):
            return {
                "success": False,
                "error": f'Working directory is not a directory: {working_dir}'
            }
        
        # Basic system information
        system_status = {
            'working_dir': working_dir,
            'platform': platform.system(),
            'os_version': f"{platform.system()} {platform.release()}",
            'today': datetime.now().strftime('%Y-%m-%d'),
            'is_git_repo': False,
            'git_status': None
        }
        
        # Check if it's a git repository
        git_dir = os.path.join(working_dir, '.git')
        if await asyncio.to_thread(os.path.exists, git_dir):
            system_status['is_git_repo'] = True
            
            try:
                # Get git information - run commands in working directory
                git_info = {}
                
                # Get main branch
                try:
                    result = subprocess.run(
                        ['git', 'rev-parse', '--abbrev-ref', 'origin/HEAD'], 
                        capture_output=True, text=True, cwd=working_dir, timeout=5
                    )
                    if result.returncode == 0:
                        git_info['main_branch'] = result.stdout.strip().replace('origin/', '', 1)
                    else:
                        # Fallback: try to get default branch
                        result = subprocess.run(
                            ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'], 
                            capture_output=True, text=True, cwd=working_dir, timeout=5
                        )
                        if result.returncode == 0:
                            git_info['main_branch'] = result.stdout.strip().replace('refs/remotes/origin/', '')
                        else:
                            git_info['main_branch'] = 'main'  # Default fallback
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    git_info['main_branch'] = 'main'
                
                # Get current branch
                try:
                    result = subprocess.run(
                        ['git', 'branch', '--show-current'], 
                        capture_output=True, text=True, cwd=working_dir, timeout=5
                    )
                    if result.returncode == 0:
                        git_info['current_branch'] = result.stdout.strip()
                    else:
                        git_info['current_branch'] = 'unknown'
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    git_info['current_branch'] = 'unknown'
                
                # Get git status
                try:
                    result = subprocess.run(
                        ['git', 'status', '--short'], 
                        capture_output=True, text=True, cwd=working_dir, timeout=5
                    )
                    if result.returncode == 0:
                        git_info['git_status'] = result.stdout.strip()
                    else:
                        git_info['git_status'] = ''
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    git_info['git_status'] = ''
                
                # Get recent commits
                try:
                    result = subprocess.run(
                        ['git', 'log', '-n', '5', '--pretty=format:"%h %s"'], 
                        capture_output=True, text=True, cwd=working_dir, timeout=5
                    )
                    if result.returncode == 0:
                        git_info['recent_commits'] = result.stdout.strip()
                    else:
                        git_info['recent_commits'] = ''
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    git_info['recent_commits'] = ''
                
                system_status['git_status'] = git_info
                
            except Exception as git_error:
                logger.error(f"Error getting git information: {git_error}")
                system_status['git_status'] = {
                    'main_branch': 'main',
                    'current_branch': 'unknown',
                    'git_status': '',
                    'recent_commits': ''
                }
        
        return  system_status
        
    except Exception as e:
        logger.error(f"Error in get_system_status: {e}")
        return {
            "error": f'Error getting system status: {str(e)}'
        }