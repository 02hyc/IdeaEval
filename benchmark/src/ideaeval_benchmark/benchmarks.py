"""Benchmark/dataset extraction from paper markdown using tables + LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from .llm_client import LLMClient
from .models import PaperInput, BenchmarkResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------
EVAL_SECTION_KEYWORDS = (
    "experiment", "evaluation", "result", "benchmark", "dataset",
    "ablation", "comparison", "performance", "analysis",
)
TABLE_CONTEXT_KEYWORDS = (
    "benchmark", "dataset", "evaluate", "accuracy", "f1", "bleu",
    "rouge", "metric", "score", "baseline", "performance", "sota",
    "state-of-the-art", "comparison", "result",
)
# Tables whose caption/context matches these patterns describe dataset
# construction rather than evaluation, and should be excluded.
COMPOSITION_KEYWORDS = (
    "composition", "constructed from", "constructed by",
    "statistics of", "overview of", "summary of",
    "sourcing", "collected from", "assembled from",
)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MD_TABLE_RE = re.compile(r"(\|.+\|(?:\n\|[-:| ]+\|)(?:\n\|.+\|)+)", re.MULTILINE)
_HTML_TABLE_RE = re.compile(r"(<table[\s\S]*?</table>)", re.IGNORECASE)
_CAPTION_RE = re.compile(r"(?:Table\s+\d+[.:]\s*)(.+?)(?:\n|$)", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"\d+\.\d+")


@dataclass
class TableExcerpt:
    table_content: str
    section_heading: str
    caption: str
    context_before: str
    context_after: str
    is_numeric: bool


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------
def _split_into_sections(text: str) -> list[tuple[str, str, int]]:
    sections: list[tuple[str, list[str], int]] = []
    current_heading = "preamble"
    current_lines: list[str] = []
    current_start = 0
    pos = 0
    for line in text.splitlines(keepends=True):
        match = _HEADING_RE.match(line.strip())
        if match:
            if current_lines:
                sections.append((current_heading, current_lines, current_start))
            current_heading = match.group(2).strip().lower()
            current_lines = [line]
            current_start = pos
        else:
            current_lines.append(line)
        pos += len(line)
    if current_lines:
        sections.append((current_heading, current_lines, current_start))
    return [(h, "".join(lines), start) for h, lines, start in sections]


def _find_section_for_pos(sections: list[tuple[str, str, int]], pos: int) -> str:
    best = "unknown"
    for heading, _, start in sections:
        if start <= pos:
            best = heading
        else:
            break
    return best


def _has_eval_keywords(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in TABLE_CONTEXT_KEYWORDS)


def _is_composition_table(t: "TableExcerpt") -> bool:
    """Detect tables that describe dataset composition/construction rather than evaluation."""
    text = (t.caption + " " + t.context_before).lower()
    return any(kw in text for kw in COMPOSITION_KEYWORDS)


# ---------------------------------------------------------------------------
# Table excerpt extraction
# ---------------------------------------------------------------------------
def extract_table_excerpts(markdown: str, max_chars: int = 16000) -> tuple[str, int]:
    """Extract evaluation-relevant tables with context. Returns (excerpt, table_count)."""
    sections = _split_into_sections(markdown)
    tables: list[TableExcerpt] = []
    seen_ranges: set = set()

    def _extract_table(match: re.Match) -> Optional[TableExcerpt]:
        table_str = match.group(1)
        start, end = match.start(), match.end()
        range_key = (start, end)
        if range_key in seen_ranges:
            return None
        seen_ranges.add(range_key)

        section = _find_section_for_pos(sections, start)
        ctx_before = markdown[max(0, start - 300):start].strip()
        ctx_after = markdown[end:min(len(markdown), end + 300)].strip()

        caption = ""
        cap_match = _CAPTION_RE.search(ctx_before[-200:] if ctx_before else "")
        if not cap_match:
            cap_match = _CAPTION_RE.search(ctx_after[:200] if ctx_after else "")
        if cap_match:
            caption = cap_match.group(1).strip()

        return TableExcerpt(
            table_content=table_str,
            section_heading=section,
            caption=caption,
            context_before=ctx_before[-200:],
            context_after=ctx_after[:200],
            is_numeric=bool(_NUMERIC_RE.search(table_str)),
        )

    for match in _HTML_TABLE_RE.finditer(markdown):
        excerpt = _extract_table(match)
        if excerpt:
            tables.append(excerpt)
    for match in _MD_TABLE_RE.finditer(markdown):
        excerpt = _extract_table(match)
        if excerpt:
            tables.append(excerpt)

    if not tables:
        eval_text = []
        for heading, body, _ in sections:
            if any(kw in heading for kw in EVAL_SECTION_KEYWORDS):
                eval_text.append(body)
        return "\n\n".join(eval_text)[:max_chars], 0

    def _table_priority(t: TableExcerpt) -> tuple:
        in_eval = any(kw in t.section_heading for kw in EVAL_SECTION_KEYWORDS)
        has_ctx = _has_eval_keywords(t.caption + " " + t.context_before + " " + t.context_after)
        return (not in_eval, not has_ctx, not t.is_numeric)

    # Filter out dataset composition/construction tables
    tables = [t for t in tables if not _is_composition_table(t)]

    tables.sort(key=_table_priority)

    parts: list[str] = []
    total_chars = 0
    used = 0
    for t in tables:
        block = ""
        if t.caption:
            block += f"Caption: {t.caption}\n"
        block += f"Section: {t.section_heading}\n"
        if t.context_before:
            block += f"Context before: {t.context_before}\n"
        block += f"\n{t.table_content}\n"
        if t.context_after:
            block += f"\nContext after: {t.context_after}\n"
        block += "\n---\n"
        if total_chars + len(block) > max_chars:
            break
        parts.append(block)
        total_chars += len(block)
        used += 1

    return "\n".join(parts), used


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------
def _normalize_name(name: str) -> str:
    return re.sub(r"[\s\-_]+", "", name).lower()


def _dedup_spelling_variants(items: list[str]) -> list[str]:
    """Merge items that are spelling variants or abbreviations of each other."""
    if not items:
        return items

    groups: dict[str, str] = {}
    for item in items:
        key = _normalize_name(item)
        if key in groups:
            if len(item) > len(groups[key]):
                groups[key] = item
        else:
            groups[key] = item

    keys = sorted(groups.keys(), key=len, reverse=True)
    merged: dict[str, str] = {}
    for key in keys:
        absorbed = False
        for canon in merged:
            if key in canon:
                if len(groups[key]) > len(merged[canon]):
                    merged[canon] = groups[key]
                absorbed = True
                break
        if not absorbed:
            merged[key] = groups[key]

    kept = set(merged.values())
    result = []
    seen = set()
    for item in items:
        key = _normalize_name(item)
        for canon, best in merged.items():
            if key == canon or key in canon:
                if best not in seen:
                    result.append(best)
                    seen.add(best)
                break
    return result


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------
BENCHMARK_EXTRACT_SYSTEM = """\
You are extracting benchmark and dataset names from ML paper evaluation tables and surrounding text.

