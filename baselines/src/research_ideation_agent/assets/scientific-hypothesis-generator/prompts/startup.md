# Scientific Hypothesis Generation Run

You are running one scientific hypothesis generation task. Use the always-on run contract in `AGENTS.md` or `CLAUDE.md` for workspace and evidence constraints. Use this startup prompt for the concrete topic, run configuration, and execution entry point.

## Topic

```text
{{topic}}
```

## Run Configuration

Selected skills:

{{selected_skills}}

Available reviewer / exploration surfaces:

{{selected_subagents}}

## Mission

Generate one literature-grounded, falsifiable research hypothesis and write `proposal.md` in the run workspace. This is a proposal for future work, not a completed empirical paper.

Do not ask the user for clarification during the run. If a blocker prevents completion, write the blocker clearly in the relevant note and final response.

## Quality Rubric

The `scientific-reviewer` scores your work against `shared/rubric.md` — five dimensions (Problem Value, Novelty, Soundness, Feasibility, Experimental Quality), each on a 1–10 scale with defined severity bands, and each gate bound by its lowest dimension. Read `shared/rubric.md` before you start writing so you produce work to that standard, and re-read it to interpret the scores and severities in each review. A gate clears only when its review has no unresolved critical finding and every major issue is either fixed or the affected claim is honestly downgraded with its limitation recorded.

## Required Workflow

Complete these stages in order. Each stage ends at an independent review gate run by the `scientific-reviewer` subagent; you must clear a gate (verdict `status: PASS`) before continuing. Allow at most two review→revise cycles per gate; on the final cycle the reviewer clears the gate (`status: PASS`) with any remaining non-critical findings recorded as accepted limitations the proposal discloses. An unresolved critical finding or failed precondition is never waived and keeps the gate closed by design. Do not grade your own work — the reviewer is independent and judges the artifacts on disk.

1. Create `notes/task_contract.md` and `notes/run_metadata.json`.
2. Use the `literature-review` skill to write `notes/literature_review.md`. Then invoke `scientific-reviewer` with `gate: evidence`; it writes `notes/reviews/evidence_review.md`. Resolve findings and clear the gate.
3. Use the `hypothesis-method` skill to write `notes/gap_and_questions.md`, `notes/hypothesis.md`, `notes/novelty_check.md` (your own search-backed novelty check against prior work), `notes/method.md`, and `notes/experiment_plan.md`. Then invoke `scientific-reviewer` with `gate: hypothesis_method`; it writes `notes/reviews/hypothesis_method_review.md`. Resolve findings and clear the gate.
4. Use the `proposal-writeup` skill to write `proposal.md` from the approved notes. Then invoke `scientific-reviewer` with `gate: proposal`; it writes `notes/reviews/proposal_review.md`. Resolve findings (revise `proposal.md` or downgrade the claim) and clear the gate.

## Persistent Task Contract

`notes/task_contract.md` is the recovery anchor for context compaction, continuation, and repair. Keep it short. Include:

- original topic verbatim;
- active asset version: `scientific-hypothesis-generator`;
- selected skills and their stage ownership;
- the required output files listed below;
- current run status, the next unfinished stage, and which gates are cleared.

After any continuation or apparent loss of task context, read `notes/task_contract.md`, inspect existing `notes/` and `notes/reviews/`, and continue from the next unfinished stage or uncleared gate.

## MCP Evidence Tools

{{mcp_tool_overview}}

Use search for discovery, fetch for triage and page evidence, and extract central papers for full-text evidence. When you already have multiple independent queries, URLs, or paper URLs, pass them as a batch to the same tool call (`queries` or `urls`) so IO can run in parallel. `extract_paper` may return both raw section paths and a summary path. Read the summary by default — it carries mechanism-level detail and grounds most claims — and open a raw section only to verify a specific figure you quote verbatim (an exact number, equation, or ablation). Reading every raw section is the main avoidable cost of a run.

If a source is unavailable through the evidence tools, record it as unavailable and do not infer its contents.

## Required Files

Write these files relative to the run workspace:

- `notes/task_contract.md`
- `notes/run_metadata.json`
- `notes/literature_review.md`
- `notes/reviews/evidence_review.md`
- `notes/gap_and_questions.md`
- `notes/hypothesis.md`
- `notes/novelty_check.md`
- `notes/method.md`
- `notes/experiment_plan.md`
- `notes/reviews/hypothesis_method_review.md`
- `proposal.md`
- `notes/reviews/proposal_review.md`

## Stop Condition

Before stopping, confirm every required file exists and all three review verdicts have `status: PASS` (or `UNAVAILABLE` with the reason recorded), with no unresolved critical findings. The final response should report written paths, source coverage, the five-dimension scores from the proposal review, and any unresolved limitations.
