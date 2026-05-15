# Message-based context compaction

This page defines a simpler context compaction design for long-running LLM sessions.
Use it as the implementation contract for replacing the current boundary-reconstruction approach.

## Quick reference

Message-based compaction keeps compaction as an explicit runtime event, but makes prompt reconstruction depend on the latest compaction message boundary instead of replaying exclusion metadata.

The core rule is:

- compaction writes a technical summary message on the current step
- prompt collection walks backward to the latest compaction message
- the next provider request includes that compaction summary plus all later prompt-visible messages
- the TUI does not render the compaction summary as a normal chat message

This design assumes backward compatibility is not required.

## Problem this design solves

The current compaction model stores a separate compaction step and then reconstructs the effective prompt by excluding older steps and messages using persisted metadata.

That creates unnecessary complexity in four places:

- prompt collection must replay compaction metadata
- token estimation can accidentally anchor to stale usage snapshots
- repeated compaction must merge overlapping hidden history correctly
- UI and history logic need to distinguish real visible transcript from hidden prompt state

For a single long-lived looped LLM node, this is more complex than needed.
What the runtime really needs is a durable prompt boundary inside the visible message stream.

## Design summary

Compaction remains an explicit technical event, but the summary is represented as a message attached to the current step rather than as a separate compaction step that owns prompt reconstruction.

The latest compaction message becomes the start of the effective prompt transcript.

Prompt reconstruction contract:

1. collect prompt-visible messages in visible history order
2. walk backward to find the latest compaction summary message
3. if found, keep that message and all later prompt-visible messages
4. if not found, keep the full prompt-visible history

No replay of compacted step ids or compacted message ids is needed.

## Goals

- simplify prompt reconstruction
- make repeated compaction stable and easy to reason about
- keep compaction as a durable runtime event for logs and debugging
- keep history editing semantics explicit
- hide technical compaction messages from normal TUI transcript rendering

## Non-goals

- preserving the old compaction persistence shape
- keeping compatibility with existing compaction step state
- exposing compaction summaries as user-visible transcript items
- introducing a second hidden prompt store separate from workflow history

## Data model

### New message kind

Add an explicit technical message discriminator for compaction summaries.

Recommended shape:

```python
class MessageKind(str, Enum):
    NORMAL = "normal"
    CONTEXT_COMPACTION = "context_compaction"
```

Add an optional field on `state.Message`:

```python
kind: Optional[MessageKind] = None
```

Rules:

- normal user and assistant messages keep `kind=None` or `kind=NORMAL`
- compaction summaries use `kind=CONTEXT_COMPACTION`
- the compaction message still uses role `system`

The message kind, not free-form text parsing, is the compaction boundary marker.

### Step ownership

Compaction should generate a message for the current step instead of creating a new dedicated compaction step.

Recommended behavior:

- the runtime creates or updates a technical step owned by the current node execution
- that step carries a single `system` message with `kind=CONTEXT_COMPACTION`
- the step exists for persistence, logs, and audit
- prompt reconstruction uses the message marker, not the step type

This step is a technical runtime artifact.
It should not be treated as a normal conversational message by the UI.

### Suggested state

Keep compaction metadata minimal and audit-oriented.

Recommended summary state:

```python
class CompactionSummaryState(BaseModel):
    tokens_before_estimate: int
    tokens_after_estimate: Optional[int] = None
    summary_input_tokens: Optional[int] = None
    summary_output_tokens: Optional[int] = None
    trigger_threshold_ratio: float
    keep_recent_ratio: float
    summary_version: str = "v2"
```

Recommended node execution state:

```python
class LLMExecutionCompactionState(BaseModel):
    latest_compaction_message_id: Optional[UUID] = None
    compaction_count: int = 0
    last_compaction_tokens_before: Optional[int] = None
```

Do not store compacted step ids or compacted message ids for prompt reconstruction.

## Prompt reconstruction

### Source of truth

The effective provider prompt is the visible message suffix after the latest compaction summary message.

Algorithm:

1. gather prompt-visible messages in active visible history order
2. scan backward for the latest message where `message.kind == CONTEXT_COMPACTION`
3. if found, return that message and all later prompt-visible messages
4. otherwise, return the full prompt-visible message list

This is the only compaction-aware prompt reconstruction rule.

### Prompt-visible message types

Only prompt-visible messages participate in the suffix scan.

Expected prompt-visible categories:

- user input messages
- assistant output messages
- technical compaction summary message

