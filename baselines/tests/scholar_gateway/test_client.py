# tests/test_client.py
import io
import json
import os
import zipfile

import httpx
import pytest

from research_ideation_agent.scholar_gateway.client import EvidencePolicyUnavailable, WebToolsClient


@pytest.fixture
def client():
    return WebToolsClient(base_url="http://test:8900", api_key="wts-test")


class TestSearch:
    @pytest.mark.anyio
    async def test_search_success(self, client, httpx_mock):
        httpx_mock.add_response(
            url="http://test:8900/search",
            json={
                "success": True,
                "agent_result": "Found 2 results",
                "results": [
                    {"title": "Result 1", "url": "http://a.com", "snippet": "snippet1", "date": "2024-01-01"},
                    {"title": "Result 2", "url": "http://b.com", "snippet": "snippet2", "date": None},
                ],
                "meta": {"mode": "rules", "raw_count": 10, "filtered_count": 2, "latency_ms": 150},
            },
        )
        result = await client.search(query="test query", pages=1, cutoff_date="2025-01-01", strict_filtering=True, restrict_domains=True)
        assert result["success"] is True
        assert len(result["results"]) == 2

    @pytest.mark.anyio
    async def test_search_sends_cutoff_even_without_domain_restriction(self, client, httpx_mock):
        httpx_mock.add_response(
            url="http://test:8900/search",
            json={"success": True, "agent_result": "Found 0 results", "results": []},
        )

        await client.search(
            query="test query",
            pages=2,
            cutoff_date="2025-01-01",
            strict_filtering=False,
            restrict_domains=False,
        )

        request = httpx_mock.get_request()
        assert request is not None
        payload = json.loads(request.content)
        assert payload["cutoff_date"] == "2025-01-01"
        assert payload["restrict_domains"] is False

    @pytest.mark.anyio
    async def test_search_http_error(self, client, httpx_mock):
        httpx_mock.add_response(url="http://test:8900/search", status_code=500, json={"detail": "internal error"})
        with pytest.raises(httpx.HTTPStatusError):
            await client.search(query="test", pages=1, cutoff_date=None, strict_filtering=True, restrict_domains=True)


class TestFetch:
    @pytest.mark.anyio
    async def test_fetch_success(self, client, httpx_mock):
        httpx_mock.add_response(
            url="http://test:8900/fetch",
            json={
                "success": True,
                "agent_result": "Page content here",
                "results": None,
                "meta": {"mode": "rules", "latency_ms": 200},
            },
        )
        result = await client.fetch(url="http://example.com", cutoff_date=None, mode="rules", format="markdown", restrict_domains=True)
        assert result["success"] is True
        assert result["agent_result"] == "Page content here"

    @pytest.mark.anyio
    async def test_fetch_sends_cutoff_even_without_domain_restriction(self, client, httpx_mock):
        httpx_mock.add_response(
            url="http://test:8900/fetch",
            json={"success": True, "agent_result": "Page content here", "results": None},
        )

        await client.fetch(
            url="http://example.com",
            cutoff_date="2025-01-01",
            mode="rules",
            format="markdown",
            restrict_domains=False,
        )

        request = httpx_mock.get_request()
        assert request is not None
        payload = json.loads(request.content)
        assert payload["cutoff_date"] == "2025-01-01"
        assert payload["restrict_domains"] is False