Rules:
- Extract EVERY benchmark or dataset name mentioned in the tables and evaluation context into a single unified list. Do NOT distinguish between benchmarks and datasets.
- Use the FULL canonical name for each benchmark/dataset (e.g., "MATH-500" not "MATH", "ImageNet-1K" not "IN-1K", "ARC-Challenge" not "Arc-C", "HellaSwag" not "HS", "Winogrande" not "WG"). If only an abbreviation appears in the paper, expand it to the well-known full name.
- Be aware that the same benchmark/dataset may appear under different names, abbreviations, or spelling variants in different tables or sections (e.g., "WG" and "Winogrande", "HS" and "HellaSwag", "IN-V2" and "ImageNet-V2", "PercepTest" and "Perception-Test" and "PerceptionTest", "L-VideoBench" and "LongVideoBench"). Deduplicate ALL such variants into a SINGLE entry using the most widely recognized full canonical name. Pay special attention to hyphenation, capitalization, spacing differences, and shortened prefixes (e.g., "L-" for "Long") — these are the same benchmark and must not appear as separate entries. When in doubt, prefer the longer, more descriptive name.
- Handle hierarchical benchmarks: when a paper uses a parent benchmark that contains sub-benchmarks (e.g., "GLUE" containing "SST-2", "MNLI", "QQP"; or "SuperGLUE" containing "BoolQ", "WiC") AND the paper reports evaluation results on each sub-benchmark, list BOTH the parent benchmark AND each individual sub-benchmark as separate entries.
- CRITICAL — dataset composition tables: When a table describes the **composition or construction** of a dataset (e.g., "Composition of Dataset-X", "Statistics of Dataset-X", listing source datasets that were merged/collected to build it), extract ONLY the composite dataset name (e.g., "Dataset-X"), NOT the individual source datasets. These source datasets are building blocks, not evaluation targets. Example: if a table titled "Detailed composition of UAVDE-2M" lists 20+ source datasets (CARPK, Cattle, LADD, …), you should extract only "UAVDE-2M" from that table. Only extract a source dataset separately if the paper **also reports performance metrics on it** in a different evaluation table.
- Include benchmarks/datasets the paper **evaluates on** (reports quantitative results), training datasets, and any new benchmark/dataset the paper **introduces**.
- If a table column or row header is a benchmark/dataset name, extract ALL of them.
- Do NOT include model names, method names, or metric names.
- Output only valid JSON.
"""

BENCHMARK_EXTRACT_USER = """\
Paper title: {title}
ArXiv ID: {arxiv_id}

