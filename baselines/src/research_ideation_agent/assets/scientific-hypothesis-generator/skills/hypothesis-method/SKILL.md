---
name: hypothesis-method
description: Use after the evidence gate to develop the scientific idea — gap and questions, hypothesis selection, a search-backed novelty check against prior work, method specification, and experiment plan — then hand off to the scientific-reviewer hypothesis_method gate.
---

# Hypothesis And Method Development

This skill turns the reviewed literature into a testable hypothesis, a specified method, and an experiment plan. Produce five design notes. After selecting a hypothesis you run your own search-backed novelty check to rule out duplication of prior work before designing the method. Do not grade your own work: an independent `scientific-reviewer` judges these notes at the hypothesis_method gate, and you revise against its findings.

## Inputs

- `notes/literature_review.md` and the cleared `notes/reviews/evidence_review.md`.
- Original research topic and any user constraints (compute, data access, venue).

## Workflow

Write the five notes in order; each carries specific structure that forces concrete thinking.

1. `notes/gap_and_questions.md` — from the literature, identify community-visible challenges, weak assumptions, contradictions, and open problems; optionally note cross-domain mechanisms that could transfer, with their analogy limits; draft candidate research questions, reject the too-broad/descriptive/unfalsifiable, and select one central question.
2. `notes/hypothesis.md` — generate multiple falsifiable hypothesis candidates for the question; compare them on novelty, evidence support, feasibility, impact, and risk; select the candidate with the highest novelty and impact that remains defensible and feasible — do not default to the safest, most incremental option; decompose the selected claim into its mechanism, intervention, setting, metrics, and causal claim (structure below).
3. `notes/novelty_check.md` — run your own dedicated, search-backed novelty check on the selected hypothesis to rule out duplication before investing in the method (structure below). If it finds the idea is already done or only cosmetically different, go back to step 2 and revise or re-select the hypothesis.
4. `notes/method.md` — specify the method that addresses the selected hypothesis (structure below).
5. `notes/experiment_plan.md` — turn the hypothesis and method into a test (structure below).

## notes/hypothesis.md structure

Decompose the selected claim into the axes the novelty check and method build on:

| Field | Description |
|---|---|
| Mechanism | scientific mechanism or causal object |
| Intervention | method, dataset, benchmark, architecture, or analysis |
| Setting | model / task / training-or-inference / domain / scale |
| Metrics | metrics and evaluation protocols |
| Causal claim | what the proposal claims to explain or prove |

Also state why the selected hypothesis could be wrong and what would refute it.

## notes/novelty_check.md structure

This is your own novelty check — run it yourself, do not defer it to the reviewer. The goal is to find work that makes the selected hypothesis redundant, before you spend effort on the method. Run dedicated, adversarial searches aimed at the hypothesis's specific mechanism, intervention, and setting: query its method terms, its claimed effect, and the closest competing approaches; citation-chase the nearest hits; and run `extract_paper` on the 1–2 closest competing works (not just their abstracts) so each delta rests on inspected raw sections, not a snippet — an abstract-only comparison is insufficient and caps the eventual novelty score. Do not only search for supporting literature, and remember that absence from search results is not evidence of absence — state how hard you looked.

Record the searches you ran (queries and what each was probing), then compare the closest work structurally:

| Prior work | Same mechanism? | Same intervention? | Same setting? | Same metrics? | Same causal claim? | Real delta (with inspected source) |
|---|---|---|---|---|---|---|

Close with an explicit verdict: `novel`, `needs reframing`, or `duplicate`. For `novel`, name the precise axis (mechanism / intervention / setting / metrics / causal claim) on which the hypothesis differs from every inspected neighbor. For `needs reframing` or `duplicate`, return to `notes/hypothesis.md`, revise or re-select, and re-run this check — do not carry an unresolved duplication risk into the method. Iterate this at most twice; if no candidate clears after that, select the surviving candidate with the largest honest delta, record the residual overlap as an explicit limitation, and let the hypothesis_method gate judge it rather than looping further.

## notes/method.md structure

Problem-requirement matrix (start from the problem, not the method):

| Problem requirement | Why required | Evidence from literature | Method component that addresses it | Gap |
|---|---|---|---|---|

Causal-bottleneck answers: what is the problem's main causal bottleneck; which variable does the method change; why should changing it affect the target outcome; could the method improve the metric while leaving the bottleneck unresolved; what would show the method failed.

Component-specification table:

| Component | Operational definition | Inputs | Outputs | Called during train/inference/eval | Needed for which claim | If removed | Implementation risk |
|---|---|---|---|---|---|---|---|

Formal specification — the method must be precise enough to implement without ambiguity. Define notation first, then specify every component in the language of math:

| Symbol | Meaning | Type / space |
|---|---|---|

- **Equations for every computed quantity.** Give each score, loss, update, transformation, gate, or decision rule as an explicit equation over the defined symbols — not a prose paraphrase. State each quantity's range and any constraints (normalization, thresholds, domains).
- **Algorithms for every procedure.** Write numbered pseudocode (inputs, state, control flow, outputs) for any non-trivial pipeline, training loop, or inference step, concrete enough to transcribe directly.
- **Complete derivations, no skipped steps.** If the method relies on a derivation, optimization, bound, or probabilistic argument, show it step by step and state every assumption it uses; never assert "it can be shown that."
- **Shapes and composition.** For tensor or learned components, state dimensions/shapes and how they compose, so there is no shape ambiguity.

Design rationale and justification — specification alone is not enough; explain the reasoning behind the method:

- **Why this design.** For each key component and design choice, state why it is built this way: the reasoning that motivates it, the assumption or insight it exploits, and the simpler or obvious alternative it is chosen over (and why that alternative is insufficient). A choice with no stated reason reads as arbitrary.
- **Why it should work.** Give the explicit end-to-end causal argument linking the intervention to the predicted effect: how each component acts on the problem's causal bottleneck, why that should move the target metric, and under what assumptions the argument holds. Make the chain inspectable; do not just assert that the method works.

Resolved-ambiguous-terms table — define every central term operationally (for example: confidence score, contradiction-aware, semantic consistency, latent margin, adaptive memory, importance score, provenance, evidence support):

| Term | Operational definition | How computed or measured |
|---|---|---|

## notes/experiment_plan.md structure

Baseline table (closest prior work, strong standard baselines, and baselines that distinguish the proposed mechanism from simple alternatives; external baselines must not be marked optional merely because integration is inconvenient):

| Baseline | Role | Why required | Feasible? | If omitted, required claim downgrade |
|---|---|---|---|---|

Subclaim→test matrix:

| Claim | Direct test | Necessary control | Confound ruled out | Refutation pattern |
|---|---|---|---|---|

Confound matrix:

| Confound | Why plausible | Test or control | If not controlled, claim downgrade |
|---|---|---|---|

Then the full experimental design: datasets or tasks, models, metrics, ablations targeted to the proposed mechanism, and the analysis plan. Justify each choice — for the models, datasets/benchmarks, metrics, settings, baselines, ablations, and analysis, state why it is chosen: what it is meant to test, rule in, or rule out, and why an obvious alternative would be weaker. An unjustified choice reads as arbitrary. Ensure the experiment can falsify the central claim; if it cannot, revise the claim.

## Required Outputs

- `notes/gap_and_questions.md`
- `notes/hypothesis.md`
- `notes/novelty_check.md`
- `notes/method.md`
- `notes/experiment_plan.md`

## Handoff

Do not self-grade. After the five notes exist, invoke the `scientific-reviewer` subagent with `gate: hypothesis_method`. It writes `notes/reviews/hypothesis_method_review.md` with per-dimension scores (against `shared/rubric.md` — read it to interpret the 1–10 scale and severities) and actionable findings. Resolve every critical and major finding — revise the relevant note, or downgrade the claim and record the residual limitation — then re-invoke until `status: PASS`. The reviewer allows at most two review→revise cycles: on the final one it issues `PASS` with any remaining non-critical findings recorded as accepted limitations, but never waives an unresolved critical finding or failed precondition. Only after the gate is cleared, hand off to `proposal-writeup`.

## Quality Checklist

- The selected gap and hypothesis are grounded in `notes/literature_review.md`; weak or disputed claims are not promoted as settled facts.
- The hypothesis is falsifiable and is not just a restatement of the research question.
- The novelty check is search-backed and done by you: closest work is found through dedicated adversarial searches, deltas rest on inspected sources (not absence of results), and `notes/novelty_check.md` ends in an explicit `novel` / `needs reframing` / `duplicate` verdict with no unresolved duplication risk carried into the method.
- The method addresses the problem's causal bottleneck and core requirements, is specified concretely, and has no unresolved internal contradiction.
- Required baselines are named; claim downgrades are made when they are infeasible.
- Every subclaim maps to a direct test, necessary control, confound, and refutation pattern.
- Both positive and negative outcomes would teach something scientifically useful.