class TestExtract:
    @pytest.mark.anyio
    async def test_extract_unpacks_to_dir(self, client, httpx_mock, monkeypatch, tmp_path):
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper_info.json", '{"title": "Test Paper", "toc": ["Abstract.md", "Introduction.md"]}')
            zf.writestr("sections/Abstract.md", "# Abstract\n\nThis is the abstract.")
            zf.writestr("sections/Introduction.md", "# Introduction\n\nThis is the intro.")
        buf.seek(0)

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=buf.getvalue(),
            headers={"content-type": "application/zip"},
        )

        refs_dir = str(tmp_path / "references")
        result = await client.extract(url="http://arxiv.org/abs/2301.00001", cutoff_date=None, references_dir=refs_dir)

        assert result["title"] == "Test Paper"
        assert result["slug"] == "test-paper"
        assert result["toc"] == ["Abstract.md", "Introduction.md"]
        assert os.path.isdir(result["path"])
        assert os.path.isdir(result["sections_dir"])
        assert os.path.isfile(os.path.join(result["path"], "paper_info.json"))
        assert os.path.isfile(os.path.join(result["path"], "sections", "Abstract.md"))
        assert os.path.isfile(os.path.join(result["path"], "sections", "Introduction.md"))
        assert result["summary"]["status"] == "skipped"
        assert result["summary"]["path"] == str(tmp_path / "papers" / "paper_summaries" / "test-paper.md")
        assert "DEEPSEEK_BASE_URL" in result["summary"]["message"]

        with open(os.path.join(result["path"], "sections", "Abstract.md")) as f:
            assert "This is the abstract." in f.read()

    @pytest.mark.anyio
    async def test_extract_auto_summarizes_when_configured(self, client, httpx_mock, monkeypatch, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper_info.json", '{"title": "Auto Summary Paper", "toc": ["Abstract.md"]}')
            zf.writestr("sections/Abstract.md", "# Abstract\n\nThis paper needs a summary.")
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
            json={"choices": [{"message": {"content": "# Auto Summary Paper\n\n## TL;DR\nSummary."}}]},
        )

        result = await client.extract(
            url="http://arxiv.org/abs/2301.00001",
            cutoff_date=None,
            references_dir=str(tmp_path / "references"),
        )

        summary_path = tmp_path / "papers" / "paper_summaries" / "auto-summary-paper.md"
        assert result["summary"]["status"] == "created"
        assert result["summary"]["path"] == str(summary_path)
        assert summary_path.read_text(encoding="utf-8") == "# Auto Summary Paper\n\n## TL;DR\nSummary."
        requests = httpx_mock.get_requests()
        assert [request.url.path for request in requests] == ["/scrape_v1", "/v1/chat/completions"]

    @pytest.mark.anyio
    async def test_extract_reuses_cached_summary(self, client, httpx_mock, monkeypatch, tmp_path):
        refs_dir = tmp_path / "references"
        summary_path = tmp_path / "papers" / "paper_summaries" / "cached-paper.md"
        summary_path.parent.mkdir(parents=True)
        summary_path.write_text("# Cached Paper\n\nAlready summarized.", encoding="utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper_info.json", '{"title": "Cached Paper", "toc": ["Abstract.md"]}')
            zf.writestr("sections/Abstract.md", "# Abstract\n\nCached.")
        buf.seek(0)

        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            content=buf.getvalue(),
            headers={"content-type": "application/zip"},
        )
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://deepseek.test/v1")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

        result = await client.extract(
            url="http://arxiv.org/abs/2301.00001",
            cutoff_date=None,
            references_dir=str(refs_dir),
        )

        assert result["summary"]["status"] == "cached"
        assert result["summary"]["path"] == str(summary_path)
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.anyio
    async def test_extract_cutoff_blocked(self, client, httpx_mock, tmp_path):
        httpx_mock.add_response(
            url="http://test:8900/scrape_v1",
            status_code=403,
            json={"detail": {"error": "blocked_by_cutoff", "published_date": "2025-03-01", "cutoff_date": "2025-01-01", "precision": "day", "source": "arxiv"}},
        )
        refs_dir = str(tmp_path / "references")
        with pytest.raises(EvidencePolicyUnavailable) as excinfo:
            await client.extract(url="http://arxiv.org/abs/2301.00001", cutoff_date="2025-01-01", references_dir=refs_dir)
        assert str(excinfo.value) == "Source unavailable under the configured evidence policy."
        assert "2025-01-01" not in str(excinfo.value)


class TestSummarizePaper:
    @pytest.mark.anyio
    async def test_summarize_paper_writes_markdown(self, client, httpx_mock, monkeypatch, tmp_path):
        paper_dir = tmp_path / "references" / "test-paper"
        sections_dir = paper_dir / "sections"
        sections_dir.mkdir(parents=True)
        (paper_dir / "paper_info.json").write_text(
            '{"title": "Test Paper", "toc": ["Introduction.md", "Results.md"]}',
            encoding="utf-8",
        )
        (sections_dir / "Introduction.md").write_text("# Introduction\n\nThe paper studies retrieval.", encoding="utf-8")
        (sections_dir / "Results.md").write_text("# Results\n\nThe method improves accuracy.", encoding="utf-8")

        monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://deepseek.test/v1")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")
        httpx_mock.add_response(
            url="http://deepseek.test/v1/chat/completions",
            json={"choices": [{"message": {"content": "# Test Paper\n\n## TL;DR\nA summary."}}]},
        )

        output_path = tmp_path / "papers" / "paper_summaries" / "test-paper.md"
        result = await client.summarize_paper(paper_dir=str(paper_dir), output_path=str(output_path))

        assert result.startswith("Summary saved to:")
        assert output_path.read_text(encoding="utf-8") == "# Test Paper\n\n## TL;DR\nA summary."
        request = httpx_mock.get_request()
        assert request is not None
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-test-model"
        assert "Paper-Reader" in payload["messages"][0]["content"]
        assert "aim for 2000-5000 tokens" in payload["messages"][0]["content"]
        assert "Reproduce result tables as markdown tables" in payload["messages"][0]["content"]
        assert "The paper studies retrieval." in payload["messages"][1]["content"]
        assert "The method improves accuracy." in payload["messages"][1]["content"]

    @pytest.mark.anyio
    async def test_summarize_paper_requires_deepseek_base_url(self, client, monkeypatch, tmp_path):
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

        result = await client.summarize_paper(
            paper_dir=str(tmp_path / "missing-paper"),
            output_path=str(tmp_path / "summary.md"),
        )

        assert result == "Error: DEEPSEEK_BASE_URL is not configured."
