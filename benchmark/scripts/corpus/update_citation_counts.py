"""Batch update s2_citation_count for all papers via the Semantic Scholar search API."""

import asyncio
import json
import time
import argparse
import os
from pathlib import Path

import aiohttp
from tqdm import tqdm

# Any S2-compatible endpoint works; S2_API_KEY is sent as x-api-key when set.
BASE = os.environ.get("S2_API_BASE", "https://api.semanticscholar.org").rstrip("/")
S2_API_KEY = os.environ.get("S2_API_KEY", "")

SEARCH_URL = f"{BASE}/graph/v1/paper/search"
FIELDS = "citationCount,externalIds"
CONCURRENCY = 15
RETRY_LIMIT = 3


async def fetch_citation(session: aiohttp.ClientSession, paper: dict, sem: asyncio.Semaphore) -> dict | None:
    arxiv_id = paper.get("arxiv_id", "")
    title = paper.get("s2_title") or paper.get("title", "")
    if not title:
        return None

    params = {"query": title, "limit": 5, "fields": FIELDS}

    for attempt in range(RETRY_LIMIT):
        async with sem:
            try:
                async with session.get(SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for hit in data.get("data", []):
                            ext_ids = hit.get("externalIds") or {}
                            if ext_ids.get("ArXiv") == arxiv_id:
                                return {"arxiv_id": arxiv_id, "citationCount": hit.get("citationCount", 0)}
                        # No exact ArXiv match — try paperId match
                        s2_pid = paper.get("s2_paper_id", "")
                        if s2_pid:
                            for hit in data.get("data", []):
                                if hit.get("paperId") == s2_pid:
                                    return {"arxiv_id": arxiv_id, "citationCount": hit.get("citationCount", 0)}
                        return None
                    elif resp.status == 429:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        text = await resp.text()
                        if attempt == RETRY_LIMIT - 1:
                            return None
                        await asyncio.sleep(1)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == RETRY_LIMIT - 1:
                    return None
                await asyncio.sleep(1)
    return None


async def main(input_path: Path, output_path: Path, concurrency: int):
    papers = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                papers.append(json.loads(line))

    print(f"Loaded {len(papers)} papers from {input_path}")

    sem = asyncio.Semaphore(concurrency)
    headers = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}

    results = {}  # arxiv_id -> new citationCount
    failed = []

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [fetch_citation(session, p, sem) for p in papers]

        pbar = tqdm(total=len(tasks), desc="Fetching citations")
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result:
                results[result["arxiv_id"]] = result["citationCount"]
            else:
                failed.append(1)
            pbar.update(1)
        pbar.close()

    # Write updated file
    updated = 0
    with open(input_path) as f_in, open(output_path, "w") as f_out:
        for line in f_in:
            line = line.strip()
            if not line:
                continue
            paper = json.loads(line)
            aid = paper.get("arxiv_id", "")
            if aid in results:
                paper["s2_citation_count"] = results[aid]
                updated += 1
            f_out.write(json.dumps(paper, ensure_ascii=False) + "\n")

    print(f"\nDone. Updated {updated}/{len(papers)} papers. Failed to match: {len(failed)}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True,
                        help="Input metadata JSONL (each line has paper_id / arxiv_id)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output path (default: <input>_citation_updated.jsonl)")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = parser.parse_args()

    if args.output is None:
        args.output = args.input.with_name(args.input.stem + "_citation_updated.jsonl")

    asyncio.run(main(args.input, args.output, args.concurrency))
