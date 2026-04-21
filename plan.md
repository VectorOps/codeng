# MCP integration design and implementation plan

## Goal

Add MCP server support with an in-house implementation under `src/vocode/mcp/` that integrates with the existing project, runner, and tool pipeline. V1 should deliver MCP tool support, but it also requires the underlying lifecycle, roots, and HTTP auth foundation needed to make tool sessions correct and usable.

This plan is based on the current extension points in:

- `src/vocode/settings/models.py`
- `src/vocode/settings/loader.py`
- `src/vocode/project.py`
- `src/vocode/runner/runner.py`
- `src/vocode/runner/executors/llm/helpers.py`
- `src/vocode/runner/executors/llm/llm.py`
- `src/vocode/tools/base.py`
- `src/vocode/tools/*.py`
- `src/vocode/know/tools.py`
- `tests/executors/test_llm.py`
- `tests/tools/test_tool_factory.py`

## Current architecture summary

### Existing configuration and tool flow

- Global settings live in `src/vocode/settings/models.py` and are loaded by `src/vocode/settings/loader.py`.
- Workflow definitions are represented by `WorkflowConfig`.
- LLM node tool configuration is expressed through `LLMNode.tools: List[ToolSpec]` in `src/vocode/runner/executors/llm/models.py`.
- Tool specs are merged by `build_effective_tool_specs()` in `src/vocode/runner/executors/llm/helpers.py`.
- Tools are instantiated per project in `Project.refresh_tools_from_registry()`.
- Built-in tools are registered through `ToolFactory` in `src/vocode/tools/base.py`.
- The LLM executor converts internal tools into `connect.ToolSpec` via `tool.openapi_spec(...)`.
- Tool execution runs later through `project.tools[name].run(...)`.

### Existing lifecycle hooks

- `Project.start()` and `Project.shutdown()` are the natural project-scoped hooks.
- `Runner._init_executors()` and `Runner._shutdown_executors()` are the natural workflow-scoped hooks.
- The repository already has a dynamic external tool adapter pattern via `convert_know_tool()` in `src/vocode/know/tools.py`.

## Product decisions

These decisions should be treated as settled unless requirements change.

- MCP tools are the primary V1 feature.
- V1 also includes the required foundations for lifecycle negotiation, roots, and HTTP auth.
- MCP tools are opt-in per workflow.
- Workflow MCP selection should be configured separately from existing `ToolSpec` configuration.
- MCP tools should be materialized into `project.tools` as dynamic adapters.
- Prompts and resources are deferred to V3.
- When prompts and resources are added, they should be exposed through a small set of helper tools rather than one synthetic tool per remote capability.
- Roots belong in the MCP session layer, not as synthetic tools.
- Local stdio sources default to exposing the project base path as a root unless overridden.
- External HTTP sources are project-scoped by default.
- Stdio sources default to workflow-scoped startup.
- HTTP auth should be centralized in the MCP subsystem, not implemented inside individual tool adapters.
- Pre-registered auth should be implemented first. Client metadata documents are optional in V1 when operator-configured. Dynamic client registration is deferred unless it becomes cheap and necessary.
- MCP auth credentials and tokens should persist across application restarts using the existing credential storage machinery where practical, rather than remaining session-only.
- MCP management and authentication UX should be exposed through dedicated `/mcp` commands for source-oriented flows, while reusing lower-level auth/session infrastructure from the existing `/auth` implementation where practical.
- Source failures and tool failures must be isolated and should not crash unrelated workflows when avoidable.

## V1 scope and non-goals

### V1 scope

V1 should include:

- MCP settings and workflow-level MCP selection
- in-house MCP protocol, transport, and client session layers
- negotiated lifecycle correctness and negotiated capability tracking
- roots support, including `roots/list` and optional `notifications/roots/list_changed`
- HTTP transport auth support for protected MCP services
- MCP tool discovery, normalization, and execution
- dynamic MCP tool materialization into `project.tools`

