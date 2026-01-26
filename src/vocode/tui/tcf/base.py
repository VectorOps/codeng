from __future__ import annotations

import typing
from abc import ABC, abstractmethod
from typing import ClassVar

from vocode.logger import logger
from vocode import settings as vocode_settings
from vocode import state as vocode_state
from vocode.tui import lib as tui_terminal
from vocode.tui.lib import base as tui_base


class BaseToolCallFormatter(ABC):
    @abstractmethod
    def format_input(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        arguments: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        raise NotImplementedError

    @abstractmethod
    def format_output(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        result: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        raise NotImplementedError


class ToolCallFormatterManager:
    _registry: ClassVar[dict[str, type[BaseToolCallFormatter]]] = {}
    _instance: ClassVar["ToolCallFormatterManager" | None] = None

    def __init__(
        self,
        *,
        tool_configs: dict[str, vocode_settings.ToolCallFormatter] | None = None,
    ) -> None:
        self._tool_configs = tool_configs or {}
        self._instances: dict[str, BaseToolCallFormatter] = {}

    @classmethod
    def instance(cls) -> "ToolCallFormatterManager":
        if cls._instance is None:
            cls._instance = cls(tool_configs={})
        return cls._instance

    @classmethod
    def configure(cls, settings: vocode_settings.Settings | None) -> None:
        tool_configs: dict[str, vocode_settings.ToolCallFormatter] = {}
        if settings is not None:
            tool_configs = dict(settings.tool_call_formatters or {})
        inst = cls.instance()
        inst._tool_configs = tool_configs

    @classmethod
    def register(
        cls,
        name: str,
        formatter_cls: type[BaseToolCallFormatter] | None = None,
    ):
        def _do_register(
            inner: type[BaseToolCallFormatter],
        ) -> type[BaseToolCallFormatter]:
            if name in cls._registry:
                raise ValueError(f"Tool call formatter '{name}' already registered.")
            cls._registry[name] = inner
            return inner

        if formatter_cls is None:
            return _do_register
        return _do_register(formatter_cls)

    def _get_instance(self, name: str) -> BaseToolCallFormatter | None:
        cached = self._instances.get(name)
        if cached is not None:
            return cached
        formatter_type = self._registry.get(name)
        if formatter_type is None:
            return None
        inst = formatter_type()
        self._instances[name] = inst
        return inst

    def _resolve(
        self, tool_name: str
    ) -> tuple[BaseToolCallFormatter | None, vocode_settings.ToolCallFormatter | None]:
        config = self._tool_configs.get(tool_name)
        formatter_name = "generic"
        if config is not None and config.formatter:
            formatter_name = config.formatter
        elif tool_name in self._registry:
            formatter_name = tool_name

        formatter = self._get_instance(formatter_name)
        if formatter is not None:
            return formatter, config

        fallback = self._get_instance("generic")
        return fallback, config

    def format_request(
        self,
        terminal: tui_terminal.Terminal,
        req: vocode_state.ToolCallReq,
    ) -> tui_base.Renderable | None:
        formatter, config = self._resolve(req.name)
        if formatter is None:
            return None
        return formatter.format_input(
            terminal=terminal,
            tool_name=req.name,
            arguments=req.arguments,
            config=config,
        )

    def format_response(
        self,
        terminal: tui_terminal.Terminal,
        resp: vocode_state.ToolCallResp,
    ) -> tui_base.Renderable | None:
        formatter, config = self._resolve(resp.name)
        if formatter is None:
            return None
        return formatter.format_output(
            terminal=terminal,
            tool_name=resp.name,
            result=resp.result,
            config=config,
        )
