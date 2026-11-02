# Project Overview — Autonomous AI Research-Ideation Agent

> **Status:** Conceptual overview for the implemented MVP.
> **Last updated:** 2026-06-03

This is the high-level, conceptual description of the system. It explains what we are building and is the current source of truth for the concept layer. A detailed requirements specification should be rewritten after this overview stabilizes.

For the implementation-aware bridge from this concept to architecture, see [`SYSTEM_DESIGN_BRIEF.md`](./SYSTEM_DESIGN_BRIEF.md). For the current MVP architecture, see [`ARCHITECTURE.md`](../design/ARCHITECTURE.md). For the prompt, skill, and subagent design that shapes proposal quality, see [`QUALITY_LAYER_DESIGN.md`](../design/QUALITY_LAYER_DESIGN.md).

---

## 1. System overview

An **autonomous AI research-ideation agent that takes a high-level AI research topic as its starting point and develops it into a complete research proposal.** The system is intended to simulate the early-stage research work of a human researcher or PhD student: starting from an ambiguous research direction, iteratively exploring the literature to identify concrete research gaps worth addressing, selecting a gap to pursue, generating and evaluating multiple possible methods for that gap, selecting the most promising novel-but-feasible method, and designing experiments to test whether it addresses the gap.

The system **stops at the proposal**. The deliverable should read like a research paper before experiments have been run: a coherent, literature-grounded research argument with a concrete experimental plan, but no executed experiments or empirical results. It is **not** a loose brainstorm, a list of disconnected ideas, a set of paper summaries, or a generic one-shot LLM-generated idea.

## 2. Expected input

A **high-level AI research topic, direction, or rough idea**. The input may be broad, incomplete, or underspecified; it only needs to provide a starting point for autonomous research ideation. The system does not ask the user to refine the topic during the run. Instead, it uses literature exploration and candidate evaluation to decide what concrete research gap and proposal to develop. Examples:

- *"Improving visual reasoning in multimodal models."*
- *"Using reinforcement learning to improve multi-step reasoning in large language models."*
- *"Memory systems for long-horizon AI agents: how can an agent store, retrieve, and
  consolidate task-relevant information over long workflows without accumulating
  irrelevant or outdated context?"*

## 3. Expected output

A **single, structured research proposal** in Markdown. It should be grounded in real and verifiable literature, and should present one selected idea rather than a list of alternatives.

The proposal follows this structure:

```
# Title
## Introduction
## Related Work
## Methods
## Experiments Design
## Impact Statement
## Success Criteria
## References
```

At a high level, the proposal should read like the front half of an AI conference paper, extended with proposal-specific decision criteria:

| Section | Role |
|---|---|
| **Title** | Names the proposed research direction specifically, ideally reflecting both the problem and the core method. |
| **Introduction** | Frames the research problem, motivates why it matters, explains why existing methods are insufficient, and states the central research question or proposal thesis. |
| **Related Work** | Synthesizes prior work into a grounded argument for the gap, including the closest retrieved work and how the proposal differs from it. |
| **Methods** | Describes the selected technical method in enough detail to be implementable in principle, including its key mechanisms and why it may address the gap. |
| **Experiments Design** | Specifies how the proposal would be tested, including hypotheses, datasets, baselines, metrics, ablations, and analysis plans, without reporting any executed results. |
| **Impact Statement** | Explains the potential scientific, practical, and broader impacts so a reader can judge whether the proposal is worth pursuing. |
| **Success Criteria** | Defines what outcomes would count as success, what outcomes would motivate further refinement, and what outcomes would suggest the direction may be wrong. |
| **References** | Lists only real, retrieved, verifiable sources. |

The system may retain an inspectable internal trace of queries, papers read, candidate gaps, candidate methods, scores, and rejected options, but the user-facing output is the proposal.

## 4. Research ideation workflow

Conceptually (not an architecture), the system follows an iterative research-ideation workflow rather than a fixed waterfall pipeline. Literature exploration, gap formation, and method ideation inform one another: early readings can change which subproblem looks promising, candidate gaps can trigger more targeted searches, and weak or saturated directions can be abandoned.