### Non-goals for V1

- Full UI management for MCP server introspection
- Persisting live MCP session state across process restarts
- Supporting every possible transport immediately
- Exhaustive schema adaptation for every possible MCP edge case
- Prompt and resource retrieval APIs and helper tools
- Discovery UX enhancements such as hidden tools and discovery tools, which belong to V2

## Normative requirements

### Protocol and session lifecycle

- The implementation must speak JSON-RPC 2.0 envelopes.
- Session phases must be explicit: initialization, operation, and shutdown.
- `initialize` must be the first normal protocol interaction.
- After a successful initialize response, the client must send `notifications/initialized`.
- Before initialization completes, normal MCP operations must not run.
- The client must track negotiated protocol version and negotiated capabilities as session state.
- The client must only use server capabilities that were successfully negotiated.
- Unsupported negotiated protocol versions must disable only the affected source.
- Request ids must be unique per session and matched to pending requests.
- Protocol errors must be surfaced separately from tool execution errors.
- Requests must support timeouts. Timed-out requests should send cancellation when meaningful.

### Capability negotiation

- The client must advertise only implemented capabilities.
- V1 should advertise `roots` only when roots are configured and supported by the session.
- V1 must not advertise `sampling`, `elicitation`, `tasks`, or `experimental` unless implemented.
- Tool operations must be gated on negotiated server `tools` capability.
- Roots notifications must be gated on negotiated `roots` support and `listChanged` support.
- Prompt and resource behavior must remain gated on negotiated `prompts` and `resources` in V3.

### Stdio transport

- Stdio transport must support subprocess launch with command, args, env, and cwd.
- It must connect protocol IO to stdin/stdout and capture stderr separately for diagnostics.
- Shutdown must be bounded and staged: close stdin, wait, terminate, then kill if needed.
- Startup and shutdown timeouts must be separately configurable.

### HTTP transport and auth

- HTTP transport must support negotiated `MCP-Protocol-Version` on post-initialization requests.
- Access tokens must only be sent in the `Authorization` header and never in query strings.
- The client must parse `WWW-Authenticate` on `401` and `403` responses.
- Auth handling applies only to HTTP sources.
- Protected resource metadata discovery and authorization server discovery are required for protected HTTP sources.
- Authorization code flow with PKCE is required for user-authorized sources.
- The `resource` parameter must be sent in both authorization and token requests.
- Tokens must be isolated per MCP source and canonical resource URI.
- `403 insufficient_scope` should trigger bounded step-up auth when possible.

### Roots

- Roots must be exposed as `file://` URIs in V1.
- Path-based configuration may be accepted, but it must normalize to `file://` URIs before exposure.
- Roots must be validated, normalized, deduplicated, and treated as a security boundary.
- The client must respond to `roots/list` with the effective current roots.
- The service must compute effective roots by project, source, and workflow context.
- `notifications/roots/list_changed` should only be emitted when the normalized root set actually changes.

### Tools

- V1 must support `tools/list` and `tools/call`.
- Tool discovery must occur only after successful session initialization.
- `tools/list` should support pagination when a server uses cursors.
- `notifications/tools/list_changed` should trigger refresh only when the server advertises that capability.
- MCP tool schemas must be normalized for the internal tool pipeline.
- Malformed tool schemas must disable only the affected tool.
- MCP tool names are case-sensitive and must be unique within a source.
- Parameterless tools must normalize to a valid empty object schema.
- Tool results must preserve `content`, `isError`, and optional `structuredContent`.
- Protocol failures must remain distinct from `isError: true` execution failures.
- MCP tools must participate in the same approval and auditing flows as internal tools.

### Reliability and security

- Source failures must be isolated from other sources.
- Validation failures in roots and auth config should fail early where possible.
- Shutdown must be best-effort and idempotent.
- Tokens should reuse existing credential infrastructure where practical.
- Persisted MCP auth state should survive process restarts and be recoverable without re-running login unless the credentials expired or were removed.
- HTTPS must be required for auth server metadata and non-local redirect flows.
- Redirect URIs must be localhost or HTTPS.
- The client must not over-advertise unsupported MCP capabilities.

