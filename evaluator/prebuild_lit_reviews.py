"""Pre-compute Phase 0 (Literature Review) for every unique topic in a
calibration benchmark, with a configurable concurrency cap.

Each topic's review is cached at:
    <cache_dir>/<topic_id>/
        DONE
        meta.json
        agents/literature_review/workspace/
            literature_review.md
            scraped_papers/...
            paper_summaries/...
            ...

Downstream benchmark runs (rank_runner) will check this cache by topic_id;
if present, copy the workspace into the pair's literature_review workspace
and skip the LLM-driven Phase 0.

Usage:
    nohup python prebuild_lit_reviews.py \
        --devset calibration_benchmark_v3/ranker_dev_set_v2.json \
        --cache-dir experiments/lit_review_cache_v3 \
        --concurrency 10 \
        > /path/to/log 2>&1 &
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime.secret_bootstrap import bootstrap_secrets
from runtime.rank_runner import RankRunConfig, _run_literature_review_agent
from utils.logger import Logger


def _pair_cutoff(p: dict) -> str:
    """Cutoff date for a pair. Prefer explicit field (v2 schema); fall back to
    min(paper_a.date, paper_b.date) for the new 260-pair schema where pairs
    are keyed by arxiv paper_id and carry no cutoff_date field."""
    if "cutoff_date" in p:
        return p["cutoff_date"]
    a = p.get("paper_a") or p.get("proposal_a") or {}
    b = p.get("paper_b") or p.get("proposal_b") or {}
    da, db = a.get("date"), b.get("date")
    if not da or not db:
        raise KeyError(f"pair {p.get('pair_id')} has neither cutoff_date nor paper_a/b dates")
    return min(da, db)


def _cache_key(topic_id: str, cutoff_date: str, mode: str) -> str:
    if mode == "topic":
        return topic_id
    if mode == "topic_cutoff":
        return f"{topic_id}__cutoff_{cutoff_date.replace('-', '')}"
    raise ValueError(f"unknown cache key mode: {mode}")


def collect_unique_topics(devset_path: Path, cache_key_mode: str = "topic") -> list[dict]:
    """Collect literature-review cache tasks.

    The default keeps historical behavior: one cache entry per topic_id, using
    the earliest cutoff among pairs with that topic. topic_cutoff mode creates
    one cache entry per (topic_id, cutoff_date), so later-cutoff pairs can use
    literature available at their own cutoff.
    """
    data = json.loads(devset_path.read_text(encoding="utf-8"))
    seen: dict[str, dict] = {}
    for p in data["pairs"]:
        tid = p["topic_id"]
        cutoff = _pair_cutoff(p)
        key = _cache_key(tid, cutoff, cache_key_mode)
        if key in seen:
            if cache_key_mode == "topic" and cutoff < seen[key]["cutoff_date"]:
                seen[key]["cutoff_date"] = cutoff
            seen[key]["pair_count"] += 1
        else:
            seen[key] = {
                "cache_key": key,
                "topic_id": tid,
                "topic_label": p["topic_label"],
                "topic_description": p["topic_description"],
                "cutoff_date": cutoff,
                "pair_count": 1,
            }
    return sorted(seen.values(), key=lambda t: (t["topic_id"], t["cutoff_date"]))


async def run_one_topic(
    topic: dict,
    cache_dir: Path,
    sem: asyncio.Semaphore,
    logger: Logger,
) -> tuple[str, str, float]:
    """Run lit review for one topic. Returns (cache_key, status, elapsed_sec)."""
    async with sem:
        cache_key = topic.get("cache_key", topic["topic_id"])
        topic_dir = cache_dir / cache_key
        marker = topic_dir / "DONE"
        if marker.exists():
            return cache_key, "skip_cached", 0.0

        # No DONE marker: this is either a fresh topic or a FAILED retry.
        # Wipe any prior workspace/markers so the agent starts from clean state.
        # Without this, a retried-FAILED topic inherits a half-written
        # literature_review.md from its previous attempt — the new agent extends
        # it, context bloats faster, and we re-hit the same failure mode.
        agent_dir = topic_dir / "agents" / "literature_review"
        failed_marker = topic_dir / "FAILED"
        if agent_dir.exists():
            stale_dir = agent_dir.with_name(
                f"{agent_dir.name}.stale_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000)}"
            )
            try:
                agent_dir.rename(stale_dir)
            except OSError:
                shutil.rmtree(agent_dir, ignore_errors=True)
        if failed_marker.exists():
            failed_marker.unlink()

        topic_dir.mkdir(parents=True, exist_ok=True)
        agent_dir.mkdir(parents=True, exist_ok=True)

        cfg = RankRunConfig(
            proposal_a="",
            proposal_b="",
            cutoff_date=topic["cutoff_date"],
            topic_label=topic["topic_label"],
            topic_description=topic["topic_description"],
            task_root=str(topic_dir),
        )

        pair_sink_id = Logger.add_pair_log(cache_key, topic_dir)
        scope_cm = logger.pair_scope(cache_key)
        scope_cm.__enter__()
        t0 = time.monotonic()
        try:
            logger.info(
                f"[prebuild] START {cache_key} | topic={topic['topic_id']} "
                f"cutoff={topic['cutoff_date']} | {topic['topic_label']}"
            )
            await _run_literature_review_agent(
                topic_label=topic["topic_label"],
                topic_description=topic["topic_description"],
                cutoff_date=topic["cutoff_date"],
                config=cfg,
                agent_dir=agent_dir,
            )
            elapsed = time.monotonic() - t0
            meta = {
                "cache_key": cache_key,
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "topic_description": topic["topic_description"],
                "cutoff_date": topic["cutoff_date"],
                "pair_count": topic["pair_count"],
                "elapsed_sec": elapsed,
                "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            (topic_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
            marker.write_text("ok\n")
            logger.info(f"[prebuild] DONE {cache_key} in {elapsed:.1f}s")
            return cache_key, "done", elapsed
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error(f"[prebuild] FAIL {cache_key} ({elapsed:.1f}s): {exc!r}")
            (topic_dir / "FAILED").write_text(repr(exc))
            return cache_key, f"fail:{type(exc).__name__}", elapsed
        finally:
            scope_cm.__exit__(None, None, None)
            Logger.remove_pair_log(pair_sink_id)


async def main_async(
    devset_path: Path,
    cache_dir: Path,
    concurrency: int,
    limit: int | None,
    shard_index: int = 0,
    shard_total: int = 1,
    cache_key_mode: str = "topic",
):
    logger = Logger()
    topics = collect_unique_topics(devset_path, cache_key_mode=cache_key_mode)
    if shard_total > 1:
        topics = [t for i, t in enumerate(topics) if i % shard_total == shard_index]
    if limit:
        topics = topics[:limit]
    logger.info(
        f"[prebuild] {len(topics)} unique topics | concurrency={concurrency} | "
        f"shard={shard_index}/{shard_total} | cache={cache_dir}"
    )
    sem = asyncio.Semaphore(concurrency)
    t_start = time.monotonic()
    results = await asyncio.gather(*(run_one_topic(t, cache_dir, sem, logger) for t in topics))

    elapsed_total = time.monotonic() - t_start
    done = sum(1 for _, s, _ in results if s == "done")
    skipped = sum(1 for _, s, _ in results if s == "skip_cached")
    failed = sum(1 for _, s, _ in results if s.startswith("fail"))
    logger.info(
        f"[prebuild] FINISHED in {elapsed_total/60:.1f}min "
        f"| done={done} skipped={skipped} failed={failed}"
    )

    shard_tag = f"_shard{shard_index}of{shard_total}" if shard_total > 1 else ""
    summary_path = cache_dir / f"_summary{shard_tag}.json"
    summary_path.write_text(
        json.dumps(
            {
                "topics_total": len(topics),
                "cache_key_mode": cache_key_mode,
                "shard_index": shard_index,
                "shard_total": shard_total,
                "done": done,
                "skipped": skipped,
                "failed": failed,
                "elapsed_sec": elapsed_total,
                "concurrency": concurrency,
                "per_topic": [
                    {"cache_key": t, "status": s, "elapsed_sec": e}
                    for t, s, e in results
                ],
            },
            indent=2,
        )
    )


def main():
    bootstrap_secrets()
    parser = argparse.ArgumentParser()
    parser.add_argument("--devset", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="Debug: process only first N topics")
    parser.add_argument("--shard-index", type=int, default=0,
                        help="0-based index of this shard. Used with --shard-total to split topics across processes.")
    parser.add_argument("--shard-total", type=int, default=1,
                        help="Total number of shards. Topics partitioned by index%%total after sorting.")
    parser.add_argument("--model", default=None,
                        help="Override the model alias for literature_review_agent at runtime "
                             "(e.g. claude-opus-4.6). Mutates in-memory config only.")
    parser.add_argument(
        "--cache-key-mode",
        choices=("topic", "topic_cutoff"),
        default="topic",
        help=(
            "topic keeps one cache entry per topic_id. topic_cutoff creates one "
            "entry per (topic_id, cutoff_date)."
        ),
    )
    args = parser.parse_args()

    if args.model:
        from agents.config import get_config
        cfg = get_config()
        cfg.agents["literature_review_agent"].model = args.model

    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Log filename includes shard so two-process runs don't overwrite each other.
    Logger.setup(log_dir=cache_dir, level="DEBUG")
    asyncio.run(main_async(
        Path(args.devset).resolve(), cache_dir, args.concurrency, args.limit,
        shard_index=args.shard_index, shard_total=args.shard_total,
        cache_key_mode=args.cache_key_mode,
    ))


if __name__ == "__main__":
    main()
