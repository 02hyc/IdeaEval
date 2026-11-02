# tests/test_server.py
import io
import json
import zipfile

import pytest

from research_ideation_agent.scholar_gateway.server import _clamp_concurrency, create_mcp_server


def test_clamp_concurrency_sane_bounds() -> None:
    assert _clamp_concurrency(20) == 20  # deployment value passes through
    assert _clamp_concurrency(0) == 1  # floor of 1
    assert _clamp_concurrency(1000) == 64  # sanity cap
    assert _clamp_concurrency("bad") == 1  # bad input falls back to 1


@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return create_mcp_server(
        base_url="http://test:8900",
        api_key="wts-test",
        cutoff_date="2025-01-01",
        restrict_domains=True,
        strict_filtering=True,
        mode="rules",
        format="markdown",
        references_dir=str(tmp_path / "references"),
    )


def _paper_zip(title: str, section_name: str = "sec1.md", section_text: str = "Content.") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("paper_info.json", json.dumps({"title": title, "toc": [section_name]}))
        zf.writestr(f"sections/{section_name}", f"# {section_name}\n\n{section_text}")
    buf.seek(0)
    return buf.getvalue()


class TestWebSearchTool:
    @pytest.mark.anyio
    async def test_calls_client_with_harness_params(self, mcp_server, httpx_mock):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/search",
            json={
                "success": True,
                "agent_result": "Found 1 result",
                "results": [{"title": "A", "url": "http://a.com", "snippet": "s", "date": "2024-06-01"}],
                "meta": {"mode": "rules", "raw_count": 5, "filtered_count": 1, "latency_ms": 100},
            },
        )
        async with Client(mcp_server) as client:
            result = await client.call_tool("web_search", {"query": "machine learning"})
        assert "Found 1 result" in result.content[0].text
        req_body = json.loads(httpx_mock.get_request().content)
        assert req_body["query"] == "machine learning"
        assert req_body["cutoff_date"] == "2025-01-01"
        assert req_body["strict_filtering"] is True

    @pytest.mark.anyio
    async def test_batch_searches_queries(self, mcp_server, httpx_mock):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/search",
            json={"success": True, "agent_result": "Found query one", "results": []},
        )
        httpx_mock.add_response(
            url="http://test:8900/search",
            json={"success": True, "agent_result": "Found query two", "results": []},
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "web_search",
                {"queries": ["query one", "query two"], "pages": 2},
            )

        text = result.content[0].text
        assert "Batch web_search completed: 2 succeeded, 0 failed, 2 total." in text
        assert "## Query 1: query one" in text
        assert "Found query one" in text
        assert "## Query 2: query two" in text
        assert "Found query two" in text
        payloads = [json.loads(request.content) for request in httpx_mock.get_requests()]
        assert sorted(payload["query"] for payload in payloads) == ["query one", "query two"]
        assert all(payload["pages"] == 2 for payload in payloads)
        assert all(payload["cutoff_date"] == "2025-01-01" for payload in payloads)


class TestWebFetchTool:
    @pytest.mark.anyio
    async def test_returns_page_content(self, mcp_server, httpx_mock):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/fetch",
            json={
                "success": True,
                "agent_result": "# Hello World\n\nContent here.",
                "results": None,
                "meta": {"mode": "rules", "latency_ms": 50},
            },
        )
        async with Client(mcp_server) as client:
            result = await client.call_tool("web_fetch", {"url": "http://example.com"})
        assert "Hello World" in result.content[0].text

    @pytest.mark.anyio
    async def test_batch_fetches_urls(self, mcp_server, httpx_mock):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/fetch",
            json={"success": True, "agent_result": "# Page One", "results": None},
        )
        httpx_mock.add_response(
            url="http://test:8900/fetch",
            json={"success": True, "agent_result": "# Page Two", "results": None},
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "web_fetch",
                {"urls": ["http://example.com/one", "http://example.com/two"]},
            )

        text = result.content[0].text
        assert "Batch web_fetch completed: 2 succeeded, 0 failed, 2 total." in text
        assert "## URL 1: http://example.com/one" in text
        assert "# Page One" in text
        assert "## URL 2: http://example.com/two" in text
        assert "# Page Two" in text
        payloads = [json.loads(request.content) for request in httpx_mock.get_requests()]
        assert sorted(payload["url"] for payload in payloads) == [
            "http://example.com/one",
            "http://example.com/two",
        ]
        assert all(payload["cutoff_date"] == "2025-01-01" for payload in payloads)


