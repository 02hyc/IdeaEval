# Scientific Hypothesis Generator Baseline Upgrade Spec

## Purpose

This spec defines the next upgrade of the `scientific-hypothesis-generator` baseline agent. The goal is to reduce avoidable proposal failures observed in generated proposals while preserving the benchmark setting: the agent should behave like a normal CLI research agent with tool-mediated literature access, not like an agent explicitly told the benchmark cutoff or scoring policy.

The upgrade targets four failure classes:

1. Missed closest prior work that should have been found by the available tools.
2. Novelty claims that are cosmetic, repackaged, or too weakly differentiated.
3. Experiment plans that can run but do not actually test or falsify the central hypothesis.
4. Baseline and confound omissions that make positive results uninterpretable.
5. Methods that are too vague to implement or internally inconsistent.
6. Methods that do not actually address the problem's causal bottleneck.
7. Reflection artifacts that identify weaknesses but do not repair them.

## Design Principle

Cutoff date is an external benchmark constraint. It must be enforced by the harness and evidence tools, recorded for reproducibility, and used by evaluators. It must not be exposed to the agent as task context.

The agent should only see:

- the research topic;
- the available evidence tools;
- normal source-grounding requirements;
- the required workflow and output artifacts;
- quality instructions for literature review, novelty stress testing, hypothesis reflection, experiment design, and proposal audit.

The agent should not see:

- the actual cutoff date;
- statements that the run is simulating a date boundary;
- post-cutoff rejection explanations;
- instructions to perform cutoff-aware novelty reasoning;
- benchmark-fairness language.

## Observed Failure Modes

### Failure Mode A: Cutoff leakage risk

Current prompts and skills mention cutoff date directly. This can contaminate benchmark behavior because the agent may optimize around the visible cutoff, use it as an excuse for weaker novelty claims, or write artificial statements such as "as of date X."

Required fix: remove cutoff date from all agent-visible prompts, skills, task contracts, and workspace metadata. Keep cutoff in framework-only metadata and MCP launch configuration.

### Failure Mode B: Missed closest prior work

The safety proposal missed a close pre-cutoff method, Safety-Aware Probing, which is highly relevant to hidden-state safety preservation during fine-tuning. The issue is not only search breadth; the agent did not run an adversarial closest-work search aimed at finding papers that could invalidate the selected hypothesis.

Required fix: add a mandatory novelty stress test after selecting a hypothesis and before final experiment design.

### Failure Mode C: Weak novelty delta

The final comparison table can make a proposal appear distinct through wording even when the mechanism, intervention, setting, and claim are substantially the same as prior work.

Required fix: require a structured novelty-delta table that compares the selected hypothesis against closest work on scientific dimensions, not prose similarity.

### Failure Mode D: Weak baseline gate

The memory proposal treated important external baselines as optional. If the strongest relevant baselines are not included, the claim must be narrowed. Otherwise positive results cannot establish the stated contribution.

Required fix: add a baseline gate that classifies required, substitute, and optional baselines and forces claim downgrades when required baselines cannot be evaluated.

### Failure Mode E: Experiments do not isolate the hypothesis

Several proposed experiments could show a performance gain without demonstrating the proposed mechanism. Examples:

- Safety regularization may improve scores by increasing over-refusal, not preserving a safety margin.
- Dual-store memory may improve scores because it uses more construction compute, stronger prompts, or better reranking, not because evidence and abstraction are separated.

Required fix: add a hypothesis-to-test matrix that links every subclaim to a direct test, control, confound ruled out, and refutation pattern.

### Failure Mode F: Method is underspecified or internally inconsistent

Some proposals describe a method with terms such as "confidence score", "contradiction-aware", "latent margin", "adaptive memory", or "safety signal" without defining exactly how the value is computed, when each module runs, what data it consumes, and what output it produces. Other proposals contain hidden contradictions, such as using the same data as both anchor construction and held-out evaluation, treating a component as both learned and fixed, or optimizing a proxy that does not match the evaluation target.

Required fix: add a method specification audit before experiment design. If the method cannot be written as an implementable pipeline with inputs, outputs, state, algorithms, hyperparameters, and failure modes, the agent must revise the method or narrow the claim.

### Failure Mode G: Method does not solve the problem

