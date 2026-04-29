# VectorOps Code

> WIP: this project is under active development and the public surface is still evolving.

VectorOps Code is a configurable, graph-driven coding harness.

## Features

- Build custom workflows for coding, review, planning, requirements discovery, patching or validation.
- Run those workflows from a developer-friendly TUI or receive work from external callers through self-hosted HTTP entry points.
- Give agents practical capabilities such as shell execution, patch application, repository discovery, web fetch, and delegated sub-tasks.
- Add repository-aware context and knowledge-backed tools through VectorOps Know.
- Extend the system with MCP sources and tools when you need external capabilities.

## Quick start

This repository targets Python `>=3.13,<3.14`.

1. Install dependencies:

```bash
uv sync
```

2. Review the example project configuration and workflows:

```text
.vocode/config.yaml
.vocode/workflows/
src/vocode/config_templates/
```

3. Launch the TUI against a project path:

```bash
uv run python -m vocode.tui.app <project path>
```

The current codebase exposes the TUI entry point as a module invocation rather than a console script.

## Elevator pitch

VectorOps Code is for teams that want more than a chat box.
It gives you a way to define how work should happen, what an agent can access, where context comes from, and how people or systems feed tasks into the runtime.

If you want an AI workflow that starts in a terminal session, understands your repository, pulls in outside information, makes code changes, runs commands, and reports progress back to the user, this project is built for that kind of work.
If you want the same core runtime to accept structured input from another service through a self-hosted HTTP endpoint, it is built to support that shape too.

The value is consistency.
You can move from one-off agent sessions to repeatable, tool-backed workflows without rebuilding the stack around every new use case.

## What it can do today

### Flexible workflow orchestration

The repository already includes workflow templates for common roles and stages such as agent, analyst, architect, coder, discovery, requirements, validation, and patch application.

That makes it easier to start from a working pattern and adapt it to your own process instead of inventing the flow from scratch.

### Flexible input and control surface

The project already supports rich interactive control through the TUI, while also exposing server-side HTTP building blocks for self-hosted integrations.

This is the kind of architecture you want when the same system needs to serve both developers in a terminal and external callers from another application.

### Connect integration

VectorOps Code integrates with VectorOps Connect so model access, provider auth, and tool-enabled LLM execution can stay behind one abstraction layer.

That makes the project easier to evolve across providers without rewriting workflow logic around each model API.

### KnowLT integration

VectorOps Code integrates with `knowlt` for repository indexing and knowledge-backed tooling.
It can manage indexed repositories, refresh them, and expose KnowLT capabilities back into workflows so agents can work with more than the raw filesystem.

### Web client and web fetch support

The project includes a built-in web client and web fetch flow so workflows can pull in documentation, reference pages, and external text content as part of the job.

It already handles the practical details you would expect in production-oriented tooling, such as timeouts, redirects, size limits, and access controls.

### MCP support

The MCP stack gives the runtime a path to external tools and services.
Support already includes source management, auth flows, transport handling, tool discovery, and workflow-aware resolution.

That makes MCP a practical extension point rather than just a protocol checkbox.

### Built-in tooling

The built-in toolset is aimed at real coding and automation work.
Today that includes patch application, shell execution, web fetch, agent delegation, planning, and project-aware discovery helpers.

### Internal HTTP hooks

There is already an internal HTTP server abstraction for dynamic route registration and authenticated internal endpoints.
That gives the project a clean foundation for self-hosted entry points and runtime integrations.

## Configuration and templates

The repository already includes sample configuration under `.vocode/` and packaged templates under `src/vocode/config_templates/`.
These files are the best starting point when creating or adapting workflows for a project.

The template helper in `src/vocode/templates.py` writes a default config and copies sample assets next to it for reference.

## Development

Run the test suite with:

```bash
uv run pytest
```

Run a narrower test target when iterating on a subsystem, for example:

```bash
uv run pytest tests/manager
uv run pytest tests/mcp
uv run pytest tests/tui
```

Format Python files with:

```bash
uv run black src tests
```

## Notable areas covered by tests

- manager command handling and autocomplete
- runner and executor behavior
- patch parsing and patch tool semantics
- MCP settings, protocol, service, and transports
- tool factory and built-in tool integrations
- TUI rendering components

## Notes

- Use `AGENTS.md` in this repository as the primary contributor guide for coding conventions and architectural boundaries.
- Avoid changing wire contracts such as packet field names, `kind` discriminators, or enum values without a migration plan.
- When changing runtime state models, keep persistence and conversion layers in sync.
