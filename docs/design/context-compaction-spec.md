# Context compaction internal spec

This page defines the implemented and intended context compaction contract for this repository.
Use it as the source of truth for runner, executor, state, UI, and persistence behavior.

## Quick reference

Context compaction reduces prompt size for long-running LLM nodes by replacing older prompt history with a structured summary while preserving recent turns verbatim.

Current scope:

- support LLM node history compaction inside the current workflow execution
- persist compaction artifacts in workflow state so resumed runs keep the reduced context
- trigger compaction automatically when estimated context usage consumes too much of the model input budget
- allow manual compaction later without changing the stored data model

Out of scope for the first implementation:

- branch-switch summarization
- compaction for non-LLM nodes
- background re-summarization of old persisted sessions
- provider-specific token counting beyond current available usage signals and heuristics

## Problem statement

Without compaction, the runner sends all visible message history for an LLM node back into the next request.
For long-running tool-heavy sessions this causes prompt growth, latency and cost growth, and eventual provider overflow.

This creates three problems:

- long tool-heavy sessions can exceed the provider input limit
- prompt growth increases latency and cost even before hard overflow
- persistence must preserve the compacted boundary so resumed runs rebuild the shorter prompt deterministically

## Current repository surfaces

### Prompt assembly

`src/vocode/runner/executors/llm/llm.py`

- `LLMExecutor._iter_prompt_messages()` currently returns all execution messages
- `LLMExecutor.build_connect_messages()` inserts the system prompt, applies preprocessors, and converts messages into Connect messages
- `LLMExecutor._build_preview_usage()` exposes the model input limit but does not estimate effective context usage

### Workflow orchestration

`src/vocode/runner/runner.py`

- `_persist_step()` is the central place where steps become durable runtime history
- `_preview_llm_usage()` and `_apply_llm_usage()` already track usage snapshots at workflow level
- `_build_next_input_messages()` forwards node outputs to downstream nodes and must continue to work even if earlier context was compacted

### Runtime state and persistence

`src/vocode/state.py`

- `WorkflowExecution` is the durable owner of messages, steps, node executions, and branch state
- `NodeExecution.step_ids` defines the active visible history for a node
- `Step.state` and `NodeExecution.state` already support executor-owned runtime metadata
- `StepType` does not yet include a compaction-specific step kind

`src/vocode/persistence/state_manager.py`

- persistence writes the full `WorkflowExecution` model on flush
- there is no separate compaction storage layer, so compaction metadata must remain fully serializable inside `state.py` models

## Target behavior

### User-visible behavior

When a long-running LLM node approaches its context window, the runtime should summarize older turns into a compact checkpoint and continue the conversation with:

- the normal system prompt
- one synthetic summary message representing compacted history
- the recent kept messages after the compaction boundary

The workflow should continue normally after compaction.
Resuming a persisted workflow should rebuild the same compacted prompt without needing the original full prompt span.

### Runner behavior

Compaction is per node execution, not global across the whole workflow.
Only the prompt history used by `LLMExecutor` is compacted.

Compaction should happen at the beginning of an LLM run, while building prompt messages, including between tool rounds.
If an assistant message requested tools and the runner later re-enters the same LLM node with tool results, the next `LLMExecutor.run()` invocation may compact before constructing the follow-up provider request.

Compaction should happen before a provider request when either of these is true:

1. estimated context usage exceeds the configured fraction of remaining input tokens
2. the previous provider call failed due to context overflow and the node can retry after compaction

### Summary shape

The summary format should be stable, structured, and optimized for continuation by another LLM.

Required sections:

```md
## Goal

## Constraints & Preferences

## Progress
### Done
### In Progress
### Blocked

## Key Decisions

## Next Steps

## Critical Context
```

The summary must preserve exact file paths, tool names, node names, outcome names, and error text when relevant.

## Data model changes

### Compaction event step

The runtime keeps a thin persisted step event:

- `CONTEXT_COMPACTION = "context_compaction"`

This step records that a compaction event happened for a node execution.
It is a durable event and a UI/runtime event, but it is not the owner of compaction metadata.

### Summary message role

No new `Role` enum is required for the first version.
The compacted summary is stored as a `state.Message` with role `models.Role.SYSTEM`.