An agent can propose a plausible-sounding method that improves a proxy metric while leaving the stated problem unresolved. For example, a safety method may reduce harmful-response rate by increasing over-refusal rather than preserving safety alignment; a memory method may improve benchmark scores by spending more construction compute rather than solving provenance collapse.

Required fix: add a problem-method fit audit. The agent must decompose the problem into requirements, identify the causal bottleneck, show which method component addresses each requirement, construct a minimal worked example, and produce counterexamples where the method would fail. If gaps remain, it must revise the method, hypothesis, evaluation, or claim scope.

### Failure Mode H: Reflection is too checklist-like

The current reflection protocol asks the right questions but does not force hard revisions. The agent can write an audit that acknowledges risks without changing the hypothesis or experiment plan.

Required fix: make reflection produce explicit revision decisions. Major weaknesses must cause a concrete revision, claim narrowing, baseline addition, ablation addition, or candidate replacement.

## Required Artifact Changes

The upgraded baseline agent must produce the following additional notes:

- `notes/novelty_stress_test.md`
- `notes/problem_method_fit_audit.md`
- `notes/method_specification_audit.md`
- `notes/baseline_gate.md`
- `notes/hypothesis_to_test_matrix.md`

These are added to the existing required outputs:

- `notes/task_contract.md`
- `notes/run_metadata.json`
- `notes/literature_review.md`
- `notes/gap_analysis.md`
- `notes/cross_domain_ideas.md`
- `notes/research_questions.md`
- `notes/hypothesis_candidates.md`
- `notes/hypothesis_reflection.md`
- `notes/experiment_design.md`
- `notes/proposal_audit.md`
- `proposal.md`

## Artifact Specifications

### `notes/novelty_stress_test.md`

Purpose: force the agent to search for and reason about the closest work that could invalidate the selected hypothesis.

Timing: after a candidate hypothesis is selected, before final experiment design.

Required sections:

- `# Novelty Stress Test`
- `## Selected Hypothesis`
- `## Claim Decomposition`
- `## Adversarial Search Queries`
- `## Closest Prior Work Candidates`
- `## Novelty Delta Table`
- `## Repackaging Risk`
- `## Revision Decision`

Required claim decomposition fields:

| Field | Description |
|---|---|
| Mechanism | The scientific mechanism or causal object claimed. |
| Intervention | The method, dataset, benchmark, architecture, or analysis introduced. |
| Setting | Model family, task type, training or inference regime, domain, and scale. |
| Metrics | Main metrics and evaluation protocols. |
| Causal claim | What the proposal claims would explain or prove. |

Required novelty delta table:

| Prior work | Same mechanism? | Same intervention? | Same setting? | Same metrics? | Same causal claim? | Real delta | Verdict |
|---|---|---|---|---|---|---|---|

Verdict values:

- `distinct`: real scientific delta remains.
- `narrow`: only a narrower claim remains.
- `replication`: useful replication or robustness study, not a new method claim.
- `repackaging`: too close; select another hypothesis or change the question.
- `uncertain`: insufficient evidence; retrieve/read more or lower confidence.

Rules:

- Absence from search results is not evidence of absence.
- Search snippets are leads, not evidence.
- Closest-work delta claims require inspected source evidence.
- If the best available closest work cannot be inspected, novelty confidence must be downgraded.
- If verdict is `repackaging`, the agent must revise or replace the hypothesis before continuing.

### `notes/baseline_gate.md`

Purpose: ensure the experiment compares against baselines strong enough to support the claim.

Timing: after novelty stress test and before final experiment design.

Required sections:

- `# Baseline Gate`
- `## Central Claim`
- `## Required Baselines`
- `## Substitute Baselines`
- `## Simple Controls`
- `## Optional Baselines`
- `## Baseline Feasibility`
- `## Claim Downgrade Rules`
- `## Gate Decision`

Required baseline table:

| Baseline | Role | Why required | Source inspected? | Feasible? | If omitted, required claim downgrade |
|---|---|---|---|---|---|

Rules:

- Required baselines include closest prior work, strong standard baselines, and baselines needed to distinguish the proposed mechanism from simple alternatives.
- If a required baseline is infeasible, the proposal must narrow the claim and explain the limitation.
- External baselines must not be marked optional solely because integration is inconvenient.
- Simple controls are not substitutes for closest-work baselines.