## Proposed architecture

Introduce a new subsystem under `src/vocode/mcp/` centered around `MCPService`, owned by `Project`.

The rest of the application should continue to work with internal tools only. MCP-specific behavior should remain behind the service and dynamic tool adapters.

### Proposed modules

- `src/vocode/mcp/__init__.py`
- `src/vocode/mcp/models.py`
- `src/vocode/mcp/protocol.py`
- `src/vocode/mcp/transports.py`
- `src/vocode/mcp/process_manager.py`
- `src/vocode/mcp/client.py`
- `src/vocode/mcp/auth.py`
- `src/vocode/mcp/registry.py`
- `src/vocode/mcp/converters.py`
- `src/vocode/mcp/tools.py`
- `src/vocode/mcp/service.py`

### Responsibilities

`models.py`

- Pydantic models for MCP settings, runtime descriptors, roots, auth config, workflow selectors, and session negotiation info

`protocol.py`

- JSON-RPC message modeling
- request id generation and pending request tracking
- initialize and initialized sequencing
- timeout and cancellation plumbing
- typed helpers for `tools/list`, `tools/call`, roots, and later prompt/resource methods

`transports.py`

- stdio transport mechanics
- HTTP transport mechanics
- header injection for negotiated protocol version and bearer tokens
- clean transport close behavior

`process_manager.py`

- stdio subprocess lifecycle
- graceful shutdown escalation
- startup and shutdown timeout handling

`client.py`

- session orchestration over protocol and transport
- initialize and initialized handshake
- negotiated session metadata capture
- roots capability declaration and roots request handling
- high-level methods such as list tools and call tool

`auth.py`

- protected resource metadata discovery
- auth server metadata discovery
- PKCE flow orchestration
- token acquisition, storage coordination, refresh, and step-up handling
- canonical resource URI handling

`registry.py`

- indexes descriptors by source and capability
- resolves workflow tool selectors
- resolves effective roots
- tracks tool refresh state after list-changed notifications

`converters.py`

- normalizes MCP tool schema into internal OpenAPI-compatible form
- preserves output schema and untrusted metadata for later use
- generates stable internal tool names

`tools.py`

- `MCPToolAdapter`
- V2 discovery tool
- V3 prompt/resource helper tools

`service.py`

- owns configured sources
- maintains active sessions
- coordinates auth state
- tracks negotiated protocol version and capabilities
- caches discovered tools
- resolves effective roots
- exposes lookup and execution methods to the rest of the app

## Configuration design

### Global settings

Add an optional `mcp` section to `Settings` in `src/vocode/settings/models.py`.

Suggested model surface:

- `MCPSettings`
  - `enabled: bool = True`
  - `sources: Dict[str, MCPSourceSettings] = Field(default_factory=dict)`
  - `roots: Optional[MCPRootSettings] = None`
  - `protocol: Optional[MCPProtocolSettings] = None`

- `MCPSourceSettings` as a discriminated union by `kind`
  - `MCPStdioSourceSettings`
  - `MCPExternalSourceSettings`

- `MCPProtocolSettings`
  - `request_timeout_s: float = 30.0`
  - `max_request_timeout_s: Optional[float] = 120.0`
  - `startup_timeout_s: float = 15.0`
  - `shutdown_timeout_s: float = 10.0`

- `MCPAuthSettings`
  - `enabled: bool = True`
  - `mode: Literal["auto", "preregistered", "client_metadata", "dynamic"] = "auto"`
  - `client_id: Optional[str] = None`
  - `client_secret_env: Optional[str] = None`
  - `client_metadata_url: Optional[str] = None`
  - `scopes: List[str] = Field(default_factory=list)`
  - `redirect_host: str = "127.0.0.1"`
  - `redirect_port: Optional[int] = None`
  - `allow_dynamic_registration: bool = False`
  - `retry_step_up: bool = True`
  - `max_step_up_attempts: int = 2`

