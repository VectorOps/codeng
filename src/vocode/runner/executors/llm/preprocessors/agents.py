from typing import Any, List

from vocode import models as models_mod
from vocode.project import Project
from vocode.state import Message
from vocode.runner.executors.llm.preprocessors import base as pre_base


_DEFAULT_HEADER = "\n\n## You can delegate tasks to these agents via the run_agent tool. Use them when appropriate:\n"


@pre_base.PreprocessorFactory.register(
    "agents",
    description=(
        "Injects a system prompt section listing allowed child agents for the current workflow "
        "(from WorkflowConfig.agents)."
    ),
)
def _agents_preprocessor(
    project: Any, spec: models_mod.PreprocessorSpec, messages: List[Message]
) -> List[Message]:
    if spec.mode != models_mod.Role.SYSTEM:
        return messages

    if not isinstance(project, Project):
        return messages

    settings = project.settings
    if settings is None:
        return messages

    parent_name = project.current_workflow
    if parent_name is None:
        return messages

    parent_cfg = settings.workflows.get(parent_name)
    if parent_cfg is None:
        return messages

    allowed = parent_cfg.agents
    if not allowed:
        return messages

    target: Message | None = None
    for msg in messages:
        if msg.role == models_mod.Role.SYSTEM:
            target = msg
            break

    if target is None:
        return messages

    opts = spec.options or {}
    header = opts.get("header")
    if not isinstance(header, str) or not header:
        header = _DEFAULT_HEADER

    base_text = target.text or ""
    if header in base_text:
        return messages

    item_format = opts.get("item_format")
    if not isinstance(item_format, str) or not item_format:
        item_format = "- {name}: {description}"

    separator = opts.get("separator")
    if not isinstance(separator, str) or not separator:
        separator = "\n"

    lines: List[str] = []
    for agent_name in allowed:
        wf = settings.workflows.get(agent_name)
        desc = wf.description if wf is not None else None
        if not isinstance(desc, str) or not desc:
            lines.append(f"- {agent_name}")
            continue
        try:
            lines.append(item_format.format(name=agent_name, description=desc).rstrip())
        except Exception:
            lines.append(f"- {agent_name}: {desc}")

    block = header + separator.join(lines)

    if spec.prepend:
        target.text = f"{block}{base_text}"
    else:
        target.text = f"{base_text}{block}"

    return messages
