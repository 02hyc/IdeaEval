import argparse
import os

from .server import create_mcp_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scholar Gateway MCP Server")
    parser.add_argument("--cutoff-date", default=None, help="YYYY-MM-DD cutoff date for filtering (harness-injected)")
    parser.add_argument("--restrict-domains", action=argparse.BooleanOptionalAction, default=True, help="Restrict to domain allowlist")
    parser.add_argument("--strict-filtering", action=argparse.BooleanOptionalAction, default=True, help="Strict date filtering for search")
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules", help="Date extraction mode for fetch")
    parser.add_argument("--format", choices=["text", "markdown"], default="markdown", help="Output format for fetch")
    parser.add_argument("--references-dir", default="./references", help="Directory to extract papers into (default: ./references)")
    parser.add_argument("--max-concurrency", type=int, default=20, help="Max concurrent web_search/web_fetch per batch (deployment ceiling)")
    parser.add_argument("--extract-max-concurrency", type=int, default=20, help="Max concurrent extract_paper per batch (deployment ceiling)")
    return parser.parse_args()


def main():
    args = parse_args()

    base_url = os.environ.get("WTS_BASE_URL", "http://localhost:8900")
    api_key = os.environ.get("WTS_API_KEY")
    if not api_key:
        raise SystemExit("Error: WTS_API_KEY environment variable is required")

    references_dir = os.path.abspath(args.references_dir)

    mcp = create_mcp_server(
        base_url=base_url,
        api_key=api_key,
        cutoff_date=args.cutoff_date,
        restrict_domains=args.restrict_domains,
        strict_filtering=args.strict_filtering,
        mode=args.mode,
        format=args.format,
        references_dir=references_dir,
        default_concurrency=args.max_concurrency,
        extract_concurrency=args.extract_max_concurrency,
    )
    mcp.run(transport="stdio")