- `MCPRootEntry`
  - `uri: Optional[str] = None`
  - `path: Optional[str] = None`
  - `name: Optional[str] = None`

- `MCPRootSettings`
  - `entries: List[MCPRootEntry] = Field(default_factory=list)`
  - `list_changed: bool = True`
  - `merge_mode: MCPRootMergeMode = replace`

### Workflow-level overrides

Add `WorkflowConfig.mcp: Optional[MCPWorkflowSettings] = None`.

Suggested model:

- `MCPWorkflowSettings`
  - `enabled: bool = True`
  - `tools: List[MCPToolSelector] = Field(default_factory=list)`
  - `disabled_tools: List[MCPToolSelector] = Field(default_factory=list)`
  - `roots: Optional[MCPRootSettings] = None`

- `MCPToolSelector`
  - `source: str`
  - `tool: str`

Selector semantics:

- `tool="*"` enables all tools from a source
- explicit tool selectors enable individual tools
- `disabled_tools` always wins over `tools`

### Root resolution policy

Recommended effective roots precedence:

1. workflow-level source-specific override, if added later
2. workflow-level `mcp.roots`
3. source-level `roots`
4. global `mcp.roots`
5. implicit default for local stdio sources: project base path

For V1, workflow roots should use `replace` semantics by default.

## Integration plan

### Project integration

`Project` should own one `MCPService` instance.

`Project.start()` should:

- create the MCP service when enabled
- start project-scoped sources
- initialize protected external sessions as needed
- record negotiated session metadata
- discover capabilities only after initialization succeeds

`Project.shutdown()` should:

- stop workflow-scoped sessions still active
- stop project-scoped stdio sessions
- close external HTTP sessions

### Runner integration

`Runner` should notify the MCP service when workflows start and finish.

Recommended hook points:

- `Runner._init_executors()` -> `await project.mcp.start_workflow(...)`
- `Runner._shutdown_executors()` -> `await project.mcp.finish_workflow(...)`

These hooks are needed for:

- workflow-scoped stdio source startup and shutdown
- workflow-scoped root recalculation and roots change notification
- workflow-scoped MCP tool availability refresh

## MCP tool materialization

### Chosen approach

Materialize enabled MCP tools into `project.tools` as dynamic adapters.

This keeps `LLMExecutor._build_connect_tools()` nearly unchanged and reuses the existing internal tool pipeline.

### Internal naming

Use canonical internal names:

- `mcp__<source>__<tool_name>`

Rules:

- source name comes from settings key
- remote tool name is preserved as much as practical
- unsafe characters normalize to `_`
- the service keeps a reverse mapping to source name and remote tool name

### Adapter shape

Introduce `MCPToolAdapter(BaseTool)` with fields such as:

- `internal_name`
- `source_name`
- `mcp_tool_name`
- descriptor snapshot or reference

Behavior:

- `openapi_spec(...)` returns normalized schema from the MCP descriptor
- `run(...)` delegates to `project.mcp.call_tool(...)`

### Selector resolution

Workflow tool resolution should follow this order:

1. if workflow MCP is disabled, expose no MCP tools
2. take discovered tools from active sources
3. apply allow selectors from `tools`
4. apply deny selectors from `disabled_tools`
5. materialize the result into `project.tools`

## Later phases

### V2

Add `skip_listing` support and a discovery tool.

Required changes:

- `ToolSpec.skip_listing: bool = False`
- merge support in `build_effective_tool_specs()`
- omit hidden tools from outbound tool definitions in `LLMExecutor`
- add a discovery-oriented MCP helper tool

### V3

Add prompts and resources through helper tools such as:

- `mcp_read_resource`
- `mcp_get_prompt`
- optional list helpers if ergonomically needed

Prompts and resources should not become one synthetic tool per remote capability by default.

## Testing plan

