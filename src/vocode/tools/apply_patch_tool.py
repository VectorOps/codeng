import asyncio
from typing import Any, Dict, Optional, TYPE_CHECKING

from vocode.logger import logger
from vocode.tools import base as tools_base
from vocode.settings import ToolSpec
from vocode.patch import apply_patch, get_supported_formats, get_system_instruction

if TYPE_CHECKING:
    from vocode.project import Project


@tools_base.ToolFactory.register("apply_patch")
class ApplyPatchTool(tools_base.BaseTool):
    """
    Apply a repository patch to the project's filesystem under base_path.
    Patch format comes from the tool config (ToolSpec.config['format']), defaults to 'v4a'.
    Returns a human-readable summary of applied changes or errors.
    """

    name = "apply_patch"

    async def run(self, req: tools_base.ToolReq, args: Any):
        spec = req.spec

        # Read patch content from args
        text: Optional[str] = None
        if isinstance(args, str):
            text = args
        elif isinstance(args, dict):
            arg_text = args.get("text")
            if isinstance(arg_text, str):
                text = arg_text

        if not text or not text.strip():
            raise ValueError("ApplyPatchTool requires 'text' (patch content)")

        # Read format from tool config (not from args)
        fmt = (spec.config.get("format") or "v4a").lower()
        supported = set(get_supported_formats())
        if fmt not in supported:
            supported_list = ", ".join(sorted(supported))
            return tools_base.ToolTextResponse(
                text=f"Unsupported patch format: {fmt}. Supported formats: {supported_list}"
            )

        try:
            base_path = self.prj.base_path  # type: ignore[attr-defined]
        except Exception:
            raise RuntimeError("ApplyPatchTool requires project.base_path")

        # Apply the patch using helper; schedule a refresh if changes occurred
        try:
            summary, _outcome, changes_map, _statuses, _errs = apply_patch(
                fmt, text, base_path
            )
            # Build refresh payload if project exposes refresh API
            try:
                from vocode.project_state import FileChangeModel, FileChangeType

                change_type_map = {
                    "created": FileChangeType.CREATED,
                    "updated": FileChangeType.UPDATED,
                    "deleted": FileChangeType.DELETED,
                }
                changed_files = [
                    FileChangeModel(type=change_type_map[kind], relative_filename=rel)
                    for rel, kind in changes_map.items()
                    if kind in change_type_map
                ]
                if changed_files:
                    asyncio.create_task(self.prj.refresh(files=changed_files))  # type: ignore[attr-defined]
            except Exception:
                # Best-effort: ignore refresh errors in tool execution path
                pass

            return tools_base.ToolTextResponse(text=summary)
        except Exception as e:
            return tools_base.ToolTextResponse(text=f"Error applying patch: {e}")

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        fmts = sorted(get_supported_formats())
        config = spec.config or {}
        fmt_config = config.get("format", "v4a")
        if isinstance(fmt_config, str):
            fmt = fmt_config.lower().strip()
        else:
            fmt = "v4a"

        description = (
            "Apply a repository patch to the current project. "
            "Patch format is configured in this tool's config (format="
            + "/".join(fmts)
            + "). Returns a human-readable summary of changes or errors."
        )

        if fmt in get_supported_formats():
            instruction = get_system_instruction(fmt)
            description = (
                description
                + "\n\n"
                + "Patch content must follow these format-specific instructions:\n"
                + instruction
            )

        return {
            "name": self.name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Patch content to apply.",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        }
