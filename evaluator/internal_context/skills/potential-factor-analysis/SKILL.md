---
name: "potential-factor-analysis"
description: "Defines citation-oriented evidence axes (Opportunity Leverage, Knowledge Delta, Citation Surface, Diffusion Pathway, Credibility Gate, Field Dynamics) for the current three-stage ranker. Read this when organizing proposal-specific evidence."
---

# Citation-Oriented Potential Factor Analysis

## Current Ranker Research-Agent Usage

For the current three-stage ranker, this skill defines evidence axes for predicting future citation / Delta-K potential. It is NOT a judge-facing scoring template.

When writing `analysis_report.md`, use these axes to organize supporting evidence, limiting evidence, unresolved uncertainty, and sources. Do NOT assign factor ratings, strongest/weakest factor labels, zero-factor verdicts, or overall potential. Pairwise comparison and final verdicts belong to the judge.

The central question is:

> If this proposal is executed competently and its central claim is tested, what concrete knowledge, artifact, or framing would later researchers cite, reuse, benchmark against, or build on?

Operationally, `K` means the community's effective knowledge state at the cutoff date, and `Delta-K` means the incremental change a proposal could add to that state if published. Delta-K is citation-relevant knowledge impact: a new method, benchmark, dataset, baseline, empirical claim, negative result, artifact, term, framing, analysis, or first demonstration that later researchers would need to cite, reuse, compare against, or build on.

## Axis A: Opportunity Leverage

**Question:** If the central claim works, how much research space could this proposal open, redirect, or unlock?

Opportunity Leverage includes both:

- **Recognized opportunity:** the literature already describes an unmet need, bottleneck, limitation, missing benchmark, or blocked downstream workflow.
- **Latent opportunity:** the literature does not yet name the problem clearly, but the proposal could create a new problem definition, research category, or agenda if the evidence is compelling.

Do not treat field activity as one-directional evidence. Many researchers in an area can amplify citation potential by creating a large audience, or dilute it by creating many substitutes. Low current activity can mean low relevance, or it can mean category-creation opportunity.

Collect evidence for:

- recognized limitations or blocked workflows;
- latent opportunity signs from adjacent capabilities, new model behavior, tooling, or infrastructure becoming ready;
- downstream areas that would become easier or newly possible;
- whether the proposal reframes the problem in a way others may adopt;
- saturation or crowding signals that could dilute attention.

## Axis B: Knowledge Delta

**Question:** What new knowledge object would the proposal add beyond absorbed prior art?

Knowledge delta is the main citation ceiling axis. It can come from:

- a new mechanism or method;
- a new problem formulation, term, or research category;
- a first clear demonstration;
- a new benchmark, dataset, metric, or protocol;
- a new empirical law, scaling pattern, performance point, or tradeoff;
- a strong analysis or explanation that changes interpretation of existing results;
- an important negative result or boundary condition;
- a result that changes a default assumption in the field.

For nearby work, collect names, dates, citation counts or absorption signals when available, what those works already provide, and the proposal's stated delta. Do not downgrade the proposal merely because related work exists; record whether the related work exactly covers the same claimed contribution or only shares ingredients.

For compositional or system proposals, record prior-art coverage at the package level as evidence: which absorbed works cover which claimed components, whether the important mechanisms and evaluation handles are already covered as a set, and what residual knowledge object remains. "No single prior covers the whole package" is useful context, but it is not enough by itself; the remaining delta may be a reusable method, strong baseline, benchmark, empirical result, mechanism evidence, boundary condition, or it may be mostly routine integration. Do not turn this into a point-wise verdict. Capture the evidence so the judge can apply the same residual-delta standard to both proposals.

## Axis C: Citation Surface

**Question:** Why would later papers cite this work?

Citation Surface is not the same as direct adoption. A paper can be highly cited because it becomes a conceptual reference, benchmark, baseline, empirical finding, analysis, or negative result, even if few groups directly reuse its method.

Collect concrete citation handles:

- cite as method or algorithm;
- cite as baseline, strong baseline, simple baseline, or sanity check;
- cite as benchmark, dataset, metric, protocol, split, or evaluation standard;
- cite as empirical result, performance point, scaling law, tradeoff, or failure mode;
- cite as analysis, explanation, decomposition, or mechanism evidence;
- cite as conceptual framing, terminology, or problem definition;
- cite as first demonstration or category-opening result;
- cite as negative/control result or boundary condition;
- cite as model, data, code, tool, or system artifact.

Also collect substitution risks: if later papers could cite several nearby works instead, reference necessity is weaker.

## Axis D: Diffusion Pathway

**Question:** How could the contribution spread through the community if it works?

Diffusion is a realization axis. It describes how the ceiling from opportunity, knowledge delta, and citation surface would materialize.

Collect evidence for:

- direct reuse or implementation path;
- workflow fit or integration with existing tools;
- availability of code, models, data, benchmark, protocol, or released artifacts;
- compute, data, hardware, engineering, or expertise barriers;
- conceptual clarity and ease of one-paragraph explanation;
- applicability across tasks, models, domains, or adjacent communities;
- whether the artifact is a method, benchmark, model organism, dataset, tool, theory, framing, analysis, or negative result.

