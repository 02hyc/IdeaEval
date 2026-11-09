#!/usr/bin/env python3
"""Fetch arXiv submissions by category and date range via the arXiv API.

Writes <output-dir>/submissions.jsonl (id, title, abstract, authors, dates, categories)
and a summary of per-category, per-month counts. The released corpus was fetched in
two runs, one per venue key:

Usage:
    uv run python scripts/corpus/fetch_arxiv.py --output-dir data/arxiv/2025_sep \
        --start-date 2025-09-01 --end-date 2025-09-30 --venue-key arxiv_2025_sep
    uv run python scripts/corpus/fetch_arxiv.py --output-dir data/arxiv/2025q4_2026q1 \
        --start-date 2025-10-01 --end-date 2026-03-31 --venue-key arxiv_2025q4_2026q1
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Sequence

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_REQUEST_DELAY = 3.5  # seconds between arXiv API requests

MIN_ABSTRACT_LEN = 50
PAGE_SIZE = 500  # arXiv API max per request is 2000, 500 is more reliable


def _load_existing_ids(output_path: Path) -> Set[str]:
    """Load already-fetched paper IDs for checkpoint/resume."""
    ids: Set[str] = set()
    if not output_path.exists():
        return ids
    with output_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pid = rec.get("paper_id", "")
            if pid:
                ids.add(pid)
    return ids


def _normalize_arxiv_id(raw_id: str) -> str:
    """Strip version suffix and normalize arXiv ID."""
    m = re.search(r"(\d{4}\.\d{4,5})", raw_id)
    if m:
        return m.group(1)
    return raw_id.strip()


def _parse_entries(xml_text: str) -> List[Dict[str, Any]]:
    """Parse arXiv API atom feed entries into dicts."""
    root = ET.fromstring(xml_text)
    entries = root.findall("atom:entry", ARXIV_NS)
    results: List[Dict[str, Any]] = []

    for entry in entries:
        # Skip error entries
        id_el = entry.find("atom:id", ARXIV_NS)
        if id_el is None or not id_el.text:
            continue
        raw_id = id_el.text.strip()
        if "arxiv.org/api/errors" in raw_id:
            continue

        arxiv_id = _normalize_arxiv_id(raw_id.split("/abs/")[-1] if "/abs/" in raw_id else raw_id)

        title_el = entry.find("atom:title", ARXIV_NS)
        title = (title_el.text or "").strip() if title_el is not None else ""
        title = re.sub(r"\s+", " ", title)

        abstract_el = entry.find("atom:summary", ARXIV_NS)
        abstract = (abstract_el.text or "").strip() if abstract_el is not None else ""
        abstract = re.sub(r"\s+", " ", abstract)

        authors = []
        for author_el in entry.findall("atom:author", ARXIV_NS):
            name_el = author_el.find("atom:name", ARXIV_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        published_el = entry.find("atom:published", ARXIV_NS)
        pub_date = ""
        if published_el is not None and published_el.text:
            pub_date = published_el.text[:10]  # YYYY-MM-DD

        primary_cat_el = entry.find("arxiv:primary_category", ARXIV_NS)
        primary_category = ""
        if primary_cat_el is not None:
            primary_category = primary_cat_el.get("term", "")

        categories = []
        for cat_el in entry.findall("atom:category", ARXIV_NS):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)

        results.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "publicationDate": pub_date,
            "primary_category": primary_category,
            "categories": categories,
        })

    return results


def _get_total_results(xml_text: str) -> int:
    """Extract totalResults from opensearch namespace."""
    root = ET.fromstring(xml_text)
    ns = {"opensearch": "http://a9.com/-/spec/opensearch/1.1/"}
    total_el = root.find("opensearch:totalResults", ns)
    if total_el is not None and total_el.text:
        return int(total_el.text)
    return 0


def fetch_arxiv_category(
    category: str,
    start_date: str,
    end_date: str,
    venue_key: str,
    existing_ids: Set[str],
    out_f,
    target_categories: Set[str],
) -> Dict[str, int]:
    """Fetch all papers from a single arXiv category within date range."""
    # arXiv date format: YYYYMMDDHHNN
    start_ymd = start_date.replace("-", "") + "0000"
    end_ymd = end_date.replace("-", "") + "2359"
    query = f"cat:{category} AND submittedDate:[{start_ymd} TO {end_ymd}]"

    session = requests.Session()
    stats = {"fetched": 0, "written": 0, "skipped_dup": 0, "skipped_quality": 0, "skipped_category": 0}

    # First request to get total
    start_offset = 0
    total_results = None

    while True:
        params = {
            "search_query": query,
            "start": str(start_offset),
            "max_results": str(PAGE_SIZE),
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        }

        for attempt in range(5):
            try:
                resp = session.get(ARXIV_API_URL, params=params, timeout=60)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                wait = min(30, ARXIV_REQUEST_DELAY * (attempt + 2))
                log.warning("[%s] Request failed (attempt %d): %s, retrying in %.1fs",
                            category, attempt + 1, exc, wait)
                time.sleep(wait)
        else:
            log.error("[%s] Failed after 5 attempts at offset %d, stopping category.", category, start_offset)
            break

        xml_text = resp.text

        if total_results is None:
            total_results = _get_total_results(xml_text)
            log.info("[%s] Total results: %d", category, total_results)

        entries = _parse_entries(xml_text)
        stats["fetched"] += len(entries)

        if not entries:
            break

        for entry in entries:
            arxiv_id = entry["arxiv_id"]
            title = entry["title"]
            abstract = entry["abstract"]

            # Skip if primary_category not in our target set
            pcat = entry.get("primary_category", "")
            if target_categories and pcat not in target_categories:
                stats["skipped_category"] += 1
                continue

            if arxiv_id in existing_ids:
                stats["skipped_dup"] += 1
                continue

            if not title or len(abstract) < MIN_ABSTRACT_LEN:
                stats["skipped_quality"] += 1
                continue

            record = {
                "paper_id": arxiv_id,
                "forum": None,
                "venue_key": venue_key,
                "title": title,
                "abstract": abstract,
                "authors": entry["authors"],
                "arxiv_id_hint": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                "publicationDate": entry["publicationDate"],
                "primary_category": pcat,
                "categories": entry.get("categories", []),
            }

            existing_ids.add(arxiv_id)
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            stats["written"] += 1

        out_f.flush()
        start_offset += len(entries)

        log.info("[%s] Offset %d/%s: fetched=%d, written=%d, dup=%d",
                 category, start_offset, total_results or "?",
                 stats["fetched"], stats["written"], stats["skipped_dup"])

        if total_results and start_offset >= total_results:
            break

        if len(entries) < PAGE_SIZE:
            break

        time.sleep(ARXIV_REQUEST_DELAY)

    return stats


def _generate_monthly_ranges(start_date: str, end_date: str) -> List[tuple]:
    """Split date range into monthly chunks to avoid arXiv API offset limit."""
    import datetime
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    ranges = []
    current = start
    while current <= end:
        month_end = (current.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        if month_end > end:
            month_end = end
        ranges.append((current.isoformat(), month_end.isoformat()))
        current = month_end + datetime.timedelta(days=1)
    return ranges


def fetch_all(
    categories: Sequence[str],
    start_date: str,
    end_date: str,
    venue_key: str,
    output_dir: Path,
) -> Dict[str, Any]:
    """Fetch papers from all categories, deduplicated by arXiv ID.

    Splits the date range into monthly chunks to avoid the arXiv API
    10K offset limit for large result sets.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "submissions.jsonl"
    stats_path = output_dir / "fetch_summary.json"

    existing_ids = _load_existing_ids(output_path)
    log.info("Loaded %d existing paper IDs from %s", len(existing_ids), output_path)

    target_categories = set(categories)
    monthly_ranges = _generate_monthly_ranges(start_date, end_date)
    log.info("Split into %d monthly ranges: %s", len(monthly_ranges),
             ", ".join(f"{s}:{e}" for s, e in monthly_ranges))

    all_stats: Dict[str, Dict[str, int]] = {}

    with output_path.open("a", encoding="utf-8") as out_f:
        for cat in categories:
            cat_stats: Dict[str, int] = {"fetched": 0, "written": 0, "skipped_dup": 0,
                                          "skipped_quality": 0, "skipped_category": 0}
            for m_start, m_end in monthly_ranges:
                log.info("=== Fetching %s [%s to %s] ===", cat, m_start, m_end)
                month_stats = fetch_arxiv_category(
                    category=cat,
                    start_date=m_start,
                    end_date=m_end,
                    venue_key=venue_key,
                    existing_ids=existing_ids,
                    out_f=out_f,
                    target_categories=target_categories,
                )
                for k in cat_stats:
                    cat_stats[k] += month_stats.get(k, 0)
                log.info("[%s %s:%s] month: %s", cat, m_start, m_end, json.dumps(month_stats))
            all_stats[cat] = cat_stats
            log.info("[%s] Category total: %s", cat, json.dumps(cat_stats))

    total_written = sum(s["written"] for s in all_stats.values())
    summary = {
        "categories": list(categories),
        "date_range": f"{start_date}:{end_date}",
        "venue_key": venue_key,
        "total_unique_papers": len(existing_ids),
        "per_category_stats": all_stats,
        "total_new_written": total_written,
    }
    stats_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("All categories done. Total unique papers: %d", len(existing_ids))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch arXiv submissions via the arXiv API."
    )
    parser.add_argument(
        "--output-dir", type=str, required=True,
        help="Output directory for submissions.jsonl"
    )
    parser.add_argument(
        "--start-date", type=str, default="2025-10-01",
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, default="2026-03-31",
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--categories", type=str, default="cs.LG,cs.AI,cs.CL,cs.CV,stat.ML",
        help="Comma-separated arXiv categories"
    )
    parser.add_argument(
        "--venue-key", type=str, default="arxiv_2025q4_2026q1",
        help="venue_key to assign to all papers"
    )
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    fetch_all(
        categories=categories,
        start_date=args.start_date,
        end_date=args.end_date,
        venue_key=args.venue_key,
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
