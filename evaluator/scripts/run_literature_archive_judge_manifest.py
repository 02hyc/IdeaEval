"""Run judge-only evaluation from a pair manifest and literature workspaces.

This is the minimal production-safe debug runner for the current ranker judge.
It provides the judge with:

- `literature_review.md`
- two proposal markdown files
- the literature agent's local archive (`paper_summaries/`, `scraped_papers/`)

It intentionally does not feed research-agent analysis reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config import get_config  # noqa: E402
from runtime.pair_manifest import (  # noqa: E402
    find_pair_item_proposal_file,
    ground_truth_from_pair_item,
    mirror_result,
    normalize_result,
    paper_id_from_pair_item,
    random_swap_for_pair,
    strip_proposal_title,
)
from runtime.rank_runner import (  # noqa: E402
    RankRunConfig,
    ToolLoopTrace,
    _parse_ranking_json,
    _run_judge,
)
from runtime.secret_bootstrap import bootstrap_secrets  # noqa: E402
from utils.logger import Logger  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _format_annotation_markdown(
    *,
    pair: dict[str, Any],
    canonical_gt: str,
    display_gt: str,
    display_a_id: str,
    display_b_id: str,
    swapped: bool,
) -> str:
    annotation = pair.get("annotation") or {}
    paper_a = pair.get("paper_a") or {}
    paper_b = pair.get("paper_b") or {}
    lines = [
        "# Human Annotation",
        "",
        "## Pair",
        "",
        f"- Pair id: `{pair.get('pair_id')}`",
        f"- Bucket: `{pair.get('bucket')}`",
        f"- Topic id: `{pair.get('topic_id')}`",
        f"- Topic label: {pair.get('topic_label') or ''}",
        f"- Cutoff date: `{pair.get('cutoff_date')}`",
        f"- Canonical ground truth: `{canonical_gt}`",
        f"- Displayed ground truth: `{display_gt}`",
        f"- Display order: `{'swapped' if swapped else 'dataset'}`",
        f"- Display Proposal A id: `{display_a_id}`",
        f"- Display Proposal B id: `{display_b_id}`",
        "",
        "## Canonical Sides",
        "",
        f"- Canonical A: `{paper_a.get('paper_id') or paper_a.get('ai_run_id') or 'unknown'}`; side=`{paper_a.get('side')}`; is_ai_generated=`{paper_a.get('is_ai_generated')}`",
        f"- Canonical B: `{paper_b.get('paper_id') or paper_b.get('ai_run_id') or 'unknown'}`; side=`{paper_b.get('side')}`; is_ai_generated=`{paper_b.get('is_ai_generated')}`",
        "",
        "## Overall Annotation",
        "",
        f"- Overall: `{annotation.get('overall') or (annotation.get('per_dimension') or {}).get('overall')}`",
        f"- Ground-truth label: `{pair.get('label')}`",
        f"- Ground-truth much better: `{pair.get('ground_truth_much_better')}`",
        f"- Annotator: `{annotation.get('annotator')}`",
        f"- Saved at: `{annotation.get('saved_at')}`",
        "",
        "## Per-Dimension Annotation",
        "",
    ]
    per_dimension = annotation.get("per_dimension") or {}
    if per_dimension:
        for key, value in per_dimension.items():
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Notes", ""])
    lines.append(annotation.get("notes") or "None recorded.")
    lines.extend(["", "## Defects", ""])
    defects = annotation.get("defects") or []
    if defects:
        for i, defect in enumerate(defects, start=1):
            if isinstance(defect, dict):
                lines.extend(
                    [
                        f"### Defect {i}",
                        "",
                        f"- Type: `{defect.get('type')}`",
                        f"- Severity: `{defect.get('severity')}`",
                        "",
                        defect.get("note") or "No note recorded.",
                        "",
                    ]
                )
                evidence = defect.get("evidence") or []
                if evidence:
                    lines.append("Evidence:")
                    for item in evidence:
                        if isinstance(item, dict):
                            lines.append(f"- `{item.get('type')}`: {item.get('value')}")
                            if item.get("note"):
                                lines.append(f"  - Note: {item.get('note')}")
                        else:
                            lines.append(f"- {item}")
                    lines.append("")
            else:
                lines.append(f"- {defect}")
    else:
        lines.append("None recorded.")
    lines.append("")
    return "\n".join(lines)


def _write_review_context(
    *,
    pair_dir: Path,
    pair: dict[str, Any],
    canonical_gt: str,
    display_gt: str,
    display_a_id: str,
    display_b_id: str,
    swapped: bool,
) -> None:
    annotation = pair.get("annotation") or {}
    _write_json(pair_dir / "source_pair.json", pair)
    _write_json(pair_dir / "human_annotation.json", annotation)
    (pair_dir / "human_annotation.md").write_text(
        _format_annotation_markdown(
            pair=pair,
            canonical_gt=canonical_gt,
            display_gt=display_gt,
            display_a_id=display_a_id,
            display_b_id=display_b_id,
            swapped=swapped,
        ),
        encoding="utf-8",
    )


def _apply_judge_model(model_name: str | None) -> dict[str, str]:
    if not model_name:
        return {}
    app_config = get_config()
    if model_name not in app_config.models:
        raise ValueError(f"Unknown judge model {model_name!r}; available models: {sorted(app_config.models)}")
    app_config.agents["judge_agent"].model = model_name
    return {"judge_agent": model_name}


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        return payload["pairs"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unsupported manifest shape in {path}")


def _lit_workspace_for_pair(
    pair: dict[str, Any], lit_cache_root: Path | None, base_dir: Path | None = None
) -> Path:
    for key in ("setc_lr_dir", "literature_workspace", "source_lit_workspace"):
        raw = pair.get(key)
        if not raw:
            continue
        raw_path = Path(str(raw))
        # Relative paths resolve against the CWD first, then the manifest's data root.
        candidates = [raw_path]
        if base_dir is not None and not raw_path.is_absolute():
            candidates.append(base_dir / raw_path)
        for path in candidates:
            if (path / "literature_review.md").is_file():
                return path
            workspace = path / "agents" / "literature_review" / "workspace"
            if (workspace / "literature_review.md").is_file():
                return workspace

    raw_path = pair.get("setc_lr_path")
    if raw_path:
        path = Path(str(raw_path))
        if path.is_file():
            return path.parent

    if lit_cache_root is None:
        raise FileNotFoundError(f"No literature workspace path in pair {pair.get('pair_id')!r}")

    explicit = pair.get("literature_cache_key") or pair.get("lit_topic_id")
    if explicit:
        candidates = [lit_cache_root / str(explicit)]
    else:
        cutoff = str(pair.get("cutoff_date") or "").replace("-", "")
        topic_id = pair.get("topic_id") or pair.get("v13_topic_id")
        candidates = [
            lit_cache_root / f"{topic_id}__cutoff_{cutoff}",
            lit_cache_root / str(topic_id),
        ]
    for base in candidates:
        workspace = base / "agents" / "literature_review" / "workspace"
        if (workspace / "literature_review.md").is_file():
            return workspace
    raise FileNotFoundError(f"No completed literature workspace for pair {pair.get('pair_id')!r}")


def _pair_dir_name(pair: dict[str, Any], display_a_id: str, display_b_id: str) -> str:
    return f"{pair['pair_id']}_{display_a_id}_vs_{display_b_id}"


def _load_pair_proposals(pair: dict[str, Any], proposal_root: Path) -> tuple[str, str, Path, Path]:
    paper_a_id = paper_id_from_pair_item(pair, "a")
    paper_b_id = paper_id_from_pair_item(pair, "b")
    path_a = find_pair_item_proposal_file(proposal_root, pair, "a", paper_a_id)
    path_b = find_pair_item_proposal_file(proposal_root, pair, "b", paper_b_id)
    return (
        strip_proposal_title(path_a.read_text(encoding="utf-8")),
        strip_proposal_title(path_b.read_text(encoding="utf-8")),
        path_a,
        path_b,
    )


def _canonical_score(ranking: dict[str, Any], canonical_gt: str, swapped: bool) -> dict[str, Any]:
    raw_result = ranking.get("result")
    try:
        displayed_prediction = normalize_result(raw_result)
    except Exception:
        displayed_prediction = str(raw_result or "")
    canonical_prediction = mirror_result(displayed_prediction) if swapped else displayed_prediction
    return {
        "displayed_prediction": displayed_prediction,
        "canonical_prediction": canonical_prediction,
        "canonical_ground_truth": canonical_gt,
        "correct": canonical_prediction == canonical_gt,
    }


async def _run_one(
    *,
    idx: int,
    total: int,
    pair: dict[str, Any],
    proposal_root: Path,
    lit_cache_root: Path | None,
    output_root: Path,
    random_order_seed: int,
    max_attempts: int,
    pair_timeout_seconds: float,
) -> dict[str, Any]:
    pair_id = str(pair["pair_id"])
    canonical_gt = ground_truth_from_pair_item(pair)
    proposal_a, proposal_b, proposal_a_path, proposal_b_path = _load_pair_proposals(pair, proposal_root)
    paper_a_id = paper_id_from_pair_item(pair, "a")
    paper_b_id = paper_id_from_pair_item(pair, "b")
    swapped = random_swap_for_pair(pair_id, random_order_seed)
    if swapped:
        display_a, display_b = proposal_b, proposal_a
        display_a_id, display_b_id = paper_b_id, paper_a_id
        display_gt = mirror_result(canonical_gt)
    else:
        display_a, display_b = proposal_a, proposal_b
        display_a_id, display_b_id = paper_a_id, paper_b_id
        display_gt = canonical_gt

    pair_dir = output_root / _pair_dir_name(pair, display_a_id, display_b_id)
    pair_dir.mkdir(parents=True, exist_ok=True)
    _write_review_context(
        pair_dir=pair_dir,
        pair=pair,
        canonical_gt=canonical_gt,
        display_gt=display_gt,
        display_a_id=display_a_id,
        display_b_id=display_b_id,
        swapped=swapped,
    )
    completed_path = pair_dir / "full_ranking_result.json"
    if completed_path.is_file():
        full = _read_json(completed_path)
        ranking = full.get("ranking", {})
        return {
            "pair_id": pair_id,
            "status": "skipped_complete",
            "output_pair_dir": str(pair_dir),
            **_canonical_score(ranking, canonical_gt, swapped),
        }

    lit_workspace = _lit_workspace_for_pair(pair, lit_cache_root, base_dir=proposal_root)
    literature_review = (lit_workspace / "literature_review.md").read_text(encoding="utf-8")
    (pair_dir / "literature_review_source.txt").write_text(str(lit_workspace.resolve()) + "\n", encoding="utf-8")

    meta = {
        "pair_id": pair_id,
        "bucket": pair.get("bucket"),
        "topic_id": pair.get("topic_id"),
        "topic_label": pair.get("topic_label"),
        "topic_description": pair.get("topic_description"),
        "cutoff_date": pair.get("cutoff_date"),
        "paper_a_id": paper_a_id,
        "paper_b_id": paper_b_id,
        "display_paper_a_id": display_a_id,
        "display_paper_b_id": display_b_id,
        "display_order": "swapped" if swapped else "dataset",
        "ground_truth": display_gt,
        "canonical_ground_truth": canonical_gt,
        "proposal_a_path": str(proposal_a_path),
        "proposal_b_path": str(proposal_b_path),
        "proposal_title_stripping": "first Markdown H1 removed before judge input",
        "literature_review_workspace": str(lit_workspace),
        "source_pair": pair,
    }

    config = RankRunConfig(
        proposal_a=display_a,
        proposal_b=display_b,
        cutoff_date=str(pair.get("cutoff_date") or ""),
        topic_label=pair.get("topic_label"),
        topic_description=pair.get("topic_description", ""),
        task_root=str(pair_dir),
        meta=meta,
    )

    logger = Logger()
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with logger.pair_scope(pair_id):
                logger.info(f"[{idx}/{total}] archive-judge START {pair_id} attempt={attempt}")
                judge_output, judge_trace = await asyncio.wait_for(
                    _run_judge(
                        proposal_a=display_a,
                        proposal_b=display_b,
                        report_a="",
                        report_b="",
                        cutoff_date=str(pair.get("cutoff_date") or ""),
                        config=config,
                        agent_dir=pair_dir / "agents" / "judge",
                        literature_review=literature_review,
                        literature_review_workspace=lit_workspace,
                    ),
                    timeout=pair_timeout_seconds,
                )
            ranking = _parse_ranking_json(judge_output) or {"raw_output": judge_output}
            _write_json(pair_dir / "ranking_result.json", ranking)
            trace_data = {
                "judge": {
                    "rounds": judge_trace.rounds if isinstance(judge_trace, ToolLoopTrace) else [],
                    "total_tool_calls": judge_trace.total_tool_calls if isinstance(judge_trace, ToolLoopTrace) else 0,
                }
            }
            _write_json(pair_dir / "trace.json", trace_data)
            full_result = {
                "meta": meta,
                "ranking": ranking,
                "trace": trace_data,
                "config": {
                    "cutoff_date": pair.get("cutoff_date"),
                    "source_manifest_pair_id": pair_id,
                    "literature_review_workspace": str(lit_workspace),
                    "judge_input_mode": "literature_archive_plus_proposals_no_reports",
                },
            }
            _write_json(completed_path, full_result)
            return {
                "pair_id": pair_id,
                "status": "done",
                "output_pair_dir": str(pair_dir),
                **_canonical_score(ranking, canonical_gt, swapped),
            }
        except Exception as exc:
            last_error = exc
            logger.exception(f"[{idx}/{total}] archive-judge FAILED {pair_id} attempt={attempt}: {exc!r}")
            await asyncio.sleep(min(5 * attempt, 30))

    return {
        "pair_id": pair_id,
        "status": "failed",
        "output_pair_dir": str(pair_dir),
        "error": repr(last_error),
        "canonical_ground_truth": canonical_gt,
    }


def _summarize(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    completed = [r for r in results if r.get("status") in {"done", "skipped_complete"}]
    correct = sum(1 for r in completed if r.get("correct"))
    summary["done"] = sum(1 for r in results if r.get("status") == "done")
    summary["skipped"] = sum(1 for r in results if r.get("status") == "skipped_complete")
    summary["failed"] = len(results) - len(completed)
    summary["correct"] = correct
    summary["completed"] = len(completed)
    summary["accuracy"] = correct / len(completed) if completed else None
    summary["results"] = results


async def _run(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset).resolve()
    proposal_root = Path(args.proposal_root).resolve()
    lit_cache_root = Path(args.lit_cache_root).resolve() if args.lit_cache_root else None
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    pairs = _load_manifest(dataset_path)
    if args.pair_id:
        selected = set(args.pair_id)
        pairs = [pair for pair in pairs if str(pair.get("pair_id")) in selected]
    if args.limit is not None:
        pairs = pairs[: args.limit]

    bootstrap_secrets()
    model_overrides = _apply_judge_model(args.judge_model)
    Logger.setup(log_dir=str(output_root), level=args.log_level)
    logger = Logger()
    logger.set_task_id("literature_archive_judge_manifest")

    summary: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": str(Path(__file__).resolve()),
        "argv": sys.argv,
        "dataset": str(dataset_path),
        "proposal_root": str(proposal_root),
        "lit_cache_root": str(lit_cache_root) if lit_cache_root else None,
        "output_root": str(output_root),
        "concurrency": args.concurrency,
        "max_attempts": args.max_attempts,
        "pair_timeout_seconds": args.pair_timeout_seconds,
        "random_order_seed": args.random_order_seed,
        "model_overrides": model_overrides,
        "total_selected": len(pairs),
        "judge_input_mode": "literature_archive_plus_proposals_no_reports",
    }
    _write_json(output_root / "summary.json", summary)

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict[str, Any]] = []

    async def guarded(i: int, pair: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await _run_one(
                idx=i,
                total=len(pairs),
                pair=pair,
                proposal_root=proposal_root,
                lit_cache_root=lit_cache_root,
                output_root=output_root,
                random_order_seed=args.random_order_seed,
                max_attempts=args.max_attempts,
                pair_timeout_seconds=args.pair_timeout_seconds,
            )

    tasks = [asyncio.create_task(guarded(i, pair)) for i, pair in enumerate(pairs, start=1)]
    for completed in asyncio.as_completed(tasks):
        result = await completed
        results.append(result)
        _summarize(summary, results)
        _write_json(output_root / "summary.json", summary)
        logger.info(
            f"progress done={summary['done']} skipped={summary['skipped']} "
            f"failed={summary['failed']} accuracy={summary['accuracy']}"
        )

    _summarize(summary, results)
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(output_root / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("completed", "correct", "accuracy", "failed")}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--proposal-root", required=True)
    parser.add_argument("--lit-cache-root")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--pair-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--random-order-seed", type=int, default=42)
    parser.add_argument("--pair-id", action="append", help="Only run the selected manifest pair id; repeatable.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
