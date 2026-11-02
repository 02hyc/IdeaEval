# Architecture - Autonomous AI Research-Ideation Agent

> **Status:** Architecture description for the implemented MVP.
> **Last updated:** 2026-06-04
> **Concept source:** [`PROJECT_OVERVIEW.md`](../concept/PROJECT_OVERVIEW.md)
> **System brief:** [`SYSTEM_DESIGN_BRIEF.md`](../concept/SYSTEM_DESIGN_BRIEF.md)
> **Quality layer:** [`QUALITY_LAYER_DESIGN.md`](./QUALITY_LAYER_DESIGN.md)
> **Runtime contracts:** [`MVP_RUNTIME_CONTRACTS.md`](../contracts/MVP_RUNTIME_CONTRACTS.md)
> **CLI spec:** [`CLI_SPEC.md`](../contracts/CLI_SPEC.md)
> **Python API spec:** [`PYTHON_API_SPEC.md`](../contracts/PYTHON_API_SPEC.md)
> **Validation and repair:** [`VALIDATION_REPAIR_AND_RUN_RECORD.md`](../contracts/VALIDATION_REPAIR_AND_RUN_RECORD.md)

This document defines the MVP architecture for the autonomous AI research-ideation agent as implemented in this repository. It is intended to explain the runtime boundaries without turning research judgment into hand-written orchestration logic.

## MVP Boundary

The first version accepts one high-level AI research topic, creates one isolated run workspace, starts one autonomous lead-agent session, gives that agent access to the configured external retrieval MCP server, and requires exactly one final user-facing artifact: `proposal.md`.

The first version must support the complete research-ideation behavior described in [`PROJECT_OVERVIEW.md`](../concept/PROJECT_OVERVIEW.md): interpret an ambiguous topic, explore the literature, inspect important sources, form and compare candidate gaps, select one gap, generate and compare candidate methods, select one method, design experiments, write the final proposal, and repair the final artifact if it is mechanically incomplete.

The first version does not run experiments, report empirical results, benchmark the agent across many topics, ask the user for mid-run clarification, maintain database-backed long-term memory, implement its own search crawler or PDF parser, prove novelty automatically, enforce semantic proposal quality mechanically, or launch a fixed externally orchestrated multi-agent pipeline.

The first version should stay small. The core product is a reliable autonomous topic-to-proposal run, not a general research operating system.

## First-Principles Design Commitments

- **One lead agent owns the run:** The framework creates the conditions for a strong autonomous run, but it does not split the research process into externally scheduled pipeline steps. One primary agent remains accountable for search decisions, evidence interpretation, candidate selection, proposal writing, and final self-checking.
- **The only required output is `proposal.md`:** Optional traces can improve inspectability, but the MVP succeeds or fails on whether the final proposal artifact exists and has the required structure.
- **Quality comes from model behavior plus instructions:** Proposal quality, source discipline, novelty reasoning, experiment design, and semantic self-review are encoded in the startup prompt and reusable skills.
- **The harness enforces mechanics only:** The framework enforces workspace isolation, SDK configuration, MCP availability, final mechanical validation, bounded repair, and run records.
- **External MCP is the evidence channel:** Literature search, URL fetch, paper download, and paper-to-Markdown conversion are delegated to the configured MCP server rather than implemented in this repository.
- **Proposal stance is mandatory:** The output is a proposal for future experiments, not a completed empirical paper. It may state hypotheses and expected observations, but it must not imply that experiments were executed.

## Two-Layer Architecture

The system is intentionally split into an infrastructure layer and a proposal-quality layer.

- **Infrastructure layer:** Implements repeatable run mechanics: configuration loading, isolated workspace creation, harness selection, startup prompt delivery, selected-skill packaging, MCP configuration, permission and budget handling, artifact collection, mechanical validation, bounded repair, and run record writing. This layer should be correct, complete, and mostly domain-agnostic.
- **Proposal-quality layer:** Shapes the research behavior that produces a strong `proposal.md`: startup prompt design, reusable skills, optional subagent prompts, and semantic self-check expectations. This layer is expected to evolve through experiments and qualitative review.
- **Boundary rule:** MCP setup belongs to infrastructure, while guidance about how to use MCP for grounded literature work belongs to prompt and skills. Mechanical validation belongs to infrastructure, while semantic proposal review belongs to prompt, skills, and optional subagent behavior.

