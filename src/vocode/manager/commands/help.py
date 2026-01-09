from __future__ import annotations

from .base import command, option


@command(
    "help",
    description="Show available commands",
    params=[],
)
@option(0, "args", type=str, splat=True)
async def _help(server, args: list[str]) -> None:
    entries = server.commands.get_help_entries()
    lines: list[str] = ["Commands:"]
    for name, description, params in entries:
        if name == "help":
            continue
        signature = "/" + name
        if params:
            signature += " " + " ".join(params)
        if description:
            lines.append(f"  {signature} - {description}")
        else:
            lines.append(f"  {signature}")
    text = "\n".join(lines)
    await server.send_text_message(text)
