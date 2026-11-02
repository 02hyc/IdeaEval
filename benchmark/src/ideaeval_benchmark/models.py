"""Shared data structures for the paper processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PaperInput:
    """Input specification for a single paper."""
    paper_id: str
    title: str = ""
    arxiv_id: str = ""


@dataclass
class S2Metadata:
    """Output of the Semantic Scholar lookup stage."""
    s2_paper_id: Optional[str] = None
    s2_title: Optional[str] = None
    citation_count: Optional[int] = None
    year: Optional[int] = None
    publication_date: Optional[str] = None  # YYYY-MM-DD
    match_method: Optional[str] = None


@dataclass
class ConversionResult:
    """Output of the PDF-to-markdown conversion stage."""
    markdown: str = ""
    source: str = ""       # "somark" or "failed"
    chars: int = 0
    tables_found: int = 0


@dataclass
class BenchmarkResult:
    """Output of the benchmark extraction stage."""
    benchmarks_or_datasets: list[str] = field(default_factory=list)
    table_summary: str = ""
    tables_used: int = 0


@dataclass
class PaperResult:
    """Composite result of all pipeline stages for one paper."""
    paper_id: str
    title: str = ""
    arxiv_id: str = ""
    # S2 metadata
    s2_paper_id: Optional[str] = None
    s2_title: Optional[str] = None
    citation_count: Optional[int] = None
    year: Optional[int] = None
    publication_date: Optional[str] = None  # YYYY-MM-DD
    s2_match_method: Optional[str] = None
    # Conversion
    markdown_source: str = ""
    markdown_chars: int = 0
    tables_found: int = 0
    # Benchmark extraction
    benchmarks_or_datasets: list[str] = field(default_factory=list)
    table_summary: str = ""
    tables_used: int = 0
    # Pipeline info
    stages_completed: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
