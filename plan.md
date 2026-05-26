# Parallel workflow execution plan

This plan describes a staged implementation for concurrent and parallel workflow execution in the management subsystem. The goal is to support multiple child workflows started from parallel tool calls while keeping the existing UI protocol and TUI behavior compatible.

## Goals

- Support multiple child workflows running at the same time from one parent workflow.
- Preserve current UI and TUI behavior where possible.
- Make request origin explicit so prompts and tool confirmations remain understandable.
- Replace stack-only runtime assumptions with a model that supports fan-out and fan-in.

## Step 1: Freeze current behavior with focused tests

- Add manager-level characterization tests for the current serial nested workflow behavior.
- Add protocol and TUI tests that capture the current stack rendering and input prompt behavior.
- Add tests that describe the desired new behavior for parallel child workflows, even if they fail initially.
- Add tests for mixed tool rounds where some tool calls are sequential and others are parallel.

## Step 2: Introduce stable manager run identities

- Add a manager-owned run id for each live workflow run.
- Extend the manager runtime frame with explicit identity and lineage fields.
- Include parent run id and root run id so nested and sibling relationships are explicit.
- Stop relying on stack position as the only way to identify a run.

## Step 3: Add explicit request origin metadata

- Define a small request-origin model for manager packets.
- Include fields for run id, workflow execution id, node execution id, workflow name, and optional tool call id.
- Attach origin metadata to `RunnerReqPacket` and `InputPromptPacket` as additive optional fields.
- Keep existing packet fields unchanged for backward compatibility.

## Step 4: Split internal runtime state from UI-focused stack state

- Replace the manager's internal stack-only view with a runtime registry keyed by run id.
- Store parent-child links between runs.
- Keep a derived focused path for the UI so the current toolbar model can continue to work.
- Introduce the concept of a focused run separate from the set of active runs.

## Step 5: Refactor BaseManager into a scheduler for multiple live runs

- Replace the single `_driver_task` loop with a scheduler that can own multiple active runner tasks.
- Give each live run its own task and pending response channel.
- Ensure lifecycle operations such as start, stop, continue, and restart are routed by run id rather than top-of-stack assumptions.
- Preserve the current serial behavior for the focused path while enabling sibling runs to proceed concurrently.

## Step 6: Model child workflow fan-out and fan-in explicitly

- Introduce a manager-side child workflow group model for a parent tool round.
- When a tool round returns multiple `START_WORKFLOW` results, create one child run per tool call.
- Record which parent run and tool call each child belongs to.
- Track completion of all child runs in the group before resuming the parent tool round finalization.

## Step 7: Change parent-child communication from stack return to explicit join

- Remove the assumption that a child workflow result is returned by popping the top frame and sending one message back to the parent.
- For each child run, capture its terminal result independently.
- Convert child completion into a structured manager-side result associated with the originating tool call.
- Resume the parent only when the required child results for that tool round are available.

## Step 8: Keep tool-round semantics stable while enabling parallel child runs

- Preserve the current tool orchestration rules for approval, execution status, and completion status.
- Allow parallel tool execution to start multiple child workflows without serializing them through the manager.
- Keep sequential tool specs honored when `parallel: false` is configured.
- Ensure failed child workflows map back to the correct tool response without affecting unrelated sibling runs.

## Step 9: Make UI state explicit about concurrency

- Extend `UIServerStatePacket` with additive fields that describe concurrent active runs or run groups.
- Keep `runners` as the focused path for compatibility with current toolbar rendering.
- Add a separate summary for other active sibling runs so the UI can expose concurrency without losing the current mental model.
- Choose one focused run to drive active usage, elapsed time, and default prompt display.

## Step 10: Update TUI rendering for explicit request source

- Keep the toolbar path display for the focused run.
- Add request-source rendering for prompts and tool confirmations using the new origin metadata.
- Make it clear when a prompt comes from a sibling child workflow rather than the currently visible parent path alone.
- Add a compact display for concurrent sibling runs so the operator can see that multiple workflows are active.

## Step 11: Define focus and input-routing rules

- Decide how focus is selected when multiple runs are waiting for input.
- Route user input to a specific waiting request using origin metadata instead of whichever waiter happens to receive it first.
- Preserve the current simple behavior when only one request is waiting.
- Define deterministic rules for stop, continue, and history edit operations when more than one run exists.

## Step 12: Update manager commands for parallel-aware behavior

- Keep `/run` as a full reset that stops all active runs and starts a new root workflow.
- Define whether `/continue`, `/reset`, `/branch`, and stop actions operate on the focused run or require explicit targeting.
- Add targeting support only where necessary to keep the first implementation practical.
- Update command help text once behavior is finalized.

## Step 13: Validate persistence and runtime ownership boundaries

- Confirm whether manager parallel-run state must be persisted or can remain in-memory and reconstructable.
- Avoid leaking manager-private grouping state into workflow execution models unless it is required by persistence or protocol contracts.
- Keep workflow execution ownership with the runner and history subsystems.
- Keep manager scheduling and focus state owned by the manager layer.

## Step 14: Implement incrementally behind compatibility-preserving changes

- Land packet and runtime identity changes first.
- Land manager scheduler changes next while preserving serial behavior.
- Enable parallel child workflows after the scheduler and join model exist.
- Update the TUI last so it consumes the new additive state without breaking current flows.

## Step 15: Verify with targeted tests and manual scenarios

- Add manager tests for multiple parallel child workflows started from one parent tool round.
- Add tests for partial child failure, mixed success and failure, and sibling completion ordering.
- Add UI protocol tests for focused path plus concurrent run summaries.
- Add TUI tests for explicit request-source presentation.
- Run the relevant manager, runner, and TUI pytest suites.

## Recommended implementation order

1. Add characterization and target tests.
2. Add run ids and request-origin metadata.
3. Refactor manager runtime storage and focus tracking.
4. Replace single-driver scheduling with multi-run scheduling.
5. Implement child workflow grouping and join behavior.
6. Extend protocol state with concurrent-run summaries.
7. Update TUI rendering and input targeting.
8. Finalize commands, edge cases, and regression coverage.

## Risks to watch

- Input routing races when multiple runs are waiting at once.
- Implicit dependence on `current_runner` or `_runner_stack[-1]` in commands and UI controllers.
- Incorrect association between child workflow results and originating tool calls.
- Status and usage aggregation becoming ambiguous when more than one run is active.
- MCP or workflow-scoped resources assuming a single active workflow context.

## Definition of done

- Multiple child workflows from one tool round can run concurrently.
- Parent tool-round completion waits for all required child workflow results.
- UI packets identify request origin explicitly.
- TUI shows the focused path and indicates concurrent sibling activity.
- User input and stop actions target the intended run deterministically.
- Existing serial nested workflow behavior remains intact.