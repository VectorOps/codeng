from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from pydantic import ValidationError

from vocode.settings import ToolSpec
from vocode.tools import base as tools_base
from vocode.webclient import errors as webclient_errors
from vocode.webclient import models as webclient_models
from vocode.webclient import service as webclient_service


class WebFetchArgs(BaseModel):
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout_s: Optional[float] = Field(default=None, gt=0)


def _merge_config_dicts(
    base_config: Dict[str, Any],
    override_config: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(base_config)
    merged.update(override_config)
    return merged


def _normalize_timeout_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config = dict(config)
    timeout_value = normalized_config.get("timeout_s")
    if isinstance(timeout_value, (int, float)):
        connect_timeout_value = normalized_config.get("connect_timeout_s")
        if (
            connect_timeout_value is not None
            and isinstance(connect_timeout_value, (int, float))
            and connect_timeout_value > timeout_value
        ):
            normalized_config["connect_timeout_s"] = timeout_value
        read_timeout_value = normalized_config.get("read_timeout_s")
        if read_timeout_value is None:
            normalized_config["read_timeout_s"] = timeout_value
        elif (
            isinstance(read_timeout_value, (int, float))
            and read_timeout_value > timeout_value
        ):
            normalized_config["read_timeout_s"] = timeout_value
    return normalized_config


def _build_tool_settings(
    project: Any,
    spec: ToolSpec,
) -> webclient_models.WebClientSettings:
    global_settings: Optional[webclient_models.WebClientSettings] = None
    settings = getattr(project, "settings", None)
    if (
        settings is not None
        and settings.tool_settings is not None
        and settings.tool_settings.web_client is not None
    ):
        global_settings = settings.tool_settings.web_client

    local_config = spec.config or {}
    global_config: Dict[str, Any] = {}
    if global_settings is not None:
        global_config = global_settings.model_dump(mode="python")
    merged_config = _merge_config_dicts(global_config, local_config)
    normalized_config = _normalize_timeout_config(merged_config)
    local_settings = webclient_models.WebClientSettings(**normalized_config)
    return webclient_service.merge_settings_layers(global_settings, local_settings)


def _build_tool_policy(project: Any) -> webclient_models.HarnessWebClientPolicy:
    settings = project.settings
    if (
        settings.tool_settings is not None
        and settings.tool_settings.web_client_policy is not None
    ):
        return settings.tool_settings.web_client_policy.model_copy(deep=True)
    return webclient_service.HarnessManagedWebClientPolicy.default_policy()


def _result_to_data(result: webclient_models.WebClientResult) -> Dict[str, Any]:
    data = dict(result.metadata)
    data.update(
        {
            "url": result.url,
            "final_url": result.final_url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "content_kind": result.content_kind.value,
        }
    )
    if result.title is not None:
        data["title"] = result.title
    return data


@tools_base.ToolFactory.register("web_fetch")
class WebFetchTool(tools_base.BaseTool):
    name = "web_fetch"

    async def run(self, req: tools_base.ToolReq, args: Any):
        try:
            parsed_args = WebFetchArgs.model_validate(args)
            settings = _build_tool_settings(self.prj, req.spec)
            policy = _build_tool_policy(self.prj)
            service = webclient_service.WebClientService(
                settings=settings,
                policy=policy,
            )
            result = await service.fetch_url(
                parsed_args.url,
                headers=parsed_args.headers,
                timeout_s=parsed_args.timeout_s,
            )
            return tools_base.ToolTextResponse(
                text=result.text,
                data=_result_to_data(result),
            )
        except ValidationError as exc:
            return tools_base.ToolTextResponse(
                text=str(exc),
                is_error=True,
            )
        except webclient_errors.WebClientError as exc:
            return tools_base.ToolTextResponse(
                text=exc.message,
                data=exc.payload,
                is_error=True,
            )

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch a URL over the configured web client backend and return "
                "normalized markdown or plain text content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to fetch.",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Optional request headers.",
                        "additionalProperties": {"type": "string"},
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "Optional per-call timeout override in seconds.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        }
