from unittest.mock import MagicMock

import pytest

from toolkit.tool_handlers.tool_state import ToolState
from toolkit.tool_handlers.web_search_dated_handler import WebSearchDatedToolHandler


def _make_handler() -> WebSearchDatedToolHandler:
    state = MagicMock(spec=ToolState)
    state.cutoff_date = "2025-01-01"
    state.task_id = "test_task"
    return WebSearchDatedToolHandler(state)


@pytest.mark.asyncio
async def test_omits_domain_filters_from_service_payload(monkeypatch):
    handler = _make_handler()
    captured_payload = {}

    class FakeResponse:
        status = 200

        async def json(self):
            return {"success": True, "agent_result": "ok"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
        def post(self, _url, *, json, headers, timeout):
            captured_payload.update(json)
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "toolkit.tool_handlers.web_search_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    result = await handler.execute_async(
        query="test query",
        allowed_domains=["arxiv.org"],
        blocked_domains=["example.com"],
    )

    assert result["result"] == "ok"
    assert captured_payload["query"] == "test query"
    assert captured_payload["cutoff_date"] == "2025-01-01"
    assert "allowed_domains" not in captured_payload
    assert "blocked_domains" not in captured_payload


@pytest.mark.asyncio
async def test_reads_web_tools_auth_from_environment_at_execution(monkeypatch):
    handler = _make_handler()
    captured = {}

    class FakeResponse:
        status = 200

        async def json(self):
            return {"success": True, "agent_result": "ok"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeSession:
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
        "toolkit.tool_handlers.web_search_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    result = await handler.execute_async(query="test query")

    assert result["result"] == "ok"
    assert captured["url"] == "http://web-tools.example/search"
    assert captured["headers"]["Authorization"] == "Bearer shared-token"