class TestExtractPaperTool:
    @pytest.mark.anyio
    async def test_returns_path_and_toc(self, mcp_server, httpx_mock):
        from fastmcp import Client

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper_info.json", json.dumps({"title": "My Paper", "toc": ["sec1.md"]}))
            zf.writestr("sections/sec1.md", "# Section 1\n\nContent.")
        buf.seek(0)

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=buf.getvalue(),
            headers={"content-type": "application/zip"},
        )
        async with Client(mcp_server) as client:
            result = await client.call_tool("extract_paper", {"url": "http://arxiv.org/abs/2301.00001"})
        text = result.content[0].text
        assert "My Paper" in text
        assert "Directory:" in text
        assert "Summary status: skipped" in text
        assert "Summary file:" in text
        assert "sec1.md" in text

    @pytest.mark.anyio
    async def test_batch_extracts_papers(self, mcp_server, httpx_mock, tmp_path):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=_paper_zip("Paper One"),
            headers={"content-type": "application/zip"},
        )
        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=_paper_zip("Paper Two"),
            headers={"content-type": "application/zip"},
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "extract_paper",
                {"urls": ["http://arxiv.org/abs/2301.00001", "http://arxiv.org/abs/2301.00002"]},
            )

        text = result.content[0].text
        assert "Batch extract_paper completed: 2 succeeded, 0 failed, 2 total." in text
        assert "## URL 1: http://arxiv.org/abs/2301.00001" in text
        assert "Paper extracted: Paper One" in text
        assert "Summary status: skipped" in text
        assert "## URL 2: http://arxiv.org/abs/2301.00002" in text
        assert "Paper extracted: Paper Two" in text
        assert (tmp_path / "references" / "paper-one" / "sections" / "sec1.md").is_file()
        assert (tmp_path / "references" / "paper-two" / "sections" / "sec1.md").is_file()
        payloads = [json.loads(request.content) for request in httpx_mock.get_requests()]
        assert sorted(payload["url"] for payload in payloads) == [
            "http://arxiv.org/abs/2301.00001",
            "http://arxiv.org/abs/2301.00002",
        ]
        assert all(payload["cutoff_date"] == "2025-01-01" for payload in payloads)

    @pytest.mark.anyio
    async def test_policy_block_returns_neutral_agent_visible_message(self, mcp_server, httpx_mock):
        from fastmcp import Client

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            status_code=403,
            json={
                "detail": {
                    "error": "blocked_by_cutoff",
                    "published_date": "2025-03-01",
                    "cutoff_date": "2025-01-01",
                }
            },
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool("extract_paper", {"url": "http://arxiv.org/abs/2301.00001"})

        text = result.content[0].text
        assert "Source unavailable under the configured evidence policy." in text
        assert "2025-01-01" not in text
        assert "2025-03-01" not in text

    @pytest.mark.anyio
    async def test_auto_summary_path_is_returned_when_configured(self, mcp_server, httpx_mock, monkeypatch, tmp_path):
        from fastmcp import Client

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper_info.json", json.dumps({"title": "My Paper", "toc": ["sec1.md"]}))
            zf.writestr("sections/sec1.md", "# Section 1\n\nContent.")
        buf.seek(0)

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=buf.getvalue(),
            headers={"content-type": "application/zip"},
        )
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://deepseek.test/v1")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
        httpx_mock.add_response(
            url="http://deepseek.test/v1/chat/completions",
            json={"choices": [{"message": {"content": "# My Paper\n\n## TL;DR\nSummary."}}]},
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool("extract_paper", {"url": "http://arxiv.org/abs/2301.00001"})

        text = result.content[0].text
        summary_path = tmp_path / "papers" / "paper_summaries" / "my-paper.md"
        assert "Summary status: created" in text
        assert f"Summary file: {summary_path}" in text
        assert summary_path.read_text(encoding="utf-8") == "# My Paper\n\n## TL;DR\nSummary."


class TestSummarizePaperTool:
    @pytest.mark.anyio
    async def test_summarizes_extracted_paper_folder(self, mcp_server, httpx_mock, monkeypatch, tmp_path):
        from fastmcp import Client

        paper_dir = tmp_path / "references" / "my-paper"
        sections_dir = paper_dir / "sections"
        sections_dir.mkdir(parents=True)
        (paper_dir / "paper_info.json").write_text(
            json.dumps({"title": "My Paper", "toc": ["sec1.md"]}),
            encoding="utf-8",
        )
        (sections_dir / "sec1.md").write_text("# Section 1\n\nContent.", encoding="utf-8")

        monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://deepseek.test/v1")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")
        httpx_mock.add_response(
            url="http://deepseek.test/v1/chat/completions",
            json={"choices": [{"message": {"content": "# My Paper\n\n## TL;DR\nSummary."}}]},
        )

        output_path = tmp_path / "papers" / "paper_summaries" / "my-paper.md"
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "summarize_paper",
                {"paper_dir": str(paper_dir), "output_path": str(output_path)},
            )

        text = result.content[0].text
        assert "Summary saved to:" in text
        assert output_path.read_text(encoding="utf-8") == "# My Paper\n\n## TL;DR\nSummary."