### `notes/problem_method_fit_audit.md`

Purpose: test whether the proposed method actually addresses the stated problem rather than merely sounding related or improving a proxy.

Timing: after novelty stress test and before method specification or baseline selection.

Required sections:

- `# Problem-Method Fit Audit`
- `## Stated Problem`
- `## Problem Requirement Matrix`
- `## Causal Bottleneck Check`
- `## Method-to-Requirement Coverage`
- `## Minimal Worked Example`
- `## Counterexample Test`
- `## Metric-Problem Alignment`
- `## Required Fixes`
- `## Revised Problem, Method, or Claim`

Required problem requirement table:

| Problem requirement | Why required | Evidence from literature | Method component that addresses it | Gap |
|---|---|---|---|---|

Required causal bottleneck answers:

- What is the problem's main causal bottleneck?
- Which variable does the method change?
- Why should changing that variable affect the target outcome?
- Could the method improve the metric while leaving the bottleneck unresolved?
- What would show that the method failed to address the bottleneck?

Required minimal worked example:

- A small concrete input.
- The intermediate output of each method component.
- The final answer, prediction, behavior, or artifact.
- The exact step where the original problem is solved.
- The exact step where the method would fail if its core assumption were false.

Required counterexample table:

| Counterexample | Why method may fail | What this reveals | Fix or claim downgrade |
|---|---|---|---|

Required metric-problem alignment table:

| Claimed problem | Metric | What the metric actually measures | Proxy failure mode | Fix |
|---|---|---|---|---|

Rules:

- If a core problem requirement has no method component, revise the method or narrow the problem.
- If the minimal worked example cannot be written concretely, the method is underspecified.
- If a counterexample exposes a plausible failure not covered by experiments, add a control, add an ablation, revise the method, or downgrade the claim.
- If metrics measure only a proxy, add direct diagnostics or narrow the conclusion to the proxy.

### `notes/method_specification_audit.md`

Purpose: make the method concrete, implementable, and internally consistent before experiment design.

Timing: after problem-method fit audit and before baseline gate.

Required sections:

- `# Method Specification Audit`
- `## Method Overview`
- `## Component Specification`
- `## Pipeline or Pseudocode`
- `## Ambiguous Terms Resolved`
- `## Internal Consistency Check`
- `## Implementation Risks`
- `## Required Fixes`
- `## Revised Method Specification`

Required component table:

| Method component | Operational definition | Inputs | Outputs | Called during train/inference/evaluation | Needed for which claim | What happens if removed | Implementation risk |
|---|---|---|---|---|---|---|---|

Required ambiguous-term table:

| Term | Operational definition | How computed or measured | Failure mode if vague |
|---|---|---|---|

Terms that must not remain undefined when used:

- confidence score
- contradiction-aware
- semantic consistency
- latent margin
- reflection
- structured hypothesis
- adaptive memory
- causal pathway
- importance score
- safety signal
- alignment signal
- provenance
- revision rule
- evidence support

Required internal consistency checks:

- Does the method rely on an unobservable variable?
- Are training objective and evaluation target aligned?
- Is any component described as both fixed and learned?
- Is any source used both to construct the method and evaluate it?
- Does the method require information not present in the proposed benchmark?
- Does the method contradict stated resource, data, or deployment constraints?
- Can two modules produce incompatible states or decisions?

Rules:

- If no implementable pipeline or pseudocode can be written, the method must be revised.
- If any central component is decorative and maps to no hypothesis claim, remove it or justify it as a control.
- If an internal contradiction remains, do not proceed to proposal writing.

### `notes/hypothesis_to_test_matrix.md`

Purpose: ensure each hypothesis subclaim is testable and confounds are addressed.

Timing: immediately before or during experiment design.

Required sections:

- `# Hypothesis To Test Matrix`
- `## Central Hypothesis`
- `## Subclaim Matrix`
- `## Confound Matrix`
- `## Refutation Patterns`
- `## Experiment Design Obligations`

Required subclaim table:

| Claim | Direct test | Necessary control | Confound ruled out | Refutation pattern |
|---|---|---|---|---|

Required confound table:

| Confound | Why plausible | Test or control | If not controlled, claim downgrade |
|---|---|---|---|