Do not equate diffusion with drop-in software convenience only. Conceptual adoption, benchmark adoption, model-organism adoption, and related-work reference adoption are also diffusion paths.

## Axis E: Credibility Gate

**Question:** Would the proposed evidence convince the community that the central claim is true enough to cite or build on?

Credibility includes soundness, feasibility, and experimental design. It is a gate: if the plan cannot produce credible evidence, Delta-K collapses. If the plan is above the publishable/convincing threshold, ordinary differences should mostly affect confidence rather than dominate the ceiling.

Collect evidence for:

- logical coherence of the mechanism;
- prior empirical or theoretical support for feasibility;
- known contradictions or negative results that apply;
- baselines matched to the central claim;
- metrics that measure the claimed effect rather than a proxy only;
- ablations and mechanism tests;
- scope and transfer tests;
- falsifiable success, pivot, and failure criteria;
- whether the plan can produce credible evidence if the idea works.

## Axis F: Field Dynamics

**Question:** Does activity in the proposal's problem area amplify citation potential or dilute credit?

This is a context modifier, not a standalone score. It covers the proposal's own problem area and closest related work clusters, not broad cross-field novelty. The same evidence can cut both ways.

Collect evidence for amplification:

- active community, workshops, benchmarks, and fast-growing related-work graph;
- many follow-on papers that would need to cite a canonical reference;
- proposal provides a standard baseline, benchmark, term, or framing for an active area;
- proposal consolidates or reorganizes a crowded area.

Collect evidence for dilution:

- many substitutable methods or baselines;
- proposal is one incremental variant among many;
- credit likely spread across several prior or concurrent works;
- the key contribution lacks a unique reference handle.

Do not convert "many people work on this" into high opportunity or low novelty by itself. Record how activity affects citation pool size and substitution risk.

## Severe-Risk Evidence

Zero-factor verdicts belong to the judge. The research agent should not declare that a proposal has zero potential. If you find severe-risk evidence, record it as limiting evidence or unresolved uncertainty under the relevant axis:

- Opportunity: no plausible audience, use case, or latent/revealed problem.
- Knowledge Delta: a named, widely absorbed work appears to cover the same claimed contribution.
- Citation Surface: no clear reason later papers would cite the work even if it succeeds.
- Diffusion: artifact or concept would be unusable outside the authors.
- Credibility: core mechanism incoherent, contradicted by source-grounded evidence, infeasible, or not testable by the proposed design.
- Field Dynamics: substitution risk so strong that credit would likely accrue to another canonical work.

## Judge-Owned Synthesis Logic

The judge should synthesize evidence with this rough structure:

`Expected Delta-K / citation potential` `≈ Opportunity Leverage × Knowledge Delta × Citation Surface` `× Diffusion Modifier × Credibility Gate` `adjusted by Field Dynamics`

The research agent should use this only to understand what evidence may matter. Do not write synthesis, overall potential, or pairwise comparisons in `analysis_report.md`.

## How To Use This Skill In `analysis_report.md`

Write the factor portion of `analysis_report.md` in this format:

```markdown
### 6. Factor Evidence Dossier

#### Opportunity Leverage Evidence
- Supporting evidence:
- Limiting evidence:
- Unresolved uncertainty:
- Sources:

#### Knowledge Delta Evidence
- Supporting evidence:
- Limiting evidence:
- Unresolved uncertainty:
- Sources:

#### Citation Surface Evidence
- Supporting evidence:
- Limiting evidence:
- Unresolved uncertainty:
- Sources:

#### Diffusion Pathway Evidence
- Supporting evidence:
- Limiting evidence:
- Unresolved uncertainty:
- Sources:

#### Credibility Gate Evidence
- Supporting evidence:
- Limiting evidence:
- Unresolved uncertainty:
- Sources:

#### Field Dynamics Evidence
- Amplification evidence:
- Dilution evidence:
- Unresolved uncertainty:
- Sources:
```

Also reflect axis-relevant evidence in earlier report sections:

- `Proposal Decomposition`: identify the mechanism, claimed contribution, experiment plan, intended artifact, and central claims requiring verification.
- `Landscape Placement`: list nearest works, baselines, benchmarks, community setting, activity level, and substitution risks.
- `Specific Increment Over Absorbed Prior Art`: record named nearby work, citation/absorption signals, overlap evidence, proposal delta, and uncertainty.
- `Citation Surface`: record why later papers would cite the work.
- `Diffusion Pathway`: record how the work would spread or be reused.
- `Targeted Search Log`: summarize `targeted_findings.md`: which evidence gaps motivated each query, which sources were scraped/fetched/read, what key findings were added, what changed relative to `literature_review.md`, and what uncertainty remains.

Do not include:

- high / moderate / low factor ratings;
- strongest / weakest factor labels;
- bottleneck factor labels;
- zero-factor verdicts;
- ceiling or multiplicative synthesis;
- overall potential;
- win/loss language;
- revision recommendations.
