# Refactor plan: normalized history state, branching history, and centralized history management

## Scope

This document analyzes the current history/session model and proposes a phased refactor for three related projects:

1. Normalize the workflow execution state while preserving current single-branch behavior.
2. Extend the state model to support branching conversation history.
3. Introduce a dedicated history manager that owns history mutations and returns UI-friendly diffs.

The plan is designed to minimize disruption to the current code base and keep compatibility layers in place while the internal model evolves.

## Current state analysis

### Core runtime model

The in-memory history model is centered on `src/vocode/state.py`:

- `WorkflowExecution`
  - owns `node_executions: Dict[UUID, NodeExecution]`
  - owns `steps: List[Step]`
  - tracks aggregate fields like `llm_usage`, `last_step_llm_usage`, `last_user_input_at`
- `NodeExecution`
  - owns `steps: List[Step]`
  - links to prior execution for the same node via `previous: Optional[NodeExecution]`
- `Step`
  - points back to `execution: NodeExecution`
  - optionally owns `message: Message`

This model is convenient to use but has two structural properties that matter for the refactor:

- it is cyclic
- it duplicates ownership of the same step in multiple places

Examples:

- `WorkflowExecution.steps -> Step.execution -> NodeExecution.steps -> Step`
- `NodeExecution.previous -> NodeExecution`

Those cycles are the main reason persistence currently requires DTO conversion.

### History mutation today

History mutation logic is spread across several layers.

`src/vocode/runner/runner.py`

- `_persist_step()` appends or updates a step in both `WorkflowExecution.steps` and `NodeExecution.steps`
- `_compute_resume_state()` mutates history by trimming incomplete or trailing steps to reestablish a resumable linear state

`src/vocode/manager/base.py`

- `edit_history_with_text()` finds the last user `INPUT_MESSAGE`
- removes all later steps from the current linear log
- mutates the target message text in place
- resumes the stopped runner

`src/vocode/manager/server.py`

- `_on_user_input_packet()` decides whether input is prompt response or history edit
- emits `StepDeletedPacket` when later linear history is removed
- re-emits the edited step so the UI can upsert it

### Persistence today

Persistence is implemented in:

- `src/vocode/persistence/dto.py`
- `src/vocode/persistence/codec.py`
- `src/vocode/persistence/state_manager.py`

The DTO layer exists to flatten runtime references into id-based payloads:

- `StepDTO.execution_id`
- `NodeExecutionDTO.previous_id`

`codec.py` converts runtime models to DTOs, serializes DTO JSON, and reconstructs runtime links during load.

### UI and protocol today

The UI protocol is linear and active-timeline-oriented.

- `RunnerReqPacket` carries one step update
- `StepDeletedPacket` tells the UI to remove steps from the visible timeline
- the TUI only renders one linear visible history at a time

Important distinction:

- `src/vocode/tui/history.py` is only input-buffer history for command-line navigation
- it is not the canonical session/conversation history model

That means project 3 should not extend the TUI history helper. It should introduce a server-side/session-side history manager for workflow execution history.

## Architectural decision: do not introduce a new top-level Assignment root

No existing `Assignment` model was found in the repository. The nearest shared owner concepts are:

- `WorkflowExecution` for one workflow run
- `Project.project_state` for project-level session configuration such as auto-approve rules

Introducing a brand-new top-level root object like `Assignment` would create broad churn because the current code already passes `WorkflowExecution` through:

- runner APIs
- manager APIs
- protocol payloads
- tests
- persistence

### Recommendation

Keep `WorkflowExecution` as the canonical root object and evolve it into the owner of a normalized store.

Use one of these shapes:

1. Preferred transition shape
   - extend `WorkflowExecution` with normalized fields directly
   - add compatibility helpers and computed accessors so existing code still feels object-oriented

2. Internal separation shape
   - introduce a nested model such as `ExecutionStore` or `HistoryStore`
   - store it as a field on `WorkflowExecution`
   - keep `WorkflowExecution` as the public root and compatibility facade

