# MCP integration refactor plan

This plan updates MCP integration so tool enablement is owned by LLM nodes, while workflow lifecycle management is owned by the workflow manager through an explicit runtime contract.

## Goals

- Move MCP tool enablement from workflow level to LLM node level.
- Let LLM executors report MCP requirements to the workflow manager at startup.
- Keep MCP session startup and shutdown managed centrally by workflow lifecycle.
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

The LLM node owns MCP intent:

- which MCP sources it wants
- which tools it wants from those sources
- whether tools should be injected directly or exposed through discovery only
- whether listed MCP tools should be hidden from direct injection

The workflow manager owns MCP lifecycle:

- collect MCP requirements for each running workflow instance
- reconcile required MCP sources for that workflow instance
- start required sessions before execution proceeds
- shut them down automatically when the workflow instance ends

The MCP service owns source/session execution:

- start and stop MCP sessions
- cache tool descriptors
- materialize tool adapters
- emit failures and cache updates upward

## Core contract

The key change is a new runtime contract between nodes and the workflow manager.

LLM executors must report MCP requirements for the workflow instance they belong to.

The manager must aggregate these reports per workflow execution instance, not globally.

This contract must work for:

- root workflows
- nested workflows
- parallel running workflows

The contract must be instance-scoped, keyed by workflow execution id.

## Proposed runtime protocol

Add new runner event types so nodes can report MCP requirements through the existing manager-runner event channel.

Recommended new events:

1. `MCP_REQUIREMENTS`
   emitted by an executor to report the full MCP requirement set for the current node and workflow execution

2. `MCP_REQUIREMENTS_CLEAR`
   emitted when a node no longer needs its previously reported requirements, if explicit cleanup becomes necessary

For the first implementation, `MCP_REQUIREMENTS_CLEAR` is optional if the manager treats requirements as workflow-instance lifetime state and clears them automatically when the workflow finishes.

## Proposed protocol payloads

Add new models in `src/vocode/runner/proto.py`.

Suggested payload shape:

```python
class MCPToolResolutionMode(str, Enum):
    inject = "inject"
    discovery = "discovery"


class MCPToolSelectorPacket(BaseModel):
    source: str
    tool: str


class MCPNodeRequirement(BaseModel):
    node_name: str
    resolution_mode: MCPToolResolutionMode
    hide_listed_tools: bool = False
    tools: list[MCPToolSelectorPacket] = Field(default_factory=list)
    disabled_tools: list[MCPToolSelectorPacket] = Field(default_factory=list)


class RunEventMCPRequirements(BaseModel):
    workflow_name: str
    workflow_execution_id: str
    node_name: str
    requirement: MCPNodeRequirement
```

Add a new `RunEventReqKind.MCP_REQUIREMENTS` variant.

The event must always include:

- workflow name
- workflow execution id
- node name
- full node MCP requirement payload

The event should be treated as an upsert for that node within that workflow execution.

## Manager-side aggregation model

The manager should store MCP requirements per workflow execution id.

Recommended internal structure:

```python
workflow_execution_id -> {
    node_name -> MCPNodeRequirement
}
```

The manager then derives:

- required MCP source set for that workflow instance
- per-node MCP tool visibility used later by LLM execution

Important rule:

The aggregation key must be workflow execution id, not workflow name.

This is required so the contract supports:

- two runs of the same workflow at the same time
- nested workflows using the same workflow definition
- future parallel execution without collisions

## Lifecycle behavior

### Workflow startup

1. Manager starts workflow runner.
2. LLM executor initializes.
3. During init, the LLM executor reports its MCP requirements through `MCP_REQUIREMENTS`.
4. Manager records the node's requirement for that workflow execution id.
5. Manager reconciles MCP sessions for that workflow execution instance.
6. Required MCP sessions are started before the node can proceed with tool-enabled execution.
7. Tool caches are refreshed for newly started sources.

### Workflow shutdown

1. Runner finishes or stops.
2. Manager clears all stored MCP requirements for that workflow execution id.
3. Manager tells the MCP service to release workflow-instance references.
4. MCP service closes workflow-scoped sessions no longer referenced by any active workflow instance.

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

The user requested that LLM nodes report required MCP servers instead of a global manager scanning arbitrary workflow config.

That means:

- no graph-wide static inspection for MCP tool selection
- no workflow-level MCP selector ownership
- LLM executor init is the source of truth for node MCP requirements

The manager only consumes the explicit reports and handles lifecycle.

## Required code changes

### 1. Settings and models

Files:

- `src/vocode/settings/models.py`
- `src/vocode/runner/executors/llm/models.py`

Changes:

- remove `WorkflowConfig.mcp`
- move MCP selector config into a new LLM-node-local model
- keep project-level `Settings.mcp` for source registry, protocol, discovery defaults, and auth config

### 2. Runner protocol

Files:

- `src/vocode/runner/proto.py`

Changes:

- add `RunEventReqKind.MCP_REQUIREMENTS`
- add MCP requirement payload models
- add response type if manager acknowledgment is needed

Recommended first version:

- manager can respond with `NOOP`
- runner just needs the manager to process the event before execution continues

### 3. LLM executor reporting

Files:

- `src/vocode/runner/executors/llm/llm.py`
- `src/vocode/runner/executors/llm/helpers.py`

Changes:

- on executor init or at the start of `run()`, emit MCP requirements for the current node
- report the complete node-local MCP configuration as an upsert event
- do not start sessions directly from executor code
- wait for manager-mediated initialization before building connect tool specs

Recommended implementation detail:

