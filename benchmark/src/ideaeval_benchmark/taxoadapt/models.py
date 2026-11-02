"""Data structures for TaxoAdapt clustering."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PaperRecord:
    paper_id: str
    title: str
    abstract: str
    publication_date: str = ""
    benchmarks: list[str] = field(default_factory=list)
    research_problem: str = ""
    task_formulation: str = ""


@dataclass
class Config:
    input_path: Path
    output_dir: Path
    benchmarks_path: Optional[Path] = None
    fields_path: Optional[Path] = None
    min_leaf: int = 10
    max_leaf: int = 50
    max_depth: int = 6
    initial_topics: int = 20
    seed: int = 42
    max_workers: int = 100
    # Cap on how many leaves/L1-nodes expand in parallel at any time.
    # Effective peak LLM concurrency ≈ max_parallel_leaves × max_workers,
    # bounded above by OPENAI_MAX_CONNECTIONS (default 512).
    max_parallel_leaves: int = 50


@dataclass
class TaxoNode:
    id: str
    label: str
    description: str = ""
    children: list[TaxoNode] = field(default_factory=list)
    paper_ids: list[str] = field(default_factory=list)
    level: int = 0
    expansion_resistant: bool = False

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def density(self) -> int:
        if self.is_leaf:
            return len(self.paper_ids)
        return sum(c.density for c in self.children)

    def all_leaves(self) -> list[TaxoNode]:
        if self.is_leaf:
            return [self]
        leaves: list[TaxoNode] = []
        for child in self.children:
            leaves.extend(child.all_leaves())
        return leaves

    def find_node(self, node_id: str) -> Optional[TaxoNode]:
        if self.id == node_id:
            return self
        for child in self.children:
            found = child.find_node(node_id)
            if found:
                return found
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "children": [c.to_dict() for c in self.children],
        }

    def to_full_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "paper_ids": self.paper_ids,
            "level": self.level,
            "children": [c.to_full_dict() for c in self.children],
        }
        if self.expansion_resistant:
            d["expansion_resistant"] = True
        return d

    @staticmethod
    def from_full_dict(d: dict, level: int = 0) -> TaxoNode:
        node = TaxoNode(
            id=d.get("id", f"node_{level}"),
            label=d.get("label", "Unknown"),
            description=d.get("description", ""),
            level=d.get("level", level),
            paper_ids=d.get("paper_ids", []),
            expansion_resistant=d.get("expansion_resistant", False),
        )
        for child_dict in d.get("children", []):
            node.children.append(TaxoNode.from_full_dict(child_dict, level=level + 1))
        return node

    def nodes_at_level(self, target_level: int) -> list[TaxoNode]:
        """Return all nodes at a specific depth level via BFS."""
        result: list[TaxoNode] = []
        queue = [self]
        while queue:
            node = queue.pop(0)
            if node.level == target_level:
                result.append(node)
            else:
                queue.extend(node.children)
        return result

    def depth(self) -> int:
        if self.is_leaf:
            return 0
        return 1 + max(c.depth() for c in self.children)


def load_papers(input_path: Path) -> list[PaperRecord]:
    papers = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            papers.append(PaperRecord(
                paper_id=r.get("paper_id", ""),
                title=r.get("title", ""),
                abstract=r.get("abstract", ""),
                publication_date=r.get("publication_date", r.get("publicationDate", "")),
                benchmarks=[],  # filled later from benchmarks file
            ))
    return papers


def load_benchmarks(benchmarks_path: Path) -> dict[str, list[str]]:
    bm_map: dict[str, list[str]] = {}
    with open(benchmarks_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            pid = r.get("paper_id", "")
            bm = r.get("benchmarks_or_datasets", [])
            if pid and bm:
                bm_map[pid] = bm
    return bm_map


def load_fields(fields_path: Path) -> dict[str, dict[str, str]]:
    """Load extracted fields (research_problem, task_formulation)."""
    fields_map: dict[str, dict[str, str]] = {}
    with open(fields_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            pid = r.get("paper_id", "")
            if pid:
                fields_map[pid] = {
                    "research_problem": r.get("research_problem", ""),
                    "task_formulation": r.get("task_formulation", ""),
                }
    return fields_map