I do not recommend inventing a separate top-level `Assignment` abstraction for this refactor. It would add another ownership layer without reducing migration cost.

## Design principles for all three projects

- `WorkflowExecution` remains the public root model.
- Canonical state becomes normalized and id-based.
- Runtime convenience is restored through helper methods and attached shared storage references.
- Serialized state must be acyclic and directly JSON-serializable.
- Existing code should continue to work against compatibility properties during migration.
- The UI may continue to consume a linear projection even after the underlying model becomes a tree.

## Project 1: normalize the data model while keeping a single linear history

Status in current codebase: complete.

### Goals

- Remove cyclic persistence requirements.
- Keep behavior effectively unchanged: one active linear history.
- Make related-object access transparent enough that most existing code can be migrated incrementally.
- Remove DTO conversion and persist the canonical state directly.

### Target model shape

Evolve `WorkflowExecution` into an id-based owner.

Suggested fields:

- `messages_by_id: dict[UUID, MessageRecord]`
- `steps_by_id: dict[UUID, StepRecord]`
- `node_executions_by_id: dict[UUID, NodeExecutionRecord]`
- `step_order: list[UUID]`
- `node_execution_order: list[UUID]`

Suggested record changes:

- `Step.execution_id` replaces serialized `Step.execution`
- `Step.message_id` replaces serialized nested `message`
- `NodeExecution.previous_execution_id` replaces serialized `previous`
- `NodeExecution.step_ids: list[UUID]`
- `NodeExecution.input_message_ids: list[UUID]`

Compatibility helpers may expose object-oriented access:

- `step.execution`
- `step.message`
- `node_execution.previous`
- `node_execution.steps`
- `node_execution.input_messages`
- `workflow.steps`
- `workflow.node_executions`

These should be helper properties backed by shared storage, not serialized ownership.

### Shared storage access

To keep old calling code readable, attach a shared owner reference to records.

Preferred approach:

- `WorkflowExecution` is the owner/store
- each `Step`, `Message`, and `NodeExecution` gets a private runtime-only owner reference
- owner reference is attached after creation and after deserialization
- owner reference is excluded from serialization

This preserves ergonomics without reintroducing persisted cycles.

Possible implementation patterns:

- `PrivateAttr` on Pydantic models
- `attach_owner(workflow_execution)` helper invoked after creation/load
- model factory methods on `WorkflowExecution` that create records already attached to the owner

### Why this is better than nested ownership

- one canonical copy of each object
- direct serialization becomes possible
- future branching becomes a metadata problem rather than a mutation-of-lists problem
- existing code can still use convenience properties

### Step-by-step implementation plan

#### Phase 1.1: introduce normalized fields without changing behavior

1. Add normalized id-based fields to `WorkflowExecution`, `Step`, and `NodeExecution`.
2. Keep current list/dict fields as compatibility fields or computed properties.
3. Introduce helper methods on `WorkflowExecution`:
   - `get_step(step_id)`
   - `get_message(message_id)`
   - `get_node_execution(execution_id)`
   - `iter_steps()`
   - `iter_node_steps(execution_id)`
   - `attach_runtime_refs()`
4. Update constructors and helper factories so newly created objects always get owner references.

#### Phase 1.2: migrate writer code to canonical normalized storage

1. Refactor `Runner._persist_step()` so it writes only through canonical store APIs.
2. Refactor `WorkflowExecution.delete_steps()` and related mutation helpers to operate on ids and indexes.
3. Keep compatibility properties returning the same object instances so existing consumers do not break.
4. Update resume logic in `Runner._compute_resume_state()` to consult canonical ordering instead of nested mutable lists.

#### Phase 1.3: migrate readers to helper APIs

1. Update code paths that iterate `execution.steps` or `node_execution.steps` to use helper methods where helpful.
2. Keep compatibility properties for low-risk call sites.
3. Add tests proving helper properties remain coherent with canonical store.