Reasoning:

- the current Connect builder already handles system messages cleanly
- using a normal message avoids widening the role contract immediately
- the compaction step type remains available for UI and durable event history

If later needed, a dedicated summary role can be introduced as a backward-compatible additive change.

### Compaction state models

Add new executor-owned Pydantic models in a dedicated compaction module under `src/vocode/runner/executors/llm/`.
Do not place compaction logic directly in `llm.py` beyond orchestration hooks.

Recommended module layout:

- `src/vocode/runner/executors/llm/compaction/models.py`
- `src/vocode/runner/executors/llm/compaction/prompting.py`
- `src/vocode/runner/executors/llm/compaction/estimation.py`
- `src/vocode/runner/executors/llm/compaction/service.py`

This module owns:

- default compaction system prompt
- configurable compaction prompt handling
- token estimation helpers
- cut-point selection
- summary generation flow
- prompt reconstruction metadata

Required models:

```python
class CompactionSettings(BaseModel):
    enabled: bool = True
    trigger_threshold_ratio: float = 0.7
    keep_recent_ratio: float = 0.25
    summary_model: Optional[str] = None
    summary_provider: Optional[str] = None
    prompt_system: Optional[str] = None
    prompt_instructions: Optional[str] = None


class CompactionSummaryState(BaseModel):
    compacted_step_ids: List[UUID] = Field(default_factory=list)
    compacted_message_ids: List[UUID] = Field(default_factory=list)
    tokens_before: int
    tokens_after_estimate: Optional[int] = None
    summary_input_tokens: Optional[int] = None
    summary_output_tokens: Optional[int] = None
    trigger_threshold_ratio: float
    summary_version: str = "v1"


class LLMExecutionCompactionState(BaseModel):
    latest_compaction_message_id: Optional[UUID] = None
    compaction_count: int = 0
    last_compaction_tokens_before: Optional[int] = None


class LLMExecutionState(BaseModel):
    selected_outcome: Optional[str] = None
    compaction: Optional[LLMExecutionCompactionState] = None
```

Usage:

- `CompactionSettings` belongs to `LLMNode`
- `CompactionSummaryState` lives on the summary `Message.state`
- `LLMExecutionCompactionState` lives inside `NodeExecution.state` for LLM nodes
- the compaction step references the summary message, but does not own compaction metadata

`CompactionSummaryState` intentionally does not store a `first_kept_step_id`.
Prompt reconstruction starts from the summary message itself and then includes later prompt-visible messages on the active path.
This makes the summary message the durable boundary marker.

### Message text envelope

The summary message text should use a stable wrapper so the LLM can distinguish summary context from live conversation.

Recommended format:

```text
The conversation history before this point was compacted into the following summary:

<summary>
...
</summary>
```

## History ownership and boundaries

The compaction owner is the dedicated LLM compaction module.
Runner owns persistence and retry flow, but it must not reconstruct summary content itself.

Ownership rules:

- the dedicated compaction module decides what prompt history is compacted and how the prompt is rebuilt
- `LLMExecutor` delegates compaction decisions and prompt rebuilding to that module
- runner triggers compaction-aware retry flow and persists the resulting step event
- persistence remains generic and stores the state models unchanged

Boundary ownership rules:

- the summary message is the durable compaction boundary
- `CompactionSummaryState` is owned by the summary message
- `LLMExecutionCompactionState.latest_compaction_message_id` points at the latest summary message for the node execution
- the compaction step is not used as the authoritative boundary for prompt reconstruction

## Prompt reconstruction contract

### Eligible source messages

For compaction prompt reconstruction, only prompt-visible messages referenced by prompt-visible steps are considered:

- `output_message`
- `input_message`
- existing `context_compaction`

Tool approval, prompt, rejection, and workflow request steps are not directly reconstructed into the live provider prompt unless their content is already embedded in a visible message.

For compaction summarization input, tool activity that is already represented through visible assistant and message payloads must be preserved and passed to the summarizer.
In practice, this means the summarizer should see:

- assistant tool call requests carried on visible assistant messages
- tool call responses carried on visible messages or synthetic follow-up assistant messages

Standalone non-prompt-visible bookkeeping steps are still excluded from both normal prompt reconstruction and compaction summarization input.

