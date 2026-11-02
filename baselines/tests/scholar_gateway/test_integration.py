# tests/test_integration.py
"""Integration tests — require running web-tools-service and WTS_API_KEY env var."""
import os

import pytest

from research_ideation_agent.scholar_gateway.client import WebToolsClient

BASE_URL = os.environ.get("WTS_BASE_URL", "http://localhost:8900")
API_KEY = os.environ.get("WTS_API_KEY", "")

pytestmark = pytest.mark.skipif(not API_KEY, reason="WTS_API_KEY not set")


@pytest.fixture
async def client():
    c = WebToolsClient(base_url=BASE_URL, api_key=API_KEY)
    yield c
    await c.close()


class TestSearchIntegration:
    @pytest.mark.anyio
    async def test_basic_search(self, client):
        result = await client.search(
            query="transformer attention mechanism",
            pages=1,
            cutoff_date="2025-01-01",
            strict_filtering=True,
            restrict_domains=True,
        )
        assert result["success"] is True
        assert len(result["results"]) > 0


class TestFetchIntegration:
    @pytest.mark.anyio
    async def test_basic_fetch(self, client):
        result = await client.fetch(
            url="https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)",
            cutoff_date=None,
            mode="rules",
            format="markdown",
            restrict_domains=True,
        )
        assert result["success"] is True
        assert len(result["agent_result"]) > 100


class TestExtractIntegration:
    @pytest.mark.anyio
    async def test_arxiv_extract(self, client, tmp_path):
        refs_dir = str(tmp_path / "references")
        result = await client.extract(
            url="https://arxiv.org/abs/1706.03762",
            cutoff_date=None,
            references_dir=refs_dir,
        )
        assert "Attention" in result["title"]
        assert os.path.isdir(result["path"])
        assert len(result["toc"]) > 0
        assert os.path.isfile(os.path.join(result["path"], "sections", result["toc"][0]))
