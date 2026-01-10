from __future__ import annotations

import shlex
import typing

from pydantic import BaseModel, ValidationError

from .. import proto as manager_proto


if typing.TYPE_CHECKING:
    from ..server import UIServer


ParsedCommand = tuple[str, list[str], str]


class _CommandMeta(BaseModel):
    description: str | None
    params: list[str] = []


class CommandError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ParamSpec:
    def __init__(
        self,
        index: int,
        name: str,
        converter: typing.Callable[[str], typing.Any],
        splat: bool = False,
    ) -> None:
        self.index = index
        self.name = name
        self.converter = converter
        self.splat = splat


class DeclarativeCommand:
    def __init__(
        self,
        name: str,
        handler: typing.Callable[..., typing.Awaitable[None]],
        params: list[ParamSpec],
        description: str | None = None,
        param_names: list[str] | None = None,
    ) -> None:
        params_sorted = sorted(params, key=lambda p: p.index)
        seen: set[int] = set()
        splat_spec: ParamSpec | None = None
        for spec in params_sorted:
            if spec.index < 0:
                raise ValueError("Parameter index must be non-negative.")
            if spec.index in seen:
                raise ValueError("Duplicate parameter index.")
            seen.add(spec.index)
            if spec.splat:
                if splat_spec is not None:
                    raise ValueError("Only one splat parameter is allowed.")
                splat_spec = spec
        if splat_spec is not None:
            for spec in params_sorted:
                if not spec.splat and spec.index > splat_spec.index:
                    raise ValueError(
                        "Non-splat parameters cannot follow a splat parameter."
                    )
        self.name = name
        self.handler = handler
        self.params = params_sorted
        self._splat = splat_spec
        self.description = description
        self.param_names = list(param_names) if param_names is not None else None

    def _parse_params(self, args: typing.Sequence[str]) -> list[typing.Any]:
        tokens = list(args)
        non_splat = [p for p in self.params if not p.splat]
        if non_splat:
            required_count = max(p.index for p in non_splat) + 1
        else:
            required_count = 0
        if len(tokens) < required_count:
            missing_specs = [p for p in non_splat if p.index >= len(tokens)]
            if missing_specs:
                first_missing = min(missing_specs, key=lambda p: p.index)
                position = first_missing.index + 1
                raise CommandError(
                    f"Missing value for '{first_missing.name}' (position {position})."
                )
            raise CommandError(
                f"Expected at least {required_count} argument(s), got {len(tokens)}."
            )
        if self._splat is None and len(tokens) > required_count:
            raise CommandError(
                f"Expected {required_count} argument(s), got {len(tokens)}."
            )
        values_by_index: dict[int, typing.Any] = {}
        for spec in non_splat:
            token = tokens[spec.index]
            values_by_index[spec.index] = self._convert(spec, token)
        if self._splat is not None:
            tail = tokens[self._splat.index :]
            values_by_index[self._splat.index] = [
                self._convert(self._splat, token) for token in tail
            ]
        ordered_indices = sorted(values_by_index.keys())
        return [values_by_index[i] for i in ordered_indices]

    def _convert(self, spec: ParamSpec, token: str) -> typing.Any:
        try:
            return spec.converter(token)
        except Exception as exc:
            position = spec.index + 1
            raise CommandError(
                f"Invalid value for '{spec.name}' at position {position}: {exc}."
            ) from exc


CommandInvoker = typing.Callable[
    ["UIServer", typing.Sequence[str]], typing.Awaitable[None]
]
CommandHandler = typing.Callable[["UIServer", list[str]], typing.Awaitable[None]]


_GLOBAL_COMMANDS: dict[str, DeclarativeCommand] = {}