Tests should be added in the same phase as the functionality they validate.

### Phase-aligned tests

Phase 1:

- settings parsing and discriminated union validation
- variable interpolation in MCP fields
- root normalization and invalid root rejection
- workflow selector parsing
- auth config validation

Phase 2:

- protocol request and response matching
- initialize and initialized sequencing
- negotiated version handling
- stdio startup and shutdown behavior
- HTTP header injection and auth challenge parsing
- protected resource and auth server discovery ordering

Phase 3:

- project-scoped versus workflow-scoped source lifecycle
- workflow root recalculation
- roots-list-changed notification behavior
- negotiated state cleared on disconnect

Phase 4:

- tool descriptor conversion
- parameterless schema normalization
- dynamic tool materialization into `project.tools`
- wildcard enable and explicit disable selector behavior
- `tools/call` protocol error versus `isError` execution error handling
- list-changed refresh behavior when supported

Phase 5:

- `skip_listing` merge behavior
- hidden tools omitted from outbound tool definitions
- discovery tool behavior

Phase 6:

- prompt and resource capability discovery
- prompt and resource helper tool execution

### Failure coverage

Across phases, add coverage for:

- source startup failure isolation
- malformed remote tool schema
- auth discovery failure
- missing PKCE support
- invalid canonical resource URI handling
- protocol version mismatch
- initialization timeout
- unknown tool protocol failure
- readable tool execution failure formatting

## Suggested file changes

### `src/vocode/settings/models.py`

Add MCP settings models, roots models, auth models, protocol models, workflow MCP models, and optional `Settings.mcp` and `WorkflowConfig.mcp` fields.

### `src/vocode/settings/loader.py`

Ensure MCP config fields participate in normal loading and interpolation behavior.

### `src/vocode/project.py`

Add `self.mcp`, project lifecycle integration, and MCP tool merging in `refresh_tools_from_registry()`.

### `src/vocode/runner/runner.py`

Add workflow lifecycle notifications to the MCP service.

### `src/vocode/runner/executors/llm/helpers.py`

Likely no V1 changes. Add `skip_listing` merge behavior in V2.

### `src/vocode/runner/executors/llm/llm.py`

Likely minimal V1 changes. In V2, omit `skip_listing=True` tools from outbound tool definitions.

### `src/vocode/mcp/`

Add the new subsystem described above.

### `src/vocode/connect_auth.py`

Extend credential persistence and interactive auth helpers so MCP auth can reuse the same file-backed credential storage and TUI callback patterns.

### `src/vocode/manager/commands/`

Add an MCP command surface for source management and authentication, including login, status, logout, cancel, and follow-up MCP management subcommands as they are introduced.

### `src/vocode/manager/server.py`

Add MCP-specific interactive session wiring parallel to existing provider auth session handling.

### `src/vocode/manager/autocomplete*.py`

Expose `/mcp` commands and MCP source names through autocomplete.

### `src/vocode/tools/`

V2 adds the discovery tool. V3 adds prompt and resource helper tools.

## Risks and open questions

- Confirm whether workflow identity needs to be stronger than workflow name for concurrent runs.
- Confirm path normalization and symlink policy for roots.
- Confirm how much auth flow logic should be reused from existing auth infrastructure versus implemented directly in `src/vocode/mcp/auth.py`.
- Confirm terminal UX for browser-based auth and callback handling.
- Confirm whether operator-configured client metadata document support is worthwhile in V1.

## Recommended phased implementation plan

### Phase 1: configuration foundation

1. Add MCP settings models in `src/vocode/settings/models.py`.
2. Add workflow-level MCP selector models.
3. Add roots models and normalization rules.
4. Add protocol and timeout settings models.
5. Add auth settings models for HTTP sources.
6. Add settings and validation tests.

Deliverable:

- the project can parse and validate MCP config and workflow overrides

### Phase 2: transport and session primitives

