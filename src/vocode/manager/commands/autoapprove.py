from __future__ import annotations

from vocode import models, state

from .base import CommandError, command, option


def _get_pending_tool_confirmation(server) -> tuple[object, state.Step]:
    runner = server.manager.current_runner
    if runner is None:
        raise CommandError("No tool confirmation is currently pending.")

    execution = runner.execution
    for step in execution.iter_steps_reversed():
        if step.type != state.StepType.TOOL_REQUEST:
            continue
        message = step.message
        if message is None:
            continue
        pending_requests = [
            tool_req
            for tool_req in message.tool_call_requests
            if tool_req.status == state.ToolCallReqStatus.REQUIRES_CONFIRMATION
        ]
        if not pending_requests:
            continue
        return runner, step

    raise CommandError("No tool confirmation is currently pending.")


@command(
    "aa",
    description="Auto-approve similar tool calls for this session",
    params=[],
    hidden=True,
)
@option(0, "args", splat=True)
async def _aa(server, args: list[str]) -> None:
    if args:
        raise CommandError("Usage: /aa")

    runner, step = _get_pending_tool_confirmation(server)
    message = step.message
    if message is None:
        raise CommandError("No tool confirmation is currently pending.")

    added: list[str] = []
    for tool_req in message.tool_call_requests:
        if tool_req.status != state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
            continue
        server.manager.project.project_state.autoapprove.add_tool(tool_req.name)
        added.append(tool_req.name)

    accepted = await server.manager.project.input_manager.publish(
        state.Message(role=models.Role.USER, text=""),
        queue=False,
    )
    if not accepted:
        raise CommandError("No tool confirmation is currently pending.")

    await server.send_packet(server.manager_proto.InputPromptPacket())

    if added:
        unique = sorted(set(added))
        await server.send_text_message(
            "Session auto-approve enabled for: " + ", ".join(unique)
        )


@command(
    "autoapprove",
    description="Manage session auto-approve rules",
    params=["<list|add|remove|clear>", "..."],
)
@option(0, "subcommand")
@option(1, "args", splat=True)
async def _autoapprove(server, subcommand: str, args: list[str]) -> None:
    st = server.manager.project.project_state.autoapprove

    if subcommand == "list":
        if args:
            raise CommandError("Usage: /autoapprove list")
        if not st.policies_by_tool:
            await server.send_text_message("No session auto-approve rules.")
            return
        lines: list[str] = ["Session auto-approve rules:"]
        for name in sorted(st.policies_by_tool.keys()):
            policy = st.policies_by_tool[name]
            if policy.approve_all:
                lines.append(f"  - {name}: approve_all")
            elif not policy.rules:
                lines.append(f"  - {name}: (no rules)")
            else:
                lines.append(f"  - {name}:")
                for rule in policy.rules:
                    lines.append(f"      - {rule.key} ~= {rule.pattern}")
        await server.send_text_message("\n".join(lines))
        return

    if subcommand == "add":
        if len(args) not in (1, 3):
            raise CommandError(
                "Usage: /autoapprove add <tool> [<dotted_key> <pattern>]"
            )
        tool_name = args[0]
        if len(args) == 1:
            st.add_tool(tool_name, approve_all=True)
            await server.send_text_message(
                f"Session auto-approve enabled for tool: {tool_name}"
            )
            return
        st.add_rule(tool_name, key=args[1], pattern=args[2])
        await server.send_text_message(
            f"Session auto-approve rule added for tool: {tool_name}"
        )
        return

    if subcommand == "remove":
        if len(args) != 1:
            raise CommandError("Usage: /autoapprove remove <tool>")
        tool_name = args[0]
        removed = st.remove_tool(tool_name)
        if removed:
            await server.send_text_message(
                f"Session auto-approve disabled for tool: {tool_name}"
            )
        else:
            await server.send_text_message(
                f"No session auto-approve rule found for tool: {tool_name}"
            )
        return

    if subcommand == "clear":
        if args:
            raise CommandError("Usage: /autoapprove clear")
        st.clear()
        await server.send_text_message("Session auto-approve rules cleared.")
        return

    raise CommandError("Usage: /autoapprove <list|add|remove|clear>")
