"""Batch PDF download + Somark markdown conversion for all arxiv papers.

Usage:
    uv run python scripts/corpus/pdf_to_markdown.py --workers 32
"""

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ideaeval_benchmark.arxiv import download_pdf
from ideaeval_benchmark.config import load_settings, PROJECT_ROOT, resolve_path
from ideaeval_benchmark.somark import convert_pdf_to_markdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def process_one(arxiv_id: str, arxiv_settings, somark_settings, md_output_dir: Path, tmp_pdf_dir: Path) -> tuple[str, str]:
    """Download PDF → convert to markdown → delete PDF. Returns (arxiv_id, status)."""
    md_path = md_output_dir / f"{arxiv_id}.md"
    if md_path.exists() and md_path.stat().st_size > 0:
        return arxiv_id, "cached"

    pdf_path = download_pdf(arxiv_id, tmp_pdf_dir, arxiv_settings)
    try:
        convert_pdf_to_markdown(pdf_path, somark_settings, cache_dir=md_output_dir)
        return arxiv_id, "ok"
    finally:
        pdf_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Batch PDF download + Somark markdown conversion")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--metadata", type=Path, default=None,
                        help="Input metadata JSONL (default: from config.yaml paths.metadata_file)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Markdown output directory (default: from config.yaml paths.markdown_dir)")
    parser.add_argument("--tmp-dir", type=Path, default=None,
                        help="Temporary PDF cache (default: from config.yaml paths.tmp_pdf_dir)")
    parser.add_argument("--errors-file", type=Path, default=None,
                        help="Error log JSONL (default: from config.yaml paths.errors_file)")
    args = parser.parse_args()

    settings = load_settings(
        config_path=Path("config.yaml"),
        env_path=Path(".env"),
    )

    metadata = args.metadata or resolve_path(settings.paths.metadata_file)
    md_output_dir = args.output_dir or resolve_path(settings.paths.markdown_dir)
    tmp_pdf_dir = args.tmp_dir or resolve_path(settings.paths.tmp_pdf_dir)
    errors_file = args.errors_file or resolve_path(settings.paths.errors_file)

    md_output_dir.mkdir(parents=True, exist_ok=True)
    tmp_pdf_dir.mkdir(parents=True, exist_ok=True)
    errors_file.parent.mkdir(parents=True, exist_ok=True)

    # Load all arxiv IDs
    arxiv_ids = []
    with open(metadata) as f:
        for line in f:
            r = json.loads(line)
            arxiv_ids.append(r["arxiv_id"])

    # Check how many already done
    already_done = sum(1 for aid in arxiv_ids if (md_output_dir / f"{aid}.md").exists())
    total = len(arxiv_ids)
    pending = total - already_done
    log.info("Total: %d | Already done: %d | Pending: %d | Workers: %d", total, already_done, pending, args.workers)

    if pending == 0:
        log.info("All papers already converted")
        return

    ok = 0
    cached = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, aid, settings.arxiv, settings.somark, md_output_dir, tmp_pdf_dir): aid
            for aid in arxiv_ids
        }
        for fut in as_completed(futures):
            aid = futures[fut]
            try:
                _, status = fut.result()
                if status == "cached":
                    cached += 1
                else:
                    ok += 1
            except Exception as e:
                failed += 1
                log.warning("FAILED %s: %s", aid, e)
                with open(errors_file, "a") as ef:
                    ef.write(json.dumps({"arxiv_id": aid, "error": str(e)}) + "\n")

            done = ok + cached + failed
            if done % 100 == 0 or done == total:
                elapsed = time.time() - t0
                new_done = ok + failed  # exclude cached from rate calc
                rate = new_done / elapsed if elapsed > 0 else 0
                remaining = pending - new_done
                eta_s = remaining / rate if rate > 0 else 0
                eta_h = eta_s / 3600
                log.info(
                    "[%d/%d] ok=%d cached=%d failed=%d | %.1f papers/s | ETA: %.1fh",
                    done, total, ok, cached, failed, rate, eta_h,
                )

    elapsed = time.time() - t0
    log.info("DONE in %.1fs (%.1fh): ok=%d cached=%d failed=%d", elapsed, elapsed / 3600, ok, cached, failed)

    # Cleanup tmp pdf dir if empty
    try:
        tmp_pdf_dir.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()