The concrete prompt, skill, and subagent design for the proposal-quality layer is specified in [`QUALITY_LAYER_DESIGN.md`](./QUALITY_LAYER_DESIGN.md).

## Python-First Runtime Stance

The MVP is Python-first. A shared Python framework owns harness-independent mechanics: configuration loading, run workspace creation, prompt rendering, skill packaging, MCP configuration generation, SDK adapter dispatch, mechanical proposal validation, repair-loop control, and run record writing.

Both supported harnesses must be reached through Python adapters that use the official Python SDK surface for the selected harness. The MVP implementation must not shell out to a CLI, use a TypeScript SDK path, or maintain alternate fallback adapter surfaces.

Python does not implement the research workflow as procedural business logic. Python prepares the run, launches the agent, validates the required artifact, and records what happened. The lead agent, prompt, skills, model, and MCP tools perform the research-ideation work.

## External Interface and Inputs

The MVP exposes a CLI as the primary user-facing interface and a Python API as the core programmatic interface. The CLI is a thin wrapper around the Python framework, not a separate implementation path.

The CLI accepts one required runtime input: the research topic. It supports passing the topic directly or through a topic file. It also accepts a configuration file path and optional simple overrides for common fields such as harness, model, effort, run ID, workspace root, and repair settings. The implemented command contract is specified in [`CLI_SPEC.md`](../contracts/CLI_SPEC.md).

```bash
uv run research-ideation-agent run --topic "Improving visual reasoning in multimodal models" --config configs/scientific-hypothesis-generator/gpt.yaml
uv run research-ideation-agent run --topic-file examples/question.txt --config configs/scientific-hypothesis-generator/gpt.yaml
```

The Python API exposes the same run behavior for tests, automation, future batch runners, and future UI layers. A representative shape is:

```python
from research_ideation_agent import load_config, run_research_ideation

config = load_config("configs/scientific-hypothesis-generator/gpt.yaml")
result = run_research_ideation(
    topic="Improving visual reasoning in multimodal models",
    config=config,
)
```

Run configuration is separate from the topic. It selects how the run executes: harness, model, effort, MCP profile and parameters, workspace root, budgets, repair limit, approval and harness-specific tool settings, selected skills, and selected subagents. Selected skills and selected subagents are part of the run request/config, not global framework defaults hardcoded into the runtime.

Secrets are not runtime inputs in CLI flags or committed config files. API keys and similar secrets must come from environment variables or deployment configuration; config files may name required environment variables but must not contain secret values.

## Core Runtime Contracts

Concrete MVP schemas for `RunConfig`, `RunRequest`, `RunResult`, `ValidationResult`, `RepairAttempt`, and `UsageSummary` are specified in [`MVP_RUNTIME_CONTRACTS.md`](../contracts/MVP_RUNTIME_CONTRACTS.md). This section describes their architectural roles rather than duplicating the full field-level contract.

The implemented public Python entrypoints, exception behavior, and serialization rules are specified in [`PYTHON_API_SPEC.md`](../contracts/PYTHON_API_SPEC.md).

### Run Request

A run request contains the user topic, run configuration, and output workspace root. The configuration resolves the selected harness, model and effort if configured, run budget settings, MCP profile and parameters, approval and harness-specific tool settings, selected skills, optional selected subagents, and repair limits. The user topic is recorded verbatim in framework-owned run metadata and is not rewritten by the framework.

### Run Workspace

Each run receives an isolated per-run directory under the configured workspace root. That directory contains an agent-visible workspace plus framework-owned metadata. The agent-visible workspace is the adapter working directory and contains `proposal.md`, retrieved paper materials, and harness-specific packaged skills or always-on guidance if needed. Framework-owned metadata contains rendered prompts, generated MCP or settings files, validation output, optional run logs, and resolved configuration.

