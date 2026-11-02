"""Fetch citation timelines for all papers from Semantic Scholar.

For each paper, fetches all citing papers with their publication dates.
Saves results as JSONL: one line per paper with its citing papers list.

Usage:
    source .env
    uv run python scripts/fetch_citation_timelines.py \
        --input clustering_results_taxoadapt_rebalanced/deep_review_full/papers_metadata_final.jsonl \
        --output datasets/citation_timelines.jsonl \
        --workers 3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ideaeval_benchmark.config import load_settings
from ideaeval_benchmark.s2_client import S2Client

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def load_papers(path: Path) -> list[dict]:
    papers = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            if p.get("s2_paper_id"):
                papers.append(p)
    return papers


def load_done(output_path: Path) -> set[str]:
    done = set()
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    done.add(rec["paper_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def fetch_one(client: S2Client, paper: dict) -> dict:
    s2_id = paper["s2_paper_id"]
    paper_id = paper["paper_id"]
    pub_date = paper.get("publication_date", "")

    try:
        citations = client.get_citations(s2_id)
    except Exception as e:
        log.warning("Error fetching citations for %s: %s", paper_id, e)
        citations = []

    citing_papers = []
    for c in citations:
        citing_papers.append({
            "s2_paper_id": c.get("paperId"),
            "title": c.get("title"),
            "publication_date": c.get("publicationDate"),
            "year": c.get("year"),
        })

    return {
        "paper_id": paper_id,
        "s2_paper_id": s2_id,
        "publication_date": pub_date,
        "s2_citation_count": paper.get("s2_citation_count", 0),
        "n_citing_fetched": len(citing_papers),
        "citing_papers": citing_papers,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch citation timelines from S2")
    parser.add_argument("--input", required=True, help="papers_metadata_final.jsonl")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers (match number of API keys)")
    parser.add_argument("--limit", type=int, default=0, help="Limit papers to fetch (0=all)")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    n_keys = len(settings.s2.api_keys)
    log.info("Loaded %d S2 API key(s), RPS=%.1f", n_keys, settings.s2.rps)

    papers = load_papers(input_path)
    log.info("Loaded %d papers with s2_paper_id", len(papers))

    done = load_done(output_path)
    remaining = [p for p in papers if p["paper_id"] not in done]
    log.info("Already done: %d, remaining: %d", len(done), len(remaining))

    if args.limit > 0:
        remaining = remaining[: args.limit]
        log.info("Limited to %d papers", len(remaining))

    if not remaining:
        log.info("Nothing to do.")
        return

    workers = min(args.workers, max(1, n_keys))
    clients = [S2Client(settings.s2) for _ in range(workers)]

    total = len(remaining)
    t0 = time.time()
    completed = 0
    errors = 0

    with open(output_path, "a", encoding="utf-8") as fout:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, paper in enumerate(remaining):
                client = clients[i % workers]
                fut = pool.submit(fetch_one, client, paper)
                futures[fut] = paper["paper_id"]

            for fut in as_completed(futures):
                paper_id = futures[fut]
                try:
                    result = fut.result()
                    fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                    fout.flush()
                    completed += 1
                except Exception as e:
                    log.error("Failed %s: %s", paper_id, e)
                    errors += 1
                    completed += 1

                if completed % 100 == 0 or completed == total:
                    elapsed = time.time() - t0
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (total - completed) / rate if rate > 0 else 0
                    log.info(
                        "Progress: %d/%d (%.1f%%), errors=%d, %.1f papers/s, ETA %.0fm",
                        completed, total, 100 * completed / total,
                        errors, rate, eta / 60,
                    )

    elapsed = time.time() - t0
    log.info("Done: %d papers in %.1f min, %d errors", completed, elapsed / 60, errors)


if __name__ == "__main__":
    main()
