from __future__ import annotations

from . import output as command_output
from .base import command, option


@command(
    "help",
    description="Show available commands",
    params=[],
)
@option(0, "args", type=str, splat=True)
async def _help(server, args: list[str]) -> None:
    entries = server.commands.get_help_entries()
    lines: list[str] = [command_output.heading("Commands:")]
    for name, description, params in entries:
        if name == "help":
            continue
        signature = "/" + name
        if params:
            signature += " " + " ".join(params)
        if description:
            lines.append(
                f"  {command_output.command(signature)} {command_output.help_text('- ' + description)}"
            )
        else:
            lines.append(f"  {command_output.command(signature)}")
    text = "\n".join(lines)
    await command_output.send_rich(server, text)