The agent-visible workspace is the boundary for agent file writes. The framework may also maintain a separate immutable asset directory for canonical prompts and skills.

### Proposal Artifact

`proposal.md` is the only required user-facing artifact. It must contain one selected proposal and recognizable versions of the full canonical proposal sections.

Optional trace artifacts may include search logs, source registries, paper notes, candidate gap tables, candidate method tables, draft outlines, validation notes, and raw SDK event logs. These are sensitive debugging and audit aids, not MVP completion requirements.

### Run Result

Every adapter returns a common run result to the shared framework: harness name, success or failure, session or thread identifier if available, model and usage metadata if available, validation result, repair attempts, artifact paths, and error details when the run fails.

## System Components

### Configuration

Configuration defines how a run is launched without changing the research prompt itself. It includes the selected harness, model, effort, MCP server profile and parameters, approval and harness-specific tool settings, workspace root, budget and timeout settings, repair-attempt limit, optional subagent profile, and selected skills. The shared framework packages only the selected skills into the agent-visible workspace or harness-specific discovery location.

A default configuration profile should be suitable for a normal MVP run, but every quality-layer surface should remain configurable. In particular, a run can expose all core skills, a subset of skills, no optional skills, selected reviewer subagents, or no subagents.

### Shared Python Framework

The shared Python framework is the top-level implementation layer. It loads configuration, creates the per-run directory and agent-visible workspace, renders the startup prompt, packages selected skills, writes MCP and settings files, invokes the selected adapter, runs the mechanical validator, controls bounded repair, and writes the run record. It must not embed research-domain heuristics such as paper-ranking rules, gap-scoring formulas, method-selection logic, or proposal-writing templates beyond static prompt and skill assets.

### Harness Adapters

A harness adapter is a thin Python integration layer for one SDK surface. It translates the shared run request into SDK-specific options, starts or resumes the agent session, exposes MCP tools and selected skills, captures available run events, and returns the common run result. Claude and Codex adapters may use different SDK options for permissions, MCP configuration, hooks, approval, event streaming, and session continuation, but neither adapter should contain research-domain logic.

### Lead Research-Ideation Agent

The lead agent is the central reasoning actor. It interprets the topic, searches and reads sources, forms and selects gaps, generates and selects methods, designs experiments, writes `proposal.md`, and performs semantic self-checking before stopping. It may loop between activities when evidence or proposal quality requires it.

### Startup Prompt

The startup prompt is the run-specific instruction passed when the lead-agent session begins. It includes the user topic, mission, workflow, output contract, MCP tool descriptions, hard constraints, semantic self-check expectations, and stopping condition. It must remain separate from skills: the prompt defines this run's task, while skills provide reusable procedures.

### Skills

Skills are reusable instruction packages for repeatable research behaviors. Canonical skill content should live in shared Markdown assets and be packaged into the selected harness's validated skill layout, exposing only the skills selected by configuration. Skills influence behavior; the harness does not mechanically verify whether a skill was invoked or followed.

### External Retrieval MCP

The retrieval MCP server provides search, fetch, and paper-reading tools. The retrieval backend is outside this repository; this project owns only MCP configuration, tool-use instructions, approval or allowlist settings, and prompt/skill guidance for responsible source use. Secrets such as API keys must remain in environment variables or deployment configuration.

### Optional Subagent Tools

Subagents are optional callable helpers inside the lead-agent run. They may help with bounded review tasks, but they are not separate pipeline stages and do not own final decisions. The current MVP ships subagent prompt assets, and the example Claude and Codex configs select the four reviewer subagents. The framework packages those assets into the selected harness's discovery layout.

### Final Validator

The final validator is a harness-level rule-based check. It verifies only that `proposal.md` exists and includes recognizable versions of the MVP-checked core sections: title, introduction, related work, methods, experiments design, and references. `Impact Statement` and `Success Criteria` remain required in the prompt and quality layer, but they are not MVP mechanical validation gates.