Rules:

- Every main hypothesis clause must appear in the matrix.
- Every proposed ablation must map to one mechanism, not a generic robustness checkbox.
- Every success criterion must correspond to at least one claim and one refutation pattern.
- If the experiment cannot falsify the central claim, the claim must be revised.

## Prompt And Skill Changes

### Startup prompt

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/prompts/startup.md`

Changes:

1. Remove the visible `Cutoff date` block.
2. Remove any statement that a configured cutoff date is injected into tools.
3. Remove the instruction to write cutoff date into `notes/task_contract.md`.
4. Keep a neutral statement that the evidence tools are the only supported literature access channel.
5. Add the five new required note files to the workflow and required files.
6. Add a stop condition requiring `novelty_stress_test.md`, `problem_method_fit_audit.md`, `method_specification_audit.md`, `baseline_gate.md`, and `hypothesis_to_test_matrix.md` to exist before final stop.

Agent-visible replacement language:

```text
Use the configured evidence tools for source discovery, fetching, and paper extraction.
If a source is unavailable through the evidence tools, record it as unavailable and do not infer its contents.
```

Forbidden agent-visible language:

```text
cutoff date
post-cutoff
as of <date>
date boundary
benchmark cutoff
```

### Literature review skill

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/skills/literature-review/SKILL.md`

Changes:

1. Remove `Cutoff date` from inputs.
2. Remove benchmark-mode cutoff confirmation.
3. Replace post-cutoff exclusion instructions with neutral unavailable-source handling.
4. Keep source grounding, query families, citation chasing, paper extraction, summaries, and raw-section verification.
5. Add a stronger instruction that the literature review should surface neighboring work that may later become closest prior work, but should not decide final novelty before a hypothesis exists.

Neutral unavailable-source language:

```text
If a source cannot be searched, fetched, or extracted by the evidence tools, record it in `## Excluded or Unfetched Sources` as unavailable, inaccessible, duplicate, weak, or out of scope. Do not infer its content.
```

### Hypothesis development skill

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/skills/hypothesis-development/SKILL.md`

Changes:

1. Add `notes/novelty_stress_test.md` after hypothesis selection and before experiment design.
2. Add `notes/problem_method_fit_audit.md` after novelty stress test.
3. Add `notes/method_specification_audit.md` after problem-method fit audit.
4. Add `notes/baseline_gate.md` before experiment design.
5. Add `notes/hypothesis_to_test_matrix.md` as the bridge into `notes/experiment_design.md`.
6. Strengthen hard revision triggers:
   - novelty stress test verdict is `repackaging`;
   - method does not cover a core problem requirement;
   - method cannot be specified as an implementable pipeline;
   - method contains an unresolved internal contradiction;
   - required baseline cannot be evaluated and the claim is not downgraded;
   - any central subclaim lacks a direct test or refutation pattern;
   - a plausible confound lacks a control and the claim is not downgraded.
7. Require `notes/hypothesis_reflection.md` to cite decisions from the five new artifacts.

### Proposal write-up and structural audit skill

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/skills/proposal-writeup-audit/SKILL.md`

Changes:

1. Add the five new notes to required inputs.
2. Require final proposal claims to match the revised hypothesis after novelty stress test, problem-method fit audit, method specification audit, baseline gate, and hypothesis-to-test matrix.
3. Require `notes/proposal_audit.md` to check structural completeness and traceability:
   - no claim exceeds `novelty_stress_test.md`;
   - the method addresses `problem_method_fit_audit.md`;
   - the method is implementable and self-consistent under `method_specification_audit.md`;
   - all required baselines from `baseline_gate.md` are included or claim downgrades are visible;
   - experiments satisfy `hypothesis_to_test_matrix.md`;
   - unresolved confounds are stated as limitations.
4. Hand off to `proposal-reflection-revision` for deep post-draft reflection and repair.
5. Do not add cutoff-aware audit language to the agent-facing skill.

### Proposal reflection and revision skill

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/skills/proposal-reflection-revision/SKILL.md`

Required behavior:

