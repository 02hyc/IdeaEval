from unittest.mock import MagicMock

import pytest

from toolkit.tool_handlers.tool_state import ToolState
from toolkit.tool_handlers.web_fetch_dated_handler import WebFetchDatedToolHandler


def _make_handler() -> WebFetchDatedToolHandler:
    state = MagicMock(spec=ToolState)
    state.cutoff_date = "2025-01-01"
    state.task_id = "test_task"
    return WebFetchDatedToolHandler(state)


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
        "toolkit.tool_handlers.web_fetch_dated_handler.aiohttp.ClientSession",
        FakeSession,
    )

    result = await handler.execute_async(
        url="https://arxiv.org/abs/2303.01212",
        prompt="summarize",
    )

    assert result["result"] == "ok"
    assert captured["url"] == "http://web-tools.example/fetch"
    assert captured["headers"]["Authorization"] == "Bearer shared-token"
    assert captured["payload"]["cutoff_date"] == "2025-01-01"
