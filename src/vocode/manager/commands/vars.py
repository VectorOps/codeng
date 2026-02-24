from __future__ import annotations

import json
from typing import Any, Optional

from vocode import settings as vocode_settings
from vocode import vars as vars_mod
from vocode.manager import proto as manager_proto

from .base import CommandError, command, option


MAX_VAR_VALUE_CHARS = 200


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _trim_value(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _parse_typed_value(raw: str, var_def: Optional[object]) -> Any:
    if raw is None:
        return raw
    if not isinstance(raw, str):
        return raw
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            return raw

    if isinstance(var_def, vars_mod.VarDef):
        current = var_def.value
        if isinstance(current, bool):
            norm = raw.strip().casefold()
            if norm in {"true", "1", "yes", "y", "on"}:
                return True
            if norm in {"false", "0", "no", "n", "off"}:
                return False
            return raw
        if isinstance(current, int) and not isinstance(current, bool):
            try:
                return int(raw)
            except Exception:
                return raw
        if isinstance(current, float):
            try:
                return float(raw)
            except Exception:
                return raw

        if var_def.options is not None:
            for opt in var_def.options:
                if str(opt) == raw:
                    return opt

    return raw


@command(
    "var",
    description="Manage variables",
    params=["<list|set>", "..."],
)
@option(0, "args", type=str, splat=True)
async def _var(server, args: list[str]) -> None:
    if not args:
        raise CommandError("Usage: /var <list|set> ...")

    project = server.manager.project
    settings = project.settings
    if settings is None or not isinstance(settings, vocode_settings.Settings):
        raise CommandError("Project settings are not loaded.")

    op = args[0]
    rest = args[1:]

    if op == "list":
        if rest:
            raise CommandError("Usage: /var list")

        vars_defs = settings.list_variables()
        if not vars_defs:
            await server.send_text_message("No variables configured.")
            return

        lines: list[str] = ["Variables:"]
        for name in sorted(vars_defs.keys()):
            val = settings.get_variable_value(name)
            value_text = _stringify_value(val)
            rendered = _trim_value(value_text, max_chars=MAX_VAR_VALUE_CHARS)
            if rendered != value_text:
                rendered = f"{rendered} [dim](trimmed)[/]"
            lines.append(f"  [bold cyan]{name}[/] = [green]{rendered}[/]")

        await server.send_text_message(
            "\n".join(lines),
            text_format=manager_proto.TextMessageFormat.RICH_TEXT,
        )
        return

    if op == "set":
        if len(rest) != 2:
            raise CommandError("Usage: /var set <name> <value>")

        name = rest[0]
        raw_value = rest[1]
        var_def = settings.get_variable_def(name)
        value = _parse_typed_value(raw_value, var_def)
        settings.set_variable_value(name, value)
        rendered = _trim_value(
            _stringify_value(value),
            max_chars=MAX_VAR_VALUE_CHARS,
        )
        await server.send_text_message(
            f"[bold cyan]{name}[/] set to [green]{rendered}[/]",
            text_format=manager_proto.TextMessageFormat.RICH_TEXT,
        )
        return

    raise CommandError("Usage: /var <list|set> ...")
