# IdeaEval Evaluator

A pairwise ranker for research proposals. Given two proposals and a cutoff date *t*, it decides which one has the larger expected contribution to the field's knowledge *V(H, K<sub>t</sub>)*, judged only against literature published before *t*.

> **Status.** This evaluator did not reach the accuracy we needed. It agrees with citation-based labels on human-vs-human pairs (75–90%), but it does not reliably catch flawed AI proposals: on the blog's 75 hand-labeled human-vs-AI pairs it scored 43–49%, and on the cleaned 40-pair subset released here it scores 67.5%, against 62.5% for always picking the human proposal. The blog post discusses why. We release it as a reference implementation and a starting point, not as a validated metric.

## Architecture

```
            ┌───────────────────────────┐
cutoff t ──►│ Literature Review Agent   │──► literature_review.md + paper_summaries/ (reusable K_t)
            └───────────────────────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
 Analysis Agent (A)        Analysis Agent (B)      ← extra searches per proposal
        │                         │
        └────────────┬────────────┘
                     ▼
               Judge Agent  ──► "A > B" or "A < B" + report
```

- **Literature Review Agent** (`agents/agenthub/literature_review_agent.py`) surveys pre-cutoff work on the shared research direction. Its workspace is cached and reused for every pair in a cohort (`prebuild_lit_reviews.py`).
- **Analysis Agents** (`agents/agenthub/research_agent.py`) analyze each proposal's incremental value against that review.
- **Judge Agent** (`agents/agenthub/judge_agent.py`) reads the review, the local paper archive and both proposals, and makes the call. It is agentic: it can open archived papers and run small code checks in its workspace.

All search goes through a cutoff-aware backend; see [`../docs/search_backend.md`](../docs/search_backend.md).

### Judge prompts

| File | What it is |
|---|---|
| `agents/prompts/sys_judge_agent.md` | Production prompt (v23). Two questions per proposal: is the central contribution actually delivered by the written method, and how original is it against absorbed prior art. Includes explicit calibration against the failure modes we observed (polish, self-flagged risks, under-specified but real methods). |
| `agents/prompts/archive/sys_judge_agent_delta_k_rubric.md` | The earlier ΔK rubric described in the blog: seven non-compensatory factors (demand, marginal contribution, realization probability, informative-negative value, adoptability, experimental credibility, cost/risk). |

## Setup

```bash
cd evaluator
uv sync
cp example.env .env   # fill in keys for the models you use
```

Model aliases live in `agents/config.yaml` (`claude-opus-4.6`, `gpt-5.4`, `gpt-5.5`, `deepseek-v4-pro`, `deepseek-v4-flash`, `gemini-3.5-flash`). Each agent picks an alias there; the judge defaults to `claude-opus-4.6`, and `--judge-model` overrides it per run.

## Reproduce the calibration results (judge only)

This path needs **only model API keys**: the released calibration data includes the pre-built literature workspaces.

```bash
# Download IdeaEval-Calibration from Hugging Face into ./data/calibration (see top-level README), then:
uv run python scripts/run_literature_archive_judge_manifest.py \
  --dataset       data/calibration/setA/manifest.json \
  --proposal-root data/calibration \
  --output-root   experiments/setA_opus46 \
  --judge-model   claude-opus-4.6 \
  --concurrency 8 --max-attempts 3 --random-order-seed 42
```

`--random-order-seed` randomizes which proposal is shown as A, so position bias cancels out over a set. Each pair gets its own directory with the judge transcript and `full_ranking_result.json`; aggregate accuracy is written to `summary.json`. `--pair-id` (repeatable) and `--limit` select subsets.

Reference results with the production judge (agentic, with literature review, Claude Opus 4.6, seed 42):

| Set | n | Construction | Accuracy |
|---|---|---|---|
| A | 40 | higher- vs lower-cited real papers | 90.0% |
| B | 40 | real papers, citation gap ≥ 15 | 77.5% |
| C | 40 | real novel paper vs synthesized sound-but-derivative proposal | 95.0% |
| human-AI | 40 | real paper vs AI-generated proposal, hand-labeled | 67.5% |

See the dataset card for how this 160-pair set relates to the 260/250/75-pair sets reported in the blog post.

> The released literature workspaces omit `scraped_papers/` (the full text of third-party papers) for licensing reasons; the judge still sees `literature_review.md` and `paper_summaries/`. Expect small differences from the numbers above.

## Run the full three-stage ranker on one pair

Requires a search backend.

```bash
uv run python rank_main.py \
  --proposal-a path/to/a.md --proposal-b path/to/b.md \
  --cutoff-date 2025-11-11 --task-dir workspace/demo
```

To score many pairs that share a research direction, build the literature reviews once and reuse them. `prebuild_lit_reviews.py` reads the same manifest, using each pair's `topic_id`, `topic_label`, `topic_description` and `cutoff_date`:

```bash
uv run python prebuild_lit_reviews.py --devset pairs.json --cache-dir lit_cache --concurrency 4
uv run python scripts/run_literature_archive_judge_manifest.py \
  --dataset pairs.json --proposal-root proposals/ --lit-cache-root lit_cache --output-root experiments/run1
```

## Manifest format

```json
{"pairs": [{
  "pair_id": "p0001",
  "cutoff_date": "2025-11-11",
  "label": "a_better",
  "paper_a": {"paper_id": "2511.09105", "proposal_path": "proposals/2511.09105/2511.09105_proposal.md"},
  "paper_b": {"paper_id": "2511.09880", "proposal_path": "proposals/2511.09880/2511.09880_proposal.md"},
  "literature_workspace": "lit_review/l1_001_02_03"
}]}
```

- `label` is `a_better` or `b_better`. If it is missing, the side with more `citations` wins.
- Relative paths resolve against the current directory first, then `--proposal-root`.
- Without `literature_workspace`, the workspace is looked up in `--lit-cache-root` by `literature_cache_key`, or by `topic_id` and `cutoff_date`.

## Other tools

```bash
uv run python scripts/verify_position_bias.py <experiment_dir>   # re-run completed pairs with A/B swapped
uv run python serve_traces.py <experiment_dir>                   # browse agent traces in trace_viewer.html
uv run pytest                                                    # unit tests (live test: IDEAEVAL_LIVE_TESTS=1)
```
