"""MVP mechanical proposal validator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from research_ideation_agent.constants import CHECKED_SECTION_IDS
from research_ideation_agent.models import ValidationResult


VALIDATOR_VERSION = "mvp-structure-v1"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_NUMERIC_PREFIX_RE = re.compile(r"^\s*\d+\s*(?:[.)]|-)\s*")
_SEPARATOR_RE = re.compile(r"[\s_-]+")

_ALIASES = {
    "introduction": {
        "introduction",
        "intro",
        "overview",
        "background",
        "motivation",
        "problem statement",
        "research motivation",
    },
    "related_work": {
        "related work",
        "prior work",
        "related literature",
        "literature review",
        "background and related work",
        "existing work",
        "previous work",
        "prior art",
        "related research",
    },
    "methods": {
        "methods",
        "method",
        "methodology",
        "approach",
        "proposed method",
        "proposed approach",
        "technical approach",
        "research plan",
        "proposed framework",
        "system design",
        "method design",
    },
    "experiments_design": {
        "experiments design",
        "experiment design",
        "experimental design",
        "experiments",
        "evaluation plan",
        "experimental plan",
        "evaluation",
        "experiments and evaluation",
        "evaluation and experiments",
        "experimental setup",
        "experiment setup",
        "evaluation setup",
        "study design",
        "empirical evaluation",
        "validation plan",
    },
    "references": {
        "references",
        "bibliography",
        "works cited",
        "sources",
        "citations",
        "cited works",
    },
}


@dataclass(frozen=True)
class _Heading:
    level: int
    text: str
    line_index: int


def validate_proposal(proposal_path: Path) -> ValidationResult:
    """Validate `proposal.md` for existence and MVP-checked sections only."""

    errors: list[str] = []
    try:
        content = proposal_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("proposal.md does not exist.")
        return _invalid(proposal_path, errors)
    except UnicodeDecodeError:
        errors.append("proposal.md is not valid UTF-8.")
        return _invalid(proposal_path, errors)
    except OSError as exc:
        errors.append(f"proposal.md could not be read: {exc.__class__.__name__}.")
        return _invalid(proposal_path, errors)

    lines = content.splitlines()
    headings = _parse_atx_headings(lines)
    present: list[str] = []

    if _title_present(headings):
        present.append("title")

    section_presence = _section_presence(headings, lines)
    for intent in CHECKED_SECTION_IDS:
        if intent == "title":
            continue
        if section_presence.get(intent, False):
            present.append(intent)

    missing = [intent for intent in CHECKED_SECTION_IDS if intent not in present]
    return ValidationResult(
        valid=not missing and not errors,
        proposal_path=proposal_path,
        missing=missing,
        present=present,
        errors=errors,
        validator_version=VALIDATOR_VERSION,
    )


def _invalid(proposal_path: Path, errors: list[str]) -> ValidationResult:
    return ValidationResult(
        valid=False,
        proposal_path=proposal_path,
        missing=list(CHECKED_SECTION_IDS),
        present=[],
        errors=errors,
        validator_version=VALIDATOR_VERSION,
    )


def _parse_atx_headings(lines: list[str]) -> list[_Heading]:
    headings: list[_Heading] = []
    in_fence = False
    fence_marker: str | None = None
    for index, line in enumerate(lines):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if in_fence and marker == fence_marker:
                in_fence = False
                fence_marker = None
            elif not in_fence:
                in_fence = True
                fence_marker = marker
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        text = _strip_closing_hashes(match.group(2))
        if text:
            headings.append(_Heading(level=level, text=text, line_index=index))
    return headings


def _strip_closing_hashes(text: str) -> str:
    return re.sub(r"\s+#+\s*$", "", text).strip()


def _title_present(headings: list[_Heading]) -> bool:
    if not headings:
        return False
    first = headings[0]
    return first.level == 1 and bool(first.text.strip())


def _section_presence(headings: list[_Heading], lines: list[str]) -> dict[str, bool]:
    presence = {intent: False for intent in _ALIASES}
    for index, heading in enumerate(headings):
        if heading.level < 2:
            continue
        intent = _intent_for_heading(heading.text)
        if intent is None or presence[intent]:
            continue
        next_line = _next_section_boundary(headings, index, len(lines))
        body = "\n".join(lines[heading.line_index + 1 : next_line]).strip()
        if body:
            presence[intent] = True
    return presence


def _next_section_boundary(headings: list[_Heading], index: int, fallback: int) -> int:
    current = headings[index]
    for later in headings[index + 1 :]:
        if later.level <= current.level:
            return later.line_index
    return fallback


def _intent_for_heading(text: str) -> str | None:
    normalized = _normalize_heading(text)
    for intent, aliases in _ALIASES.items():
        if normalized in aliases:
            return intent
    return None


def _normalize_heading(text: str) -> str:
    stripped = _strip_closing_hashes(text).lower().strip()
    stripped = _NUMERIC_PREFIX_RE.sub("", stripped)
    stripped = stripped.replace("&", " and ")
    stripped = stripped.replace("+", " and ")
    stripped = stripped.replace("/", " ")
    stripped = stripped.rstrip(":").strip()
    normalized = _SEPARATOR_RE.sub(" ", stripped).strip()
    if normalized.endswith(" section"):
        normalized = normalized[: -len(" section")].strip()
    return normalized