#### Phase 1.4: replace DTO-based persistence

1. Remove the need for `dto.py` by making `WorkflowExecution.model_dump()` persistence-safe.
2. Keep runtime-only owner references excluded from serialization.
3. Replace `codec.to_dto()` and `codec.from_dto()` with:
   - direct `model_dump_json()`
   - direct `model_validate()`
   - `attach_runtime_refs()` after load
4. Keep `codec.py` as the persistence boundary for gzip and path management, but make it trivial.
5. Either remove `dto.py` entirely or keep a short-lived compatibility wrapper during migration.

### Tests to add or update

- direct persistence roundtrip without DTOs
- identity coherence of helper properties after load
- deletion and trimming behavior still match current linear semantics
- `_compute_resume_state()` still resumes the same node/step as before

### Risks

- compatibility properties may hide accidental O(n) lookups if implemented carelessly
- mixed old/new fields can drift if both are writable
- owner attachment after load must be mandatory and well tested

### Recommendation for project 1

Use `WorkflowExecution` as the canonical root and normalized owner. If separation is desired, embed a new `ExecutionStore` model inside `WorkflowExecution`, but do not create a brand-new public root like `Assignment`.

## Project 2: extend the state model to be a tree

### Goals

- Preserve all history after edits.
- Allow branching from earlier user input instead of destructive truncation.
- Keep existing linear consumers working through an active-branch projection.
- Keep the UI protocol compatible at first by simulating removals from the visible path.

### Target tree model

Once project 1 normalization is complete, branching can be modeled with links and branch metadata.

Suggested additions:

- `Step.parent_step_id: Optional[UUID]`
- `Step.child_step_ids: list[UUID]`
- `WorkflowExecution.branches_by_id: dict[UUID, BranchRecord]`
- `WorkflowExecution.active_branch_id: UUID`

Suggested branch record fields:

- `id`
- `head_step_id`
- `base_step_id`
- `created_at`
- `label: Optional[str]`
- `is_active`

For a compatibility-first rollout, continue exposing:

- `workflow.steps` as the active branch's linear projection
- `node_execution.steps` as the active branch's node-local projection

### Branching semantics

Editing a previous user input should no longer mutate and truncate the existing linear tail.

Instead:

1. identify the step being edited
2. create a new replacement input step on a new branch
3. set the new branch as active
4. resume execution from the new branch head
5. keep the old branch intact

Prefer creating a new step rather than mutating the original step in place. Immutable historical nodes make branching and diffing much simpler.

### Protocol strategy

The request asks for protocol extension and also for temporary UI compatibility. The best approach is two-stage.

#### Stage 2A: keep current UI protocol behavior

Internally branch, but keep sending the current UI a linear diff against the active visible path.

Server behavior:

- compute `old_active_path_step_ids`
- compute `new_active_path_step_ids`
- emit removals for steps that left the visible path
- emit upserts for newly visible steps

For compatibility, `StepDeletedPacket` can remain in place temporarily even though the steps are not actually deleted from storage. It is acting as a view-removal packet.

#### Stage 2B: add additive branch-aware protocol

Add new optional packets for future branch-aware UIs, for example:

- `BranchChangedPacket`
- `BranchListPacket`
- `HistoryViewDiffPacket`

Do not require the TUI to understand them initially.

### Transparent integration with existing code

To minimize churn:

- keep `Runner` operating on an active linear projection
- keep `BaseManager` editing APIs returning a visible-path diff result
- keep UI rendering a single path
- move tree awareness into the canonical history manager and projection helpers

### Step-by-step implementation plan

#### Phase 2.1: add tree metadata while still maintaining one active path

1. Add `parent_step_id` and `child_step_ids` to steps.
2. Add branch metadata to `WorkflowExecution`.
3. Initialize a default branch for all existing executions during load/creation.
4. Use `WorkflowExecution.step_ids` as the canonical flat visible-path view.
5. Provide helper APIs where the flat visible-path view is not sufficient:
   - `switch_branch(branch_id)`
   - `fork_from_step(step_id, replacement_message)`

