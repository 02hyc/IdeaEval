"""Extract structured research fields for every filtered paper.

Extracts three structured fields per paper from title + abstract:
  - research_problem: specific problem the paper addresses (1-2 sentences)
  - task_formulation: specific AI/ML task using standard terminology
  - application_domain: application domain or "general"

Usage:
    uv run python scripts/features/extract_fields.py [--workers 128] [--limit 50] [--model deepseek-v4-pro]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ideaeval_benchmark.config import PROJECT_ROOT, LLMSettings
from ideaeval_benchmark.llm_client import LLMClient

log = logging.getLogger(__name__)

FILTERED_PAPERS = PROJECT_ROOT / "data" / "arxiv" / "arxiv_2025sep_2026jan_metadata_filtered.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "field_extraction" / "fields_extracted.jsonl"

SYSTEM_PROMPT = """\
You are a research paper analyst. Given a paper's title and abstract, extract three structured fields. Be precise and grounded — every field must be directly supported by the text provided.

Output ONLY valid JSON with exactly these three keys:
{
  "research_problem": "<1-2 sentences describing the specific research problem this paper addresses. Must be concrete and falsifiable, not vague. Example: 'Reducing hallucination in retrieval-augmented generation when retrieved context contains contradictory information'>",
  "task_formulation": "<The specific AI/ML task(s) using established terminology, semicolon-separated if multiple. Example: 'open-domain question answering', 'monocular depth estimation', 'text-to-image generation'. If the task is novel, describe as input→output.>",
  "application_domain": "<The application domain. Use 'general' if domain-agnostic. Example: 'medical imaging', 'autonomous driving', 'code generation', 'general'>"
}

Rules:
- research_problem must capture WHAT problem is solved, not HOW (no method names).
- task_formulation must use standard ML/AI terminology where possible.
- application_domain should be the most specific applicable domain; use 'general' only if truly domain-agnostic.
- Do NOT include any text outside the JSON object."""

USER_TEMPLATE = """\
Title: {title}

Abstract: {abstract}"""


def load_papers(path: Path) -> list[dict]:
    papers = []
    with open(path) as f:
        for line in f:
            papers.append(json.loads(line))
    return papers


def load_done_ids(output_path: Path) -> set[str]:
    done = set()
    if output_path.exists():
        with open(output_path) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["paper_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def process_one(
    paper: dict, llm: LLMClient, llm_semaphore: threading.Semaphore
) -> dict:
    pid = paper["paper_id"]
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")

    result = {
        "paper_id": pid,
        "title": title,
        "research_problem": "",
        "task_formulation": "",
        "application_domain": "",
        "status": "ok",
    }

    if not abstract.strip():
        result["status"] = "no_abstract"
        return result

    user_msg = USER_TEMPLATE.format(title=title, abstract=abstract)

    time.sleep(random.uniform(0, 1))
    with llm_semaphore:
        try:
            raw = llm.generate(
                system=SYSTEM_PROMPT,
                user=user_msg,
                max_tokens=512,
                temperature=0.1,
            )
            # Parse JSON from response — handle possible markdown fences
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            parsed = json.loads(text)
            result["research_problem"] = parsed.get("research_problem", "")
            result["task_formulation"] = parsed.get("task_formulation", "")
            result["application_domain"] = parsed.get("application_domain", "")
        except json.JSONDecodeError as e:
            result["status"] = f"json_parse_error: {e}"
            result["raw_response"] = raw[:500] if raw else ""
        except Exception as e:
            result["status"] = f"llm_error: {e}"

    return result


def main():
    parser = argparse.ArgumentParser(description="Batch structured field extraction")
    parser.add_argument("--workers", type=int, default=128)
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int, default=0, help="Limit papers (0=all)")
    parser.add_argument("--model", type=str,
                        default=os.environ.get("LLM_MODEL", "deepseek-v4-pro"))
    parser.add_argument("--concurrency", type=int, default=50, help="Max concurrent LLM calls")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("ideaeval_benchmark.llm_client").setLevel(logging.WARNING)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    papers = load_papers(FILTERED_PAPERS)
    log.info("Loaded %d papers", len(papers))

    done_ids = load_done_ids(output_path)
    pending = [p for p in papers if p["paper_id"] not in done_ids]
    if done_ids:
        log.info("Resuming: %d already done, %d pending", len(done_ids), len(pending))

    if args.limit > 0:
        pending = pending[: args.limit]
        log.info("Limited to %d papers", len(pending))

    if not pending:
        log.info("All papers already processed")
        return

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    os.environ.setdefault("OPENAI_MAX_CONNECTIONS", str(args.workers + 50))
    os.environ.setdefault("OPENAI_MAX_KEEPALIVE_CONNECTIONS", str(args.workers + 50))
    os.environ.setdefault("OPENAI_TIMEOUT_SECONDS", "120")

    llm_settings = LLMSettings(
        provider="openai",
        model=args.model,
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", ""),
        max_retries=5,
        max_tokens=512,
        temperature=0.1,
    )
    llm = LLMClient(llm_settings)

    write_lock = threading.Lock()

    def write_result(result: dict):
        line = json.dumps(result, ensure_ascii=False) + "\n"
        with write_lock:
            with open(output_path, "a") as f:
                f.write(line)

    llm_concurrency = min(args.workers, args.concurrency)
    llm_semaphore = threading.Semaphore(llm_concurrency)

    log.info(
        "Starting with %d workers, %d max concurrent LLM calls, model=%s",
        args.workers, llm_concurrency, args.model,
    )
    t0 = time.time()
    completed = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, p, llm, llm_semaphore): p for p in pending
        }
        for fut in as_completed(futures):
            paper = futures[fut]
            try:
                result = fut.result()
                write_result(result)

                completed += 1
                if "error" in result.get("status", ""):
                    errors += 1

                if completed % 100 == 0:
                    elapsed = time.time() - t0
                    rate = completed / elapsed
                    eta = (len(pending) - completed) / rate if rate > 0 else 0
                    log.info(
                        "[%d/%d] %.1f papers/s, %d errors, ETA %.0fs",
                        completed, len(pending), rate, errors, eta,
                    )
            except Exception as e:
                log.error("Fatal error for %s: %s", paper.get("paper_id", "?"), e)

    elapsed = time.time() - t0
    log.info(
        "Done: %d papers in %.0fs (%.1f/s), %d errors",
        completed, elapsed, completed / max(elapsed, 1), errors,
    )


if __name__ == "__main__":
    main()
