---
name: proposal-writeup
description: Use after the hypothesis_method gate to draft proposal.md from the approved notes, then hand off to the scientific-reviewer proposal gate.
---

# Proposal Write-Up

This skill writes the first complete `proposal.md` from the approved design notes. It does not invent new literature or change the hypothesis without updating the relevant note. Do not self-grade: the independent `scientific-reviewer` judges the draft at the proposal gate, and you revise against its findings.

## Inputs

- `notes/task_contract.md`
- `notes/literature_review.md`
- `notes/gap_and_questions.md`
- `notes/hypothesis.md`
- `notes/method.md`
- `notes/experiment_plan.md`
- the cleared `notes/reviews/hypothesis_method_review.md`

## Workflow

1. Read all design notes before drafting.
2. Write `proposal.md` using the template in `## proposal.md Template`. Replace every placeholder with concrete content; do not leave bracketed placeholder text in the final file.
3. Synthesize literature rather than listing sources mechanically. Cite only sources present in `notes/literature_review.md`.
4. Use `notes/hypothesis.md` for the hypothesis and its novelty positioning, `notes/method.md` for the method, and `notes/experiment_plan.md` for baselines, controls, ablations, metrics, and refutation patterns. Do not exceed the novelty delta supported by `notes/hypothesis.md`.
5. Include why the hypothesis could be wrong and what would refute it.
6. Run a final consistency pass before review: every inline citation resolves to a `## References` entry and every reference is cited (no dangling or uncited entries); each cited claim matches what its source actually says (no mis-attribution); every section the proposal's scope promises is present; numbers, symbols, and notation are internally consistent; and no bracketed placeholder text remains.
7. After writing `proposal.md`, do not self-grade. Invoke the `scientific-reviewer` subagent with `gate: proposal`. It writes `notes/reviews/proposal_review.md` with the five-dimension scores (against `shared/rubric.md` — read it to interpret the 1–10 scale and severities) and actionable findings. Resolve every critical and major finding (revise `proposal.md` or downgrade the claim and record the limitation) and re-invoke until `status: PASS`. The reviewer allows at most two cycles: on the final one it issues `PASS` with any remaining non-critical findings recorded as accepted limitations, but an unresolved critical finding or failed precondition keeps the gate closed.

## Required Outputs

- `proposal.md`

## Required Proposal Sections

- `## Introduction`
- `### Context and Motivation`
- `### The Problem`
- `### Key Insight and Hypothesis`
- `### Why This Could Be Wrong`
- `## Proposed Approach`
- `### Overview`
- `### Preliminaries and Notation`
- `### Method Details`
- `### Design Rationale`
- `### Key Innovations`
- `## Related Work`
- `### Field Overview`
- `### Related Papers`
- `### Closest Prior Work`
- `### Comparison Table`
- `## Experiments`
- `### Experimental Setup`
- `### Benchmarks and Metrics`
- `### Main Results or Expected Results`
- `### Ablation Studies`
- `### Experimental Rigor`
- `### Design Justification`
- `## Success Criteria`
- `## Impact Statement`
- `## References`

## proposal.md Template

Use this structure for the final `proposal.md`. Replace every placeholder with concrete content grounded in the design notes. Do not leave bracketed placeholder text in the final file.

