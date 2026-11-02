---
name: "writing-analysis-report"
description: "How to write the current research-agent analysis_report.md as a judge-facing proposal positioning dossier: evidence-only, proposal-specific, no point-wise ratings, no revision guidance."
---

# Writing the Analysis Report

## Purpose

`analysis_report.md` is a judge-facing evidence dossier for one proposal. It is not a miniature literature review and not a point-wise proposal review. The later judge compares two dossiers, so this file should organize evidence without pre-deciding the proposal's isolated quality.

The main failure mode is point-wise judgment bias: an isolated label like `Opportunity: high` or `overall potential: moderate` is not calibrated against the other proposal, but the judge may still treat it as a comparable signal. Keep the judge-facing report evidence-first so the judge compares facts, not the research agent's adjectives.

## Core Rules

- Do not repeat the broad topic review. Use `literature_review.md` as the map and extract only the parts relevant to this proposal.
- Do not assign factor ratings such as high / moderate / low.
- Do not name a strongest factor, weakest factor, bottleneck, zero-factor, or overall potential.
- Do not include revision advice. That belongs in `revision_feedback.md`.
- Do write supporting evidence, limiting evidence, unresolved uncertainty, and sources for each factor axis.
- Label inference as inference when the source does not state the conclusion directly.

## Before Writing

1. Re-read `proposal.md`.
2. Re-read the relevant sections of `literature_review.md`.
3. Read `evidence_gaps.md` and verify each gap states why `literature_review.md` was insufficient and what part of `analysis_report.md` the gap could change. If a targeted search does not map to such a gap, do not use it as strong report evidence.
4. Read `targeted_findings.md` and use it as the authoritative record of targeted-search sources read, incremental findings, and remaining uncertainty. Check that each targeted search/read cycle has its own entry; do not rely on a retroactive end-of-search summary.
5. Check paper summaries or scraped papers for precise evidence when the report depends on a named paper.

## What To Include

### Proposal decomposition

State the problem, mechanism, claimed contribution, experiment plan, expected citation / adoption handle, and claims requiring verification. Keep this concrete.

### Landscape placement

Identify the subfield, nearest absorbed works, recent low-absorption work, reviewer-expected baselines, benchmarks, and metrics. Keep this section short and proposal-specific; it should not reproduce the full topic-level literature review.

### Increment over absorbed prior art

Document what the community already has and what this proposal claims to add. For nearby work, record names, citation or absorption signals when available, what the work covers, and the proposal's delta. Do not decide whether this makes the knowledge delta large or small; the judge makes that comparison.

### Citation surface

Describe concrete reasons later papers would cite this work if it succeeds: method, baseline, benchmark/protocol, empirical result or performance point, analysis/explanation, conceptual framing, negative/control result, dataset, model, or tool artifact. This is about citation handles, not only direct reuse.

### Diffusion pathway

Describe how the contribution would spread: who would reuse it, what artifact would be reused, how it plugs into existing workflows, what barriers limit use, and what follow-on communities could build on it. Include conceptual/model- organism/benchmark diffusion, not only software adoption.

### Factor evidence dossier

For each factor axis, use only:

- Supporting evidence
- Limiting evidence
- Unresolved uncertainty
- Sources

The evidence axes are:

- Opportunity Leverage
- Knowledge Delta
- Citation Surface
- Diffusion Pathway
- Credibility Gate
- Field Dynamics

## Recommended Structure

Use this structure so the judge can compare the two proposal reports symmetrically:

```markdown
## Proposal Positioning Dossier: [short label]

### 1. Proposal Decomposition
- Problem / unmet need:
- Core mechanism:
- Claimed contribution:
- Experimental plan:
- Expected citation / adoption handle:
- Claims requiring verification:

### 2. Landscape Placement
- Subfield / paradigm:
- Relevant `literature_review.md` sections:
- Closest absorbed prior work:
- Closest recent or low-absorption work:
- Current reviewer-expected baselines:
- Relevant benchmarks / metrics:

### 3. Specific Increment Over Absorbed Prior Art
- What the community already has:
- What this proposal adds:
- Overlap / delta evidence:
- Citation / absorption notes:
- Remaining uncertainty:

### 4. Citation Surface
- Who would cite this work:
- Why they would cite it:
- Reference handles this work would create:
- Possible citation-dilution or substitution risks:
- What kind of citation this would likely attract:

### 5. Diffusion Pathway
- Who would reuse or build on this work:
- What artifact, framing, benchmark, protocol, model, data, or tool would diffuse:
- Workflow fit or integration path:
- Practical or conceptual barriers to diffusion:
- Communities beyond the immediate subfield that could pick it up:

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

### 7. Targeted Search Log
- Evidence gaps addressed:
- Queries run:
- Sources scraped/fetched/read:
- Key findings added after reading sources:
- What changed relative to `literature_review.md`:
- Remaining uncertainty:
```

## Common Mistakes

1. Rewriting `literature_review.md` instead of positioning the proposal.
2. Writing `Opportunity is high` rather than listing opportunity evidence and limits.
3. Treating the absence of a found paper as proof that no such work exists.
4. Mixing revision advice into the judge-facing report.
5. Hiding uncertainty. If targeted search did not settle a question, say so.
6. Comparing to the other proposal. The research agent sees only one proposal; the judge owns pairwise comparison.
