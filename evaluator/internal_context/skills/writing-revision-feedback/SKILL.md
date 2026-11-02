---
name: "writing-revision-feedback"
description: "How to write generator-facing revision_feedback.md: preserve constraints, highest-leverage revision target, required changes, do-not-do boundaries, and acceptance checklist."
---

# Writing Revision Feedback

## Purpose

`revision_feedback.md` is for the ideation generator. It should be a constrained rewrite specification, not a judge-facing assessment and not a long reviewer wish list.

The generator needs to know:

- what to preserve
- what to change
- why the change matters
- what not to do
- how to check whether the revision succeeded

## Core Rules

- Keep `revision_feedback.md` separate from `analysis_report.md`.
- Do not write a broad critique. Write concrete rewrite instructions.
- Prefer 2-3 required changes over many optional suggestions.
- Include preserve constraints so the generator does not destroy the core idea.
- Include `Do Not Do` boundaries to prevent scope creep.
- Separate local revisions from future-proposal seeds.
- Do not ask the generator to imitate the competing proposal.

## Recommended Structure

```markdown
## Revision Feedback: [short label]

### Revision Objective
[One sentence describing the highest-leverage improvement for the next version.]

### Preserve
- [Core idea or contribution that should remain]
- [Important scope boundary that should remain]
- [Existing strength that should not be diluted]

### Highest-Leverage Revision Target
- Target issue:
- Evidence:
- Why this matters for adoption / citation:

### Required Changes
1. Change:
   - Target section:
   - Concrete edit:
   - Evidence source:
   - Expected effect:
   - Risk if overdone:

### Optional Changes
1. Change:
   - When to include:
   - Why it helps:

### Do Not Do
- [Avoided scope creep or mode collapse risk]
- [Avoided overclaim]
- [Avoided unrelated pivot]

### Acceptance Checklist
- [ ] The revised proposal fixes the target issue.
- [ ] The revised proposal preserves the core contribution.
- [ ] The revised proposal does not add uncontrolled scope.
- [ ] The revised proposal's evidence plan is concrete enough to evaluate.
```

## Writing Guidance

### Revision Objective

Make the objective narrow enough to guide a single rewrite. Avoid objectives like "improve the proposal overall". Prefer objectives like "replace the single-protocol evaluation plan with a multi-protocol faithfulness test while preserving the sparse-training hypothesis".

### Preserve

This is mandatory. State which idea, contribution boundary, and useful existing choice should survive the rewrite. Without this section, the generator may fix one weakness by drifting to a different proposal.

### Highest-Leverage Revision Target

This may name an issue such as experiment credibility, mechanism feasibility, missing absorption evidence, weak adoption path, or unclear contribution. Ground it in `analysis_report.md` evidence, but do not restate the full diagnosis.

### Required Changes

Each required change must include:

- the target section of the proposal
- the concrete edit
- the evidence source
- the expected effect on adoption or citation potential
- the risk if overdone

### Optional Changes

Keep this short. Optional changes are for improvements that help only if they do not expand scope too much.

### Do Not Do

Use this section aggressively. Most proposal iterations fail by adding too much: too many baselines, too many tasks, speculative claims promoted to main claims, or pivots away from the core idea.

### Acceptance Checklist

The checklist should be binary enough that an automated or human reviewer can verify whether the revised proposal followed the instruction.

## Common Mistakes

1. Listing every weakness as a required change.
2. Adding baselines or experiments without saying what claim they test.
3. Encouraging a pivot that erases the proposal's distinctive contribution.
4. Mixing future research directions into the immediate revision plan.
5. Omitting the risks of overdoing a change.