1. **Start from an ambiguous research direction** — treat the user input as a starting point, generate provisional interpretations and search angles, and avoid committing too early to a final research question.
2. **Explore the literature iteratively** — retrieve, fetch, and read relevant papers, including full text for the most important ones, to build a grounded view of existing problems, methods, assumptions, results, and limitations.
3. **Form candidate gaps worth addressing** — use the literature map to identify concrete limitations, contradictions, open challenges, or underexplored assumptions that are not merely unfilled, but research-relevant, worth addressing, and potentially testable.
4. **Select a gap to pursue** — evaluate candidate gaps for grounding, importance, novelty potential, testability, and whether they appear to have plausible technical routes, without committing to a specific method too early.
5. **Generate possible methods for the selected gap** — produce several distinct methods or solution strategies for the chosen gap, each with a clear rationale for why it might address the problem.
6. **Evaluate and select the method** — compare the possible methods against the proposal quality criteria, reject weak or unsupported methods, and select the most promising novel-but-feasible method.
7. **Develop the method and experimental plan** — flesh out the selected method with enough technical detail to be implementable in principle, then design experiments, baselines, metrics, ablations, and analysis plans that test whether it addresses the gap.
8. **Write the proposal** — assemble the structured, cited proposal while retaining an inspectable trace of queries, papers read, candidate gaps, candidate methods, scores, and rejected options.

The workflow runs **fully autonomously** (topic in → proposal out) and explores **multiple candidates before committing to one** ("generate N, return the best").

## 5. Proposal quality target

This section defines the quality target for the system. Because the agent must choose which gap and which method to develop, these criteria are not just output preferences; they are the basis for evaluating candidate gaps, candidate methods, and the final proposal.

Two hard constraints come before scoring:

- **No fabricated or ungrounded literature claims** — every reference must be a real, retrieved, verifiable source, and claims about prior methods, results, limitations, and gaps must be faithful to the cited sources.
- **No fake empirical results** — the proposal may design experiments and state hypotheses, but it must not claim or imply that unexecuted experiments have already produced results.

Subject to those constraints, a good proposal should be evaluated across six dimensions:

1. **Topic Faithfulness & Problem Value** — the proposal should develop naturally from the user's research topic, identify a concrete problem or gap, and show that the gap is important, timely, worth addressing, and possible to investigate.
2. **Evidence & Positioning** — the proposal should be grounded in real literature, synthesize relevant prior work, identify the closest existing work, explain the delta, and handle uncertainty about saturation or novelty honestly.
3. **Method Quality** — the proposed method should directly address the selected gap, differ meaningfully from prior work, be specific enough to implement in principle, remain technically feasible, and have a clear rationale for why it may solve the problem.
4. **Experimental Testability** — the experimental design should test the central claim with suitable hypotheses, datasets or tasks, baselines, metrics, ablations, and analysis plans; be sufficiently complete by the standards of the relevant subfield; and include ways to expose failure rather than only favorable results.
5. **Decision Value & Risk Awareness** — the proposal should help a reader decide whether the direction is worth pursuing by explaining potential impact, success criteria, refinement criteria, stop criteria, assumptions, risks, limitations, and likely resource tradeoffs.
6. **Coherence & Claim Discipline** — the proposal should form one clear research argument from problem to literature to gap to method to experiments, while avoiding overclaiming and preserving the stance of a proposal rather than a completed empirical paper.

For the `scientific-hypothesis-generator` asset, the operative scoring rubric is the five-dimension rubric in `src/research_ideation_agent/assets/scientific-hypothesis-generator/shared/rubric.md` (Problem Value, Novelty, Soundness, Feasibility, Experimental Quality, each scored 1–10), which refines these dimensions and is applied both by the in-run reviewer gates and the offline evaluator.

A future requirements specification should translate these dimensions into explicit scoring criteria for gap selection, method selection, and final proposal review.

## 6. Out of scope

- **Running experiments** — the system designs experiments, baselines, metrics, and analysis plans, but never executes experiments or reports empirical results.
- **System-level evaluation / benchmarking** — this project does not implement evaluation of the agent itself across many topics; external evaluation may be used separately.
- **Human-in-the-loop checkpoints** — the run is end to end: the user provides one research topic and receives one proposal, with no human intervention or approval step in the middle.
