# IdeaEval Ideation Baselines

Ideation baselines for the IdeaEval benchmark. Given a research direction and a cutoff date, a CLI coding agent (Claude Code via the Claude Agent SDK, or Codex via the Codex SDK) reviews the pre-cutoff literature through cutoff-restricted search tools, analyzes gaps, proposes a falsifiable hypothesis, designs experiments, and writes `proposal.md`. The proposal uses the same section structure as the benchmark's human proposals, so the two can be compared directly by the evaluator.

The agent only designs future experiments. It never claims to have run them or reports empirical results.

## Two asset sets

| Asset set | Config | What it is |
|---|---|---|
| `simple` | `configs/simple/{claude,gpt,deepseek,glm,kimi}.yaml` | A single agent with one startup prompt, no skills or subagents. **This is the setup behind the win-rate table in the blog post** (Opus 4.6, GPT-5.4 + Codex, DeepSeek-V4-Pro, Kimi-K2.6, GLM-5.1). |
| `scientific-hypothesis-generator` | `configs/scientific-hypothesis-generator/base.yaml` + `configs/model-profiles.yaml` | A later, stronger harness: three skills (`literature-review`, `hypothesis-method`, `proposal-writeup`), a `scientific-reviewer` subagent that gates each stage, and a stop hook that blocks finishing until the required artifacts exist. It leaves auditable intermediate notes (`notes/literature_review.md`, `notes/hypothesis_reflection.md`, `notes/proposal_audit.md`, ...). |

Proposals from both sets are released in the IdeaEval-Bench dataset (see the [top-level README](../README.md#data)).

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). The Claude Agent SDK and Codex SDK come in as dependencies (the Codex binary is pinned for Linux; elsewhere point `CODEX_BIN` at a local Codex CLI).

```bash
cd baselines
uv sync
cp .env.example .env   # then fill in the keys you need
```

## Search tools (required)

Literature access goes through a local MCP server, `scholar-gateway-mcp` (in `src/research_ideation_agent/scholar_gateway/`), which exposes `web_search`, `web_fetch`, `extract_paper` and `summarize_paper`. Every result is filtered to the run's **cutoff date**, so the agent cannot see work published after it. The built-in web tools of Claude Code and Codex are disabled in every config.

The MCP server is a thin client over a cutoff-aware search service ("Web Tools Service", `WTS_BASE_URL` + `WTS_API_KEY`). That service was internal to our team and is **not** part of this release. See [`../docs/search_backend.md`](../docs/search_backend.md) for the HTTP interface it has to implement and for public building blocks you can use to stand one up.

`summarize_paper` additionally uses an OpenAI-compatible endpoint (`DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`). It is optional: without it the agent reads the extracted paper sections directly.

## Models

Model profiles (`configs/model-profiles.yaml`) set the harness, the model id and the names of the env vars that hold its key and base URL. Only env var *names* are stored in config files.

| Profile | Harness | Env vars |
|---|---|---|
| `claude-opus-4.6` | Claude Agent SDK | `CLAUDE_OPUS_4_6_API_KEY`, `CLAUDE_OPUS_4_6_BASE_URL` |
| `kimi-k2.6` | Claude Agent SDK | `KIMI_K2_6_API_KEY`, `KIMI_K2_6_BASE_URL` |
| `glm-5.2` | Claude Agent SDK | `GLM_5_2_API_KEY`, `GLM_5_2_BASE_URL` |
| `deepseek-v4-pro` | Claude Agent SDK | `DEEPSEEK_V4_PRO_API_KEY`, `DEEPSEEK_V4_PRO_BASE_URL` |
| `gpt-5.5` | Codex SDK | `GPT_5_5_API_KEY`, `GPT_5_5_BASE_URL` |
| `gpt-5.4` | Codex SDK | `GPT_5_4_API_KEY`, `GPT_5_4_BASE_URL` |

Non-Anthropic models run inside Claude Code through an Anthropic-compatible endpoint (the `simple` configs for GLM, Kimi and DeepSeek point at `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL`, which is what we used). The Claude model id defaults to `claude-opus-4-6`; change it if you go through Bedrock or another gateway.

## Run one topic

```bash
uv run research-ideation-agent run \
  --topic "How auxiliary information shapes LLM reasoning" \
  --cutoff-date 2025-11-11 \
  --config configs/scientific-hypothesis-generator/base.yaml \
  --model-profile gpt-5.4 \
  --run-id demo-gpt54
```

Switch models with `--model-profile`, or use a `simple` config directly (`--config configs/simple/glm.yaml`). Long topics can be passed with `--topic-file`. Other useful flags: `--workspace-root`, `--set key=value` (override any config field, e.g. `--set budgets.timeout_seconds=7200`), `--json`, `--quiet`.

## Run the benchmark

`inputs/ideaeval_bench_inputs.json` holds the 91 cohorts of the ideation benchmark: `id` (cohort id), `topic` (a 30–50 word description of the research direction) and `date` (the cutoff, one day before the earliest paper in the cohort).

```bash
# The setup behind the blog's table, e.g. GLM-5.1:
uv run python scripts/run_batch.py run \
  --config configs/simple/glm.yaml \
  --input ideaeval_bench_inputs.json --name simple-glm --concurrency 20

# The upgraded harness, one script per model profile:
scripts/benchmark/run-claude-opus-4.6.sh            # background, 20 concurrent
CONCURRENCY=10 scripts/benchmark/run-gpt-5.4.sh --foreground

uv run python scripts/run_batch.py status --name simple-glm
uv run python scripts/evaluate_runs.py --runs-dir runs/v5-gpt-5.4   # reviewer-gate scorecard (scientific-hypothesis-generator runs only)
```

Batches are resumable: re-running skips runs that already succeeded. Per-run and aggregate metrics (wall time, tokens, USD cost, tool-call counts, number of extracted papers) are written to `runs/<name>/_batch/state.json`.

To score proposals against the human proposals of the same cohort, use the evaluator (`../evaluator`).

## Outputs

Each run writes to `.runs/<run_id>/` (or `runs/<batch>/<run_id>/` in batch mode):

```
agent_workspace/proposal.md            # final deliverable
agent_workspace/notes/                 # intermediate notes (scientific-hypothesis-generator)
agent_workspace/references/            # raw paper markdown saved by extract_paper
agent_workspace/papers/paper_summaries/
framework/validation.json              # proposal structure check
framework/logs/adapter_events.jsonl    # full event stream
run_record.json                        # config, status, usage, cost
```

If `proposal.md` fails validation, the runtime asks the same session to repair it (`repair.max_attempts`).

## Cost tracking

Token usage is normalized into `run_record.json` → `usage`. When a provider does not report cost, `pricing.models` in the config (USD per million tokens, keyed by the exact model id) is used to compute `usage.total_cost_usd`; `budgets.max_cost_usd` is checked against it after every adapter call. See `configs/scientific-hypothesis-generator/base.yaml` for a filled-in price table.

## Inspecting runs

```bash
uv run viz .runs              # lightweight log viewer (Python)
```

`trace-viewer/` is a richer Next.js viewer with per-turn timing and cost; see its README.

## Tests

```bash
uv run pytest
```

## Further reading

`docs/` has the design notes: architecture, runtime contracts, CLI and Python API specs, the validation/repair loop, and the scholar-gateway MCP reference.