### Effective prompt after compaction

Given a node execution with one or more compaction events, prompt reconstruction must behave as follows:

1. find the latest summary message with `CompactionSummaryState` visible on the active path for the node execution
2. include that summary message exactly once
3. include only normal prompt-visible messages after that summary message
4. exclude compacted older visible messages from the provider request

Older messages remain persisted in workflow history and remain exportable, but they are not resent to the model.

For efficiency, prompt reconstruction should search backward through `NodeExecution.step_ids` until either:

- the latest summary message with compaction state is found
- or the beginning of the node execution history is reached

The runtime should not rescan the entire workflow step graph when a node-local backward scan is sufficient.

### Repeated compaction

On subsequent compaction passes, the summarized span starts at the previous compaction boundary represented by the latest summary message.

That means the next summary should merge:

- the prior summary content
- any additional old turns that became eligible for compaction

This prevents information loss across multiple passes.

## Triggering policy

### Settings source

`CompactionSettings` belongs to `LLMNode`.
The runtime contract is:

```yaml
kind: llm
model: provider/model-name
compaction:
  enabled: true
  trigger_threshold_ratio: 0.7
  keep_recent_ratio: 0.25
  summary_model: null
  summary_provider: null
  prompt_system: null
  prompt_instructions: null
```

Rules:

- `enabled: false` disables automatic compaction
- `trigger_threshold_ratio` is the fraction of remaining input capacity that may be consumed before compaction triggers
- `keep_recent_ratio` controls how much of the available input budget should remain verbatim after compaction
- `summary_model`, when set, overrides the model used for summary generation
- `summary_provider`, when set, overrides the provider used for summary generation
- if summary provider or model is not overridden, summary generation uses the same provider and model family configured for the current `LLMNode`
- `prompt_system`, when set, overrides the default compaction system prompt
- `prompt_instructions`, when set, appends or overrides the default summary instructions according to implementation choice documented in code

### Context estimation

Compaction should reuse existing usage signals when available and fall back to heuristics.

Estimation algorithm:

1. locate the latest completed assistant step with `llm_usage`
2. treat its `prompt_tokens + completion_tokens` only as a rough upper bound for recent context state, not as exact replay cost
3. estimate trailing messages after that point with a chars-to-tokens heuristic
4. if no usage snapshot is available, estimate all prompt-visible messages heuristically

Implementation note:

the current implementation accepts coarse estimation and optimizes for avoiding overflow, not perfect token accounting.

### Trigger decision

For a node model input limit `L`, let:

- `E` be estimated context tokens before the next request
- `R = max(L - E, 0)` be estimated remaining input tokens
- `T` be `trigger_threshold_ratio`

Compaction should trigger when the estimated request has consumed too much of the remaining budget, using a percentage-based rule instead of hard reserve counts.

Recommended decision rule:

```text
compact when E >= L * T
```

The implementation may refine this into an equivalent remaining-budget expression, but the contract is percentage-based, not absolute-token based.

If `L` is unknown, do not auto-compact preemptively.
In that case, only compact on explicit overflow errors or future manual invocation.

## Cut-point algorithm

The cut-point algorithm should keep recent history intact, summarize older history, and avoid splitting tool semantics incorrectly.

### Definitions

A turn is:

- one user input message
- followed by zero or more assistant outputs and tool-result continuation rounds
- until the next user input message

### Valid cut points

First version valid cut points:

- user input messages
- completed assistant output messages

Never cut between:

- an assistant message that requests tools
- and the later assistant continuation that incorporates those tool results

Practical rule for current codebase:

- treat a completed `OUTPUT_MESSAGE` with `tool_call_requests` as the start of a tool round
- if that round has corresponding synthetic tool-response follow-up steps added by runner, keep the whole round together

### Selection algorithm

1. collect prompt-visible steps for the node execution after the previous compaction boundary
2. walk backward from newest to oldest, accumulating estimated tokens
3. stop when accumulated tokens reach or exceed the token budget derived from `keep_recent_ratio` and the node input limit
4. move the cut to the nearest earlier valid boundary that preserves tool-round consistency
5. summarize everything before the boundary
6. keep everything from the boundary onward verbatim