1. Run only after `proposal.md` has been written.
2. Treat final reflection as a mandatory quality gate, not an optional polish pass.
3. Write `notes/proposal_reflection.md`.
4. Check novelty against prior work, including whether the proposal is genuine innovation or only repackaging existing technology.
5. Check problem-method fit, method implementability, hidden assumptions, internal contradictions, experiment-hypothesis alignment, necessary baselines, targeted ablations, standard benchmarks/models/metrics, falsifiability, feasibility, and scientific impact.
6. For every critical or major issue, either revise `proposal.md` or narrow/downgrade the affected claim and record the residual limitation.

### Stop hook

File:

- `src/research_ideation_agent/assets/scientific-hypothesis-generator/hooks/stop_artifact_gate.py`

Changes:

1. Add the five new required note files and `notes/proposal_reflection.md` to the required artifact list.
2. Update the reflection reminder to point at `proposal-reflection-revision` and include novelty stress test, problem-method fit audit, method specification audit, baseline gate, and hypothesis-to-test matrix.
3. Do not mention cutoff.
4. Preserve bounded reminder behavior to avoid infinite stop-hook loops.

## Harness And Tool Changes

### Harness metadata

Files:

- `src/research_ideation_agent/runtime.py`
- `src/research_ideation_agent/prompting.py`
- `run_record.json` writing path

Changes:

1. Keep cutoff date in framework-only run metadata and `run_record.json`.
2. Do not include cutoff date in startup prompt variables rendered for the agent.
3. Do not include cutoff date in agent workspace files generated by the harness.
4. Ensure trace viewer can display cutoff as framework metadata only, clearly separate from agent messages.

### MCP launch

Files:

- `src/research_ideation_agent/mcp.py`
- `src/research_ideation_agent/scholar_gateway/cli.py`
- `src/research_ideation_agent/scholar_gateway/server.py`

Changes:

1. Continue passing cutoff date to scholar-gateway MCP as a startup argument.
2. Do not expose cutoff date in tool schemas.
3. Do not expose cutoff date through agent-visible tool descriptions.

### Scholar gateway responses

Files:

- `src/research_ideation_agent/scholar_gateway/client.py`
- server/tool response paths that format errors

Changes:

1. When a source is blocked by evidence policy, return a neutral unavailable message to the agent.
2. Avoid messages that reveal exact cutoff or post-cutoff reason.
3. Preserve detailed reason in framework logs if needed for evaluator debugging, but not in agent-visible content.

Suggested agent-visible message:

```text
Source unavailable under the configured evidence policy.
```

Suggested framework-only metadata:

```json
{
  "unavailable_reason": "post_cutoff",
  "cutoff_date": "YYYY-MM-DD",
  "detected_publication_date": "YYYY-MM-DD"
}
```

## Evaluator Changes

The evaluator is allowed to know cutoff. It should produce two distinct critique modes.

### Benchmark-fair critique

Only use literature available at or before the run cutoff. This answers:

- What should the agent have found?
- Which missing prior work is a fair defect?
- Which novelty claims were too strong under the available literature?

### Current-world critique

Use current literature. This answers:

- Would the idea still be novel today?
- Did later work invalidate or subsume the proposal?
- How should human reviewers interpret the result now?

Rules:

- Post-cutoff work must not be counted as a benchmark-fair agent failure.
- Post-cutoff work may be reported as current-world novelty risk.
- The evaluator report must label each novelty criticism as `pre-cutoff`, `post-cutoff`, or `unknown-date`.

## Quality Gates

### Gate 1: Literature sufficiency

Pass conditions:

- Main research lines are mapped.
- Benchmark/dataset/evaluation infrastructure is mapped when relevant.
- Neighboring work likely to become closest prior work is identified.
- Source inventory separates inspected, fetched-only, extracted, unavailable, and excluded sources.

### Gate 2: Novelty stress test

Pass conditions:

- Claim decomposition exists.
- Adversarial closest-work queries exist.
- Closest prior candidates are inspected or uncertainty is declared.
- Novelty delta table exists.
- No closest prior verdict is `repackaging`.

### Gate 3: Baseline gate

Pass conditions:

- Required baselines are named.
- Feasibility is assessed.
- If a required baseline is infeasible, the claim is downgraded.
- Simple controls and closest-work baselines are not conflated.

### Gate 4: Problem-method fit

Pass conditions:

