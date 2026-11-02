import re
from typing import Optional


_PROMPT_LINE_RE = re.compile(r"^[>$](?:\s+[>$])*$")


def normalize_bash_status(status: Optional[str]) -> str:
    """Map internal bash session states to tool-facing states."""
    if status in (None, "", "idle"):
        return "completed"
    return status


def strip_trailing_prompt_lines(output: Optional[str]) -> str:
    """Remove shell prompt-only lines that are not real command output."""
    if not output:
        return ""

    lines = output.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    while lines and _PROMPT_LINE_RE.fullmatch(lines[0].strip()):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)

    while lines and _PROMPT_LINE_RE.fullmatch(lines[-1].strip()):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()

    return "\n".join(lines).strip()