### Split-turn handling

The first implementation may defer true split-turn handling.

Required behavior for v1:

- prefer cutting only at user-message boundaries
- if no user boundary can satisfy `keep_recent_tokens`, allow a fallback assistant boundary
- do not create a separate turn-prefix summary yet

This is a deliberate simplification for initial rollout.
If split-turn degradation becomes noticeable, add a second-phase enhancement for turn-prefix summaries.

## Summary generation flow

### Inputs

The summarizer receives:

- messages to summarize, serialized as plain text
- the previous summary, if a prior compaction exists
- optional custom instructions in future manual mode
- prompt settings from `LLMNode.compaction`

### Provider selection

Summary generation provider selection must follow this precedence:

1. `LLMNode.compaction.summary_provider` plus `LLMNode.compaction.summary_model`, when configured
2. `LLMNode.compaction.summary_model`, resolved through the same provider family as the current node when provider override is absent
3. the current `LLMNode` provider and model configuration

This makes summary generation configurable while preserving a safe default.

### Prompt configuration

Compaction prompting must be configurable through `CompactionSettings`, but the subsystem must ship with a strong default system prompt.

The default prompt should instruct the summarizer to produce a structured continuation checkpoint with these properties:

- preserve the user's goal
- preserve constraints and preferences
- record completed and in-progress work separately
- record blockers and key decisions
- preserve exact file paths, tool names, identifiers, and error text
- produce concrete next steps
- stay concise and optimized for another LLM continuing the same task

`prompt_system` overrides the default system prompt.
`prompt_instructions` provides additional or replacement task instructions as defined by implementation.

### Serialization format

Before summarization, convert prompt-visible messages to a text transcript rather than sending them as live chat turns.

The transcript must include tool-call information extracted from those prompt-visible messages so the summarizer can preserve tool names, arguments at a useful level of detail, and important tool results.

Recommended format:

```text
[User]: ...
[Assistant]: ...
[Assistant thinking]: ...
[Assistant tool calls]: tool_name({...})
[Tool result]: ...
```

Rules:

- truncate oversized tool results before summarization
- preserve exact tool names and important error fragments
- omit binary payloads and replace them with a short marker

### Update mode

If a previous summary exists, use an update prompt rather than resummarizing the entire historic span from scratch.

The update prompt must instruct the model to:

- preserve earlier facts unless superseded
- move progress items from in-progress to done when appropriate
- keep exact paths, identifiers, and errors
- refresh next steps to reflect current progress

## Execution flow

### Pre-request compaction path

Before `LLMExecutor.run()` sends a provider request:

1. build the candidate prompt message list
2. estimate context usage
3. if compaction is needed, generate and persist a compaction step
4. rebuild prompt messages from the new boundary
5. send the provider request

This check is allowed at the beginning of every LLM run, including runs that occur after a tool round.

This requires a thin orchestration layer in `LLMExecutor` that delegates to the compaction module, for example:

- `_prepare_prompt_messages()`
- compaction service hooks for estimation and compaction

### Overflow recovery path

If the provider returns a context-length or similar overflow error:

1. detect the error as compaction-eligible
2. if automatic compaction is enabled and not yet attempted for this run, compact immediately
3. retry the same node once with rebuilt prompt context
4. if the retry still fails with overflow, return a normal rejection step

The retry guard must prevent infinite compaction loops.

Phase 2 requirement:

- add robust detection of token-limit or context-length errors from Connect responses
- if Connect does not expose enough structured error information yet, extend Connect so the runner can distinguish token-limit failures from generic provider errors
- when such an error is detected, force compaction and retry once even if pre-request estimation did not trigger

## Persistence contract

### What gets persisted

Compaction persistence must include:

- the summary message itself in `messages_by_id`
- the compaction step in `steps_by_id`
- the compaction step id in the node execution active path
- compaction metadata in `Message.state`
- latest compaction metadata in `NodeExecution.state`

### What does not get deleted

Code must not physically remove compacted older steps or messages from `WorkflowExecution`.

Reasons:

- safer rollout
- easier debugging and export
- no persistence migration needed for historical data cleanup

Compaction is logical prompt exclusion, not data deletion.

## Integration points