Notes:

- `get_active_step_ids()` and `get_active_steps()` are not needed because `WorkflowExecution.step_ids` already provides the active visible history projection.
- `find_lowest_common_ancestor(old_head, new_head)` is not required for the current compatibility rollout.

#### Phase 2.2: refactor edit-history behavior to fork instead of truncate

1. Replace `BaseManager.edit_history_with_text()` destructive behavior with a call into the new history manager.
2. Return a result object containing the visible-path mutation information needed by the compatibility layer.
3. Keep manager/server behavior compatible by translating that result into current packets.

Notes:

- Explicit resume cursor information is not needed.

#### Phase 2.3: project node executions onto the active branch

1. Decide whether `NodeExecution` remains a stored record or becomes mostly a derived view.
2. Recommended transition:
   - keep `NodeExecution` records
   - keep branch-safe ordering by using stored `step_ids`
   - do not require `node_execution.steps` to become a separately derived active-branch projection yet
3. Update `iter_execution_messages()` and resume logic to follow active-path projections.

Notes:

- This is expected for the current rollout. `WorkflowExecution.step_ids` is the primary active visible-path projection.

#### Phase 2.4: compatibility diff layer for the current UI

1. Keep visible-path diffing centralized in the history manager.
2. Continue emitting `StepDeletedPacket` for steps leaving the active view.
3. Continue sending `RunnerReqPacket` upserts for newly visible or updated steps.
4. Do not expose the full tree to the TUI yet.

Notes:

- The centralized diff result is represented by `HistoryMutationResult`.
- Removed visible items are returned as step ids.
- Added or changed visible items are returned as upserted steps.

#### Phase 2.5: additive branch-aware protocol

1. Add branch packets behind capability-gated or optional handling.
2. Update tests for packet shapes and diff semantics.
3. Keep the old UI working unchanged.

### Tests to add or update

- editing last user input creates a new branch and preserves the old branch
- switching branches recomputes visible-path removals/additions correctly
- lowest common ancestor diff behaves correctly
- current TUI still renders only the active path and hides removed visible nodes

### Risks

- if historical steps remain mutable, branch identities become ambiguous
- projecting node executions from tree state may be subtly wrong if ordering rules are not explicit
- resume logic is the most sensitive part and must be guarded by tests

## Project 3: introduce a centralized history manager

### Goals

- Move workflow history mutation logic out of UI/server glue and scattered runner helpers.
- Provide explicit APIs for history operations.
- Return rich mutation results so UI updates are computed once in one place.
- Make branching and persistence logic share the same canonical operations.

### Why this is needed

Today, history mutation is split across:

- `Runner._persist_step()`
- `Runner._compute_resume_state()`
- `WorkflowExecution.delete_steps()`
- `BaseManager.edit_history_with_text()`
- `UIServer._on_user_input_packet()`

That distribution makes branching and persistence harder because there is no single owner of history rules.

### Recommended new component

Introduce a server-side history module, for example:

- `src/vocode/history/manager.py`
- `src/vocode/history/models.py`

The history manager should operate on `WorkflowExecution` and own all structural history mutations.

### Suggested API surface

Read/query APIs:

- `get_active_steps(execution)`
- `get_visible_step_ids(execution)`
- `get_last_user_input_step(execution)`
- `get_branch(execution, branch_id)`
- `build_resume_cursor(execution)`

Mutation APIs:

- `append_step(execution, step)`
- `upsert_step(execution, step)`
- `delete_visible_tail_from_step(...)` for compatibility-only linear mode
- `edit_user_input(execution, step_id, new_text)`
- `fork_from_step(execution, step_id, replacement_message)`
- `switch_branch(execution, branch_id)`
- `trim_orphaned_records(execution)`

