# System Design Brief - Autonomous AI Research-Ideation Agent

> **Status:** MVP-aligned bridge brief for the implemented runtime.
> **Last updated:** 2026-06-03
> **Concept source:** [`PROJECT_OVERVIEW.md`](./PROJECT_OVERVIEW.md)

This document sits between the concept overview and the architecture document. It is not a full requirements specification and does not define code structure, schemas, or SDK integration details. Its purpose is to translate the concept into an implementation-aware system brief: what existing capabilities the project will rely on, what workflow the agent must follow, what output must exist, what optional trace material may be useful, and what checks must happen before the final proposal is accepted.

## 1. SDK-based implementation strategy

### Dual-harness target

The project should target both Claude Code Agent SDK and Codex SDK. This is not an either-or choice: the research-ideation workflow should be designed so it can run on either harness, with shared conceptual behavior and harness-specific integration details where necessary. A single run is expected to be hosted by one harness at a time; the goal is portable behavior across both SDKs, not simultaneous execution through both.

The implementation stance is Python-first: a shared Python framework owns run setup, workspace management, prompt rendering, skill packaging, MCP configuration, final mechanical validation, and repair-loop control, while thin Python adapters translate that shared run request into the verified Claude Code or Codex execution surface. The production MVP adapters use the official Python SDK surfaces for Claude Code and Codex; CLI-backed, TypeScript, or other fallback adapter paths are not used for the production MVP. The run configuration selects the harness, model, effort, MCP parameters, budget settings, repair limits, optional subagents, and the subset of skills to expose to the lead agent.

The MVP exposes a CLI as the primary external interface and a Python API as the core programmatic interface. The CLI accepts a topic or topic file plus a required configuration file and simple overrides, then calls the same Python API used by tests or future automation. The public Python API exposes config loading, single-run execution, typed result records, and typed pre-run errors without exposing adapter-specific SDK objects. Configuration selects the harness, model, effort, MCP profile, budgets, selected skills, and selected subagents; secrets remain environment variables rather than committed config values.

The implemented runtime schemas for config, request, result, validation, repair, and usage records are defined in [`MVP_RUNTIME_CONTRACTS.md`](../contracts/MVP_RUNTIME_CONTRACTS.md).

The implemented command-line contract is defined in [`CLI_SPEC.md`](../contracts/CLI_SPEC.md).

The implemented Python API contract is defined in [`PYTHON_API_SPEC.md`](../contracts/PYTHON_API_SPEC.md).

### Capabilities provided by the harnesses

Claude Code Agent SDK and Codex SDK already provide the generic agent machinery this project needs: iterative agent execution, tool calling, MCP integration, context and session handling, permissions or sandbox controls, and extension points such as prompts, skills, hooks, or structured outputs.

Because these capabilities already exist, the project should avoid rebuilding low-level agent infrastructure unless a later architecture decision proves that the SDKs cannot support a required behavior.

### Project-specific work

The project-specific work should focus on the research-ideation workflow layer. This includes prompts, skills, MCP tool-use expectations, output requirements, optional trace conventions, gap and method selection criteria, proposal-writing instructions, final mechanical validation checks, Python harness adapters, MCP configuration, and deterministic checks where useful.

In short, the harnesses execute the agent loop; this project defines what a high-quality autonomous research-ideation run should do inside that loop.

### Single lead-agent execution

The expected execution model is one lead agent run, not an externally orchestrated multi-agent pipeline. The SDK should start one primary research-ideation agent and provide it with the full workflow instructions, relevant skills, MCP tools, final mechanical checks, and any available subagent tools. The lead agent is responsible for deciding when to search, read, form gaps, call tools, use skills, ask subagents if available, evaluate options, and write the proposal.

Subagents, if used, should be treated as callable tools or delegated helpers inside the lead agent's run. They should not become a fixed pipeline of separately launched role agents with externally assigned prompts such as "literature reviewer", "method designer", and "proposal writer" unless a later architecture decision explicitly changes this execution model.

## 2. External capabilities

Literature access is expected to come from an external MCP server rather than from code implemented in this repository. The current intended capability set includes:

- **WebSearch** - search the web or academic web for relevant papers, project pages, benchmarks, datasets, and related technical material.
- **WebFetch** - fetch a specific URL so the agent can inspect source content instead of relying on search snippets.
- **Paper download and Markdown conversion** - download a paper, usually from a paper page or PDF URL, and convert it into Markdown suitable for full-text reading.

This project depends on those capabilities through an MCP-style interface. The search backend, crawler, PDF parser, and paper-to-Markdown conversion implementation are outside this repository. The implemented scholar-gateway profile exposes `web_search`, `web_fetch`, and `extract_paper` to the harnesses as MCP tools; detailed backend schemas remain owned by the external MCP server.

The agent should treat retrieved and fetched sources as the evidence base for the proposal. A paper or web page should not be cited or used as support unless it has been retrieved or fetched, and claims about prior work should be grounded in content the agent has actually inspected.

## 3. Workflow contract

The end-to-end run should follow the conceptual workflow from `PROJECT_OVERVIEW.md`, but expressed here as a system contract for the lead agent. These steps describe the work the lead agent must accomplish during one autonomous run; they are not a prescription to launch separate agents for each stage.

