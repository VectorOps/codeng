from __future__ import annotations

from pathlib import Path

from vocode import models as models_mod
from vocode import settings as settings_mod
from vocode import state as state_mod
from vocode.project import Project
from vocode.runner.executors.llm.preprocessors import base as pre_base


def test_agents_preprocessor_registers_with_factory() -> None:
    pre = pre_base.PreprocessorFactory.get("agents")
    assert pre is not None
    assert pre.name == "agents"


def test_agents_preprocessor_injects_allowed_agents(tmp_path: Path) -> None:
    settings = settings_mod.Settings(
        workflows={
            "agent": settings_mod.WorkflowConfig(agents=["agent-discovery"]),
            "agent-discovery": settings_mod.WorkflowConfig(
                description="Discovery agent"
            ),
        }
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.current_workflow = "agent"

    spec = models_mod.PreprocessorSpec(
        name="agents",
        mode=models_mod.Role.SYSTEM,
        prepend=False,
    )
    messages = [state_mod.Message(role=models_mod.Role.SYSTEM, text="base")]

    out = pre_base.apply_preprocessors([spec], project, messages)
    assert len(out) == 1
    assert out[0].role == models_mod.Role.SYSTEM
    assert "agent-discovery" in (out[0].text or "")
    assert "Discovery agent" in (out[0].text or "")
