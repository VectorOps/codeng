import pytest

from vocode import settings as vocode_settings
from vocode.state import WorkflowExecution
from vocode.tools.base import ToolReq
from vocode.tools.base import ToolTextResponse
from vocode.tools.mcp_get_prompt_tool import MCPGetPromptTool
from vocode.tools.mcp_read_resource_tool import MCPReadResourceTool


class _MCPServiceStub:
    def list_prompt_sources(self) -> list[str]:
        return ["local"]

    def list_resource_sources(self) -> list[str]:
        return ["local"]

    async def list_prompts(self, source_name: str):
        assert source_name == "local"
        return [
            type(
                "_PromptDescriptor",
                (),
                {
                    "source_name": "local",
                    "prompt_name": "summarize",
                    "title": None,
                    "description": "Summarize text",
                    "arguments": [
                        type(
                            "_Argument",
                            (),
                            {
                                "name": "topic",
                                "description": "Topic",
                                "required": True,
                            },
                        )()
                    ],
                },
            )()
        ]

    async def get_prompt(
        self,
        source_name: str,
        prompt_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        assert source_name == "local"
        assert prompt_name == "summarize"
        assert arguments == {"topic": "release"}
        return {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Prompt body"},
                    ],
                }
            ]
        }

    async def list_resources(self, source_name: str):
        assert source_name == "local"
        return [
            type(
                "_ResourceDescriptor",
                (),
                {
                    "source_name": "local",
                    "uri": "file:///docs/readme.md",
                    "name": "readme",
                    "title": None,
                    "description": "Readme",
                    "mime_type": "text/markdown",
                },
            )()
        ]

    async def read_resource(
        self,
        source_name: str,
        uri: str,
    ) -> dict[str, object]:
        assert source_name == "local"
        assert uri == "file:///docs/readme.md"
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": "Resource body",
                }
            ]
        }


class _ProjectStub:
    def __init__(self) -> None:
        self.settings = vocode_settings.Settings()
        self.current_workflow = "wf"
        self.tools = {}
        self.mcp = _MCPServiceStub()


def _make_req(name: str) -> ToolReq:
    return ToolReq(
        execution=WorkflowExecution(workflow_name="wf"),
        spec=vocode_settings.ToolSpec(name=name),
    )


@pytest.mark.asyncio
async def test_mcp_get_prompt_tool_lists_prompts() -> None:
    tool = MCPGetPromptTool(_ProjectStub())

    result = await tool.run(_make_req("mcp_get_prompt"), {})

    assert isinstance(result, ToolTextResponse)
    assert result.data == {
        "prompts": [
            {
                "source": "local",
                "name": "summarize",
                "title": None,
                "description": "Summarize text",
                "arguments": [
                    {
                        "name": "topic",
                        "description": "Topic",
                        "required": True,
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_mcp_get_prompt_tool_fetches_prompt() -> None:
    tool = MCPGetPromptTool(_ProjectStub())

    result = await tool.run(
        _make_req("mcp_get_prompt"),
        {"name": "summarize", "arguments": {"topic": "release"}},
    )

    assert isinstance(result, ToolTextResponse)
    assert result.text == "Prompt body"
    assert result.data is not None
    assert result.data["messages"][0]["content"][0]["text"] == "Prompt body"


@pytest.mark.asyncio
async def test_mcp_read_resource_tool_lists_resources() -> None:
    tool = MCPReadResourceTool(_ProjectStub())

    result = await tool.run(_make_req("mcp_read_resource"), {})

    assert isinstance(result, ToolTextResponse)
    assert result.data == {
        "resources": [
            {
                "source": "local",
                "uri": "file:///docs/readme.md",
                "name": "readme",
                "title": None,
                "description": "Readme",
                "mime_type": "text/markdown",
            }
        ]
    }


@pytest.mark.asyncio
async def test_mcp_read_resource_tool_reads_resource() -> None:
    tool = MCPReadResourceTool(_ProjectStub())

    result = await tool.run(
        _make_req("mcp_read_resource"),
        {"uri": "file:///docs/readme.md"},
    )

    assert isinstance(result, ToolTextResponse)
    assert result.text == "Resource body"
    assert result.data is not None
    assert result.data["contents"][0]["text"] == "Resource body"
