from typing import Any, Dict, Optional

from .base import BaseTool, ToolStartWorkflowResponse, ToolReq
from vocode.settings import ToolSpec


class RunAgentTool(BaseTool):
    """Tool that requests starting a child workflow/agent."""

    # Public tool name exposed to LLMs and configs
    name = "run_agent"

    async def run(self, req: ToolReq, args: Any):
        spec = req.spec
        if not isinstance(args, dict):
            raise TypeError("RunAgentTool requires dict args with a 'name' key")

        workflow = args.get("name")
        if not workflow or not isinstance(workflow, str):
            raise ValueError("RunAgentTool requires 'name' argument (string)")

        prj = self.prj
        parent_name: Optional[str] = prj.current_workflow
        settings = prj.settings
        if parent_name and settings and settings.workflows:
            parent_cfg = settings.workflows.get(parent_name)
            if parent_cfg and parent_cfg.agent_workflows is not None:
                if workflow not in parent_cfg.agent_workflows:
                    raise ValueError(
                        f"Workflow '{workflow}' is not allowed to be executed by '{parent_name}'"
                    )

        initial_text: Optional[str] = None
        if isinstance(args.get("text"), str):
            initial_text = args.get("text")

        return ToolStartWorkflowResponse(workflow=workflow, initial_text=initial_text)

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Run an agent by name. "
                "Provide 'name' as the agent name and 'text' as "
                "the agent prompt value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the agent to run",
                    },
                    "text": {
                        "type": "string",
                        "description": "Free-form text to pass to an agent.",
                    },
                },
                "required": ["workflow"],
                "additionalProperties": False,
            },
        }


try:
    from .base import register_tool

    register_tool(RunAgentTool.name, RunAgentTool)
except Exception:
    pass
