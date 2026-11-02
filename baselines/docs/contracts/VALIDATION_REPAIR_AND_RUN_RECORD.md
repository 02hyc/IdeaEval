# Validation, Repair, and Run Record Contract

> **Status:** Implemented MVP contract for final mechanical validation, repair-loop control, and run records.
> **Last updated:** 2026-06-04
> **Runtime contracts:** [`MVP_RUNTIME_CONTRACTS.md`](./MVP_RUNTIME_CONTRACTS.md)

This document defines the infrastructure-layer completion gate for an MVP run. It is intentionally mechanical: it checks whether the required file and core sections exist, controls bounded repair attempts, and records what happened. It does not judge novelty, source faithfulness, method quality, experiment quality, impact quality, or writing quality.

## Proposal Versus Validator

The lead agent must still be instructed to write the full canonical proposal structure:

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

The MVP mechanical validator gates only these section intent IDs:

| Intent ID | Canonical heading |
|---|---|
| `title` | `# <Actual Proposal Title>` |
| `introduction` | `## Introduction` |
| `related_work` | `## Related Work` |
| `methods` | `## Methods` |
| `experiments_design` | `## Experiments Design` |
| `references` | `## References` |

`Impact Statement` and `Success Criteria` are part of the proposal-quality contract, startup prompt, and skill-guided self-check, but they are not MVP mechanical validation gates. If they are missing, the MVP validator must not mark the run invalid for that reason.

## File Rules

The validator checks `proposal.md` under the agent workspace, not under `framework/` and not under the repository root. The validator reads the file as UTF-8. A missing file, unreadable file, or undecodable file produces `valid: false` and records a human-readable entry in `errors`.

The validator should ignore leading and trailing whitespace when checking whether a heading body or section body is non-empty. A section whose body contains only whitespace is treated as missing.

## Heading Matching

The validator should parse Markdown ATX headings, meaning lines beginning with one to six `#` characters followed by heading text. It must ignore ATX-looking lines inside fenced code blocks. It does not need to parse Setext headings or HTML headings for the MVP.

`title` is present when the first non-empty ATX heading is an H1 heading with non-empty text after stripping surrounding whitespace and optional closing `#` characters. The validator does not require the literal text `Title`.

For section headings other than `title`, the validator accepts ATX heading levels 2 through 6. The matching process should normalize heading text by lowercasing, removing optional trailing `#` characters, trimming whitespace, removing a leading numeric prefix such as `1.`, `1)`, or `1 -`, replacing `&` and `+` with `and`, treating `/` as a separator, replacing runs of whitespace, hyphens, and underscores with one space, stripping a trailing colon, and stripping a trailing `section` suffix.

The MVP accepted heading aliases are:

| Intent ID | Accepted normalized headings |
|---|---|
| `introduction` | `introduction`, `intro`, `overview`, `background`, `motivation`, `problem statement`, `research motivation` |
| `related_work` | `related work`, `prior work`, `related literature`, `literature review`, `background and related work`, `existing work`, `previous work`, `prior art`, `related research` |
| `methods` | `methods`, `method`, `methodology`, `approach`, `proposed method`, `proposed approach`, `technical approach`, `research plan`, `proposed framework`, `system design`, `method design` |
| `experiments_design` | `experiments design`, `experiment design`, `experimental design`, `experiments`, `evaluation plan`, `experimental plan`, `evaluation`, `experiments and evaluation`, `evaluation and experiments`, `experimental setup`, `experiment setup`, `evaluation setup`, `study design`, `empirical evaluation`, `validation plan` |
| `references` | `references`, `bibliography`, `works cited`, `sources`, `citations`, `cited works` |

The validator should not infer a section from prose paragraphs. If the agent writes an unheaded paragraph that clearly discusses methods, the validator still treats `methods` as missing. This keeps the validator tolerant about heading wording but strict about document structure.

## Section Body Rules

A checked section is present only if an accepted heading exists and the body belonging to that section is non-empty after trimming whitespace. The section body runs until the next ATX heading with level less than or equal to the accepted heading's level, so nested subsection headings and their content count as part of the parent section body. For `references`, non-empty body is sufficient; the MVP validator does not verify citation reality, source retrieval, citation format, DOI validity, or whether references are cited in the text.

When duplicate headings match the same intent, the validator may use the first matching section with a non-empty body. Duplicate headings are not themselves an MVP validation error.

## ValidationResult

`ValidationResult.present` and `ValidationResult.missing` contain only checked intent IDs: `title`, `introduction`, `related_work`, `methods`, `experiments_design`, and `references`. `Impact Statement` and `Success Criteria` should not appear in `present` or `missing` for the MVP validator.

The validator version should be a stable string such as `mvp-structure-v1`. Implementations may include extra diagnostic fields in persisted `validation.json`, but the public `ValidationResult` must remain harness-neutral.

## Repair Loop

The MVP repair strategy is `external-loop`. The shared framework controls validation and repair attempts; adapters do not independently decide when a run is complete.

The framework flow is:

1. Launch the initial lead-agent run.
2. Validate `proposal.md`.
3. If validation passes, return a successful `RunResult`.
4. If validation fails and `repair.max_attempts` is greater than the number of repair prompts already sent, render a focused repair prompt.
5. Send the repair prompt through the selected harness adapter.
6. Validate again.
7. Stop when validation passes or configured repair attempts are exhausted.