Standalone bookkeeping steps remain out of prompt reconstruction unless they already own a prompt-visible message.

### Repeated compaction

Repeated compaction is naturally defined by the latest boundary marker.

When a new compaction summary is created:

- it summarizes the currently visible prefix before the chosen cut point
- it becomes the newest compaction boundary marker
- older compaction messages may remain persisted for audit but are outside the active prompt suffix

Only the latest compaction message matters for prompt collection.

## Compaction flow

### Trigger point

Compaction still runs before the next provider request.
It does not require node completion.

This supports a single long-lived looped LLM node with `reset_policy: keep` as long as there are completed prior prompt-visible messages to compact.

### Summary creation

When compaction triggers:

1. collect the current prompt-visible message list
2. compute the cut point
3. summarize the prefix before the cut
4. create one technical compaction message with `kind=CONTEXT_COMPACTION`
5. attach that message to the current technical step
6. rebuild the effective prompt as:
   - compaction summary message
   - retained suffix after the cut point

### Summary envelope

The summary message text should remain wrapped and structured.

Recommended format:

```text
The conversation history before this point was compacted into the following summary:

<summary>
...
</summary>
```

The exact markdown sections should stay continuation-oriented and stable.

## History editing semantics

History editing remains explicit.

Required behavior:

- if the edited message is after the latest compaction marker, normal branching behavior applies and the compaction marker stays in history
- if the edited message is before the latest compaction marker, the branch drops all later messages, including the compaction marker

This matches the desired rule:

- edit after compaction marker -> compaction remains valid
- edit before compaction marker -> compaction becomes invalid for that branch and disappears from the active suffix

This should already align with branch-from-message semantics as long as the compaction message is treated as a technical message in visible history.

## TUI behavior

The TUI should not show compaction summary messages as normal transcript entries.

Required behavior:

- compaction messages are hidden from the main message timeline
- optional compact runtime indication may still be shown, for example a small technical event or status notice
- the full compaction summary text is not rendered in the normal chat transcript

This keeps the session readable while preserving the summary for prompt reconstruction and persistence.

## Logging and metrics

Compaction logs should report two distinct values:

- real summary-generation request usage, when available
- estimated next prompt size after compaction

Recommended fields:

- `source_tokens`
- `final_tokens`
- `summary_input_tokens`
- `summary_output_tokens`
- `remaining_messages_count`

The compaction summary request usage must not be added to the next-prompt estimate.
They describe different requests.

## Advantages of this design

### Simpler prompt logic

Prompt collection becomes a suffix scan instead of a replay-and-exclude algorithm.

### Safer repeated compaction

The latest marker always wins.
No overlapping exclusion metadata must be merged.

### Better token accounting

The effective prompt is exactly the suffix after the latest marker, so estimates can be computed directly from the actual prompt-visible messages.

### Better fit for long-lived looped nodes

The model operates on message history boundaries, not node-completion boundaries.

## Trade-offs

### Less implicit information about what was compacted

If the runtime needs a precise compacted range later, that range is no longer reconstructed from exclusion metadata.
If needed, store audit metadata separately.

### Stronger dependency on visible message ordering

This design assumes active visible message ordering is the source of truth.
All prompt building must use one canonical collector.

### Technical-message handling must be consistent

If any UI or history code accidentally treats compaction messages as normal user-visible transcript messages, the abstraction breaks.

## Estimated implementation complexity

Because backward compatibility is not required, this is a medium-sized change rather than a large migration.

Expected work:

1. add message kind support and compaction-summary marker semantics
2. simplify prompt collection to latest-marker suffix logic
3. refactor compaction flow to emit a message on the current technical step
4. hide compaction messages in TUI rendering
5. update history-edit tests and compaction tests

Estimated complexity: medium.

This is likely simpler than continuing to evolve the current exclusion-list design.

## Implementation notes

- keep a single canonical helper for collecting prompt-visible messages
- make all token estimation operate on that collected suffix
- avoid storing replay metadata for prompt reconstruction
- keep compaction step state audit-oriented only
- keep compaction message role as `system`, but require explicit `kind=CONTEXT_COMPACTION`

## Open decisions

The implementation should resolve these before coding starts:

1. whether compaction uses a dedicated technical step type or reuses an existing technical step category
2. whether older compaction messages remain stored on the active branch or are bypassed implicitly by the latest-marker scan only
3. the exact TUI indicator for hidden compaction events
4. whether history exports should include hidden compaction messages by default