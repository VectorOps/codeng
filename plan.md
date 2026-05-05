# MCP integration refactor plan

This plan updates MCP integration so `LLMExecutor` owns `LLMNode` and all node-local MCP configuration, while workflow lifecycle management remains owned by the workflow manager through an explicit runtime contract.

The key ownership rule is that no code outside the LLM executor may scan or depend on the private shape of `LLMNode` to infer MCP requirements. If `LLMNode` changes, only the LLM executor should need to change.

The key startup behavior is that all MCP servers are initialized at workflow start, as they are today, but the initialization path moves behind `LLMExecutor.init()`. The executor reads its own `LLMNode` MCP config, calls MCP service APIs directly to initialize the required servers for the workflow run, and handles MCP tool injection and resolution there.

## Goals

- Move MCP tool enablement from workflow level to LLM node level.
- Make `LLMExecutor` the sole owner of `LLMNode` MCP config and initialization behavior.
- Keep all MCP servers initialized at workflow start, matching current behavior.
- Have `LLMExecutor.init()` call MCP service APIs and perform tool injection and resolution directly.
- Support nested workflows and multiple workflows running in parallel.
- Preserve wildcard matching for MCP tool selectors.
- Auto-inject matched MCP tools for a node without requiring users to manually list each internal tool name.
- If a node uses dynamic MCP discovery, inject only the discovery tool instead of all matched MCP tools.
- Surface MCP startup and connection failures to the TUI.

## Non-goals

- No backward compatibility with workflow-level MCP tool config is required.
- No compatibility shim for old configs is needed.

## Desired architecture

There are three distinct responsibilities.

The LLM executor owns `LLMNode` and node-local MCP configuration:

- read `LLMNode` and its related MCP config directly
- translate node-local MCP intent into MCP service calls
- initialize required MCP servers from `LLMExecutor.init()` at workflow start
- resolve and inject the node-visible MCP tools there
- shield the rest of the system from `LLMNode` shape changes

The workflow manager owns workflow lifecycle only:

- start and stop workflow execution
- keep workflow execution state scoped to a workflow instance
- avoid scanning `LLMNode` or reconstructing node-local MCP behavior from outside the executor

The MCP service owns source/session execution:

- start and stop MCP sessions
- cache tool descriptors
- materialize tool adapters
- emit failures and cache updates upward

## Core contract

The key change is a new ownership contract between the executor, workflow manager, and MCP service.

`LLMExecutor.init()` is the MCP initialization entry point for `LLMNode`-backed execution. It reads the node-local MCP config, calls MCP service APIs, and performs tool injection and resolution for that node.

The manager must not inspect the workflow graph, inspect workflow config, or scan `LLMNode` to infer MCP requirements.

Workflow start remains the MCP startup trigger, but the trigger flows through executor-owned initialization instead of external config scanning. That preserves current startup timing while removing dependencies on private `LLMNode` structure.

This contract must work for:

- root workflows
- nested workflows
- parallel running workflows

The contract must still be instance-scoped, keyed by workflow execution id.

## Runtime contract

There is no new runner event or manager-side MCP requirements protocol.

`LLMExecutor.init()` reads `LLMNode.mcp`, initializes the required MCP sources directly through the MCP service, refreshes tool cache as needed, and wires the node-visible MCP tools there.

The manager and runner do not scan node config and do not receive a separate MCP requirement event.

## Lifecycle behavior

### Workflow startup

1. Manager starts workflow runner.
2. Manager records workflow lifecycle state only.
3. No workflow-level MCP scanning happens.
4. LLM executor initializes.
5. `LLMExecutor.init()` starts the MCP sources needed by that node for the current workflow run.
6. Tool caches are refreshed for newly started sources.
7. The executor resolves and injects the node-visible MCP tools before execution continues.

Important startup invariant:

- if no LLM node reports MCP requirements, no workflow-scoped MCP session is started
- runner workflow-start status alone must not cause MCP activity

### Workflow shutdown

1. Runner finishes or stops.
2. Workflow-scoped MCP sessions started for that workflow run are released.
3. MCP service closes workflow-scoped sessions that are no longer needed.

### Nested workflows

Each nested workflow has its own workflow execution id and reports requirements independently.

Manager behavior:

- parent workflow requirements remain associated with parent workflow execution id
- child workflow requirements remain associated with child workflow execution id
- shared MCP sources may remain running if referenced by either instance
- shutdown of one workflow instance must not tear down sessions still needed by another instance

## MCP ownership model

Move MCP configuration next to `LLMNode.tools`.

Recommended model addition in `src/vocode/runner/executors/llm/models.py`:

```python
class LLMNodeMCPSettings(BaseVarModel):
    enabled: bool = True
    resolution_mode: Literal["inject", "discovery"] = "inject"
    tools: List[MCPToolSelector] = Field(default_factory=list)
    disabled_tools: List[MCPToolSelector] = Field(default_factory=list)
    hide_listed_tools: bool = False
```

And then:

```python
class LLMNode(models.Node):
    ...
    mcp: Optional[LLMNodeMCPSettings] = None
```

## Tool resolution rules

Resolution is node-local.

For a given LLM node:

1. Read `node.mcp`.
2. Resolve allowed MCP selectors.
3. Apply disabled selectors.
4. Preserve wildcard matching.
5. Build the node-visible MCP tool set.

Injection rules:

- `resolution_mode == inject`
  - inject all matched MCP tool adapters for that node
  - if prompt/resource helper tools are applicable, inject them too
  - if `hide_listed_tools` is true, do not inject concrete MCP tool adapters

- `resolution_mode == discovery`
  - inject only `mcp_discovery`
  - do not inject concrete MCP adapters
  - discovery results must still respect node selectors and disabled selectors

Important rule:

`mcp_discovery` must never be duplicated. If dynamic discovery mode is enabled, only a single discovery tool is injected for that node.

## Startup ownership

The desired startup path keeps current behavior where all MCP servers are initialized at workflow start, but it moves that responsibility behind the LLM executor boundary.

That means:

- no graph-wide static inspection for MCP tool selection
- no external scanning of private `LLMNode` fields
- no workflow-level MCP selector ownership
- workflow start still causes MCP initialization, like today
- `LLMExecutor.init()` calls MCP service APIs for the current node and workflow instance
- tool injection and tool resolution happen inside the LLM executor

This removes the dependency web around `LLMNode` and keeps ownership aligned with the code that already owns the model.

## Required code changes

### 1. Settings and models

Files:

- `src/vocode/settings/models.py`
- `src/vocode/runner/executors/llm/models.py`

Changes:

- remove `WorkflowConfig.mcp`
- move MCP selector config into a new LLM-node-local model
- keep project-level `Settings.mcp` for source registry, protocol, discovery defaults, and auth config

There are no workflow-level MCP definitions after this change.

Important note:

- project settings still define available MCP sources
- node runtime behavior determines which of those sources are actually started

### 3. LLM executor initialization

Files:

- `src/vocode/runner/executors/llm/llm.py`
- `src/vocode/runner/executors/llm/helpers.py`

Changes:

- make `LLMExecutor.init()` the place that reads `LLMNode.mcp`
- call MCP service APIs from `LLMExecutor.init()` during workflow start
- initialize all required MCP servers there, matching current startup timing
- resolve and inject MCP-backed tools there before execution continues
- do not require any external component to understand `LLMNode` internals
- completion indicator: a workflow with node-level MCP config starts its required MCP session during executor init, without any new runner event

Recommended implementation detail:

- keep the runner workflow-start flow intact
- hand executor-owned MCP initialization enough workflow-instance context to initialize the right servers
- keep the executor as the only layer that translates `LLMNode` config into MCP service operations

### 5. MCP service lifecycle refactor

Files:

- `src/vocode/mcp/service.py`

Changes:

- stop using `WorkflowConfig.mcp`
- add workflow-instance reference tracking
- recommended structure:

```python
source_name -> set[workflow_execution_id]
workflow_execution_id -> set[source_name]
```

- add methods similar to:
  - `apply_workflow_requirements(workflow_execution_id, source_names)`
  - `clear_workflow_requirements(workflow_execution_id)`
- reconcile session start and shutdown based on active workflow-instance references
- keep project-scoped sessions independent

### 5. Node-local tool materialization

Files:

- `src/vocode/mcp/tool_resolution.py`
- `src/vocode/mcp/registry.py`
- `src/vocode/mcp/tool_materialization.py`
- `src/vocode/runner/executors/llm/llm.py`

Changes:

- replace workflow-based enablement helpers with node-based helpers
- keep tool resolution owned by the MCP manager/service layer for a specific LLM node, not by the LLM executor and not by the whole project tool registry
- keep wildcard matching intact
- preserve disabled selector precedence

### 6. Global project tool registry cleanup

Files:

- `src/vocode/project.py`

Changes:

- stop treating node-scoped MCP adapters as globally registered project tools
- keep only static tools globally available
- allow LLM execution to compose node-local MCP tools dynamically

This is important because different LLM nodes may expose different MCP tool subsets.

### 7. TUI failure surfacing

Files:

- `src/vocode/mcp/service.py`
- `src/vocode/project.py`
- `src/vocode/manager/server.py`
- optionally `src/vocode/manager/proto.py`

Changes:

- add a notification callback from MCP service upward for lifecycle failures
- on session start/connect failure, send a user-visible packet to the TUI

Minimal approach:

- reuse `TextMessagePacket`

Optional stronger approach:

- add a dedicated manager packet for system notifications with severity

For the first implementation, reuse `TextMessagePacket` unless a stronger UX requirement appears.

## Recommended implementation sequence

### Step 1: move MCP config to LLM nodes

Developer instructions:

- [ ] Remove `mcp` from `WorkflowConfig`.
- [ ] Add a new LLM-node-local MCP settings model.
- [ ] Add `mcp: Optional[...] = None` to `LLMNode`.
- [ ] Remove or rewrite tests that currently expect workflow-level MCP config.

Verification:

- settings tests pass with node-level MCP config only
- no workflow-level MCP references remain in config models

### Step 2: wire MCP startup directly into `LLMExecutor.init()`

Developer instructions:

- [ ] Read `LLMNode.mcp` directly inside `LLMExecutor.init()`.
- [ ] Start the required MCP sources there through the MCP service.
- [ ] Refresh tool cache for sources newly started for that workflow run.
- [ ] Build the node-local MCP tool set there without adding any new runner event.

Completion indicator:

- a workflow with one LLM node starts its required MCP sources during executor init
- workflow start alone does not start workflow-scoped MCP sessions when no node needs them
- repeated runs do not leak MCP session state across workflow run ids

### Step 3: refactor MCP service lifecycle tracking

Developer instructions:

- [ ] Replace workflow-name-based active state with workflow-execution-id-based references.
- [ ] Add apply and clear methods for workflow-instance source requirements.
- [ ] Reconcile start and shutdown based on active references.
- [ ] Keep project-scoped sessions unchanged.

Verification:

- if two workflow instances require the same source, closing one instance keeps the session alive
- closing the last referencing workflow instance shuts the session down

### Step 4: move MCP tool injection to node-local LLM execution

Developer instructions:

- [ ] Replace workflow-based enablement helpers with node-based helpers.
- [ ] Build the effective MCP tool set for the current LLM node only.
- [ ] Auto-inject matched concrete tools in inject mode.
- [ ] Inject only `mcp_discovery` in discovery mode.
- [ ] Keep prompt/resource helper behavior aligned with node-local MCP selection.

Completion indicator:

- one node can see a source/tool subset that another node cannot
- discovery mode injects only the discovery tool
- wildcard selectors still work

### Step 5: stop leaking node-scoped MCP tools into `project.tools`

Developer instructions:

- [ ] Keep global static tools in `project.tools`.
- [ ] Stop materializing node-specific MCP adapters into the global registry.
- [ ] Let the LLM executor compose node-local MCP tools when building connect tool specs.

Completion indicator:

- MCP tool availability differs correctly across nodes in the same workflow
- non-MCP tools still behave exactly as before

### Step 6: surface MCP failures to the TUI

Developer instructions:

- [ ] Add an MCP service notification callback for start/connect/reconcile failures.
- [ ] Register that callback from the manager/UI server path.
- [ ] Send visible user-facing messages to the TUI when MCP startup or connection fails.
- [ ] Add tests proving the message is emitted.

Completion indicator:

- failing stdio startup produces a visible UI message
- failing external connection produces a visible UI message
- messages identify the source name and failure reason

### Step 7: add and update tests

Developer instructions:

- [ ] Replace workflow-level MCP config tests with node-level config tests.
- [ ] Add tests for executor-owned node-level MCP startup and wiring.
- [ ] Add manager aggregation tests for nested workflows and parallel workflow instances.
- [ ] Add node-local tool injection tests.
- [ ] Add TUI notification tests for MCP failures.

Completion indicator:

- targeted test suite passes

### Step 8: format and validate

Developer instructions:

- [ ] Run `uv run black` on changed files.
- [ ] Run targeted pytest modules first.
- [ ] If green, run broader MCP, manager, and LLM executor test groups.

Suggested test commands:

```bash
uv run pytest tests/mcp tests/tools/test_mcp_discovery_tool.py tests/manager tests/runner/executors/llm
```

## Concrete decisions to preserve during implementation

- No workflow-level MCP tool selection config remains.
- No workflow-level MCP definitions remain.
- `LLMExecutor` owns `LLMNode` and all related MCP config.
- Workflow start still initializes MCP servers, matching current behavior.
- `LLMExecutor.init()` is the initialization entry point and calls MCP service APIs directly.
- Tool injection and tool resolution happen inside the LLM executor.
- No external component scans private `LLMNode` shape.
- Workflow execution id is the isolation key.
- Nested and parallel workflows must both work.
- Wildcard selectors remain supported.
- Discovery mode injects only one discovery tool.
- MCP failures must be visible in the TUI.

## Open implementation preference

The only optional design choice left is the user-facing packet shape for MCP failures.

Recommended first pass:

- reuse `TextMessagePacket`

Upgrade later only if needed for severity-specific UI rendering.