7. Create runtime descriptor and session models in `src/vocode/mcp/models.py`.
8. Implement `src/vocode/mcp/protocol.py` for JSON-RPC, request tracking, initialization sequencing, and timeouts.
9. Implement `src/vocode/mcp/transports.py` for stdio and HTTP transport behavior.
10. Implement `src/vocode/mcp/process_manager.py` for stdio subprocess lifecycle.
11. Implement `src/vocode/mcp/client.py` for session orchestration and negotiated metadata capture.
12. Add protocol, transport, and negotiation tests.

Deliverable:

- the codebase has a usable in-house MCP session layer

### Phase 3: HTTP auth and service orchestration

13. Implement `src/vocode/mcp/auth.py` for protected resource discovery, auth server discovery, PKCE flow support, and token handling.
14. Implement `src/vocode/mcp/registry.py` for capability indexing, root resolution, and selector support.
15. Implement `src/vocode/mcp/converters.py` for tool normalization.
16. Implement `src/vocode/mcp/service.py` for source lifecycle, session ownership, auth coordination, capability caching, and roots handling.
17. Integrate `Project.start()` and `Project.shutdown()` with the service.
18. Add service and auth tests.

Deliverable:

- the project can connect to configured sources, authorize protected HTTP sources, and track negotiated sessions

### Phase 4: workflow lifecycle integration

19. Add runner hooks to notify MCP service on workflow start and finish.
20. Implement workflow-scoped stdio source startup and shutdown behavior.
21. Implement workflow-scoped root recalculation and roots change notification.
22. Add workflow lifecycle tests.

Detailed work breakdown:

- `src/vocode/runner/runner.py`
  - call `project.on_workflow_started(...)` from `_init_executors()` before executor init completes
  - call `project.on_workflow_finished(...)` from `_shutdown_executors()` after executor shutdown
  - preserve current stop/resume semantics so workflow-scoped MCP sessions can survive runner stop and be finalized only when appropriate
- `src/vocode/project.py`
  - route runner lifecycle callbacks into `MCPService.start_workflow(...)` and `MCPService.finish_workflow(...)`
  - refresh MCP tool cache when new workflow-scoped sources start
  - refresh merged project tool registry when workflow-scoped MCP source availability changes
- `src/vocode/mcp/service.py`
  - reconcile desired workflow-scoped sessions against currently running workflow-scoped sessions
  - avoid restarting unchanged workflow-scoped sessions across stop/resume cycles
  - add workflow-aware roots recalculation entrypoints once roots handling is implemented
- tests
  - verify runner start initializes workflow-scoped MCP sessions
  - verify runner finish closes workflow-scoped MCP sessions
  - verify runner stop/resume reuses workflow-scoped MCP sessions without restart
  - verify project refreshes MCP tools exactly once per workflow start when new workflow-scoped sources appear
  - later add root recalculation and `roots/list_changed` coverage when root support is completed

Deliverable:

- workflow-scoped MCP sources behave correctly over runner lifecycle

### Phase 5: MCP tools V1

23. Implement `MCPToolAdapter` and conversion helpers.
24. Implement `tools/list` pagination handling and refresh support for `notifications/tools/list_changed`.
25. Implement `tools/call` handling with protocol-error versus execution-error separation.
26. Extend `Project.refresh_tools_from_registry()` to merge enabled MCP tools.
27. Implement workflow selector resolution with wildcard enable and explicit disable support.
28. Add executor and tool materialization tests.
29. Integrate MCP auth flows with the TUI for operator-guided login and callback handling.
30. Persist MCP auth credentials and tokens across restarts using the existing credential store.
31. Wire `/mcp` commands into manager, server, and autocomplete for source-oriented auth and management flows.

Detailed work breakdown:

- `src/vocode/mcp/converters.py`
  - validate remote MCP tool payloads and reject malformed descriptors without failing the whole source
  - normalize missing `inputSchema` to an empty object schema
  - preserve descriptive metadata needed by adapters and later discovery UX
  - later extend conversion to retain richer output or annotation metadata if needed