```markdown
# <Concise Proposal Title>

## Introduction

### Context and Motivation

<Explain the scientific setting, why the topic matters now, and why existing progress makes the question timely. Ground motivation in `notes/literature_review.md` and `notes/gap_and_questions.md`.>

### The Problem

<Define the unresolved problem or gap. Explain what prior work cannot yet answer, measure, or explain, and why solving it would matter scientifically.>

### Key Insight and Hypothesis

<State the core insight, then the selected hypothesis from `notes/hypothesis.md` in falsifiable form. Prefer: "We hypothesize that [mechanism/intervention] will [measurable effect] under [conditions] because [causal rationale].">

If useful, break the hypothesis into subclaims:

1. <Subclaim or effect 1.>
2. <Subclaim or interaction 2.>
3. <Quantity, scaling, boundary, or mechanism claim.>

### Why This Could Be Wrong

<List concrete reasons the hypothesis may fail: theoretical, empirical, measurement, dataset, confounding, and feasibility risks.>

1. <Failure mode 1.>
2. <Failure mode 2.>

## Proposed Approach

### Overview

<Summarize the proposed research plan and the method's main moving parts at a high level, so the formal details that follow have a frame. Name the dataset, method, intervention, evaluation setting, or theoretical object being introduced or analyzed.>

### Preliminaries and Notation

<Define the formal setting before any method step: the objects the method operates on, every symbol with its type/space, and any background definitions the reader needs. Introduce here every symbol used in Method Details.>

| Symbol | Meaning | Type / space |
|---|---|---|
| <symbol> | <what it denotes> | <domain, shape, or set> |

### Method Details

<Specify the method precisely enough to implement without ambiguity, in the language of math. Build it up step by step from `notes/method.md`; confirm it matches the selected hypothesis in `notes/hypothesis.md`. Prose alone is not sufficient.>

- Give every computed quantity — each score, loss, update, transformation, gate, or decision rule — as an explicit **equation** over the symbols defined above, not a prose paraphrase. State ranges, constraints, and normalization.
- Present every non-trivial procedure (pipeline, training loop, inference step) as numbered **algorithm pseudocode** with inputs, state, and outputs, concrete enough to transcribe directly.
- Show **complete derivations with no skipped steps** for any optimization, bound, or probabilistic argument the method relies on; state every assumption, and never write "it can be shown that."
- State shapes/dimensions for tensor or learned components, and what happens to each component if it is removed. Resolve every ambiguous term using `notes/method.md`.

### Design Rationale

<Explain the reasoning behind the method, not just its mechanics. Ground both parts in `notes/method.md`.>

- **Why it is designed this way.** For each key component and design choice, give the motivation, the assumption or insight it exploits, and the simpler alternative it is chosen over and why that alternative is insufficient. A choice with no stated reason reads as arbitrary.
- **Why it should work.** Give the explicit end-to-end causal argument from the intervention to the predicted effect: how each component acts on the problem's causal bottleneck, why that should move the target metric, and under what assumptions the argument holds. Make the reasoning inspectable rather than asserting that the method works.

### Key Innovations

1. <Innovation 1: dataset, benchmark, causal design, mechanism, method, or evaluation frame.>
2. <Innovation 2.>

## Related Work

### Field Overview

<Synthesize the relevant literature landscape: main research lines, milestone works, paradigm shifts, benchmarks, recent advances, and open challenges. Do not list papers mechanically. Cite only sources present in `notes/literature_review.md`.>

### Related Papers

<Group related papers by research line or problem. For each group, state what it establishes and what remains unresolved.>

### Closest Prior Work

<Identify the nearest prior work, what it did, what it did not test or resolve, and why the proposed hypothesis remains distinct. Trace this section to `notes/hypothesis.md` and `notes/literature_review.md`. If the novelty positioning required a narrower claim, use the narrower claim.>

### Comparison Table

| Related work | What it does | Key limitation | What we change | Why this matters |
|---|---|---|---|---|
| <Source> | <Finding or method> | <Limitation> | <Difference> | <Scientific value> |

## Experiments

### Experimental Setup

<Describe the full setup: conditions, models or systems, data, interventions, inference settings, sample size, and resource estimate.>

<Use `notes/experiment_plan.md` for required baselines, controls, confounds, refutation patterns, and ablations. If a required baseline is infeasible, state the claim downgrade here and in limitations.>

### Benchmarks and Metrics

| Benchmark or dataset | Description | Metrics | Split or sample | Evaluation protocol | Why chosen |
|---|---|---|---|---|---|
| <Name> | <What it measures> | <Metrics> | <Split> | <Protocol> | <What this benchmark/metric tests for the hypothesis, and why over alternatives> |

### Main Results or Expected Results

<This is a proposal: provide the planned result table and mark values as TBD. State the primary comparisons and derived quantities such as deltas or effect sizes.>

| Condition | Baseline/System A | Proposed/System B | Difference | Interpretation |
|---|---|---|---|---|
| <Condition> | TBD | TBD | TBD | <What this comparison tests> |

### Ablation Studies

| Variant | What changes | Expected finding | Why it matters |
|---|---|---|---|
| <Ablation> | <Change> | <Expected pattern> | <Mechanism or validity check> |

### Experimental Rigor

<State reproducibility plan, variance handling, statistical tests, controls, confound checks, and measurement-quality checks.>

### Design Justification

<Explain why this experimental design is the right test of the hypothesis, not just what it is. Ground in `notes/experiment_plan.md`.>

- **Why this design tests the hypothesis.** State how the design would confirm the hypothesis if it holds and falsify it if it does not, mapping experiments to the subclaims.
- **Why each choice.** Justify the models, benchmarks/datasets, metrics, settings, baselines, ablations, and analysis: for each, what it is meant to test, rule in, or rule out, and why an obvious alternative would be weaker or less diagnostic. An unjustified choice reads as arbitrary.

## Success Criteria

<Define proceed, pivot, and refutation criteria before results are known, with thresholds or decision rules where possible.>

| Outcome | Criterion | Decision |
|---|---|---|
| Success | <Concrete threshold or pattern> | <Proceed implication> |
| Pivot | <Mixed or partial evidence> | <What to change next> |
| Refutation | <Evidence against hypothesis> | <What conclusion to draw> |

## Impact Statement

<Explain the scientific impact if the hypothesis is supported (what changes in understanding, evaluation, methodology, or future research), what would be learned if it is refuted, and the broader implications, scope limits, and assumptions.>

## References

<List only sources used in the proposal and present in `notes/literature_review.md`. Keep citation formatting consistent.>
```
