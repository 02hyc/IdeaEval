"""Phase 7: Deep review with full markdown + web search.

Reads paper original text (abstract + intro ~10000 chars) and uses LLM
for per-paper judgment. Optionally does Serper web search for unknown
benchmarks/concepts before judging.

Uses inclusive review criteria: papers are kept if they fit the broad
cluster theme (label), not just the narrow RQ.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .llm import LLMClient, RateLimiter, parse_json

log = logging.getLogger(__name__)

MD_DIR: Path | None = None


def _get_md_dir() -> Path:
    """Resolve markdown directory from env var or default."""
    if MD_DIR is not None:
        return MD_DIR
    return Path(os.environ.get("MARKDOWN_DIR", "data/markdown"))

REVIEW_SYSTEM_ROUND1 = """\
You are a research paper reviewer for a thematic cluster. You will be given:
1. The cluster's theme label and research question (RQ)
2. A paper's original text (abstract + introduction)

Your task: determine whether this paper belongs in this thematic cluster.

Be INCLUSIVE:
- If the paper directly addresses the RQ, it belongs
- If the paper does not strictly match the RQ but falls within the broader \
cluster theme indicated by the label, it belongs
- Only kick papers that are genuinely IRRELEVANT to the cluster theme \
(e.g., clearly from a different research area)

When in doubt, KEEP the paper.

If you encounter concepts you are unsure about and that are CRITICAL to your \
judgment, list them in "search_queries". Otherwise leave "search_queries" empty.

Return ONLY this JSON:
{{"belongs": true/false, "confidence": 0.0-1.0, "reasoning": "1-2 sentence explanation", "search_queries": ["query1", "query2"] or []}}"""

REVIEW_SYSTEM_ROUND2 = """\
You are a research paper reviewer. You previously reviewed a paper but needed \
additional context. Here are the web search results for your queries.

Re-evaluate your judgment with this new information. Remember: be INCLUSIVE, \
only kick papers that are genuinely irrelevant to the cluster theme.

Web search results:
{web_results}

Return ONLY this JSON:
{{"belongs": true/false, "confidence": 0.0-1.0, "reasoning": "1-2 sentence explanation"}}"""

REVIEW_USER = """\
## Cluster Theme
{cluster_label}

## Research Question
{research_question}

## Research Question Detail
{research_question_detail}

## Paper: {paper_title} [{paper_id}]

### Original Text (abstract + introduction)
{paper_text}"""


def _read_paper_text(paper_id: str, md_dir: Path | None = None) -> tuple[str, str]:
    """Read paper markdown, return (text, source). Source is 'markdown' or 'none'."""
    base = md_dir if md_dir is not None else _get_md_dir()
    md_path = base / f"{paper_id}.md"
    if not md_path.exists():
        return "", "none"

    text = md_path.read_text(encoding="utf-8", errors="replace")

    lower = text.lower()
    start = 0
    for marker in ["abstract", "# abstract", "## abstract"]:
        idx = lower.find(marker)
        if idx != -1:
            start = idx
            break

    return text[start:start + 10000], "markdown"


def _serper_search(query: str) -> str:
    """Quick web search via Serper API."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return ""
    try:
        conn = http.client.HTTPSConnection("google.serper.dev", timeout=10)
        payload = json.dumps({"q": query})
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        conn.request("POST", "/search", payload, headers)
        res = conn.getresponse()
        data = json.loads(res.read().decode("utf-8"))
        conn.close()
        snippets = []
        if "knowledgeGraph" in data:
            snippets.append(data["knowledgeGraph"].get("description", ""))
        for item in data.get("organic", [])[:2]:
            snippets.append(item.get("snippet", ""))
        return "\n".join(s for s in snippets if s)[:500]
    except Exception:
        return ""


def _review_one_paper(
    llm: LLMClient,
    paper_id: str,
    paper_title: str,
    research_question: str,
    research_question_detail: str,
    cluster_id: str,
    cluster_label: str = "",
    md_dir: Path | None = None,
) -> dict:
    """Review a single paper: read markdown, LLM round 1, optional web search, LLM round 2."""
    t0 = time.time()

    paper_text, source = _read_paper_text(paper_id, md_dir=md_dir)
    if not paper_text:
        paper_text = f"[No full text available. Title: {paper_title}]"
        source = "title_only"

    user = REVIEW_USER.format(
        cluster_label=cluster_label,
        research_question=research_question,
        research_question_detail=research_question_detail or "",
        paper_title=paper_title,
        paper_id=paper_id,
        paper_text=paper_text,
    )

    result = {
        "paper_id": paper_id,
        "cluster_id": cluster_id,
        "source": source,
        "web_searched": False,
    }

    try:
        raw = llm.generate(system=REVIEW_SYSTEM_ROUND1, user=user, max_tokens=4096, temperature=0.0)
        parsed = parse_json(raw)

        if not isinstance(parsed, dict):
            result["belongs"] = True
            result["confidence"] = 0.0
            result["reasoning"] = f"Parse failed: {raw[:150]}"
            return _log_result(result, t0)

        search_queries = parsed.get("search_queries", [])

        if search_queries:
            web_results_parts = []
            for q in search_queries[:3]:
                snippet = _serper_search(q)
                if snippet:
                    web_results_parts.append(f"Query: {q}\nResult: {snippet}")

            if web_results_parts:
                result["web_searched"] = True
                web_results_text = "\n\n".join(web_results_parts)

                raw2 = llm.generate(
                    system=REVIEW_SYSTEM_ROUND2.format(web_results=web_results_text),
                    user=user,
                    max_tokens=4096,
                    temperature=0.0,
                )
                parsed2 = parse_json(raw2)
                if isinstance(parsed2, dict):
                    parsed = parsed2

        result["belongs"] = parsed.get("belongs", True)
        result["confidence"] = parsed.get("confidence", 0.5)
        result["reasoning"] = parsed.get("reasoning", "")

    except Exception as e:
        result["belongs"] = True
        result["confidence"] = 0.0
        result["reasoning"] = f"LLM error: {e}"

    return _log_result(result, t0)


