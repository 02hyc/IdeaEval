# paper2proposal

Converts a published paper (Markdown) into a structured **pre-experiment research proposal**. The method design and experimental plan are preserved; every experimental result is removed (the *information boundary*). The text is rewritten in forward-looking language, and the proposed method's numbers in the main results table are marked **TBD**.

This is the converter used to build the human proposals in the IdeaEval benchmark. Ideation baselines produce proposals with the same section structure, so the two can be compared directly.

## Setup

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/). From the `benchmark/` directory:

```bash
uv sync --package paper2proposal
export ANTHROPIC_API_KEY=sk-ant-...
# export ANTHROPIC_BASE_URL=...   # optional, for a proxy; defaults to the Anthropic API
```

The default model is Claude Opus 4.6 (`claude-opus-4-6`) with extended thinking, matching the configuration used to build the released benchmark. Pass `--config my_config.yaml` to override (see `src/paper2proposal/default_config.yaml` for the format).

## Usage

```bash
# Single paper
uv run paper2proposal --paper path/to/2509.13414.md --output-dir results/proposals

# Batch: a flat directory of <paper_id>.md files
uv run paper2proposal --papers-dir papers/ --output-dir results/proposals --workers 6
```

Re-running skips papers that already have a valid proposal, so interrupted batches can be resumed.

| Flag | Required | Default | Description |
|---|---|---|---|
| `--paper` | one of | — | Path to a single paper `.md` |
| `--papers-dir` | one of | — | Directory of paper `.md` files (batch mode) |
| `--output-dir` | yes | — | Output directory |
| `--config` | no | built-in `default_config.yaml` | Custom LLM config |
| `--limit` | no | all | Only process the first N papers |
| `--workers` | no | `4` | Max concurrent requests |
| `-v`, `--verbose` | no | off | Debug logging |

## Output

```
output-dir/<paper_id>/<paper_id>_proposal.md
```

Every proposal follows a fixed structure:

```
# <Title>
## Introduction          — problem, motivation, core insight, falsifiable hypothesis, failure modes
## Proposed Approach     — full method design
## Related Work
## Experiments           — setup, baselines, metrics, ablations (proposed-method results: TBD)
## Success Criteria      — proceed / pivot / refute thresholds
## Impact Statement
## References
```

A proposal is accepted only if it contains `## Introduction`, `## Proposed Approach`, `## Experiments` and `## Success Criteria`. Otherwise it is saved as `.md.invalid` and regenerated on the next run.

## Layout

```
src/paper2proposal/
├── __main__.py          # CLI
├── converter.py         # PaperConverter + parallel runner
├── llm_client.py        # Anthropic client (streaming, retry, extended thinking)
├── default_config.yaml
└── prompts/
    ├── system.md        # proposal structure and information-boundary rules
    └── user.md
```