class CommandManager:
    def __init__(self) -> None:
        self._commands: dict[str, CommandInvoker] = {}
        self._metadata: dict[str, _CommandMeta] = {}
        for name, decl in _GLOBAL_COMMANDS.items():
            if name not in self._commands:
                self._commands[name] = self._build_invoker(decl)
                if decl.param_names is None:
                    params = [spec.name for spec in decl.params]
                else:
                    params = list(decl.param_names)
                self._metadata[name] = _CommandMeta(
                    description=decl.description,
                    params=params,
                )

    def _build_invoker(self, decl: DeclarativeCommand) -> CommandInvoker:
        async def invoker(server: UIServer, args: typing.Sequence[str]) -> None:
            values = decl._parse_params(args)
            await decl.handler(server, *values)

        return invoker

    async def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        description: str | None = None,
        params: list[str] | None = None,
    ) -> None:
        async def invoker(server: UIServer, args: typing.Sequence[str]) -> None:
            await handler(server, list(args))

        await self.register_invoker(
            name,
            invoker,
            description=description,
            params=params,
        )

    async def register_invoker(
        self,
        name: str,
        invoker: CommandInvoker,
        *,
        description: str | None = None,
        params: list[str] | None = None,
    ) -> None:
        if name in self._commands:
            raise ValueError(f"Command with name '{name}' already registered.")
        self._commands[name] = invoker
        if params is None:
            params = []
        self._metadata[name] = _CommandMeta(description=description, params=params)

    async def unregister(self, name: str) -> bool:
        return self._commands.pop(name, None) is not None

    def get_help_entries(self) -> list[tuple[str, str | None, list[str]]]:
        entries: list[tuple[str, str | None, list[str]]] = []
        for name in sorted(self._commands.keys()):
            meta = self._metadata.get(name)
            if meta is None:
                entries.append((name, None, []))
            else:
                entries.append((name, meta.description, list(meta.params)))
        return entries

    async def execute(self, server: UIServer, text: str) -> bool:
        try:
            parsed = self._parse_command(text)
        except CommandError as exc:
            await self._send_command_error(server, exc.message)
            return True
        if parsed is None:
            return False
        name, args, raw_args = parsed
        invoker = self._commands.get(name)
        if invoker is None:
            await self._send_unknown_command(server, name)
            return True
        try:
            await invoker(server, args)
        except CommandError as exc:
            await self._send_command_error(server, exc.message)
        return True

    def _parse_args(self, args: str) -> list[str]:
        if not args.strip():
            return []
        try:
            return shlex.split(args, posix=True)
        except ValueError as exc:
            raise CommandError(f"Invalid command arguments: {exc}.") from exc

    def _parse_command(self, text: str) -> typing.Optional[ParsedCommand]:
        raw = text.lstrip()
        if not raw:
            return None
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError as exc:
            raise CommandError(f"Invalid command syntax: {exc}.") from exc
        if not tokens:
            return None
        name = tokens[0]
        args = tokens[1:]
        raw_args = raw[len(name) :].lstrip()
        return name, args, raw_args

    async def _send_unknown_command(self, server: UIServer, name: str) -> None:
        await server.send_text_message(f"Unknown command: /{name}")

    async def _send_command_error(self, server: UIServer, message: str) -> None:
        await server.send_text_message(f"Command error: {message}")


def option(
    index: int,
    name: str,
    *,
    type: (
        typing.Callable[[str], typing.Any] | type[BaseModel] | type[ValidationError]
    ) = str,
    splat: bool = False,
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[None]]],
    typing.Callable[..., typing.Awaitable[None]],
]:
    def decorator(
        func: typing.Callable[..., typing.Awaitable[None]],
    ) -> typing.Callable[..., typing.Awaitable[None]]:
        existing = getattr(func, "_command_params", None)
        if existing is None:
            params: list[ParamSpec] = []
        else:
            params = list(existing)
        params.append(ParamSpec(index=index, name=name, converter=type, splat=splat))
        setattr(func, "_command_params", params)
        return func

    return decorator


def command(
    name: str,
    *,
    description: str | None = None,
    params: list[str] | None = None,
) -> typing.Callable[
    [typing.Callable[..., typing.Awaitable[None]]], DeclarativeCommand
]:
    def decorator(
        func: typing.Callable[..., typing.Awaitable[None]],
    ) -> DeclarativeCommand:
        params_specs = getattr(func, "_command_params", None)
        if not params_specs:
            raise ValueError("Declarative command requires option declarations.")
        decl = DeclarativeCommand(
            name=name,
            handler=func,
            params=list(params_specs),
            description=description,
            param_names=params,
        )
        _GLOBAL_COMMANDS[name] = decl
        return decl

    return decorator