- `src/vocode/tools/mcp_tool.py`
  - implement `MCPToolAdapter` over cached MCP descriptors
  - expose normalized OpenAPI-compatible schema through `openapi_spec(...)`
  - delegate runtime execution to `project.mcp.call_tool(...)`
  - later improve result handling so protocol failures stay distinct from remote `isError: true` tool results in the internal tool response path
- `src/vocode/mcp/client.py`
  - keep `tools/list` pagination support in the session layer
  - add notification-driven refresh support for `notifications/tools/list_changed` once notification handling is implemented
  - keep `tools/call` transport/protocol behavior separated from higher-level execution formatting
- `src/vocode/mcp/service.py`
  - cache discovered tool descriptors per source
  - expose refresh helpers used by project and workflow lifecycle code
  - later add refresh triggers when servers advertise `tools.listChanged`
- `src/vocode/project.py`
  - materialize enabled MCP tools into `project.tools` using canonical internal names
  - respect workflow MCP enable/disable state and selector filtering
  - apply disabled tool overrides after wildcard allow selectors
- tests
  - verify parameterless tool schema normalization
  - verify MCP tool adapters are materialized into `project.tools`
  - verify adapter execution delegates into the MCP service
  - verify wildcard allow plus explicit disable selector behavior
  - verify MCP auth flows can surface browser/login prompts through the TUI and complete callback-driven authorization
  - later add server notification refresh coverage and protocol-vs-execution failure formatting coverage
  - later add TUI coverage for cancellation and failed MCP auth login flows

- `src/vocode/mcp/auth.py`, `src/vocode/connect_auth.py`, `src/vocode/manager/`, `src/vocode/tui/`
  - connect MCP auth login flows to the existing interactive auth/session infrastructure where practical
  - surface authorization URL, progress, prompts, and completion/failure state through the TUI
  - support callback-driven browser auth for MCP sources without embedding auth logic in tool adapters
  - ensure credentials and tokens remain isolated per MCP source and canonical resource
  - persist MCP token and credential state in the same user-scoped credential store used by existing auth flows so login survives restart
  - avoid env-only caching for MCP-issued tokens except as an explicit operator-provided secret source
  - add a dedicated `/mcp` command with at least `login`, `status`, `logout`, and `cancel` subcommands keyed by MCP source name
  - leave provider `/auth` focused on provider authentication rather than overloading it with MCP source semantics
  - wire `/mcp` command registration and autocomplete so MCP auth and future MCP management commands are discoverable in the TUI

Deliverable:

- MCP tools work through the existing internal tool pipeline

### Phase 6: V2 enhancements

29. Extend `ToolSpec` with `skip_listing`.
30. Update effective tool merge logic.
31. Update LLM tool injection to omit hidden tools.
32. Add an MCP discovery tool.
33. Add V2 tests.

Deliverable:

- hidden tools and discovery-driven MCP UX are supported

### Phase 7: V3 prompts and resources

34. Extend capability discovery to include prompts and resources.
35. Add `mcp_read_resource` helper tool.
36. Add `mcp_get_prompt` helper tool.
37. Optionally add list helpers if needed.
38. Add V3 tests.

Deliverable:

- prompts and resources are available through helper tools without capability explosion

## Minimal viable V1 scope

If implementation must be cut to the smallest safe version, MVP V1 should include:

1. MCP settings and workflow selector config
2. roots settings and effective roots resolution
3. protocol settings and negotiated session tracking
4. stdio and HTTP session connectivity
5. HTTP auth support for protected MCP sources
6. workflow-scoped stdio lifecycle and roots updates
7. MCP tool discovery, normalization, and adapter conversion
8. dynamic MCP tool materialization into `project.tools`

V2 then adds hidden tools and discovery UX. V3 then adds prompt and resource access tools.

This delivers the core requested capability while keeping the current architecture intact.

## Current implementation status

### Phase 1 status