`repair.max_attempts` counts only repair prompts after the initial run. A value of `0` disables repair. A value of `1` allows one initial run plus one repair prompt.

## Repair Prompt Contract

The repair prompt should be generated from `assets.repair_prompt_template` and written to `framework/input/repair_prompt.md`. The template must be able to receive these variables:

| Variable | Meaning |
|---|---|
| `topic` | Original user topic, preserved verbatim. |
| `proposal_path` | Agent-workspace-relative proposal path, normally `proposal.md`. |
| `missing_intents` | List of checked intent IDs that failed validation. |
| `missing_headings` | Canonical headings corresponding to `missing_intents`. |
| `validation_errors` | Validator error strings, such as missing file or decode failure. |
| `attempt_index` | One-based repair attempt index. |
| `max_attempts` | Configured maximum repair attempts. |

The repair prompt must instruct the agent to repair the existing `proposal.md` in place, preserve valid content where possible, add or fix only the missing mechanically required structure, avoid inventing sources or fake results, and stop after the file is mechanically valid.

## Session Continuation

Repair should continue the same Claude or Codex Python SDK session/thread whenever the SDK exposes reliable continuation. If continuation fails or is unavailable in a specific SDK version, the adapter may start a fresh run for the repair attempt, but it must provide the existing `proposal.md`, the missing intents, and the validator errors as context.

The adapter must record whether each repair attempt continued the prior session. This is represented by `RepairAttempt.continued_session`, `RepairAttempt.previous_session_id`, `RepairAttempt.session_id`, and `RepairAttempt.continuation_error`.

The adapter must not silently start a fresh repair run while reporting it as a same-session repair.

## Run Record

`run_record.json` is written at `<workspace.root>/<run_id>/run_record.json` when `logging.write_run_record` is true. It is the durable debugging and reproduction artifact for one run; it may contain more detail than the public CLI summary but must not contain secret values.

The MVP `run_record.json` should use this top-level shape:

```json
{
  "schema_version": 1,
  "run_id": "20260602-153012-a8f3c1",
  "status": "succeeded",
  "failure_kind": null,
  "created_at": "2026-06-02T15:30:12Z",
  "started_at": "2026-06-02T15:30:14Z",
  "completed_at": "2026-06-02T15:45:38Z",
  "topic": "Improving visual reasoning in multimodal models",
  "harness": "codex",
  "model": "gpt-5.4",
  "config_path": "configs/scientific-hypothesis-generator/gpt.yaml",
  "overrides": {},
  "resolved_config": {},
  "redaction": {
    "secrets_written": false,
    "redacted_fields": ["env.WTS_API_KEY", "env.WTS_BASE_URL", "env.GPT_API_KEY", "env.GPT_API_BASE_URL"],
    "env_var_names_recorded": ["WTS_API_KEY", "WTS_BASE_URL", "GPT_API_KEY", "GPT_API_BASE_URL"]
  },
  "workspace": {
    "run_path": ".runs/20260602-153012-a8f3c1",
    "agent_workspace_path": ".runs/20260602-153012-a8f3c1/agent_workspace",
    "framework_path": ".runs/20260602-153012-a8f3c1/framework"
  },
  "rendered": {
    "startup_prompt_path": ".runs/20260602-153012-a8f3c1/framework/input/startup_prompt.md",
    "repair_prompt_paths": []
  },
  "adapter": {
    "sdk": "codex-python",
    "sdk_version": null,
    "session_id": "019e87bc-3d41-7773-be5e-48b61f6875c5"
  },
  "validation": {
    "valid": true,
    "present": ["title", "introduction", "related_work", "methods", "experiments_design", "references"],
    "missing": [],
    "errors": [],
    "validator_version": "mvp-structure-v1"
  },
  "repair_attempts": [],
  "usage": null,
  "artifacts": {
    "proposal": ".runs/20260602-153012-a8f3c1/agent_workspace/proposal.md",
    "validation": ".runs/20260602-153012-a8f3c1/framework/validation.json",
    "run_record": ".runs/20260602-153012-a8f3c1/run_record.json"
  },
  "error": null
}
```

`resolved_config` should be a redacted serialization of the resolved config. It may include secret environment variable names but must not include secret environment variable values. `WTS_API_KEY` is required by default; `WTS_BASE_URL` is optional by default and should be forwarded only when present. The run record also records selected-harness API environment variable names: Claude records whichever names are configured under `adapters.claude.api_key_env` and `adapters.claude.base_url_env` (commonly `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` but configurable per-config to point at a different pair such as `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL`); Codex records whichever names are configured under `adapters.codex.api_key_env` and `adapters.codex.base_url_env` (defaulting to `GPT_API_KEY` / `GPT_API_BASE_URL`).

## Non-Goals

The MVP validator does not check source truth, reference format, citation coverage, novelty, significance, method feasibility, experiment sufficiency, Impact Statement quality, or Success Criteria quality. Those checks remain prompt/skill-guided quality-layer behavior unless a later contract promotes them into infrastructure validation.