### `src/vocode/runner/executors/llm/llm.py`

Required changes:

- keep compaction-specific logic minimal and delegated
- extend `_iter_prompt_messages()` so it respects the latest message-owned compaction boundary
- call into the compaction module before final message construction
- teach `build_connect_messages()` to include the summary message and only the kept suffix
- keep preview usage compatible with compacted prompt history

### `src/vocode/runner/runner.py`

Required changes:

- support one retry path after overflow-triggered compaction
- treat `context_compaction` as a normal persisted step event
- ensure `_build_next_input_messages()` ignores compaction bookkeeping and summary system messages for downstream inputs

### `src/vocode/state.py`

Required changes:

- add the new `StepType`
- ensure `LLMNode` owns `CompactionSettings`
- ensure `Message.state` can carry executor-owned message metadata
- ensure new fields remain backward compatible and optional where needed

### UI surfaces

Required changes:

- the UI should mention that compaction happened
- the UI should show compact compaction stats such as estimated tokens before, estimated tokens after, and number of summarized steps or messages
- the UI should not expose the full compacted summary prompt by default
- the compaction step should render as a condensed system/runtime event derived from the summary message state rather than exposing the summary body

### Persistence

Required changes:

- no special codec shape should be required if the new state models are normal Pydantic models
- add tests that serialize and restore workflow executions containing compaction summary messages and compaction step events

## Failure handling

If summarization fails:

- do not mutate prompt history
- do not persist a partial compaction step
- continue without compaction if the original request can still be attempted safely
- if the trigger was a hard overflow retry, emit a rejection step describing that compaction failed

If a summary is generated but cannot be persisted, fail the node run with a normal rejection step.

## Testing plan

Add focused tests in the existing runner and executor suites.

Required coverage:

- prompt reconstruction with no compaction
- prompt reconstruction after one message-owned compaction boundary
- iterative compaction that merges previous summary and newer old turns
- backward scan stops at the latest summary message boundary instead of rescanning unnecessary older history
- cut-point selection that keeps recent messages and preserves tool-round consistency
- persistence round-trip for workflow state containing compaction steps
- overflow-triggered compaction retry happens at most once
- pre-request compaction can happen on tool-round follow-up runs
- downstream node input building ignores compaction bookkeeping steps
- UI-facing compaction metadata renders in condensed form without exposing the full summary text

Recommended test names:

- `tests/executors/test_llm_context_compaction.py`
- `tests/test_runner_context_compaction.py`
- `tests/test_persistence_context_compaction.py`

## Design constraints to preserve

- compaction is iterative, not one-shot
- summaries are structured and continuation-oriented
- recent context stays verbatim
- file paths, tool names, and error text must survive summarization
- compaction metadata is persisted as part of session state
- the summary message is the durable compaction boundary
- compaction metadata is message-owned, not step-owned

Deferred capabilities:

- separate branch summarization flow
- full split-turn dual-summary handling
- file-operation inventories appended to the summary
- extension hooks for custom compaction providers

## Current status

The current implementation uses:

- a message-owned compaction boundary through the summary message
- `CompactionSummaryState` on `Message.state`
- `LLMExecutionState.compaction.latest_compaction_message_id` on `NodeExecution.state`
- a thin persisted `context_compaction` step for event history and UI rendering
- a stable summary wrapper using only the `<summary>` envelope
- condensed UI rendering that shows compaction stats without exposing the raw summary body by default

## TODO

### Message-based usage snapshots and simpler threshold calculation

The current trigger logic still relies on coarse estimation over prompt-visible messages.
Follow-up work should simplify this by adding message-level usage snapshots for assistant messages produced by an LLM request.

Target direction:

1. add optional request-level `llm_usage` to assistant messages produced by the LLM
2. treat the latest usage-bearing assistant message on the active path as the exact prompt-usage snapshot anchor for that request
3. calculate the next trigger threshold from:
   - that latest real prompt usage snapshot
   - plus a small estimate for messages added after that snapshot
4. stop estimating the entire prompt when a recent real usage snapshot exists

Constraints:

- message usage remains request-level, not additive per-message token accounting
- do not sum usage across messages
- use only the latest usage-bearing message as the baseline
- keep overflow-triggered compaction retry unchanged