Semantic quality checks are lead-agent behaviors encoded in prompts and skills. The MVP validator does not judge novelty, citation faithfulness, experiment quality, or writing quality.

## Run Flow

### Framework Flow

1. **Accept topic:** Receive one high-level AI research topic and selected harness configuration.
2. **Create workspace:** Create a per-run directory with an agent-visible workspace and framework-owned metadata paths.
3. **Render assets:** Render the startup prompt, package selected skills into the agent-visible workspace when required by the harness, and write harness-specific MCP/settings files with paper output paths targeting the agent-visible workspace.
4. **Start lead agent:** Invoke the selected Python adapter with the prompt, agent workspace, skills, MCP config, permissions, budgets, and optional subagent configuration.
5. **Let agent run:** Allow the lead agent to complete autonomously within SDK, timeout, turn, or cost boundaries.
6. **Validate proposal:** Run the mechanical validator against `proposal.md`.
7. **Repair if needed:** If validation fails, generate a focused repair instruction and continue the same session or thread when supported.
8. **Stop boundedly:** Repeat validation and repair until the proposal is mechanically valid or the configured repair-attempt limit is exhausted.
9. **Write run record:** Record success or failure, validation result, repair attempts, session identifiers, artifact paths, and available usage metadata.

### Lead-Agent Behavioral Flow

The lead agent's behavioral flow is specified by the startup prompt and skills. It should use MCP tools to search and inspect sources, read central papers, form and select a grounded gap, generate and select a method, design experiments, write `proposal.md`, perform semantic self-checking, and stop only after the final artifact exists.

The framework does not monitor whether each behavioral step happened. It only sets up the run and validates the final mechanical artifact.

## SDK and Harness Boundary

- **SDKs own agent execution:** model invocation, session or thread management, tool calling, MCP exposure, file editing tools, sandbox or permission behavior, event streaming, lifecycle hooks, subagents, and structured output features where available. The MVP adapters must use official Python SDKs only.
- **The shared Python framework owns run mechanics:** configuration, workspace setup, prompt rendering, skill packaging, MCP config rendering, adapter dispatch, proposal validation, repair-loop counting, and run records.
- **Prompts and skills own research behavior:** literature-search strategy, source discipline, candidate gap and method evaluation, experiment-design expectations, proposal-writing expectations, and semantic self-checking.
- **The external MCP server owns retrieval implementation:** web search, source fetch, paper download, PDF extraction, paper-to-Markdown conversion, backend ranking, retries, and retrieval-specific error behavior.

If a decision is about research quality, place it in prompt or skill assets. If it is about running the agent or accepting the final artifact, place it in the shared framework or adapter. If it is about retrieving external literature, place it in the MCP server or MCP configuration.

## Prompt, Skills, MCP, and Subagent Organization

### Startup Prompt Organization

The startup prompt should include:

- The user topic, quoted or otherwise clearly delimited.
- The mission: produce one literature-grounded research proposal, not a brainstorm or paper summary.
- The autonomous workflow: explore literature, inspect sources, form candidate gaps, select a gap, generate candidate methods, select a method, design experiments, write the proposal.
- The MCP tool overview, because MCP tool semantics are not guaranteed to be automatically understood by the model.
- Grounding rules: inspect sources before citing them, distinguish hypotheses from claims, and treat retrieval failures as uncertainty.
- Hard prohibitions: no fabricated references, no uninspected citations, no fake empirical results, no mid-run clarification questions, no list of alternatives as the final answer.
- Required `proposal.md` headings and completion behavior.
- Semantic self-check expectations before stopping.

### Skill Organization

The initial shared skill set should be small and procedural:

- **Literature grounding:** Guide search, source inspection, paper reading, source-faithful notes, and uncertainty handling as one evidence-chain procedure.
- **Gap selection:** Generate multiple candidate gaps and compare them for grounding, importance, novelty potential, testability, topic faithfulness, and plausible technical routes.
- **Method design:** Generate multiple concrete methods for the selected gap and reject methods that are generic, unsupported, infeasible, or too close to existing work.
- **Experiment design:** Specify hypotheses, tasks or datasets, baselines, metrics, ablations, analysis plans, failure modes, and resource assumptions.
- **Proposal writing and final check:** Write one coherent proposal in the required structure and check for fabricated literature claims, fake results, missing closest-work comparison, weak experiments, unsupported novelty claims, and missing success/refinement/stop criteria.

Skill files should describe when to use the skill, what inputs it assumes, what internal reasoning output it should produce, and how it contributes to `proposal.md`. Skills should not require mandatory persisted intermediate artifacts unless a future architecture revision promotes those artifacts into the completion contract.

Always-critical constraints must not live only in optional skills. Requirements such as no fabricated references, no fake empirical results, required proposal headings, and writing `proposal.md` must appear in the startup prompt or other always-on workspace guidance. Skills may reinforce these constraints, but the run must not depend on the lead agent choosing to invoke a skill before seeing them.

### MCP Tool Organization

MCP tools should be organized conceptually into search, fetch, and paper-reading capabilities. The startup prompt should name the configured tools and explain when to use each category.

Search tools identify candidate sources. Fetch tools inspect source pages or abstracts. Paper-reading tools provide full-text Markdown for central papers. The lead agent should use fetched or full-text content for final related-work claims whenever possible.

Harness adapters may need different MCP configuration formats, tool restrictions, or approval settings. Those differences belong in adapter configuration, not in the shared research prompt except where the agent needs to know tool names.

### Subagent Organization

Subagents should be exposed only as optional helpers. Suitable subagent prompts are bounded review prompts such as "check whether this selected gap is supported by the provided notes" or "challenge this experiment plan for missing baselines and ablations."

The lead agent decides whether to call subagents if they are available. The final proposal should not depend on subagents being present. In the implemented MVP, reviewer subagents are packaged for Claude under `.claude/agents/*.md` and for Codex under `.codex/agents/*.toml` when selected.

## Proposal Artifact Generation

The final user-facing artifact is `proposal.md`. It must contain one selected research proposal rather than a set of alternatives.

The canonical section structure is:

```markdown
# <Actual Proposal Title>
## Introduction
## Related Work
## Methods
## Experiments Design
## Impact Statement
## Success Criteria
## References
```

The proposal should read like the front half of an AI conference paper extended with proposal-specific decision criteria. It should motivate the problem, synthesize related work, identify the closest prior work and the delta, describe the selected method in implementable detail, define experiments that test the central claim, explain impact, and specify success, refinement, and stop criteria.

The `References` section must list only real sources that were retrieved or fetched during the run. The proposal may express uncertainty when retrieval is incomplete, but it must not invent plausible-looking citations.

## Grounding and Source Discipline

Grounding is a hard behavioral rule for the lead agent, encoded in the startup prompt and skills. It is not a harness-monitored condition in the MVP.

A source can support a final proposal claim only after the lead agent has inspected it through fetch or paper-reading tools. Search results can guide what to inspect, but they are not enough to justify final related-work claims unless the search result itself contains the inspected source content.

The lead agent should preserve enough source metadata to write verifiable references: title, authors if available, year if available, URL or stable identifier, retrieval method, and notes on what claims the source supports.

The proposal should distinguish grounded claims, hypotheses, and uncertainties. If the agent cannot verify whether a direction is saturated, it should state the limits of retrieval rather than claiming novelty as fact.

## Final Validation and Repair Loop

Final validation is a harness-level completion gate. It runs after the lead agent stops or after an SDK turn completes.

The mechanical validator must check whether `proposal.md` exists and whether it contains recognizable non-empty versions of the MVP-checked section intents: title, introduction, related work, methods, experiments design, and references. It must not fail the run only because `Impact Statement` or `Success Criteria` is missing.

