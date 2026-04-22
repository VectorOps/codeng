from pathlib import Path
from typing import Optional

from vocode import state, settings as vocode_settings
from vocode.auth import ProjectCredentialManager
from vocode.history.manager import HistoryManager
from vocode.input_manager import InputManager
from vocode.mcp.service import MCPService
from vocode.project_state import ProjectState
from vocode.proc.manager import ProcessManager
from vocode.proc.shell import ShellManager
from vocode.persistence import state_manager as persistence_state_manager


class StubProject:
    def __init__(
        self,
        process_manager: ProcessManager | None = None,
        settings: vocode_settings.Settings | None = None,
    ) -> None:
        self.llm_usage = state.LLMUsageStats()
        self.settings = settings or vocode_settings.Settings()
        self.current_workflow = None
        self.current_workflow_run_id = None
        self.last_root_workflow = None
        self.tools = {}
        self.history = HistoryManager()
        self.input_manager = InputManager()
        self.credentials = ProjectCredentialManager(env={})
        self.state_manager = persistence_state_manager.NullWorkflowStateManager()
        self.project_state = ProjectState()
        self.mcp: MCPService | None = None
        self.processes: ProcessManager | None = process_manager
        self.shells: ShellManager | None = None
        if self.processes is not None:
            shell_settings = (
                self.settings.process.shell
                if self.settings.process is not None
                else None
            )
            self.shells = ShellManager(
                process_manager=self.processes,
                settings=shell_settings,
                default_cwd=self.processes._default_cwd,
            )

    def add_llm_usage(
        self,
        prompt_delta: int,
        completion_delta: int,
        cost_delta: float,
    ) -> None:
        stats = self.llm_usage
        stats.prompt_tokens += int(prompt_delta or 0)
        stats.completion_tokens += int(completion_delta or 0)
        stats.cost_dollars += float(cost_delta or 0.0)

    def refresh_tools_from_registry(self) -> None:
        return None

    async def on_workflow_started(
        self,
        workflow_name: str,
        workflow_run_id: Optional[str] = None,
    ) -> None:
        self.current_workflow = workflow_name
        self.current_workflow_run_id = workflow_run_id
        if self.mcp is None:
            return
        workflow = self.settings.workflows.get(workflow_name)
        change = await self.mcp.start_workflow(
            workflow_name,
            workflow,
            workflow_run_id=workflow_run_id,
        )
        for source_name in change.started_sources:
            await self.mcp.refresh_tools(source_name)
        if change.started_sources or change.stopped_sources:
            self.refresh_tools_from_registry()

    async def on_workflow_finished(
        self,
        workflow_name: str,
        keep_mcp_sessions: bool = False,
        workflow_run_id: Optional[str] = None,
    ) -> None:
        if self.mcp is None:
            return
        change = await self.mcp.finish_workflow(
            workflow_name,
            keep_mcp_sessions,
            workflow_run_id=workflow_run_id,
        )
        if self.current_workflow_run_id == workflow_run_id:
            self.current_workflow = None
            self.current_workflow_run_id = None
        if change.started_sources or change.stopped_sources:
            self.refresh_tools_from_registry()

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        await self.input_manager.reset_all()
        return None