Evaluation tables and context extracted from the paper:

{table_excerpts}

---

Additionally, here is the full paper text (for context on benchmark mentions outside tables):

{full_text_snippet}

---

Extract ALL benchmark and dataset names from the above content into a single list.
Return a JSON object with this exact structure:
{{
  "benchmarks_or_datasets": ["name1", "name2", "name3"],
  "table_summary": "Brief 1-2 sentence description of what the tables evaluate"
}}

If no benchmarks or datasets are found, return an empty list with a note in table_summary.
"""


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------
def _parse_json_response(raw: str) -> Optional[dict]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                continue
    return None


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------
def extract_benchmarks(
    paper: PaperInput,
    table_excerpt: str,
    full_text: str,
    llm: LLMClient,
    max_tokens: int = 2048,
    temperature: float = 0.1,
) -> BenchmarkResult:
    """Use LLM to extract benchmark/dataset names from table excerpts."""
    full_text_snippet = ""
    if full_text:
        sections = _split_into_sections(full_text)
        eval_parts = [body for heading, body, _ in sections
                      if any(kw in heading for kw in EVAL_SECTION_KEYWORDS)]
        full_text_snippet = "\n\n".join(eval_parts)[:6000] if eval_parts else full_text[:6000]

    user_prompt = BENCHMARK_EXTRACT_USER.format(
        title=paper.title,
        arxiv_id=paper.arxiv_id,
        table_excerpts=table_excerpt,
        full_text_snippet=full_text_snippet,
    )

    raw = llm.generate(
        system=BENCHMARK_EXTRACT_SYSTEM,
        user=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    parsed = _parse_json_response(raw)
    if parsed is None:
        log.warning("Failed to parse LLM response for %s", paper.paper_id)
        return BenchmarkResult(table_summary="LLM response parsing failed")

    items = parsed.get("benchmarks_or_datasets", [])
    if not items:
        items = list(dict.fromkeys(
            parsed.get("benchmarks", []) + parsed.get("datasets", [])
        ))

    items = _dedup_spelling_variants(items)

    return BenchmarkResult(
        benchmarks_or_datasets=items,
        table_summary=parsed.get("table_summary", ""),
    )