The validator should be tolerant rather than format-strict. It may normalize whitespace and capitalization, accept a non-empty top-level heading as the proposal title, and use the alias rules defined in [`VALIDATION_REPAIR_AND_RUN_RECORD.md`](../contracts/VALIDATION_REPAIR_AND_RUN_RECORD.md). Its purpose is to catch missing or obviously malformed output, not to enforce a Markdown style guide.

If validation fails, the framework should generate a focused repair instruction that names the missing artifact or section intents. The repair instruction should tell the lead agent to repair the existing `proposal.md` in place, preserve valid content where possible, add missing checked sections, and stop after the file is mechanically valid.

Repair must be bounded by configuration and controlled by the shared framework using `repair.strategy: external-loop`. Same-session or same-thread repair should be attempted first. If the Python SDK cannot reliably continue the prior session/thread, the adapter may fall back to a fresh repair run only if that fallback is recorded in `RepairAttempt`. If the validator still fails after the maximum number of repair attempts, the run fails with the validator result and available run metadata.

Semantic validation is not part of the MVP harness. Candidate future checks could include reference-format checks, citation-to-source-registry checks, banned empirical-result phrasing checks, and lightweight section-quality rubrics, but those checks require a later architecture decision and reliable validator inputs.

## Implemented MVP Units

The MVP implementation is organized around these units:

- **Configuration loader:** Read harness, model, effort, MCP profile and parameters, selected skills, workspace root, budgets, repair limits, approval settings, harness-specific tool settings, and secret environment variable names.
- **Workspace manager:** Create isolated per-run directories and prepare prompt, skill, config, artifact, and log locations.
- **Prompt renderer:** Render the startup prompt from shared assets and the user topic.
- **Skill packager:** Copy only the configured shared skill assets into the selected harness's discoverable skill layout.
- **MCP config renderer:** Generate harness-specific MCP configuration without embedding secrets.
- **Harness adapters:** Implement Claude and Codex Python adapters behind a shared interface.
- **Proposal validator:** Check `proposal.md` existence and recognizable MVP-checked proposal sections with tolerant matching.
- **Repair controller:** Generate repair instructions and bound same-session or same-thread repair attempts.
- **Run recorder:** Persist run metadata, validation results, repair attempts, and useful SDK output.
- **Python API:** Expose the core `run_research_ideation(topic, config)` behavior for tests, automation, future batch runners, and future UI layers.
- **CLI entrypoint:** Accept a topic or topic file, configuration path, and simple overrides, run the Python API, and report the path to `proposal.md` plus success or failure.

These units are implementation guidance, not a requirement to create exactly these filenames or classes.

## Non-MVP and Future Architecture Decisions

- **Concrete MCP schemas:** Define exact tool names, input parameters, output fields, source identifiers, retry behavior, and error handling after the retrieval MCP server interface is finalized.
- **Trace persistence:** Decide whether source registries, paper notes, candidate gaps, candidate methods, and scoring tables should remain prompt/skill-guided scratch artifacts or become persisted artifacts in a later version.
- **Citation format:** Decide whether the proposal should use plain Markdown references, numbered citations, author-year citations, BibTeX-compatible entries, or another convention.
- **Semantic validators:** Decide whether any proposal-quality checks can be promoted from prompt/skill-guided self-checking into deterministic or semi-deterministic harness checks.
- **Harness parity:** Define how much behavior must be identical between Claude Code Agent SDK and Codex SDK and where harness-specific differences are acceptable.
- **Fallback adapters:** Alternate harness surfaces such as CLI or TypeScript SDK paths are outside the MVP and must not be implemented as production fallback paths unless a later owner decision changes the Python-SDK-only constraint.
- **Subagent availability:** Decide whether specific subagent helpers should be exposed by default or only when the harness already provides them.
- **Run budgets:** Separate prompt/skill guidance for search breadth and paper reading from framework limits such as maximum run duration, turn limits, cost limits, and repair attempts.
- **Evaluation outside MVP:** Decide later how to evaluate agent quality across many topics without making benchmarking part of the core proposal-generation workflow.