def _log_result(result: dict, t0: float) -> dict:
    elapsed = time.time() - t0
    status = "KICK" if not result.get("belongs", True) else "KEEP"
    ws = " [WS]" if result.get("web_searched") else ""
    log.info("[%s]%s %s (%.1fs) conf=%.2f | %s",
             status, ws, result["paper_id"], elapsed,
             result.get("confidence", 0),
             result.get("reasoning", "")[:80])
    return result


def run_deep_review(
    clusters: list[dict],
    output_dir: Path,
    max_workers: int = 30,
    min_leaf: int = 20,
    md_dir: Path | None = None,
) -> tuple[list[dict], int]:
    """Run deep review on all clusters using LLM API + markdown reading."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "deep_review_results.jsonl"

    if results_path.exists():
        results_path.unlink()

    rate_limiter = RateLimiter(rpm=18_000)
    llm = LLMClient(rate_limiter=rate_limiter)

    tasks = []
    for cluster in clusters:
        rq = cluster.get("research_question", cluster.get("label", ""))
        rq_detail = cluster.get("research_question_detail", "")
        label = cluster.get("label", "")
        for pid in cluster["paper_ids"]:
            tasks.append({
                "paper_id": pid,
                "paper_title": label,
                "research_question": rq,
                "research_question_detail": rq_detail,
                "cluster_id": cluster["cluster_id"],
                "cluster_label": label,
            })

    total = len(tasks)
    log.info("Deep review: %d papers across %d clusters (workers=%d)",
             total, len(clusters), max_workers)

    all_results: list[dict] = []
    done = 0
    import threading
    write_lock = threading.Lock()

    def _process(task: dict) -> dict:
        nonlocal done
        result = _review_one_paper(
            llm,
            task["paper_id"],
            task["paper_title"],
            task["research_question"],
            task["research_question_detail"],
            task["cluster_id"],
            cluster_label=task["cluster_label"],
            md_dir=md_dir,
        )
        with write_lock:
            with open(results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
            done += 1
            if done % 100 == 0:
                kicked = sum(1 for r in all_results if not r.get("belongs", True))
                log.info("=== Progress: %d/%d done, %d kicked ===", done, total, kicked)
        return result

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process, t): t for t in tasks}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                all_results.append(result)
            except Exception as e:
                task = futures[fut]
                log.warning("Review failed for %s: %s", task["paper_id"], e)
                all_results.append({
                    "paper_id": task["paper_id"],
                    "cluster_id": task["cluster_id"],
                    "belongs": True,
                    "confidence": 0.0,
                    "reasoning": f"Exception: {e}",
                })

    kicked_by_cluster: dict[str, set[str]] = {}
    for r in all_results:
        if not r.get("belongs", True):
            kicked_by_cluster.setdefault(r["cluster_id"], set()).add(r["paper_id"])

    total_kicked = 0
    verified = []
    for cluster in clusters:
        cid = cluster["cluster_id"]
        kicked_pids = kicked_by_cluster.get(cid, set())
        remaining = [pid for pid in cluster["paper_ids"] if pid not in kicked_pids]
        cluster_copy = dict(cluster)
        cluster_copy["paper_ids"] = remaining
        cluster_copy["n_papers"] = len(remaining)
        cluster_copy["n_kicked"] = len(kicked_pids)
        total_kicked += len(kicked_pids)
        if len(remaining) >= min_leaf:
            verified.append(cluster_copy)

    with open(output_dir / "clusters_deep_verified.json", "w", encoding="utf-8") as f:
        json.dump(verified, f, ensure_ascii=False, indent=2)

    summary = {
        "n_clusters_input": len(clusters),
        "n_clusters_after": len(verified),
        "n_papers_reviewed": len(all_results),
        "n_papers_kicked": total_kicked,
        "n_papers_remaining": sum(c["n_papers"] for c in verified),
    }
    with open(output_dir / "deep_review_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info("Deep review: %d/%d kicked, %d/%d clusters remain (min_leaf=%d)",
             total_kicked, len(all_results), len(verified), len(clusters), min_leaf)

    return verified, total_kicked
