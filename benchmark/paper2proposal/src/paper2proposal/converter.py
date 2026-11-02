"""Paper-to-proposal converter using direct LLM call (no agent loop)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from paper2proposal.llm_client import LLMClient, load_llm_config

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG_PATH = _MODULE_DIR / "default_config.yaml"
_PROMPTS_DIR = _MODULE_DIR / "prompts"

REQUIRED_HEADINGS = [
    "## Introduction",
    "## Proposed Approach",
    "## Experiments",
    "## Success Criteria",
]


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def validate_proposal(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size == 0:
        return False, "file missing or empty"
    content = path.read_text(encoding="utf-8")
    missing = [h for h in REQUIRED_HEADINGS if h not in content]
    if missing:
        return False, f"missing headings: {missing}"
    return True, "ok"


class PaperConverter:
    """Single-call paper-to-proposal converter."""

    def __init__(self, config_path: Optional[str] = None):
        cfg = load_llm_config(config_path or str(_DEFAULT_CONFIG_PATH))
        self.llm = LLMClient(cfg)
        self._system_prompt = _load_prompt("system.md")
        self._user_template = _load_prompt("user.md")

    async def convert(self, paper_path: Path, output_path: Path) -> tuple[bool, str]:
        """Convert a single paper to proposal. Returns (success, reason)."""
        paper_content = paper_path.read_text(encoding="utf-8")
        user_prompt = self._user_template.format(output_filename=output_path.name)
        user_message = f"{user_prompt}\n\n---\n\n{paper_content}"

        messages = [
            {"role": "user", "content": [{"type": "text", "text": user_message}]}
        ]

        try:
            response = await self.llm.call(
                messages=messages,
                system_prompt=self._system_prompt,
            )
        except Exception as e:
            return False, f"LLM call failed: {e}"

        text = _extract_text(response)
        if not text:
            return False, "LLM returned empty response"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

        valid, reason = validate_proposal(output_path)
        if not valid:
            output_path.rename(output_path.with_suffix(".md.invalid"))
            return False, f"validation failed: {reason}"

        return True, "ok"


def _extract_text(response: dict) -> str:
    """Extract text content from LLM response."""
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return _strip_markdown_fence(block["text"])
    return ""


def _strip_markdown_fence(text: str) -> str:
    """Remove wrapping ```markdown ... ``` if present."""
    stripped = text.strip()
    if stripped.startswith("```markdown"):
        stripped = stripped[len("```markdown"):].lstrip("\n")
    elif stripped.startswith("```"):
        stripped = stripped[3:].lstrip("\n")
    if stripped.endswith("```"):
        stripped = stripped[:-3].rstrip("\n")
    return stripped


async def run_conversions(
    paper_ids: list[str],
    papers_dir: Path,
    output_dir: Path,
    workers: int = 4,
    config_path: Optional[str] = None,
) -> None:
    """Convert papers to proposals in parallel with checkpoint/resume."""
    output_dir.mkdir(parents=True, exist_ok=True)

    def _output_path(pid: str) -> Path:
        return output_dir / pid / f"{pid}_proposal.md"

    pending = [
        pid for pid in paper_ids
        if not validate_proposal(_output_path(pid))[0]
    ]

    if not pending:
        logger.info("All %d proposals already generated", len(paper_ids))
        return

    already_done = len(paper_ids) - len(pending)
    logger.info(
        "Converting %d papers (%d already done, workers=%d)",
        len(pending), already_done, workers,
    )

    converter = PaperConverter(config_path)
    sem = asyncio.Semaphore(workers)
    t0 = time.time()

    async def _convert_one(pid: str) -> tuple[str, bool, str]:
        async with sem:
            paper_path = papers_dir / f"{pid}.md"
            if not paper_path.exists():
                return pid, False, f"paper not found: {paper_path}"
            out = _output_path(pid)
            ok, reason = await converter.convert(paper_path, out)
            return pid, ok, reason

    results = await asyncio.gather(*[_convert_one(pid) for pid in pending])

    ok_count = 0
    for pid, ok, reason in results:
        if ok:
            ok_count += 1
            logger.info("[%d/%d] %s: ok", ok_count, len(pending), pid)
        else:
            logger.warning("%s: %s", pid, reason)

    elapsed = time.time() - t0
    logger.info("Done: %d/%d succeeded in %.1fs", ok_count, len(pending), elapsed)
