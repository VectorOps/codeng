from __future__ import annotations

import pytest

from vocode import state as vocode_state
from vocode.settings import Settings
from vocode.settings import ToolSpec
from vocode.settings import ToolSettings
from vocode.tools import ToolFactory, base as tools_base
from vocode.tools.web_fetch_tool import WebFetchTool
from vocode.tools.web_fetch_tool import _build_tool_policy
from vocode.webclient import models as webclient_models
from vocode.webclient import service as webclient_service
from vocode.webclient.errors import WebClientAccessError
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_web_fetch_tool_returns_normalized_text_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch_url(
        self,
        url: str,
        *,
        headers=None,
        timeout_s=None,
    ) -> webclient_models.WebClientResult:
        return webclient_models.WebClientResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/plain",
            content_kind=webclient_models.WebContentKind.text,
            text="hello world",
            metadata={"source": "fake"},
        )

    monkeypatch.setattr(
        webclient_service.WebClientService, "fetch_url", _fake_fetch_url
    )

    project = StubProject()
    tool = WebFetchTool(project)
    execution = vocode_state.WorkflowExecution(workflow_name="test")
    tool_req = tools_base.ToolReq(
        execution=execution,
        spec=ToolSpec(name="web_fetch", config={"timeout_s": 15}),
    )

    resp = await tool.run(tool_req, {"url": "https://example.com"})
    assert resp is not None
    assert resp.is_error is False
    assert resp.text == "hello world"
    assert resp.data is not None
    assert resp.data["url"] == "https://example.com"
    assert resp.data["status_code"] == 200
    assert resp.data["content_kind"] == "text"
    assert resp.data["source"] == "fake"


@pytest.mark.asyncio
async def test_web_fetch_tool_surfaces_webclient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch_url(
        self,
        url: str,
        *,
        headers=None,
        timeout_s=None,
    ):
        raise WebClientAccessError("blocked destination")

    monkeypatch.setattr(
        webclient_service.WebClientService, "fetch_url", _fake_fetch_url
    )

    project = StubProject()
    tool = WebFetchTool(project)
    execution = vocode_state.WorkflowExecution(workflow_name="test")
    tool_req = tools_base.ToolReq(
        execution=execution,
        spec=ToolSpec(name="web_fetch"),
    )

    resp = await tool.run(tool_req, {"url": "https://localhost"})
    assert resp is not None
    assert resp.is_error is True
    assert resp.text == "blocked destination"


@pytest.mark.asyncio
async def test_web_fetch_tool_uses_registered_tool_class() -> None:
    tool_cls = ToolFactory.get("web_fetch")
    assert tool_cls is WebFetchTool


def test_web_fetch_tool_policy_defaults_to_empty() -> None:
    project = StubProject()
    policy = _build_tool_policy(project)
    assert policy.default_url_blocklist == []


def test_web_fetch_tool_policy_uses_project_settings() -> None:
    project = StubProject(
        settings=Settings(
            tool_settings=ToolSettings(
                web_client_policy=webclient_models.HarnessWebClientPolicy(
                    default_url_blocklist=["localhost", "127.0.0.1"]
                )
            )
        )
    )
    policy = _build_tool_policy(project)
    assert policy.default_url_blocklist == ["localhost", "127.0.0.1"]
