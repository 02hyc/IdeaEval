#!/usr/bin/env python3
"""Check if papers in ranker pairs have date leaks (published before arXiv date).

A "date leak" means the paper existed (published, posted to OpenReview, etc.)
before its arXiv submission date. Such papers have inflated citation counts
that don't reflect the arXiv publication window.

Detection strategies:
  1. S2 paper detail: check if S2's own publicationDate for the arXiv paper
     is significantly earlier than the arXiv date (same-paper self-check).
  2. S2 title search: search for title matches published before arXiv date.
     Same arXiv ID with earlier date is the STRONGEST leak signal (not a skip).
  3. Citation anomaly: flag papers whose citation rate is implausibly high
     for their arXiv age (e.g., >50 citations in <6 months).

Outputs a JSONL of results and a filtered version of the pairs JSON.

Usage:
    uv run python scripts/check_date_leaks.py \
        --pairs experiments/clustering_v3/ranker_dev_set_200.json \
        --metadata /path/to/arxiv_metadata.jsonl \
        --output-dir experiments/clustering_v3 \
        --workers 6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

S2_API = os.environ.get("S2_API_BASE", "https://api.semanticscholar.org").rstrip("/") + "/graph/v1"
# Optional basic auth for a self-hosted S2-compatible mirror.
S2_BASIC_AUTH: tuple[str, str] | None = None
_s2_user = os.environ.get("S2_PROXY_USER", "")
_s2_pass = os.environ.get("S2_PROXY_PASS", "")
if _s2_user and _s2_pass:
    S2_BASIC_AUTH = (_s2_user, _s2_pass)


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


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Strategy 4: Local markdown header inspection
# ---------------------------------------------------------------------------
# Patterns that indicate the paper was already published in a venue
_VENUE_PATTERNS = [
    # DOI links
    re.compile(r"10\.\d{4,}/[^\s]+", re.IGNORECASE),
    # Journal volume/issue patterns
    re.compile(r"Vol\.?\s*\d+\s*[\(,]\s*\d+\s*\)", re.IGNORECASE),
    re.compile(r"\d{4},\s*Vol\.?\s*\d+", re.IGNORECASE),
    # Copyright notices
    re.compile(r"©\s*(The\s+)?Author", re.IGNORECASE),
    # IEEE/ACM/Springer markers
    re.compile(r"IEEE\s+(Transactions|Journal|Access|Conference)", re.IGNORECASE),
    re.compile(r"ACM\s+(Transactions|Conference|Computing\s+Surveys)", re.IGNORECASE),
    re.compile(r"Springer|Elsevier|Wiley|AAAI|NeurIPS|ICML|ICLR|CVPR|ECCV|ICCV|ACL|EMNLP|NAACL", re.IGNORECASE),
    # Explicit "Published in" or "Accepted at"
    re.compile(r"(Published|Accepted|Appeared)\s+(in|at|by)", re.IGNORECASE),
    # sagepub / journal home
    re.compile(r"journals\.sagepub|journals\.elsevier|link\.springer|dl\.acm\.org", re.IGNORECASE),
]


def check_local_markdown_header(paper_id: str, papers_dir: Path | None) -> dict | None:
    """Strategy 4: Scan the first 40 lines of the paper markdown for venue indicators."""
    if not papers_dir:
        return None
    md_path = papers_dir / f"{paper_id}.md"
    if not md_path.exists():
        return None

    try:
        with open(md_path, encoding="utf-8") as f:
            header_lines = [f.readline() for _ in range(40)]
        header_text = "".join(header_lines)
    except Exception:
        return None

    found_patterns = []
    for pat in _VENUE_PATTERNS:
        m = pat.search(header_text)
        if m:
            found_patterns.append(m.group()[:80])

    if len(found_patterns) >= 2:
        return {
            "reason": "local_header_venue_markers",
            "detail": f"Found {len(found_patterns)} venue indicators in header: {found_patterns[:4]}",
        }
    return None


CITATION_RATE_SUSPECT = 3.0  # >3 citations/month is suspicious for recent arXiv papers


def check_citation_anomaly(citations: int | None, pub_date_str: str | None) -> dict | None:
    """Flag papers whose citation rate is implausibly high for their arXiv age.

    A freshly posted arXiv paper typically accumulates 0-2 citations/month.
    Rates above 3/month strongly suggest pre-existing venue publication.
    """
    if not citations or citations < 10:
        return None
    pub = parse_date(pub_date_str)
    if not pub:
        return None
    age_days = (date.today() - pub).days
    if age_days <= 0:
        return None
    age_months = age_days / 30.0
    rate = citations / max(age_months, 0.5)
    if rate > CITATION_RATE_SUSPECT:
        return {
            "reason": "citation_rate_anomaly",
            "detail": (f"{citations} citations in {age_months:.1f} months "
                       f"= {rate:.1f}/month (threshold: >{CITATION_RATE_SUSPECT}/month)"),
        }
    return None


async def _s2_get(session: aiohttp.ClientSession, url: str, params: dict | None,
                  s2_keys: list[str], key_index: list[int], lock: asyncio.Lock,
                  max_attempts: int = 5) -> dict | None:
    """Generic S2 API GET with retry and key rotation. Returns JSON or None."""
    for attempt in range(max_attempts):
        async with lock:
            key = s2_keys[key_index[0] % len(s2_keys)] if s2_keys else ""
            key_index[0] += 1
        headers = {"x-api-key": key} if key else {}
        auth = aiohttp.BasicAuth(_s2_user, _s2_pass) if S2_BASIC_AUTH else None
        try:
            async with session.get(url, params=params, headers=headers, auth=auth, timeout=30) as resp:
                if resp.status in (429, 403, 500, 502, 503, 504):
                    await asyncio.sleep(min(20, 2 ** attempt))
                    continue
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    if attempt == max_attempts - 1:
                        return None
                    await asyncio.sleep(min(20, 2 ** attempt))
                    continue
                return await resp.json()
        except (asyncio.TimeoutError, Exception):
            if attempt == max_attempts - 1:
                return None
            await asyncio.sleep(min(20, 2 ** attempt))
    return None


_ARXIV_LIKE_VENUES = {"arxiv.org", "arxiv", ""}


def _is_non_arxiv_venue(venue: str | None) -> bool:
    """Return True if the venue indicates publication outside arXiv."""
    if not venue:
        return False
    return venue.strip().lower() not in _ARXIV_LIKE_VENUES


CROSSREF_API = "https://api.crossref.org/works"


def _extract_crossref_date(date_obj: dict | None) -> date | None:
    """Extract a date from CrossRef date-parts format like {'date-parts': [[2025, 8, 22]]}."""
    if not date_obj:
        return None
    parts = date_obj.get("date-parts")
    if not parts or not parts[0]:
        return None
    p = parts[0]
    try:
        if len(p) >= 3:
            return date(p[0], p[1], p[2])
        elif len(p) == 2:
            return date(p[0], p[1], 1)
        elif len(p) == 1:
            return date(p[0], 1, 1)
    except (ValueError, TypeError):
        return None
    return None


async def _check_doi_crossref(session: aiohttp.ClientSession, doi: str) -> date | None:
    """Query CrossRef for the earliest publication date of a DOI."""
    try:
        async with session.get(f"{CROSSREF_API}/{doi}", timeout=15) as resp:
            if resp.status != 200:
                return None
            data = (await resp.json()).get("message", {})
            candidates = []
            for field in ("published-online", "published", "issued", "created"):
                d = _extract_crossref_date(data.get(field))
                if d:
                    candidates.append(d)
            return min(candidates) if candidates else None
    except Exception:
        return None


async def check_paper_s2_detail(session: aiohttp.ClientSession, paper_id: str,
                                arxiv_date: str, s2_keys: list[str],
                                key_index: list[int], lock: asyncio.Lock) -> dict | None:
    """Strategy 1: Check S2's own record for this arXiv paper.

    Leak conditions:
      a) S2 publicationDate < arXiv date → leak
      b) S2 has non-arXiv venue but date >= arXiv date → S2 date may be wrong,
         verify via CrossRef DOI lookup for actual publication date
    """
    cutoff = parse_date(arxiv_date)
    if not cutoff:
        return None

    url = f"{S2_API}/paper/ARXIV:{paper_id}"
    params = {"fields": "title,publicationDate,externalIds,venue,year"}
    data = await _s2_get(session, url, params, s2_keys, key_index, lock)
    if not data:
        return None

    s2_date_str = data.get("publicationDate")
    s2_date = parse_date(s2_date_str)
    s2_venue = data.get("venue", "")
    ext_ids = data.get("externalIds") or {}
    doi = ext_ids.get("DOI", "")
    has_venue = _is_non_arxiv_venue(s2_venue)

    # Case a): S2 date is before arXiv date — direct leak
    if s2_date:
        gap_days = (cutoff - s2_date).days
        if gap_days >= 1:
            detection = "s2_venue_pre_arxiv" if has_venue else "s2_detail_date_mismatch"
            return {
                "paper_id": paper_id,
                "status": "leak",
                "detection": detection,
                "arxiv_date": arxiv_date,
                "leak_date": s2_date_str,
                "leak_venue": s2_venue,
                "leak_title": data.get("title", ""),
                "gap_days": gap_days,
                "similarity": 1.0,
                "all_matches": [{
                    "title": data.get("title"),
                    "publication_date": s2_date_str,
                    "venue": s2_venue,
                    "external_ids": ext_ids,
                    "source": "s2_paper_detail",
                }],
            }

    # Case b): S2 has non-arXiv venue but date >= arXiv date
    # S2 date is often wrong (set to arXiv date). Verify via CrossRef.
    if has_venue and doi:
        crossref_date = await _check_doi_crossref(session, doi)
        if crossref_date and crossref_date < cutoff:
            cr_gap = (cutoff - crossref_date).days
            return {
                "paper_id": paper_id,
                "status": "leak",
                "detection": "crossref_doi_pre_arxiv",
                "arxiv_date": arxiv_date,
                "leak_date": crossref_date.isoformat(),
                "leak_venue": s2_venue,
                "leak_title": data.get("title", ""),
                "gap_days": cr_gap,
                "doi": doi,
                "similarity": 1.0,
                "all_matches": [{
                    "title": data.get("title"),
                    "publication_date": crossref_date.isoformat(),
                    "venue": s2_venue,
                    "external_ids": ext_ids,
                    "source": "crossref_doi",
                }],
            }

    return None


async def check_paper_title_search(session: aiohttp.ClientSession, paper_id: str,
                                   title: str, arxiv_date: str,
                                   s2_keys: list[str], key_index: list[int],
                                   lock: asyncio.Lock) -> dict | None:
    """Strategy 2: Search S2 for title matches published before arXiv date.

    Key fix: same arXiv ID with earlier publicationDate is the STRONGEST leak
    signal (the paper was published elsewhere before arXiv), not a reason to skip.
    """
    cutoff = parse_date(arxiv_date)
    if not cutoff or not title:
        return None

    cutoff_end = (cutoff - timedelta(days=1)).isoformat()
    params = {
        "query": title,
        "limit": "10",
        "fields": "title,publicationDate,externalIds,venue,url",
        "publicationDateOrYear": f":{cutoff_end}",
    }

    url = f"{S2_API}/paper/search"
    data = await _s2_get(session, url, params, s2_keys, key_index, lock)
    if not data:
        return None

    matches = []
    for item in data.get("data") or []:
        item_date = item.get("publicationDate") or ""
        item_parsed = parse_date(item_date)
        if not item_parsed or item_parsed >= cutoff:
            continue
        sim = title_similarity(title, item.get("title") or "")
        if sim < 0.85:
            continue
        ext_ids = item.get("externalIds") or {}
        arxiv_id_in_result = str(ext_ids.get("ArXiv") or "").replace("v1", "").replace("v2", "")

        # Same arXiv ID with earlier date = strongest leak signal
        # (paper existed in another venue before arXiv submission)
        is_same_paper = (arxiv_id_in_result == paper_id)

        matches.append({
            "title": item.get("title"),
            "publication_date": item_date,
            "venue": item.get("venue"),
            "url": item.get("url"),
            "similarity": round(sim, 4),
            "external_ids": ext_ids,
            "same_arxiv_id": is_same_paper,
        })

    if matches:
        # Prefer same-paper matches (strongest signal), then highest similarity
        best = max(matches, key=lambda m: (m.get("same_arxiv_id", False), m["similarity"]))
        return {
            "paper_id": paper_id,
            "title": title,
            "arxiv_date": arxiv_date,
            "status": "leak",
            "detection": "same_paper_earlier_venue" if best.get("same_arxiv_id") else "title_match",
            "leak_date": best["publication_date"],
            "leak_venue": best.get("venue", ""),
            "leak_title": best["title"],
            "similarity": best["similarity"],
            "all_matches": matches,
        }
    return None


async def search_s2(session: aiohttp.ClientSession, paper_id: str, title: str,
                    pub_date: str, s2_keys: list[str], key_index: list[int],
                    lock: asyncio.Lock, citations: int | None = None,
                    papers_dir: Path | None = None) -> dict:
    """Run all leak detection strategies for a single paper."""
    cutoff = parse_date(pub_date)
    if not cutoff or not title:
        return {"paper_id": paper_id, "status": "skip", "reason": "missing date or title"}

    # Strategy 3: Citation anomaly (no API call needed)
    anomaly = check_citation_anomaly(citations, pub_date)

    # Strategy 4: Local markdown header check (no API call needed)
    header_check = check_local_markdown_header(paper_id, papers_dir)

    # Strategy 1: S2 paper detail self-check
    detail_result = await check_paper_s2_detail(
        session, paper_id, pub_date, s2_keys, key_index, lock)
    if detail_result:
        if anomaly:
            detail_result["citation_anomaly"] = anomaly
        if header_check:
            detail_result["header_check"] = header_check
        return detail_result

    # Strategy 2: Title search
    search_result = await check_paper_title_search(
        session, paper_id, title, pub_date, s2_keys, key_index, lock)
    if search_result:
        if anomaly:
            search_result["citation_anomaly"] = anomaly
        if header_check:
            search_result["header_check"] = header_check
        return search_result

    # No API-based leak found; check local signals
    # If BOTH citation anomaly AND header venue markers, flag as leak
    if anomaly and header_check:
        return {
            "paper_id": paper_id,
            "title": title,
            "arxiv_date": pub_date,
            "status": "leak",
            "detection": "citation_anomaly_plus_header",
            "citation_anomaly": anomaly,
            "header_check": header_check,
            "citations": citations,
        }

    # Either one alone is suspect
    if anomaly:
        return {
            "paper_id": paper_id,
            "title": title,
            "arxiv_date": pub_date,
            "status": "suspect",
            "detection": anomaly["reason"],
            "detail": anomaly["detail"],
            "citations": citations,
            "header_check": header_check,
        }

    if header_check:
        return {
            "paper_id": paper_id,
            "title": title,
            "arxiv_date": pub_date,
            "status": "suspect",
            "detection": header_check["reason"],
            "detail": header_check["detail"],
        }

    return {"paper_id": paper_id, "status": "clean"}


async def run_checks(papers: list[dict], s2_keys: list[str], workers: int,
                     cache_path: Path, force_recheck: bool = False,
                     papers_dir: Path | None = None) -> dict[str, dict]:
    """Run date leak checks for all papers."""
    # Load cache
    cached = {}
    if cache_path.exists() and not force_recheck:
        with open(cache_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("status") not in ("error",):
                    cached[r["paper_id"]] = r

    pending = [p for p in papers if p["paper_id"] not in cached]
    log.info("Cached: %d, Pending: %d", len(cached), len(pending))

    if not pending:
        return cached

    sem = asyncio.Semaphore(workers)
    lock = asyncio.Lock()
    key_index = [0]
    done = [0]
    total = len(pending)

    timeout = aiohttp.ClientTimeout(total=60)
    connector = aiohttp.TCPConnector(limit=max(8, workers * 2))

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        async def check_one(paper: dict) -> dict:
            async with sem:
                result = await search_s2(
                    session, paper["paper_id"], paper["title"],
                    paper["date"], s2_keys, key_index, lock,
                    citations=paper.get("citations"),
                    papers_dir=papers_dir)
                cached[paper["paper_id"]] = result
                # Append to cache file
                async with lock:
                    with open(cache_path, "a") as f:
                        f.write(json.dumps(result, ensure_ascii=False) + "\n")
                done[0] += 1
                if done[0] % 10 == 0:
                    n_leaks = sum(1 for r in cached.values()
                                  if r.get("status") in ("leak", "suspect"))
                    log.info("[%d/%d] leaks/suspects=%d", done[0], total, n_leaks)
                return result

        tasks = [check_one(p) for p in pending]
        await asyncio.gather(*tasks)

    return cached


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Check date leaks in ranker pairs")
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, help="Optional metadata JSONL (fallback for title/date)")
    parser.add_argument("--papers-dir", type=Path, help="Directory containing paper markdown files for header inspection")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--force-recheck", action="store_true",
                        help="Ignore cache and recheck all papers")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    s2_keys = [k.strip() for k in os.environ.get("S2_API_KEYS", "").split(",") if k.strip()]
    log.info("S2 API keys: %d", len(s2_keys))

    # Load pairs
    with open(args.pairs) as f:
        pairs_data = json.load(f)

    # Collect unique papers directly from pairs (title, date, citations are inline)
    paper_meta = {}
    if args.metadata and args.metadata.exists():
        with open(args.metadata) as f:
            for line in f:
                r = json.loads(line)
                paper_meta[r["paper_id"]] = r

    pids_info: dict[str, dict] = {}
    for p in pairs_data["pairs"]:
        for side in ("paper_a", "paper_b"):
            info = p[side]
            pid = info["paper_id"]
            if pid not in pids_info:
                pids_info[pid] = {
                    "paper_id": pid,
                    "title": info.get("title", ""),
                    "date": info.get("date", ""),
                    "citations": info.get("citations"),
                }
                # Fallback to metadata if inline info is sparse
                if (not pids_info[pid]["title"] or not pids_info[pid]["date"]) and pid in paper_meta:
                    m = paper_meta[pid]
                    pids_info[pid]["title"] = pids_info[pid]["title"] or m.get("title", "")
                    pids_info[pid]["date"] = pids_info[pid]["date"] or m.get("publication_date", "")
                    if pids_info[pid]["citations"] is None:
                        pids_info[pid]["citations"] = m.get("s2_citation_count")

    papers_to_check = sorted(pids_info.values(), key=lambda x: x["paper_id"])
    log.info("Papers to check: %d", len(papers_to_check))

    # Run checks
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / "date_leak_results_v2.jsonl"
    results = asyncio.run(run_checks(
        papers_to_check, s2_keys, args.workers, cache_path,
        force_recheck=args.force_recheck,
        papers_dir=args.papers_dir))

    # Stats
    leaks = {pid for pid, r in results.items() if r.get("status") == "leak"}
    suspects = {pid for pid, r in results.items() if r.get("status") == "suspect"}
    errors = {pid for pid, r in results.items() if r.get("status") == "error"}
    clean = {pid for pid, r in results.items() if r.get("status") == "clean"}

    log.info("Results: %d clean, %d leaks, %d suspects, %d errors, %d skipped",
             len(clean), len(leaks), len(suspects), len(errors),
             len(results) - len(clean) - len(leaks) - len(suspects) - len(errors))

    # Show leak details
    if leaks:
        log.info("=== Confirmed date leaks ===")
        for pid in sorted(leaks):
            r = results[pid]
            log.info("  [%s] %s  arxiv=%s leak=%s venue=%s detection=%s",
                     pid, (r.get("leak_title") or r.get("title", ""))[:60],
                     r.get("arxiv_date", ""), r.get("leak_date", ""),
                     r.get("leak_venue", ""), r.get("detection", ""))

    if suspects:
        log.info("=== Suspects (citation anomaly, no venue match found) ===")
        for pid in sorted(suspects):
            r = results[pid]
            log.info("  [%s] %s  %s", pid, r.get("title", "")[:60], r.get("detail", ""))

    # Build date correction map: leaked paper_id -> corrected date (leak_date)
    date_corrections: dict[str, str] = {}
    for pid in leaks:
        r = results[pid]
        leak_date = r.get("leak_date")
        if leak_date:
            date_corrections[pid] = leak_date

    # Also remove technical reports / manually flagged papers
    force_remove = set()
    # Dream-Coder 7B is a technical report, not a research paper
    if "2509.01142" in pids_info:
        force_remove.add("2509.01142")

    # Filter pairs:
    # - For leaked papers, correct date to leak_date
    # - Recalculate date_diff with corrected dates
    # - Remove only if new date_diff > 31 days OR paper is force-removed
    kept = []
    removed = 0
    removed_pairs_info = []
    corrected_pairs_info = []

    for p in pairs_data["pairs"]:
        pid_a = p["paper_a"]["paper_id"]
        pid_b = p["paper_b"]["paper_id"]

        # Force-remove (technical reports, etc.)
        if pid_a in force_remove or pid_b in force_remove:
            removed += 1
            removed_pairs_info.append({
                "pair_id": p["pair_id"],
                "reason": "force_remove",
                "paper": pid_a if pid_a in force_remove else pid_b,
            })
            continue

        # Get effective dates (corrected if leaked)
        date_a_str = date_corrections.get(pid_a, p["paper_a"]["date"])
        date_b_str = date_corrections.get(pid_b, p["paper_b"]["date"])
        date_a = parse_date(date_a_str)
        date_b = parse_date(date_b_str)

        if not date_a or not date_b:
            kept.append(p)
            continue

        new_diff = abs((date_a - date_b).days)

        # If either paper was leaked and new date diff > 31 days, remove
        has_leak = pid_a in date_corrections or pid_b in date_corrections
        if has_leak and new_diff > 31:
            removed += 1
            leaked_pid = pid_a if pid_a in date_corrections else pid_b
            removed_pairs_info.append({
                "pair_id": p["pair_id"],
                "reason": "date_diff_exceeds_31d",
                "paper": leaked_pid,
                "original_date": p["paper_a" if leaked_pid == pid_a else "paper_b"]["date"],
                "corrected_date": date_corrections[leaked_pid],
                "new_date_diff": new_diff,
            })
            continue

        # Keep pair — apply date corrections if any
        if pid_a in date_corrections:
            p["paper_a"]["date"] = date_corrections[pid_a]
            p["paper_a"]["date_corrected"] = True
            p["paper_a"]["original_arxiv_date"] = pids_info[pid_a]["date"]
        if pid_b in date_corrections:
            p["paper_b"]["date"] = date_corrections[pid_b]
            p["paper_b"]["date_corrected"] = True
            p["paper_b"]["original_arxiv_date"] = pids_info[pid_b]["date"]

        if has_leak:
            p["date_diff_days"] = new_diff
            corrected_pairs_info.append({
                "pair_id": p["pair_id"],
                "leaked_paper": pid_a if pid_a in date_corrections else pid_b,
                "corrected_date": date_corrections.get(pid_a) or date_corrections.get(pid_b),
                "new_date_diff": new_diff,
            })

        kept.append(p)

    n_nontie = sum(1 for p in kept if p["label"] == "a_better")
    n_tie = sum(1 for p in kept if p["label"] == "tie")

    pairs_data["metadata"]["total_pairs"] = len(kept)
    pairs_data["metadata"]["non_tie_pairs"] = n_nontie
    pairs_data["metadata"]["tie_pairs"] = n_tie
    pairs_data["metadata"]["tie_ratio"] = round(n_tie / max(len(kept), 1), 3)
    pairs_data["metadata"]["date_leak_papers"] = len(leaks)
    pairs_data["metadata"]["date_leak_suspects"] = len(suspects)
    pairs_data["metadata"]["date_leak_pairs_removed"] = removed
    pairs_data["metadata"]["date_leak_pairs_corrected"] = len(corrected_pairs_info)
    pairs_data["pairs"] = kept

    out_path = args.output_dir / "ranker_pairs_clean.json"
    with open(out_path, "w") as f:
        json.dump(pairs_data, f, ensure_ascii=False, indent=2)

    print(f"\n=== Date Leak Check Complete ===")
    print(f"Papers checked: {len(papers_to_check)}")
    print(f"Confirmed leaks: {len(leaks)}")
    print(f"Suspects (citation anomaly): {len(suspects)}")
    print(f"Pairs removed: {removed}")
    print(f"Pairs corrected (kept with new date): {len(corrected_pairs_info)}")
    print(f"Pairs remaining: {len(kept)} (non-tie={n_nontie}, tie={n_tie})")
    if removed_pairs_info:
        print(f"\nRemoved pairs:")
        for info in removed_pairs_info:
            reason = info["reason"]
            if reason == "date_diff_exceeds_31d":
                print(f"  {info['pair_id']} — {info['paper']} corrected {info['original_date']}→{info['corrected_date']}, new diff={info['new_date_diff']}d > 31d")
            else:
                print(f"  {info['pair_id']} — {info['paper']} ({reason})")
    if corrected_pairs_info:
        print(f"\nCorrected pairs (kept):")
        for info in corrected_pairs_info:
            print(f"  {info['pair_id']} — {info['leaked_paper']} date→{info['corrected_date']}, diff={info['new_date_diff']}d")
    if suspects:
        print(f"\nSuspect papers (manual review recommended):")
        for pid in sorted(suspects):
            r = results[pid]
            print(f"  {pid}: {r.get('detail', '')}")
    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
