"""Date-aware Scraper handler for the ranker pipeline.

Uses the team-hosted web-tools-service /extract endpoint for academic paper extraction.
Stores extracted PDF paper files in the agent workspace.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

import aiohttp

from .base_tool_handler import BaseToolHandler, ToolState, ToolFailedError

logger = logging.getLogger(__name__)


class ScraperDatedToolHandler(BaseToolHandler):
    """Paper extraction via web-tools-service /extract endpoint.

    After a successful extraction, every paper folder is immediately fed to
    SummarizePaper in parallel. This guarantees no scraped paper is left
    without a Layer-2 summary (forgetting-to-summarize was observed in the
    decoupled design).
    """

    def __init__(self, tool_state: ToolState):
        super().__init__(tool_state=tool_state)
        self.requires_confirmation = False
        self._summarizer = None  # lazily instantiated to avoid circular imports

    def _get_summarizer(self):
        if self._summarizer is None:
            from .summarize_paper_tool import SummarizePaperToolHandler
            self._summarizer = SummarizePaperToolHandler(tool_state=self.tool_state)
        return self._summarizer

    @property
    def name(self) -> str:
        return "Scraper"

    @property
    def display_result_type(self) -> str:
        return "scraper_result"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of paper URLs to extract (arxiv, openreview, ACL Anthology, etc)."
                    ),
                },
            },
            "required": ["sources"],
            "additionalProperties": False,
        }

    async def execute_async(self, sources: List[str]) -> Dict[str, Any]:
        if not sources:
            raise ToolFailedError("Error: 'sources' must be a non-empty list")

        import asyncio
        import time

        service_url = os.getenv("WEB_TOOLS_SERVICE_URL", "http://localhost:8900").rstrip("/")
        service_token = os.getenv("WEB_TOOLS_SERVICE_TOKEN", "")

        headers = {
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "application/json",
        }

        cutoff_date = self.tool_state.cutoff_date

        valid_urls = [url.strip() for url in sources if url.strip()]
        logger.info(f"[scraper_dated] Launching {len(valid_urls)} extract+summarize in parallel")

        connector = aiohttp.TCPConnector(limit_per_host=20)
        async with aiohttp.ClientSession(connector=connector) as session:
            t0 = time.perf_counter()
            tasks = [
                self._extract_and_summarize(session, service_url, headers, url, cutoff_date)
                for url in valid_urls
            ]
            results = list(await asyncio.gather(*tasks))
            elapsed = time.perf_counter() - t0
            logger.info(f"[scraper_dated] All {len(tasks)} extract+summarize done in {elapsed:.1f}s")

        # Add entries for empty URLs that were skipped
        empty_count = sum(1 for url in sources if not url.strip())
        results.extend([{"error": "empty URL", "url": ""}] * empty_count)

        num_success = sum(1 for r in results if "result" in r)
        combined_text = self._format_results(results)

        return {
            "tool_success": num_success > 0,
            "result": combined_text,
            "display_result": {
                "type": "scraper_result",
                "data": {"num_success": num_success, "num_failed": len(results) - num_success},
                "tool_success": num_success > 0,
            },
        }

    async def _extract_and_summarize(
        self,
        session: aiohttp.ClientSession,
        service_url: str,
        headers: dict,
        url: str,
        cutoff_date: str | None,
    ) -> dict:
        """Extract one paper, then immediately summarize it.

        Per-URL parallelism: all URLs run extract+summarize in their own task
        via the outer asyncio.gather; here we only sequence the two steps
        within ONE URL (summary can't start before extract finishes).
        """
        # Fast path: try the grobid-backed /scrape_v1 first (~10x faster on
        # fresh URLs). Fall back to /extract when grobid is unhappy:
        #   - degraded single-Content.md zip (grobid parsed but produced junk)
        #   - retriable HTTP errors (5xx / upstream / generic exception)
        # Non-retriable errors (cutoff 403, invalid URL 422, auth 401) bubble
        # up immediately — /extract won't help either.
        res = await self._extract_one(
            session, service_url, headers, url, cutoff_date, endpoint="/scrape_v1"
        )
        needs_fallback = False
        if "result" in res:
            if self._is_grobid_degraded(res["result"]):
                logger.warning(
                    f"[scraper_dated] /scrape_v1 returned degraded output for {url}; "
                    "falling back to /extract"
                )
                needs_fallback = True
        elif self._error_is_retriable(res.get("error", "")):
            logger.warning(
                f"[scraper_dated] /scrape_v1 returned retriable error for {url}: "
                f"{res.get('error', '')[:120]}; falling back to /extract"
            )
            needs_fallback = True

        if needs_fallback:
            res = await self._extract_one(
                session, service_url, headers, url, cutoff_date, endpoint="/extract"
            )
        if "result" not in res:
            return res  # extraction failed; no summarization
        output_dir = res["result"].get("output_dir")
        if not output_dir:
            return res
        try:
            summarizer = self._get_summarizer()
            sr = await summarizer.execute_async(paper_folder=output_dir)
            if sr.get("tool_success") and isinstance(sr.get("result"), dict):
                res["result"]["summary_path"] = sr["result"].get("summary_path", "")
                res["result"]["summary_status"] = sr["result"].get("status", "success")
            else:
                err = (sr.get("result") or {}).get("error") if isinstance(sr.get("result"), dict) else None
                res["result"]["summary_status"] = "error"
                res["result"]["summary_error"] = err or "summary failed"
        except Exception as e:
            logger.error(f"[scraper_dated] auto-summarize failed for {url}: {e}")
            res["result"]["summary_status"] = "error"
            res["result"]["summary_error"] = f"{type(e).__name__}: {e}"
        return res

    async def _extract_one(
        self,
        session: aiohttp.ClientSession,
        service_url: str,
        headers: dict,
        url: str,
        cutoff_date: str | None,
        endpoint: str = "/scrape_v1",
    ) -> dict:
        import asyncio
        import time

        payload: dict[str, Any] = {"url": url}
        if cutoff_date:
            payload["cutoff_date"] = cutoff_date

        # Retry on transient network/service errors. Without this, a 30-90s
        # blip on the web-tools-service produces hard tool failures, and the
        # lit_review agent falls back to fabricating paper_summaries from
        # training-data knowledge (silent quality collapse).
        backoffs = [0.0, 3.0, 8.0]
        last_err: str | None = None
        for attempt, delay in enumerate(backoffs):
            if delay > 0:
                logger.warning(
                    f"[scraper_dated] retry {attempt}/{len(backoffs)-1} after {delay:.0f}s "
                    f"for {url}: {last_err[:120] if last_err else '?'}"
                )
                await asyncio.sleep(delay)
            try:
                logger.info(f"[scraper_dated] START extracting ({endpoint}): {url}")
                t0 = time.perf_counter()
                async with session.post(
                    f"{service_url}{endpoint}",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=900),
                ) as resp:
                    if resp.status == 401:
                        return {"error": "auth failed (401)", "url": url}
                    if resp.status == 403:
                        data = await resp.json()
                        detail = data.get("detail", {})
                        if not isinstance(detail, dict):
                            return {"error": f"service forbidden (403): {detail}", "url": url}
                        return {
                            "error": f"blocked by cutoff: published {detail.get('published_date', '?')} after {detail.get('cutoff_date', '?')}",
                            "url": url,
                        }
                    if resp.status == 422:
                        data = await resp.json()
                        return {"error": f"invalid URL: {data.get('detail', 'non-paper URL')}", "url": url}
                    if resp.status in (502, 504):
                        body = await resp.text()
                        last_err = f"upstream error ({resp.status}): {body[:200]}"
                        continue  # retriable
                    if resp.status != 200:
                        body = await resp.text()
                        last_err = f"service error {resp.status}: {body[:200]}"
                        if 500 <= resp.status < 600:
                            continue  # retriable
                        return {"error": last_err, "url": url}

                    zip_bytes = await resp.read()

                elapsed = time.perf_counter() - t0
                logger.info(f"[scraper_dated] {url} fetched in {elapsed:.1f}s ({len(zip_bytes)} bytes)")

                # Offload synchronous zip extraction to thread to avoid blocking event loop
                return await asyncio.to_thread(self._store_zip, zip_bytes, url)

            except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
                    asyncio.TimeoutError, ConnectionError) as e:
                last_err = f"transient: {type(e).__name__}: {e}"
                continue
            except Exception as e:
                logger.error(f"[scraper_dated] Error extracting {url}: {e}")
                return {"error": str(e), "url": url}

        # Exhausted retries
        logger.error(f"[scraper_dated] exhausted {len(backoffs)} attempts for {url}: {last_err}")
        return {"error": last_err or "transient error after retries", "url": url}

    @staticmethod
    def _error_is_retriable(err: str) -> bool:
        """True iff the /scrape_v1 error string indicates a transient issue
        worth retrying on /extract. Non-retriable: cutoff/422/401."""
        if not err:
            return False
        e = err.lower()
        # Known terminal errors — /extract can't fix these.
        if "blocked by cutoff" in e or "invalid url" in e or "auth failed" in e:
            return False
        # Retriable signals: service 5xx, upstream errors, raw exceptions,
        # and connection-level failures (Errno 111 etc.) that aiohttp surfaces
        # as bare exception strings.
        return (
            "service error 5" in e
            or "upstream error" in e
            or "timeout" in e
            or "internal error" in e
            or "invalid zip" in e
            or "transient:" in e
            or "cannot connect" in e
            or "connection refused" in e
            or "connection reset" in e
            or "errno 111" in e
            or "serverdisconnected" in e
        )

    @staticmethod
    def _is_grobid_degraded(result: dict) -> bool:
        """Detect the fallback zip /scrape_v1 emits when grobid parsing fails.

        Signature: title is "Untitled Document" and sections collapse to a
        single Content.md whose body is the conversion-error stub.
        """
        title = (result.get("title") or "").lower()
        if "untitled" in title:
            return True
        # Cheap structural check: only 1 section file and it's named Content.md.
        if result.get("section_count") == 1:
            output_dir = result.get("output_dir")
            if output_dir:
                content_md = Path(output_dir) / "sections" / "Content.md"
                if content_md.is_file():
                    head = content_md.read_text(encoding="utf-8", errors="ignore")[:400]
                    if "Fatal error" in head or "exited abruptly" in head:
                        return True
        return False

    def _store_zip(self, zip_bytes: bytes, url: str) -> dict:
        """Unzip the extract service response into the agent workspace."""
        # Extract to a temporary slug first; we'll rename after reading paper_info.json
        temp_slug = self._storage_slug_from_url(url)
        output_dir = Path(self.tool_state.get_working_dir()) / "scraped_papers" / temp_slug
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                if output_dir.exists():
                    shutil.rmtree(output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)

                self._extract_zip_safely(zf, output_dir)

                paper_info: dict[str, Any] = {}
                paper_info_path = output_dir / "paper_info.json"
                if paper_info_path.is_file():
                    try:
                        paper_info = json.loads(paper_info_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        paper_info = {}

                # Rename folder to include title if available
                final_slug = self._storage_slug_with_title(url, paper_info.get("title", ""))
                if final_slug != temp_slug:
                    final_dir = output_dir.parent / final_slug
                    if final_dir.exists():
                        shutil.rmtree(final_dir)
                    output_dir.rename(final_dir)
                    output_dir = final_dir

                sections_dir = output_dir / "sections"
                section_count = len(list(sections_dir.glob("*.md"))) if sections_dir.is_dir() else 0
                images_dir = output_dir / "images"

                return {
                    "result": {
                        "title": paper_info.get("title", url),
                        "url": url,
                        "output_dir": str(output_dir),
                        "section_count": section_count,
                        "has_images": images_dir.is_dir() and any(images_dir.iterdir()),
                    }
                }
        except zipfile.BadZipFile:
            return {"error": "invalid zip response from extract service", "url": url}

    def _extract_zip_safely(self, zf: zipfile.ZipFile, output_dir: Path) -> None:
        """Extract zip members without allowing paths to escape output_dir."""
        output_root = output_dir.resolve()
        for member in zf.infolist():
            target = (output_dir / member.filename).resolve()
            if output_root not in (target, *target.parents):
                raise ValueError(f"unsafe path in zip: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    def _storage_slug_from_url(self, url: str) -> str:
        """Initial slug from URL only (used before paper_info.json is available)."""
        parsed = urlparse(url)
        arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url, flags=re.IGNORECASE)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1).removesuffix(".pdf")
            return f"arxiv_{arxiv_id}"

        base = f"{parsed.netloc}{parsed.path}" if parsed.netloc else url
        slug = re.sub(r"[^\w\-.]", "_", base).strip("_")[:80] or "paper"
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        return f"{slug}_{url_hash}"

    def _storage_slug_with_title(self, url: str, title: str) -> str:
        """Final slug: title-only for readability. Falls back to URL-based slug."""
        # Clean the title: strip leading "[arxiv_id] " prefix if present
        clean_title = re.sub(r"^\[[^\]]+\]\s*", "", (title or "").strip())
        title_slug = self._sanitize_slug(clean_title)[:80] if clean_title else ""

        if title_slug:
            return title_slug

        # No usable title — fall back to URL-based slug
        arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url, flags=re.IGNORECASE)
        if arxiv_match:
            arxiv_id = arxiv_match.group(1).removesuffix(".pdf")
            return f"arxiv_{arxiv_id}"

        parsed = urlparse(url)
        base = f"{parsed.netloc}{parsed.path}" if parsed.netloc else url
        url_slug = self._sanitize_slug(base)[:40] or "paper"
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        return f"{url_slug}_{url_hash}"

    @staticmethod
    def _sanitize_slug(value: str) -> str:
        """Sanitize to filesystem-safe characters. Preserves case, uses spaces between words."""
        slug = re.sub(r"[^\w\s\-.]", "", value).strip()
        slug = re.sub(r"\s+", " ", slug)
        return slug

    def _format_results(self, results: list[dict]) -> str:
        """Format extraction results as text for the agent."""
        parts = []
        for r in results:
            if "error" in r:
                parts.append(f"--- FAILED: {r.get('url', '?')} ---\n{r['error']}\n")
                continue

            info = r["result"]
            title = info["title"]
            url = info["url"]
            output_dir = info["output_dir"]
            section_count = info["section_count"]
            images_note = "Yes" if info.get("has_images") else "No"

            summary_line: str
            sstatus = info.get("summary_status")
            if sstatus in ("success", "already_exists") and info.get("summary_path"):
                summary_line = f"Summary: {info['summary_path']}  ({sstatus})\n"
            elif sstatus == "error":
                summary_line = f"Summary: ERROR — {info.get('summary_error', 'unknown')}\n"
            else:
                summary_line = ""

            parts.append(
                f"--- EXTRACTED + SUMMARIZED: {title} ---\n"
                f"URL: {url}\n"
                f"Stored at: {output_dir}\n"
                f"Files: paper_info.json, sections/ ({section_count} markdown files), images/ present: {images_note}\n"
                f"{summary_line}"
                "Use View on the summary path to read the structured Layer-2 summary; "
                "use View / Bash on `Stored at` for raw sections when needed.\n"
            )

        return "\n\n".join(parts)