[x] Add MCP settings models in `src/vocode/settings/models.py`
[x] Add workflow-level MCP selector models
[x] Add roots models and normalization rules
[x] Add protocol and timeout settings models
[x] Add auth settings models for HTTP sources
[x] Add settings and validation tests
[x] Add stricter validation for source names, URL schemes, and root merge edge cases
[x] Add loader/include coverage for MCP config composition and interpolation

### Phase 2 status

[x] Create runtime descriptor and session models in `src/vocode/mcp/models.py`
[x] Implement `src/vocode/mcp/protocol.py` for JSON-RPC, request tracking, initialization sequencing, and timeouts
[x] Implement stdio transport behavior in `src/vocode/mcp/transports.py`
[x] Implement HTTP transport behavior in `src/vocode/mcp/transports.py`
[x] Implement `src/vocode/mcp/process_manager.py` for stdio subprocess lifecycle
[x] Implement `src/vocode/mcp/client.py` for session orchestration and negotiated metadata capture
[x] Implement `tools/list` pagination in the client
[x] Implement `tools/call` in the client
[x] Add timeout and cancellation handling in the protocol/client layer
[x] Add protocol, transport, client, process-manager, and negotiation tests
[ ] Add roots request handling in the client
[ ] Add roots notification handling in the client
[ ] Add list-changed notification handling in the client

### Phase 3 status

[x] Implement `src/vocode/mcp/auth.py` for protected resource discovery, auth server discovery, PKCE flow support, and token handling
[x] Implement `src/vocode/mcp/registry.py` for source descriptor indexing
[x] Extend `src/vocode/mcp/registry.py` with effective root resolution and selector utilities
[x] Implement `src/vocode/mcp/converters.py` for basic tool normalization
[x] Implement `src/vocode/mcp/service.py` for source lifecycle, session ownership, negotiated metadata lookup, and tool cache management
[x] Extend `src/vocode/mcp/service.py` with auth coordination
[ ] Extend `src/vocode/mcp/service.py` with roots handling and root recalculation
[x] Integrate `Project.start()` and `Project.shutdown()` with the service for project-scoped stdio sources
[x] Add service tests
[x] Add auth tests
[x] If workflow did not change, but runner started, stopped or restarted - there's no need to restart MCP servers. Implement differential start/stop of MCP servers needed.
[x] Move MCP resolution, state and tool starting logic out of project
[x] Clear negotiated session state on disconnect and isolate failed session startup

### Phase 4 status

[x] Add runner hooks to notify MCP service on workflow start and finish
[x] Implement workflow-scoped stdio source startup and shutdown behavior
[ ] Implement workflow-scoped root recalculation and roots change notification
[x] Add workflow lifecycle tests
[x] Preserve workflow-scoped MCP sessions across runner stop/resume when the workflow does not change
[x] Refresh MCP tool cache when workflow-scoped sources start
[x] Refresh merged project tools when workflow-scoped MCP source availability changes

### Phase 5 status

[x] Implement `MCPToolAdapter` and basic conversion helpers
[x] Normalize parameterless MCP tool schemas to an empty object schema
[x] Extend `Project.refresh_tools_from_registry()` to merge enabled MCP tools
[x] Implement workflow selector resolution with wildcard enable and explicit disable support
[x] Add executor and tool materialization tests
[x] Implement `tools/list` pagination handling in the client session layer
[x] Implement refresh support for `notifications/tools/list_changed`
[x] Implement `tools/call` handling with protocol-error versus execution-error separation through the internal tool pipeline
[x] Preserve richer MCP tool result payloads beyond plain text extraction in the adapter layer
[x] Integrate MCP auth flows into the TUI for operator-guided login and callback handling. Follow existing patterns by introducing a /mcp command with "login" subcommand.
[x] Persist MCP auth credentials and tokens across restarts using the existing credential store instead of env-only token caching.
[x] Wire `/mcp` auth and MCP management commands into manager, server, and autocomplete.
[x] Implement keyring support for credentials manager