1. **Run initialization** - accept one high-level AI research topic, record the topic verbatim, and create a provisional interpretation without asking the user for clarification.
2. **Exploratory literature search** - generate multiple search angles from the topic, use `WebSearch`, and collect candidate sources that define the field, recent work, closest prior work, benchmarks, and possible limitations.
3. **Source inspection and paper reading** - use `WebFetch` and paper-to-Markdown conversion for important sources, with full-text reading for the most central papers rather than relying only on titles, abstracts, or snippets.
4. **Paper notes and literature map** - extract structured notes about problems, methods, assumptions, results, limitations, datasets, metrics, and relationships among papers.
5. **Candidate gap formation** - form multiple candidate gaps that are not merely unfilled, but research-relevant, worth addressing, and potentially testable.
6. **Gap selection** - evaluate candidate gaps against the proposal quality target, considering grounding, importance, novelty potential, testability, topic faithfulness, and plausible technical routes without committing too early to a specific method.
7. **Candidate method generation** - for the selected gap, generate multiple possible methods or solution strategies with rationales for why each might address the gap.
8. **Method selection** - evaluate candidate methods against the proposal quality target, rejecting weak, unsupported, generic, or infeasible methods and selecting one method to develop.
9. **Proposal development** - write the final Markdown proposal in the required section structure, preserving the stance of a proposal rather than a completed empirical paper.
10. **Final validation** - have the lead agent follow its prompt/skill-guided self-check before stopping, then run a harness-level mechanical check for `proposal.md` existence and MVP-checked sections.

The workflow is allowed to loop. For example, weak candidate gaps may trigger additional targeted search, and method ideation may reveal that the selected gap needs more literature inspection. The final user-facing output should still be one proposal, not a transcript or list of alternatives.

## 4. Required output artifact

The only required persisted artifact is `proposal.md`, the final user-facing Markdown proposal.

The proposal itself should use the full canonical structure:

- `# Title`
- `## Introduction`
- `## Related Work`
- `## Methods`
- `## Experiments Design`
- `## Impact Statement`
- `## Success Criteria`
- `## References`

The mechanical check should be tolerant rather than Markdown-format-strict. It can accept a non-empty top-level proposal title instead of the literal heading `# Title`, and it can use normalized regular-expression matching for checked section intents. If `proposal.md` is missing or any checked section intent is absent, the shared framework should prompt the lead agent to repair the output before the run is considered complete.

The MVP mechanical validator checks only title, `Introduction`, `Related Work`, `Methods`, `Experiments Design`, and `References`. `Impact Statement` and `Success Criteria` remain part of the prompt/skill-guided proposal-quality contract, but their absence alone must not fail the MVP mechanical validator.

Other run materials, such as source registries, paper notes, candidate gaps, candidate methods, search logs, literature maps, draft outlines, and scoring tables, may be useful for debugging and auditability, but they are optional rather than required by this brief. Later design work should decide whether to persist any of them.

## 5. Prompt and skill strategy

The system should be primarily shaped through instructions and reusable skills rather than through a large custom runtime. The prompt and skill layer should encode the research process, not just the final writing format.

At minimum, the design should include guidance for:

- **Literature grounding** - how to generate search angles, decide when to fetch sources, request full-text Markdown for central papers, preserve source metadata, and avoid relying on snippets or model memory.
- **Gap formation and selection** - how to distinguish worthwhile research gaps from trivial unfilled spaces and select one grounded, important, specific, testable gap.
- **Method ideation and selection** - how to generate multiple concrete methods for one selected gap and reject methods that are generic, unsupported, infeasible, or too close to existing work.
- **Experiment design** - how to specify hypotheses, datasets, baselines, metrics, ablations, analysis plans, failure modes, and subfield-appropriate sufficiency.
- **Proposal writing and semantic final checking** - how to write the final document as the front half of an AI conference paper plus Impact Statement and Success Criteria, then check for fabricated or ungrounded literature claims, fake empirical results, weak experiment design, missing success/refinement/stop criteria, and other proposal-quality failures before stopping.

The detailed prompt, skill, and subagent design for this layer lives in [`QUALITY_LAYER_DESIGN.md`](../design/QUALITY_LAYER_DESIGN.md). The expected direction is a combination of always-on startup instructions for hard constraints and configurable skills for reusable procedures; selected skills should be provided to the model for a run, while unselected skills should remain unavailable or out of context.

## 6. MCP retrieval expectations

The MCP retrieval tools are the evidence channel for the proposal. The lead agent should use them to search, fetch, and read sources before making literature-grounded claims.

At this level, the key expectations are simple:

- Do not rely on model memory or search snippets for related-work claims.
- Fetch or read sources before citing them or using them to support a gap.
- Use full-text paper Markdown for the most central papers when available.
- Treat retrieval failures as uncertainty, not evidence.

The implemented scholar-gateway tool names are `web_search`, `web_fetch`, and `extract_paper`; harness-specific rendered names include the `mcp__scholar-gateway__` prefix where applicable. Thresholds, retry behavior, and deeper source-tracking rules remain quality-layer concerns unless promoted into a later runtime contract.

## 7. Final validation expectations

Final validation refers to the harness-level completion gate.

Before final validation runs, the lead agent should be instructed through prompts and skills to review its own proposal before finishing. This prompt/skill-guided self-check should cover the hard constraints and proposal quality target: source-faithful literature claims, no fake empirical results, one selected idea, closest prior work and delta, concrete gap, specific method, sufficient experimental design, non-boilerplate impact and success criteria, and honest limitations. The exact checking process is led by the agent during the run.

The final validation check itself is harness-level mechanical checking after the lead agent stops. This check should be rule-based, tolerant, and mandatory: verify that `proposal.md` exists and contains recognizable non-empty versions of the MVP-checked sections: title, `Introduction`, `Related Work`, `Methods`, `Experiments Design`, and `References`. If this mechanical check fails, the shared framework should run the configured external repair loop before the run is considered complete.

Later design work should decide whether any semantic checks are promoted from lead-agent self-checks into deterministic or semi-deterministic validators. At this stage, only the `proposal.md` existence and required-section checks are assumed to be mandatory harness-level checks.
