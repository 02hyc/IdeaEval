# Scientific Hypothesis Generator Repair

The external validator found that `{{proposal_path}}` is mechanically invalid or incomplete for this hypothesis-generation run.

Original user topic:

```text
{{topic}}
```

Repair attempt {{attempt_index}} of {{max_attempts}}.

Missing checked section intent IDs:

{{missing_intents}}

Canonical headings for the missing checked sections:

{{missing_headings}}

Validation errors:

{{validation_errors}}

## Repair Objective

Repair the existing `{{proposal_path}}` in place while preserving the scientific hypothesis generation contract. Keep valid content where possible. Add or fix missing required structure, and update note files if the proposal depends on a missing hypothesis reflection, experiment design, proposal audit, or proposal reflection.

## Recovery Anchor

Before editing, read `notes/task_contract.md` if it exists. If it does not exist, create it from the original topic and the existing notes before repairing the proposal. Then inspect the existing `notes/` files to recover the selected hypothesis, literature grounding, reflection decisions, and experiment design. Do not restart the whole research process unless the current artifacts are unusable.

## Constraints

- Do not invent new sources, references, paper metadata, or prior-work claims.
- Do not add claims that depend on sources you have not inspected.
- Do not claim or imply that experiments were run.
- Do not turn the proposal into a list of alternatives.
- If a missing section cannot be fully supported by existing evidence, write a narrow, uncertainty-aware section rather than fabricating confidence.
- Use the proposal structure and template in `proposal-writeup`; do not create a separate proposal format here.
- If `proposal.md` exists but a gate review (`notes/reviews/*_review.md`) is missing or not `status: PASS`, re-invoke `scientific-reviewer` for that gate after the structural repair and resolve any critical or major findings.

Stop after `{{proposal_path}}` and the required notes exist and the proposal is mechanically valid.