Diff/result APIs:

- `compute_view_diff(before_visible_ids, after_visible_ids)`
- `build_ui_update_result(...)`

### Suggested result model

Create a structured mutation result model instead of ad hoc tuples and lists.

Example fields:

- `changed: bool`
- `active_branch_id: Optional[UUID]`
- `resume_step_id: Optional[UUID]`
- `removed_visible_step_ids: list[str]`
- `upserted_step_ids: list[str]`
- `upserted_steps: list[state.Step]`
- `created_branch_id: Optional[UUID]`

This result becomes the contract between history mutation code and UI/protocol translation code.

### Step-by-step implementation plan

#### Phase 3.1: introduce manager in linear mode only

1. Create history manager and route `Runner._persist_step()` through it.
2. Move `WorkflowExecution.delete_steps()` semantics into history manager methods.
3. Move `BaseManager.edit_history_with_text()` logic into history manager.
4. Keep server behavior unchanged by adapting history result to current packets.

#### Phase 3.2: make manager the only mutation path

1. Remove direct structural mutations from runner and manager layers.
2. Restrict low-level models to simple helpers, not policy-heavy mutation methods.
3. Ensure persistence, branching, and UI diff generation all use the same manager.

#### Phase 3.3: extend manager for tree operations

1. Add branch creation and branch switching APIs.
2. Add visible-path diff generation.
3. Use the manager from both edit-history and any future branch-selection commands.

### Tests to add or update

- manager append/upsert semantics
- linear edit semantics through manager
- branching edit semantics through manager
- server packet translation from manager results

## Recommended execution order across the three projects

The safest order is:

1. Project 1, phases 1.1 to 1.3
   - normalize state shape while preserving current semantics
2. Project 3, phases 3.1 to 3.2
   - centralize mutations before adding branching
3. Project 1, phase 1.4
   - switch persistence to direct serialization
4. Project 2, phases 2.1 to 2.4
   - add tree model and compatibility visible-path diffs
5. Project 2, phase 2.5
   - optional future branch-aware protocol
6. Project 3, phase 3.3
   - finalize branching APIs in the history manager

This order keeps the hardest logic change, branching, until after the state shape and mutation ownership are already clean.

## Concrete file-by-file impact forecast

### High-impact files

- `src/vocode/state.py`
  - canonical model refactor
- `src/vocode/runner/runner.py`
  - step persistence and resume logic migration
- `src/vocode/manager/base.py`
  - history edit API migration
- `src/vocode/manager/server.py`
  - translate history-manager results into UI packets
- `src/vocode/persistence/codec.py`
  - simplify to direct serialization
- `src/vocode/persistence/state_manager.py`
  - likely minor changes only

### New files likely needed

- `src/vocode/history/manager.py`
- `src/vocode/history/models.py`
- possibly `src/vocode/history/__init__.py`

### Test files that should expand

- `tests/manager/test_manager_events.py`
- `tests/manager/test_ui_server.py`
- `tests/test_persistence.py`
- new focused tests for history manager behavior

## Migration constraints and compatibility rules

- Do not break `RunnerReqPacket.step` payload shape during the compatibility phases.
- Keep `StepDeletedPacket` working until the current UI is updated, even if its semantic meaning becomes view-removal rather than storage deletion.
- Keep `WorkflowExecution` as the root object passed through the runner and protocol stack.
- Prefer additive fields and helper methods over immediate removal of legacy access patterns.

## Summary recommendations

- Use `WorkflowExecution` as the canonical root. Do not add a new public `Assignment` root.
- Normalize state around ids and canonical maps, then rebuild convenience through runtime-only owner references and helper properties.
- Remove DTO conversion after the canonical model becomes directly serializable.
- Introduce a dedicated history manager before adding tree semantics.
- Add branching internally first, while continuing to feed the current UI a linear visible-path diff.
- Treat current deletion packets as compatibility view diffs during the transition.