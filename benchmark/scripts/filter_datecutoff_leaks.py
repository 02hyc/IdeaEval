"""Filter papers whose original publication is searchable before arXiv date.

This script creates a filtered copy of the clustering outputs.  It does not
modify the source directory.

Typical usage:
    uv run python scripts/filter_datecutoff_leaks.py --workers 8

For a quick smoke test:
    uv run python scripts/filter_datecutoff_leaks.py --max-papers 20 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import aiohttp
from openai import AsyncOpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "experiments/clustering_results_taxoadapt_rebalanced"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "experiments/clustering_results_taxoadapt_rebalanced_datecutoff_filtered"
CURRENT_ENV = PROJECT_ROOT / ".env"
ARXIV_METADATA = PROJECT_ROOT / "data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl"
CLUSTER_WINDOWS_ALL = PROJECT_ROOT / "datasets/cluster_windows_all.jsonl"

S2_API = "https://api.semanticscholar.org/graph/v1"
TAVILY_API = "https://api.tavily.com/search"
JUDGE_MODEL = os.environ.get("THINKING_MODEL") or os.environ.get("LLM_MODEL", "deepseek-v4-pro")

log = logging.getLogger("datecutoff_filter")


@dataclass
class PaperRecord:
    paper_id: str
    arxiv_id: str = ""
    title: str = ""
    publication_date: str = ""
    abstract: str = ""
    cluster_id: str = ""
    cluster_label: str = ""
    venue: str = ""
    sources: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        if key in os.environ and not override:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def exclusive_end_date(cutoff: str) -> str | None:
    parsed = parse_iso(cutoff)
    if parsed is None:
        return None
    return (parsed - timedelta(days=1)).isoformat()


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def merge_record(papers: dict[str, PaperRecord], raw: dict[str, Any], source: str) -> None:
    pid = str(raw.get("paper_id") or raw.get("arxiv_id") or "").strip()
    if not pid:
        return
    rec = papers.get(pid)
    if rec is None:
        rec = PaperRecord(paper_id=pid)
        papers[pid] = rec
    rec.sources.add(source)
    rec.raw.update({k: v for k, v in raw.items() if v not in (None, "", [])})
    for attr, key in (
        ("arxiv_id", "arxiv_id"),
        ("title", "title"),
        ("publication_date", "publication_date"),
        ("abstract", "abstract"),
        ("cluster_id", "cluster_id"),
        ("cluster_label", "cluster_label"),
        ("venue", "venue"),
    ):
        value = raw.get(key)
        if value and not getattr(rec, attr):
            setattr(rec, attr, str(value))


def load_target_papers(input_dir: Path) -> dict[str, PaperRecord]:
    papers: dict[str, PaperRecord] = {}

    for row in jsonl_records(input_dir / "deep_review_full/papers_metadata_final.jsonl"):
        merge_record(papers, row, "deep_review_full/papers_metadata_final.jsonl")

    for row in jsonl_records(ARXIV_METADATA):
        merge_record(papers, row, "data/arxiv")

    for cluster in jsonl_records(CLUSTER_WINDOWS_ALL):
        for paper in cluster.get("papers") or []:
            enriched = dict(paper)
            enriched.setdefault("cluster_id", cluster.get("cluster_id"))
            enriched.setdefault("cluster_label", cluster.get("cluster_label"))
            merge_record(papers, enriched, "datasets/cluster_windows_all.jsonl")

    for rel in (
        "clusters.json",
        "verification/clusters_labeled.json",
        "verification/clusters_verified.json",
        "deep_review_full/clusters_final.json",
        "deep_review_full/clusters_deep_verified.json",
    ):
        path = input_dir / rel
        if not path.is_file():
            continue
        for cluster in json.loads(path.read_text(encoding="utf-8")):
            for pid in cluster.get("paper_ids") or []:
                pid = str(pid)
                if pid not in papers:
                    papers[pid] = PaperRecord(
                        paper_id=pid,
                        cluster_id=str(cluster.get("cluster_id") or ""),
                        cluster_label=str(cluster.get("label") or cluster.get("cluster_label") or ""),
                    )
                else:
                    if not papers[pid].cluster_id:
                        papers[pid].cluster_id = str(cluster.get("cluster_id") or "")
                    if not papers[pid].cluster_label:
                        papers[pid].cluster_label = str(cluster.get("label") or cluster.get("cluster_label") or "")
                papers[pid].sources.add(rel)

    return papers


def is_pre_cutoff(value: str | None, cutoff: str) -> bool:
    pub = parse_iso(value)
    cut = parse_iso(cutoff)
    return bool(pub is not None and cut is not None and pub < cut)


def is_tavily_suspect(paper: PaperRecord) -> bool:
    venue = normalize_title(paper.venue)
    if venue and venue not in {"arxiv", "arxiv org"}:
        return True
    iclr_type = str(paper.raw.get("iclr_type") or "").strip()
    if iclr_type:
        return True
    return False


class DateCutoffSearcher:
    def __init__(
        self,
        *,
        s2_keys: list[str],
        tavily_key: str,
        openai_key: str,
        openai_base_url: str,
        workers: int,
        tavily_mode: str,
    ):
        self.s2_keys = s2_keys
        self.tavily_key = tavily_key
        self.tavily_mode = tavily_mode
        self.s2_key_index = 0
        self.s2_lock = asyncio.Lock()
        self.search_sem = asyncio.Semaphore(workers)
        self.llm_sem = asyncio.Semaphore(max(1, min(3, workers)))
        self.llm = AsyncOpenAI(api_key=openai_key, base_url=openai_base_url or None)

    async def _s2_headers(self) -> dict[str, str]:
        if not self.s2_keys:
            return {}
        async with self.s2_lock:
            key = self.s2_keys[self.s2_key_index % len(self.s2_keys)]
            self.s2_key_index += 1
        return {"x-api-key": key}

    async def search_s2(self, session: aiohttp.ClientSession, paper: PaperRecord) -> list[dict[str, Any]]:
        cutoff_end = exclusive_end_date(paper.publication_date)
        if not cutoff_end or not paper.title:
            return []
        params = {
            "query": paper.title,
            "limit": "5",
            "fields": "title,abstract,year,citationCount,url,publicationDate,externalIds,venue",
            "publicationDateOrYear": f":{cutoff_end}",
        }
        for attempt in range(5):
            headers = await self._s2_headers()
            try:
                async with session.get(f"{S2_API}/paper/search", params=params, headers=headers, timeout=30) as resp:
                    if resp.status in (429, 403, 500, 502, 503, 504):
                        await asyncio.sleep(min(20, 2**attempt))
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        return [{"source": "s2_error", "error": f"HTTP {resp.status}: {text[:200]}"}]
                    data = await resp.json()
                    out = []
                    for item in data.get("data") or []:
                        pub = item.get("publicationDate") or ""
                        if not is_pre_cutoff(pub, paper.publication_date):
                            continue
                        sim = title_similarity(paper.title, item.get("title") or "")
                        if sim < 0.82:
                            continue
                        out.append({
                            "source": "semantic_scholar",
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "publication_date": pub,
                            "year": item.get("year"),
                            "venue": item.get("venue"),
                            "citation_count": item.get("citationCount"),
                            "external_ids": item.get("externalIds") or {},
                            "abstract": item.get("abstract"),
                            "title_similarity": round(sim, 4),
                        })
                    return out
            except Exception as exc:
                if attempt == 4:
                    return [{"source": "s2_error", "error": str(exc)}]
                await asyncio.sleep(min(20, 2**attempt))
        return []

    async def search_tavily(self, session: aiohttp.ClientSession, paper: PaperRecord) -> list[dict[str, Any]]:
        if not self.tavily_key or not paper.title:
            return []
        payload = {
            "query": f'"{paper.title}"',
            "max_results": 5,
            "search_depth": "basic",
            "include_raw_content": "markdown",
            "include_images": False,
            "end_date": paper.publication_date,
        }
        headers = {
            "Authorization": f"Bearer {self.tavily_key}",
            "Content-Type": "application/json",
            "X-Client-Source": "paper-pipeline-datecutoff-filter",
        }
        for attempt in range(5):
            try:
                async with session.post(TAVILY_API, json=payload, headers=headers, timeout=60) as resp:
                    if resp.status in (429, 500, 502, 503, 504):
                        await asyncio.sleep(min(20, 2**attempt))
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        return [{"source": "tavily_error", "error": f"HTTP {resp.status}: {text[:200]}"}]
                    data = await resp.json()
                    out = []
                    for item in data.get("results") or []:
                        pub = item.get("published_date") or ""
                        if not is_pre_cutoff(pub, paper.publication_date):
                            continue
                        title = item.get("title") or ""
                        content = item.get("content") or item.get("raw_content") or ""
                        sim = max(title_similarity(paper.title, title), title_similarity(paper.title, content[:400]))
                        if sim < 0.55 and normalize_title(paper.title) not in normalize_title(content[:2000]):
                            continue
                        out.append({
                            "source": "tavily",
                            "title": title,
                            "url": item.get("url"),
                            "publication_date": pub,
                            "score": item.get("score"),
                            "content": content[:6000],
                            "title_similarity": round(sim, 4),
                        })
                    return out
            except Exception as exc:
                if attempt == 4:
                    return [{"source": "tavily_error", "error": str(exc)}]
                await asyncio.sleep(min(20, 2**attempt))
        return []

    async def judge(self, paper: PaperRecord, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        real_evidence = [e for e in evidence if not str(e.get("source", "")).endswith("_error")]
        if not real_evidence:
            return {
                "decision": "keep",
                "filter_out": False,
                "confidence": 1.0,
                "reason": "No confirmed pre-cutoff search results matched this title.",
            }

        # Deterministic high-precision path for exact S2 pre-cutoff matches.
        for item in real_evidence:
            if item.get("source") == "semantic_scholar" and float(item.get("title_similarity") or 0) >= 0.97:
                ext = item.get("external_ids") or {}
                if str(ext.get("ArXiv") or "").replace("v1", "") != (paper.arxiv_id or paper.paper_id):
                    return {
                        "decision": "filter",
                        "filter_out": True,
                        "confidence": 0.95,
                        "reason": "Semantic Scholar has an exact pre-cutoff title match for the same paper.",
                        "matched_source": item,
                    }

        async with self.llm_sem:
            prompt = {
                "paper": {
                    "paper_id": paper.paper_id,
                    "arxiv_id": paper.arxiv_id,
                    "title": paper.title,
                    "arxiv_publication_date": paper.publication_date,
                    "abstract": paper.abstract[:1500],
                },
                "pre_cutoff_evidence": real_evidence,
            }
            resp = await self.llm.chat.completions.create(
                model=JUDGE_MODEL,
                temperature=0.0,
                max_tokens=700,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Decide whether search evidence shows the same original paper was publicly "
                            "findable before its arXiv publication date. Filter only for strong same-paper "
                            "evidence: exact/near-exact title, same authors/venue/DOI/OpenReview/conference "
                            "page, or content clearly describing the paper. Do not filter on weak mentions, "
                            "undated pages, citations, or merely related work. Return only JSON with keys: "
                            "filter_out boolean, confidence number 0-1, reason string, matched_url string."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            )
        text = resp.choices[0].message.content or "{}"
        parsed = parse_json_object(text)
        filter_out = bool(parsed.get("filter_out"))
        return {
            "decision": "filter" if filter_out else "keep",
            "filter_out": filter_out,
            "confidence": float(parsed.get("confidence") or 0.0),
            "reason": str(parsed.get("reason") or ""),
            "matched_url": str(parsed.get("matched_url") or ""),
        }

    async def evaluate(self, session: aiohttp.ClientSession, paper: PaperRecord) -> dict[str, Any]:
        if not paper.publication_date or parse_iso(paper.publication_date) is None:
            return {
                "paper_id": paper.paper_id,
                "decision": "keep",
                "filter_out": False,
                "confidence": 1.0,
                "reason": "Missing or invalid arXiv publication_date.",
                "status": "missing_cutoff_date",
                "evidence": [],
            }
        async with self.search_sem:
            s2 = await self.search_s2(session, paper)
            tavily = []
            s2_has_real_evidence = any(not str(e.get("source", "")).endswith("_error") for e in s2)
            should_search_tavily = (
                self.tavily_mode == "all"
                or (self.tavily_mode == "suspect" and (is_tavily_suspect(paper) or s2_has_real_evidence))
            )
            if should_search_tavily:
                tavily = await self.search_tavily(session, paper)
        evidence = s2 + tavily
        real_evidence = [e for e in evidence if not str(e.get("source", "")).endswith("_error")]
        error_evidence = [e for e in evidence if str(e.get("source", "")).endswith("_error")]
        if error_evidence and not real_evidence:
            return {
                "paper_id": paper.paper_id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "publication_date": paper.publication_date,
                "cluster_id": paper.cluster_id,
                "cluster_label": paper.cluster_label,
                "venue": paper.venue,
                "status": "search_error",
                "evidence": evidence,
                "decision": "keep",
                "filter_out": False,
                "confidence": 0.0,
                "reason": "All search providers failed; rerun will retry this paper.",
            }
        judgment = await self.judge(paper, evidence)
        return {
            "paper_id": paper.paper_id,
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "publication_date": paper.publication_date,
            "cluster_id": paper.cluster_id,
            "cluster_label": paper.cluster_label,
            "venue": paper.venue,
            "status": "judged",
            "evidence": evidence,
            **judgment,
        }


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            pid = row.get("paper_id")
            if row.get("status") in {"error", "search_error"}:
                continue
            if row.get("status") == "skipped_not_suspect":
                continue
            evidence = row.get("evidence") or []
            has_real_evidence = any(not str(e.get("source", "")).endswith("_error") for e in evidence)
            has_error_evidence = any(str(e.get("source", "")).endswith("_error") for e in evidence)
            if has_error_evidence and not has_real_evidence:
                continue
            if pid:
                out[str(pid)] = row
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run_evaluations(
    papers: list[PaperRecord],
    cache_path: Path,
    *,
    workers: int,
    limit: int | None,
    tavily_mode: str,
) -> dict[str, dict[str, Any]]:
    s2_keys = [k.strip() for k in os.environ.get("S2_API_KEYS", "").split(",") if k.strip()]
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is required in current project .env")
    if not tavily_key:
        raise RuntimeError("TAVILY_API_KEY is required; expected in benchmark/.env")

    cached = load_cache(cache_path)
    pending = [p for p in papers if p.paper_id not in cached]
    if limit is not None:
        pending = pending[:limit]
    log.info("Loaded %d cached judgments; pending=%d", len(cached), len(pending))

    if not pending:
        return cached

    searcher = DateCutoffSearcher(
        s2_keys=s2_keys,
        tavily_key=tavily_key,
        openai_key=openai_key,
        openai_base_url=openai_base_url,
        workers=workers,
        tavily_mode=tavily_mode,
    )
    timeout = aiohttp.ClientTimeout(total=120)
    connector = aiohttp.TCPConnector(limit=max(8, workers * 3))
    done = 0
    failed = 0
    started = time.time()
    write_lock = asyncio.Lock()

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        queue: asyncio.Queue[PaperRecord] = asyncio.Queue()
        for paper in pending:
            queue.put_nowait(paper)

        async def worker() -> None:
            nonlocal done, failed
            while True:
                try:
                    paper = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    row = await searcher.evaluate(session, paper)
                    cached[paper.paper_id] = row
                    async with write_lock:
                        append_jsonl(cache_path, row)
                    done += 1
                except Exception as exc:
                    failed += 1
                    row = {
                        "paper_id": paper.paper_id,
                        "arxiv_id": paper.arxiv_id,
                        "title": paper.title,
                        "publication_date": paper.publication_date,
                        "decision": "keep",
                        "filter_out": False,
                        "confidence": 0.0,
                        "status": "error",
                        "reason": str(exc),
                        "evidence": [],
                    }
                    cached[paper.paper_id] = row
                    async with write_lock:
                        append_jsonl(cache_path, row)
                    log.warning("Failed %s: %s", paper.paper_id, exc)
                finally:
                    total = done + failed
                    if total % 50 == 0 or total == len(pending):
                        elapsed = max(1e-6, time.time() - started)
                        rate = done / elapsed
                        eta = (len(pending) - total) / rate if rate > 0 else 0
                        filtered = sum(1 for r in cached.values() if r.get("filter_out"))
                        log.info(
                            "[%d/%d] done=%d failed=%d filtered=%d rate=%.2f/s eta=%.1fmin",
                            total, len(pending), done, failed, filtered, rate, eta / 60,
                        )
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(max(1, workers))))

    return cached


def copy_input_tree(input_dir: Path, output_dir: Path) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    ignore = shutil.ignore_patterns("__pycache__", ".DS_Store")
    shutil.copytree(input_dir, output_dir, ignore=ignore)


def filter_json_clusters(path: Path, removed: set[str]) -> None:
    if not path.is_file():
        return
    clusters = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for cluster in clusters:
        paper_ids = [pid for pid in cluster.get("paper_ids", []) if pid not in removed]
        if not paper_ids:
            continue
        cluster["paper_ids"] = paper_ids
        cluster["n_papers"] = len(paper_ids)
        out.append(cluster)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def filter_jsonl_paper_file(path: Path, removed: set[str]) -> None:
    if not path.is_file():
        return
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if str(row.get("paper_id")) not in removed:
                rows.append(row)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def recompute_stats(output_dir: Path) -> None:
    clusters_path = output_dir / "clusters.json"
    if not clusters_path.is_file():
        return
    clusters = json.loads(clusters_path.read_text(encoding="utf-8"))
    sizes = [len(c.get("paper_ids") or []) for c in clusters]
    stats = {
        "n_clusters": len(clusters),
        "n_papers_covered": sum(sizes),
        "size_min": min(sizes) if sizes else 0,
        "size_max": max(sizes) if sizes else 0,
        "size_avg": (sum(sizes) / len(sizes)) if sizes else 0,
        "below_min": sum(1 for s in sizes if s < 30),
        "above_max": sum(1 for s in sizes if s > 50),
        "min_size": 30,
        "max_size": 50,
    }
    (output_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_filtered_outputs(input_dir: Path, output_dir: Path, judgments: dict[str, dict[str, Any]]) -> None:
    removed = {pid for pid, row in judgments.items() if row.get("filter_out")}
    copy_input_tree(input_dir, output_dir)

    for rel in (
        "clusters.json",
        "verification/clusters_labeled.json",
        "verification/clusters_verified.json",
        "deep_review_full/clusters_final.json",
        "deep_review_full/clusters_deep_verified.json",
    ):
        filter_json_clusters(output_dir / rel, removed)

    for rel in (
        "deep_review_full/papers_metadata_final.jsonl",
        "deep_review_full/paper_type_classification.jsonl",
        "deep_review_full/paper_type_v2.jsonl",
        "deep_review_full/iclr2026_matched.jsonl",
    ):
        filter_jsonl_paper_file(output_dir / rel, removed)

    recompute_stats(output_dir)

    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "n_results": len(judgments),
        "n_external_searched": sum(1 for row in judgments.values() if row.get("status") == "judged"),
        "n_skipped_not_suspect": sum(1 for row in judgments.values() if row.get("status") == "skipped_not_suspect"),
        "n_filtered": len(removed),
        "n_kept": len(judgments) - len(removed),
        "n_errors": sum(1 for row in judgments.values() if row.get("status") == "error"),
        "n_missing_cutoff_date": sum(1 for row in judgments.values() if row.get("status") == "missing_cutoff_date"),
        "filtered_by_cluster": {},
    }
    for row in judgments.values():
        if row.get("filter_out"):
            cid = row.get("cluster_id") or ""
            summary["filtered_by_cluster"][cid] = summary["filtered_by_cluster"].get(cid, 0) + 1

    (output_dir / "datecutoff_leak_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "datecutoff_leak_results.jsonl").open("w", encoding="utf-8") as f:
        for pid in sorted(judgments):
            f.write(json.dumps(judgments[pid], ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_OUTPUT_DIR / "datecutoff_leak_results.jsonl")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-papers", type=int, default=None, help="Only judge this many uncached papers, for smoke tests.")
    parser.add_argument(
        "--tavily-mode",
        choices=("suspect", "all", "off"),
        default="suspect",
        help=(
            "Tavily WebSearch scope. 'suspect' runs Tavily only for non-arXiv/ICLR/S2-hit papers; "
            "'all' searches every paper; 'off' uses only Semantic Scholar."
        ),
    )
    parser.add_argument(
        "--candidate-mode",
        choices=("suspect", "all"),
        default="suspect",
        help=(
            "Which papers receive external searches. 'suspect' searches papers with non-arXiv venue/ICLR markers "
            "and keeps other papers with a metadata skip record; 'all' searches every paper."
        ),
    )
    parser.add_argument("--only-output", action="store_true", help="Skip API calls and regenerate filtered outputs from cache.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    load_env_file(CURRENT_ENV, override=False)

    papers_by_id = load_target_papers(args.input_dir)
    all_papers = sorted(papers_by_id.values(), key=lambda p: p.paper_id)
    if args.candidate_mode == "suspect":
        papers = [p for p in all_papers if is_tavily_suspect(p)]
    else:
        papers = all_papers
    log.info(
        "Loaded %d unique target papers; external-search candidates=%d (candidate_mode=%s)",
        len(all_papers),
        len(papers),
        args.candidate_mode,
    )
    args.cache.parent.mkdir(parents=True, exist_ok=True)

    if args.only_output:
        judgments = load_cache(args.cache)
    else:
        judgments = asyncio.run(
            run_evaluations(
                papers,
                args.cache,
                workers=max(1, args.workers),
                limit=args.max_papers,
                tavily_mode=args.tavily_mode,
            )
        )

    if args.candidate_mode == "suspect":
        candidate_ids = {p.paper_id for p in papers}
        for paper in all_papers:
            if paper.paper_id in judgments or paper.paper_id in candidate_ids:
                continue
            judgments[paper.paper_id] = {
                "paper_id": paper.paper_id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "publication_date": paper.publication_date,
                "cluster_id": paper.cluster_id,
                "cluster_label": paper.cluster_label,
                "venue": paper.venue,
                "status": "skipped_not_suspect",
                "decision": "keep",
                "filter_out": False,
                "confidence": 1.0,
                "reason": "No non-arXiv venue or ICLR/OpenReview marker in local metadata; external search skipped in suspect mode.",
                "evidence": [],
            }

    for paper in all_papers:
        row = judgments.get(paper.paper_id)
        if not row:
            continue
        if not row.get("cluster_id") and paper.cluster_id:
            row["cluster_id"] = paper.cluster_id
        if not row.get("cluster_label") and paper.cluster_label:
            row["cluster_label"] = paper.cluster_label
        if not row.get("venue") and paper.venue:
            row["venue"] = paper.venue

    write_filtered_outputs(args.input_dir, args.output_dir, judgments)
    log.info("Wrote filtered output to %s", args.output_dir)


if __name__ == "__main__":
    main()
