# Documentation Index

This directory is organized by documentation purpose rather than by creation order. Read the files in the order below when understanding or changing the MVP implementation.

## Concept

- [`concept/PROJECT_OVERVIEW.md`](./concept/PROJECT_OVERVIEW.md) - conceptual source of truth for what the system is, input/output, workflow, quality target, and out-of-scope items.
- [`concept/SYSTEM_DESIGN_BRIEF.md`](./concept/SYSTEM_DESIGN_BRIEF.md) - implementation-aware bridge from concept to architecture.

## Design

- [`design/ARCHITECTURE.md`](./design/ARCHITECTURE.md) - MVP architecture, runtime boundaries, adapter responsibilities, workspace rules, validation, and repair flow.
- [`design/QUALITY_LAYER_DESIGN.md`](./design/QUALITY_LAYER_DESIGN.md) - startup prompt, skill, subagent, and proposal-quality guidance.

## Contracts

- [`contracts/MVP_RUNTIME_CONTRACTS.md`](./contracts/MVP_RUNTIME_CONTRACTS.md) - implemented runtime schemas for config, requests, results, validation, repair, usage, and workspaces.
- [`contracts/CLI_SPEC.md`](./contracts/CLI_SPEC.md) - MVP command-line interface contract.
- [`contracts/PYTHON_API_SPEC.md`](./contracts/PYTHON_API_SPEC.md) - MVP public Python API contract for config loading, single-run execution, errors, and serialization.
- [`contracts/VALIDATION_REPAIR_AND_RUN_RECORD.md`](./contracts/VALIDATION_REPAIR_AND_RUN_RECORD.md) - MVP mechanical validator rules, external repair loop, session continuation recording, and `run_record.json` shape.

## References

- [`references/scholar-gateway-mcp.md`](./references/scholar-gateway-mcp.md) - external retrieval MCP reference and tool semantics.
- [`references/claude-code-subagents.md`](./references/claude-code-subagents.md) - Claude Code subagent reference: invocation tool, full frontmatter surface, runtime isolation, and design rules.

## Example Inputs

- [`../examples/question.txt`](../examples/question.txt) - sample high-level AI research topic for smoke runs.
- [`../configs/scientific-hypothesis-generator/gpt.yaml`](../configs/scientific-hypothesis-generator/gpt.yaml) - example Codex harness config.
- [`../configs/init/claude.yaml`](../configs/init/claude.yaml) - example Claude harness config.
- [`../configs/simple/gpt.yaml`](../configs/simple/gpt.yaml) - simple-asset Codex/GPT config.

The example configs use shipped assets under `src/research_ideation_agent/assets/` and invoke the folded-in scholar-gateway MCP server through the `scholar-gateway-mcp` console script.

## Implementation Tooling

Use `uv` for all Python project management. Run commands through `uv run`, sync environments with `uv sync`, and add or remove Python dependencies with `uv add` / `uv remove` so `pyproject.toml` and the lockfile stay consistent. Run the test suite with `uv run pytest`.
