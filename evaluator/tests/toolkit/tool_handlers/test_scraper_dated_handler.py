import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest

from toolkit.tool_handlers.tool_state import ToolState
from toolkit.tool_handlers.scraper_dated_handler import ScraperDatedToolHandler


@pytest.fixture(autouse=True)
def _no_auto_summarize(monkeypatch):
    """Extraction auto-summarizes each paper with a real LLM; stub it out."""

    class _StubSummarizer:
        async def execute_async(self, paper_folder):
            return {"tool_success": True, "result": {"summary_path": "", "status": "skipped"}}

    monkeypatch.setattr(ScraperDatedToolHandler, "_get_summarizer", lambda self: _StubSummarizer())


def _make_handler() -> ScraperDatedToolHandler:
    state = MagicMock(spec=ToolState)
    state.cutoff_date = "2025-01-01"
    state.task_id = "test_task"
    return ScraperDatedToolHandler(state)


@pytest.mark.asyncio
async def test_extract_uses_web_tools_service_credentials(monkeypatch):
    handler = _make_handler()
    captured = {}

    class FakeResponse:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, *, json, headers, timeout):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("WEB_TOOLS_SERVICE_URL", "http://web-tools.example")
    monkeypatch.setenv("WEB_TOOLS_SERVICE_TOKEN", "shared-token")
    monkeypatch.setattr(
        "toolkit.tool_handlers.scraper_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    await handler.execute_async(["https://arxiv.org/abs/2303.01212"])

    assert captured["url"] == "http://web-tools.example/scrape_v1"
    assert captured["headers"]["Authorization"] == "Bearer shared-token"
    assert captured["payload"]["cutoff_date"] == "2025-01-01"


@pytest.mark.asyncio
async def test_extract_reads_web_tools_auth_from_environment_at_execution(monkeypatch):
    handler = _make_handler()
    captured = {}

    class FakeResponse:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, *, json, headers, timeout):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("WEB_TOOLS_SERVICE_URL", "http://web-tools.example")
    monkeypatch.setenv("WEB_TOOLS_SERVICE_TOKEN", "shared-token")
    monkeypatch.setattr(
        "toolkit.tool_handlers.scraper_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    await handler.execute_async(["https://arxiv.org/abs/2303.01212"])

    assert captured["url"] == "http://web-tools.example/scrape_v1"
    assert captured["headers"]["Authorization"] == "Bearer shared-token"


@pytest.mark.asyncio
async def test_extract_handles_string_detail_403(monkeypatch):
    handler = _make_handler()

    class FakeResponse:
        status = 403

        async def json(self):
            return {"detail": "Not authenticated"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, *, json, headers, timeout):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "toolkit.tool_handlers.scraper_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    result = await handler.execute_async(["https://arxiv.org/abs/2303.01212"])

    assert result["tool_success"] is False
    assert "service forbidden (403): Not authenticated" in result["result"]


@pytest.mark.asyncio
async def test_extract_accepts_more_than_five_sources(monkeypatch):
    handler = _make_handler()
    captured_urls = []

    class FakeResponse:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, *, json, headers, timeout):
            captured_urls.append(json["url"])
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "toolkit.tool_handlers.scraper_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    sources = [f"https://arxiv.org/abs/2303.0121{i}" for i in range(6)]

    result = await handler.execute_async(sources)

    assert len(captured_urls) == 6
    assert result["tool_success"] is False
    assert "at most 5 sources" not in result["result"]


@pytest.mark.asyncio
async def test_extract_unzips_paper_into_workspace_and_returns_path(monkeypatch, tmp_path):
    state = MagicMock(spec=ToolState)
    state.cutoff_date = "2025-01-01"
    state.task_id = "test_task"
    state.get_working_dir.return_value = str(tmp_path)
    handler = ScraperDatedToolHandler(state)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("paper_info.json", json.dumps({"title": "Stored Paper", "toc": ["Abstract.md"]}))
        zf.writestr("sections/Abstract.md", "FULL PAPER CONTENT SHOULD NOT BE IN TOOL RESULT")

    class FakeResponse:
        status = 200

        async def read(self):
            return zip_buffer.getvalue()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, *, json, headers, timeout):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "toolkit.tool_handlers.scraper_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    result = await handler.execute_async(["https://arxiv.org/pdf/2303.01212"])

    # Papers are stored under a slug derived from their title in paper_info.json.
    paper_dir = tmp_path / "scraped_papers" / handler._storage_slug_with_title(
        "https://arxiv.org/pdf/2303.01212", "Stored Paper"
    )
    assert (paper_dir / "paper_info.json").is_file()
    assert (paper_dir / "sections" / "Abstract.md").read_text() == "FULL PAPER CONTENT SHOULD NOT BE IN TOOL RESULT"
    assert result["tool_success"] is True
    assert str(paper_dir) in result["result"]
    assert "FULL PAPER CONTENT SHOULD NOT BE IN TOOL RESULT" not in result["result"]
