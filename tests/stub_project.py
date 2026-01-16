from pathlib import Path

from vocode import state, settings as vocode_settings
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
        self.tools = {}
        self.state_manager = persistence_state_manager.NullWorkflowStateManager()
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

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None