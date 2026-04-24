from __future__ import annotations

import typing

from rich import markup as rich_markup

from vocode.manager import proto as manager_proto


def escape(value: object) -> str:
    return rich_markup.escape(str(value))


def heading(text: str) -> str:
    return f"[bold]{escape(text)}[/]"


def command(text: str) -> str:
    return f"[bold cyan]{escape(text)}[/]"


def help_text(text: str) -> str:
    return escape(text)


def meta_text(text: str) -> str:
    return escape(text)


def success(text: str) -> str:
    return f"[green]{escape(text)}[/]"


def warning(text: str) -> str:
    return f"[yellow]{escape(text)}[/]"


def error(text: str) -> str:
    return f"[bold red]Command error:[/] {escape(text)}"


def format_help(
    title: str,
    entries: typing.Sequence[tuple[str, str]],
) -> str:
    lines = [heading(title)]
    for signature, description in entries:
        lines.append(f"  {command(signature)}")
        lines.append(f"    {help_text(description)}")
    return "\n".join(lines)


async def send_rich(server, text: str) -> None:
    await server.send_text_message(
        text,
        text_format=manager_proto.TextMessageFormat.RICH_TEXT,
    )


async def send_success(server, message: str) -> None:
    await send_rich(server, success(message))


async def send_warning(server, message: str) -> None:
    await send_rich(server, warning(message))


async def send_error(server, message: str) -> None:
    await send_rich(server, error(message))