- Core problem requirements are explicit.
- Each core requirement is addressed by a method component or the problem is narrowed.
- A minimal worked example concretely demonstrates the method flow.
- Counterexamples have fixes, controls, or claim downgrades.
- Metrics directly test the problem or proxy limits are stated.

### Gate 5: Method specification

Pass conditions:

- Every central component has inputs, outputs, timing, operational definition, and implementation risk.
- Ambiguous terms are defined operationally.
- Pipeline or pseudocode exists.
- No unresolved internal contradiction remains.
- Decorative components are removed or justified.

### Gate 6: Hypothesis-to-test alignment

Pass conditions:

- Every central subclaim has a direct test.
- Every key confound has a control or claim downgrade.
- Refutation patterns are explicit.
- Ablations isolate mechanisms.

### Gate 7: Proposal audit

Pass conditions:

- Proposal uses the revised hypothesis.
- Proposal claims do not exceed the novelty stress test.
- Required baselines and claim downgrades are reflected.
- Method fixes from problem-method fit and method specification audits are reflected.
- Experiments can support or falsify the central hypothesis.
- Limitations include unresolved confounds and unavailable evidence.

## Implementation Plan

### Phase 1: Remove cutoff leakage

1. Remove cutoff rendering from startup prompt.
2. Remove cutoff instructions from literature-review skill.
3. Remove cutoff from task contract requirements.
4. Remove cutoff language from agent-facing repair or stop-hook messages.
5. Keep cutoff in config, MCP launch, framework input, and run record.

Acceptance tests:

- Rendered `startup_prompt.md` contains no `cutoff`, `post-cutoff`, or date-boundary wording.
- Agent workspace `AGENTS.md`, `task_contract.md` template instructions, and skill instructions contain no cutoff leakage.
- `run_record.json` still records cutoff for evaluator use.
- MCP is still launched with the cutoff argument when configured.

### Phase 2: Add new artifacts and workflow gates

1. Update startup prompt required workflow.
2. Update hypothesis-development skill.
3. Update proposal-writeup-audit skill.
4. Update stop hook required files.
5. Update validator or artifact checks if the framework has a static required-file list.

Acceptance tests:

- A completed run is invalid or stop-hook-blocked if any new note is missing.
- `proposal.md` is not the only artifact containing novelty and experiment reasoning.
- The final reflection reminder names problem-method fit and method specification checks.
- Existing tests for simple runs are updated intentionally if they assert old artifact lists.

### Phase 3: Tool response sanitization

1. Audit scholar-gateway error messages.
2. Replace agent-visible cutoff-specific errors with neutral unavailable messages.
3. Preserve detailed cutoff reason only in framework logs or structured metadata hidden from the agent.

Acceptance tests:

- A post-policy-blocked source does not reveal the cutoff date in tool output.
- Framework logs can still explain why the source was blocked.

### Phase 4: Evaluator upgrade

1. Read cutoff from `run_record.json`.
2. Generate benchmark-fair and current-world novelty critiques separately.
3. Require each novelty criticism to include source date class: pre-cutoff, post-cutoff, or unknown-date.

Acceptance tests:

- A post-cutoff prior work is not counted as a benchmark-fair defect.
- A pre-cutoff missed closest work is counted as a fair defect.

## Non-Goals

- Do not make the agent aware of cutoff.
- Do not require the agent to prove novelty exhaustively.
- Do not turn the baseline into a large fixed multi-agent pipeline.
- Do not mechanically score scientific quality in the core validator beyond artifact existence and required sections, unless a later spec promotes semantic checks into infrastructure.
- Do not require every optional external baseline to be implemented, but require claim downgrades when required baselines are infeasible.

## Open Questions

1. Should `notes/novelty_stress_test.md`, `notes/problem_method_fit_audit.md`, `notes/method_specification_audit.md`, `notes/baseline_gate.md`, and `notes/hypothesis_to_test_matrix.md` be hard validator requirements or stop-hook-only requirements?
2. Should the evaluator run its own post-hoc closest-work search with cutoff filtering, or consume only the agent's source inventory plus additional reviewer searches?
3. Should neutral unavailable tool messages include a stable opaque code for debugging, such as `UNAVAILABLE_EVIDENCE_POLICY`, while keeping cutoff hidden?
4. Should claim downgrades be represented in a structured field so evaluator can check whether the final proposal respected the gate?
