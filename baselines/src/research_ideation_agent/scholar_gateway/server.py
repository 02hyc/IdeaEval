import asyncio
from collections.abc import Awaitable, Callable

from fastmcp import FastMCP

from .client import WebToolsClient

_MAX_CONCURRENCY = 64


def create_mcp_server(
    base_url: str,
    api_key: str,
    cutoff_date: str | None,
    restrict_domains: bool,
    strict_filtering: bool,
    mode: str,
    format: str,
    references_dir: str,
    default_concurrency: int = 20,
    extract_concurrency: int = 20,
) -> FastMCP:
    mcp = FastMCP(
        name="scholar-gateway",
        instructions="Use web_search for finding information online. Use web_fetch to read a specific web page. Use extract_paper to extract academic papers to local files; each tool accepts either a single item or a batch list for parallel IO. Extraction automatically attempts a best-effort paper summary and returns both raw and summary paths. Use summarize_paper to rerun or repair summaries. Read raw sections for exact details before relying on a claim.",
    )

    client = WebToolsClient(base_url=base_url, api_key=api_key)

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def web_search(
        query: str | None = None,
        pages: int = 1,
        queries: list[str] | None = None,
    ) -> str:
        """Search the web. Pass query for one search or queries for parallel batch search. Returns titles, URLs, snippets, and dates."""
        items = _coerce_batch(single=query, batch=queries)
        if isinstance(items, str):
            return items

        async def run_one(item: str) -> str:
            try:
                result = await client.search(
                    query=item,
                    pages=pages,
                    cutoff_date=cutoff_date,
                    strict_filtering=strict_filtering,
                    restrict_domains=restrict_domains,
                )
            except Exception as e:
                return f"Error: {e}"

            if not result.get("success"):
                return result.get("agent_result", "Search failed.")
            return result["agent_result"]

        results = await _run_batch(items, run_one, default_concurrency)
        if len(results) == 1:
            return results[0][1]
        return _format_batch_results("web_search", "Query", results)

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def web_fetch(
        url: str | None = None,
        urls: list[str] | None = None,
    ) -> str:
        """Fetch web pages. Pass url for one page or urls for parallel batch fetch. Returns page text."""
        items = _coerce_batch(single=url, batch=urls)
        if isinstance(items, str):
            return items

        async def run_one(item: str) -> str:
            try:
                result = await client.fetch(
                    url=item,
                    cutoff_date=cutoff_date,
                    mode=mode,
                    format=format,
                    restrict_domains=restrict_domains,
                )
            except Exception as e:
                return f"Error: {e}"

            if not result.get("success"):
                return result.get("agent_result", "Fetch failed.")
            return result["agent_result"]

        results = await _run_batch(items, run_one, default_concurrency)
        if len(results) == 1:
            return results[0][1]
        return _format_batch_results("web_fetch", "URL", results)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def extract_paper(
        url: str | None = None,
        urls: list[str] | None = None,
        auto_summarize: bool = True,
    ) -> str:
        """Extract academic papers. Pass url for one paper or urls for parallel batch extraction. Returns raw paths, section lists, and summary path/status. Use raw sections for exact details."""
        items = _coerce_batch(single=url, batch=urls)
        if isinstance(items, str):
            return items

        async def run_one(item: str) -> str:
            try:
                result = await client.extract(
                    url=item,
                    cutoff_date=cutoff_date,
                    references_dir=references_dir,
                    auto_summarize=auto_summarize,
                )
            except Exception as e:
                return f"Error: {e}"
            return _format_extract_result(result)

        results = await _run_batch(items, run_one, extract_concurrency)
        if len(results) == 1:
            return results[0][1]
        return _format_batch_results("extract_paper", "URL", results)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def summarize_paper(paper_dir: str, output_path: str) -> str:
        """Summarize an extracted paper folder into one structured markdown file. Use after extract_paper; inspect raw sections for exact details."""
        try:
            return await client.summarize_paper(paper_dir=paper_dir, output_path=output_path)
        except Exception as e:
            return f"Error: {e}"

    return mcp


def _coerce_batch(single: str | None, batch: list[str] | None) -> list[str] | str:
    items: list[str] = []
    if single and single.strip():
        items.append(single.strip())
    if batch:
        items.extend(item.strip() for item in batch if item and item.strip())
    if not items:
        return "Error: provide either a single item or a non-empty batch list."
    return items


async def _run_batch(
    items: list[str],
    run_one: Callable[[str], Awaitable[str]],
    concurrency: int,
) -> list[tuple[str, str]]:
    semaphore = asyncio.Semaphore(_clamp_concurrency(concurrency))

    async def guarded(item: str) -> tuple[str, str]:
        async with semaphore:
            return item, await run_one(item)

    return await asyncio.gather(*(guarded(item) for item in items))


def _clamp_concurrency(value: int) -> int:
    # Concurrency is set by the deployment config, not the agent; clamp only as a
    # sanity bound (floor of 1, generous ceiling) against a misconfigured value.
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(parsed, _MAX_CONCURRENCY))


def _format_batch_results(tool_name: str, item_label: str, results: list[tuple[str, str]]) -> str:
    failed = sum(1 for _, text in results if text.startswith("Error:"))
    lines = [
        f"Batch {tool_name} completed: {len(results) - failed} succeeded, {failed} failed, {len(results)} total.",
        "",
    ]
    for index, (item, text) in enumerate(results, start=1):
        lines.extend([f"## {item_label} {index}: {item}", "", text.strip(), ""])
    return "\n".join(lines).rstrip()


def _format_extract_result(result: dict) -> str:
    summary = result["summary"]
    lines = [
        f"Paper extracted: {result['title']}",
        f"Directory: {result['path']}",
        f"Raw paper directory: {result['path']}",
        f"Sections directory: {result['sections_dir']}",
        f"Summary status: {summary['status']}",
        f"Summary file: {summary['path']}",
        f"Summary message: {summary['message']}",
        "",
        "Sections:",
    ]
    for filename in result["toc"]:
        lines.append(f"  - {result['path']}/sections/{filename}")
    return "\n".join(lines)