- emit the MCP requirements event early in `Runner.run()` before executor tool construction for that node
- the runner already owns the event stream to the manager, so this is a better fit than letting executor code bypass the runner

### 4. Manager aggregation and reconciliation

Files:

- `src/vocode/manager/base.py`
- `src/vocode/manager/server.py`
- possibly `src/vocode/project.py`

Changes:

- extend manager-side runner event handling for `MCP_REQUIREMENTS`
- store per-workflow-execution and per-node MCP requirements
- derive required source sets per workflow execution instance
- notify project or MCP service to reconcile sessions for that workflow execution instance
- clear requirements automatically on workflow finish/stop

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

### 6. Node-local tool materialization

Files:

- `src/vocode/mcp/tool_resolution.py`
- `src/vocode/mcp/registry.py`
- `src/vocode/mcp/tool_materialization.py`
- `src/vocode/runner/executors/llm/llm.py`

Changes:

- replace workflow-based enablement helpers with node-based helpers
- materialize MCP tools for a specific LLM node, not for the whole project tool registry
- keep wildcard matching intact
- preserve disabled selector precedence

### 7. Global project tool registry cleanup

Files:

- `src/vocode/project.py`

Changes:

- stop treating node-scoped MCP adapters as globally registered project tools
- keep only static tools globally available
- allow LLM execution to compose node-local MCP tools dynamically

This is important because different LLM nodes may expose different MCP tool subsets.

### 8. TUI failure surfacing

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

1. Remove `mcp` from `WorkflowConfig`.
2. Add a new LLM-node-local MCP settings model.
3. Add `mcp: Optional[...] = None` to `LLMNode`.
4. Update tests that currently expect workflow-level MCP config.

Verification:

- settings tests pass with node-level MCP config only
- no workflow-level MCP references remain in config models

### Step 2: add runner protocol for MCP requirements

Developer instructions:

1. Add MCP requirement event models to `src/vocode/runner/proto.py`.
2. Add a new run event kind for MCP requirements.
3. Ensure manager event dispatch can receive the new event.

Verification:

- protocol models serialize and validate
- manager can receive and ignore a no-op MCP requirement event without breaking existing flows

### Step 3: make LLM nodes report MCP requirements

Developer instructions:

1. Add a helper that converts `LLMNode.mcp` into a protocol payload.
2. Emit that event from the node execution path before tools are built.
3. Treat the report as authoritative for that node instance.

Verification:

- a workflow with one LLM node emits one requirement report
- repeated runs do not leak requirement state across workflow execution ids

### Step 4: implement manager-side aggregation

Developer instructions:

1. Add manager storage keyed by workflow execution id and node name.
2. On MCP requirement events, upsert stored requirements.
3. Derive required source names for that workflow instance.
4. Reconcile MCP sessions through the project/MCP service.
5. On workflow finish or stop, clear requirement state for that workflow execution id.

Verification:

- nested workflows keep independent requirement sets
- stopping a child workflow does not clear parent requirements
- two workflow instances can coexist without collisions

### Step 5: refactor MCP service to track workflow-instance references

Developer instructions:

1. Replace workflow-name-based active state with workflow-execution-id-based references.
2. Add apply and clear methods for workflow-instance source requirements.
3. Reconcile start and shutdown based on active references.
4. Keep project-scoped sessions unchanged.

Verification:

- if two workflow instances require the same source, closing one instance keeps the session alive
- closing the last referencing workflow instance shuts the session down

### Step 6: move MCP tool injection to node-local LLM execution

Developer instructions:

1. Replace workflow-based enablement helpers with node-based helpers.
2. Build the effective MCP tool set for the current LLM node only.
3. Auto-inject matched concrete tools in inject mode.
4. Inject only `mcp_discovery` in discovery mode.
5. Keep prompt/resource helper behavior aligned with node-local MCP selection.

Verification:

- one node can see a source/tool subset that another node cannot
- discovery mode injects only the discovery tool
- wildcard selectors still work

### Step 7: stop leaking node-scoped MCP tools into `project.tools`

Developer instructions:

1. Keep global static tools in `project.tools`.
2. Stop materializing node-specific MCP adapters into the global registry.
3. Let the LLM executor compose node-local MCP tools when building connect tool specs.

Verification:

- MCP tool availability differs correctly across nodes in the same workflow
- non-MCP tools still behave exactly as before

### Step 8: surface MCP failures to the TUI

Developer instructions:

1. Add an MCP service notification callback for start/connect/reconcile failures.
2. Register that callback from the manager/UI server path.
3. Send visible user-facing messages to the TUI when MCP startup or connection fails.
4. Add tests proving the message is emitted.

Verification:

- failing stdio startup produces a visible UI message
- failing external connection produces a visible UI message
- messages identify the source name and failure reason

### Step 9: add and update tests

Developer instructions:

1. Replace workflow-level MCP config tests with node-level config tests.
2. Add protocol tests for MCP requirement events.
3. Add manager aggregation tests for nested workflows and parallel workflow instances.
4. Add node-local tool injection tests.
5. Add TUI notification tests for MCP failures.

Verification:

- targeted test suite passes

### Step 10: format and validate

Developer instructions:

1. Run `uv run black` on changed files.
2. Run targeted pytest modules first.
3. If green, run broader MCP, manager, and LLM executor test groups.

Suggested test commands:

```bash
uv run pytest tests/mcp tests/tools/test_mcp_discovery_tool.py tests/manager tests/runner/executors/llm
```

## Concrete decisions to preserve during implementation

- No workflow-level MCP tool selection config remains.
- LLM nodes are the source of truth for MCP requirements.
- Workflow manager owns lifecycle based on node reports.
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