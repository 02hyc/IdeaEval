# IdeaEval Benchmark Construction

End-to-end pipeline used to build the IdeaEval benchmarks: from arXiv paper collection, through research-question clustering and time-windowed cohorts, to structured research proposals.

> The released data (corpus, 91-cohort ideation benchmark, calibration pairs) is on Hugging Face — see the [top-level README](../README.md#data). You only need this pipeline to rebuild the benchmark or extend it to new papers.

## Overview

```
ArXiv crawl → PDF→Markdown → Field extraction → TaxoAdapt clustering
  → Post-processing (verify, deep review, citation timelines, window slicing, date-cutoff filter, merge)
  → Paper-to-Proposal conversion
```

## Quick Start

```bash
# Prerequisites: Python 3.12+, uv (https://docs.astral.sh/uv/)
uv sync

# Configure
cp .env.example .env              # Fill in API keys
cp config.example.yaml config.yaml  # Adjust paths and model settings
```

## Pipeline Stages

Full documentation with DAG and dependencies: [docs/PIPELINE.md](docs/PIPELINE.md)

### Stage 0: Data Acquisition

| Script | Purpose | Output |
|--------|---------|--------|
| `fetch_arxiv_s2_bulk.py` | Crawl ArXiv by category + date range, enrich with S2 | `data/arxiv/submissions.jsonl` |
| `run_batch_pdf2md.py` | Download PDFs + convert to markdown via Somark API | `data/markdown/*.md` |
| `run_filter_papers.py` | Date-filter + LLM type classification (remove pure benchmarks/surveys) | `data/arxiv/*_filtered.jsonl` |

### Stage 1: Feature Extraction

| Script | Purpose | Output |
|--------|---------|--------|
| `run_field_extraction.py` | Extract research_problem, task_formulation per paper (LLM) | `data/field_extraction/fields_extracted.jsonl` |
| `run_benchmark_extraction.py` | Extract benchmarks/datasets from paper markdown (LLM) | `data/benchmark_extraction/benchmarks_extracted.jsonl` |

### Stage 2: Clustering

| Script | Purpose | Output |
|--------|---------|--------|
| `run_taxoadapt_v3.py` | **Current** clustering pipeline — dedup-before-assign at each level ([TaxoAdapt](https://arxiv.org/abs/2506.10737), modified) | `experiments/clustering_v3/` |
| `run_taxoadapt.py` | Original 7-stage pipeline (kept for reference) | `experiments/clustering_results/` |
| `run_rebalance.py` | Rebalance leaf sizes to target range (zero LLM calls) | `experiments/*_rebalanced/` |

See [Running the clustering](#running-the-clustering-v3) below for the full command + human-in-the-loop step.

### Stage 3: Post-Processing

| Script | Purpose | Output |
|--------|---------|--------|
| `run_verify.py` | Generate RQ per cluster + batch coherence verification | `experiments/verification/` |
| `run_deep_review.py` | Full-text per-paper review (reads markdown + optional web search) | `experiments/deep_review/` |
| `fetch_citation_timelines.py` | Fetch citing papers from Semantic Scholar | `datasets/citation_timelines.jsonl` |
| `build_cluster_windows.py` | Sliding 30-day windows + citation rungs/ladder computation | `datasets/cluster_windows_all.jsonl` |
| `filter_datecutoff_leaks.py` | Detect and remove papers with pre-cutoff publication leaks | `experiments/*_datecutoff_filtered/` |
| `merge_clusters_embedding.py` | Merge clusters via BGE embedding similarity within L1 domains | `experiments/clustering_results_merged/` |

### Stage 4: Paper-to-Proposal

| Script | Purpose | Output |
|--------|---------|--------|
| `paper2proposal` CLI | Convert papers to structured research proposals via single LLM call | `results/proposals/` |

## Workspace Packages

This project uses [uv workspaces](https://docs.astral.sh/uv/concepts/workspaces/):

| Package | Description | CLI |
|---------|-------------|-----|
| `ideaeval-benchmark` | Main pipeline (crawl, extract, cluster, post-process) | `uv run ideaeval-benchmark` |
| `paper2proposal` | Paper → structured research proposal via Anthropic LLM | `uv run paper2proposal` |

## Project Structure

```
├── src/ideaeval_benchmark/   # Main package
│   ├── config.py                   # Settings (YAML + env + paths)
│   ├── pipeline.py                 # Paper processing orchestrator
│   ├── s2_client.py                # Semantic Scholar API client
│   ├── arxiv.py                    # PDF download
│   ├── somark.py                   # PDF → Markdown (SoMark API client)
│   ├── llm_client.py              # LLM client (OpenAI-compatible)
│   ├── benchmarks.py              # Benchmark extraction
│   └── taxoadapt/                 # TaxoAdapt clustering system
│       ├── pipeline.py            # 7-stage clustering pipeline
│       ├── rebalance.py           # Leaf node size rebalancing
│       ├── review.py              # Deep review (full-text + web search)
│       ├── llm.py                 # Rate-limited LLM client
│       └── prompts.py            # LLM prompt templates
├── paper2proposal/                 # Workspace: paper → proposal conversion
│   ├── src/paper2proposal/        # LLM-based converter (single call + extended thinking)
│   └── pyproject.toml
├── scripts/                        # Pipeline stage entry points (see table above)
├── docs/PIPELINE.md               # Full execution guide with DAG
├── Makefile                       # Stage-based targets
├── config.example.yaml            # Configuration template
└── .env.example                   # API keys template
```

## Configuration

### Three-layer resolution (highest priority wins)

1. **CLI arguments** — per-invocation overrides (e.g., `--markdown-dir ./custom/`)
2. **Environment variables** — set in `.env` or exported (e.g., `MARKDOWN_DIR`)
3. **config.yaml** — stable project-wide settings (see `paths:` section)

### Required API Keys (.env)

| Key | Used by | Required |
|-----|---------|----------|
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | All LLM calls | Yes |
| `S2_API_KEYS` | Metadata fetch, citation timelines | Yes |
| `SOMARK_AUTH_*` | PDF→Markdown conversion | Only for Stage 0b |
| `SERPER_API_KEY` | Deep review web search | Optional |
| `TAVILY_API_KEY` | Date-cutoff detection | Optional |
| `ANTHROPIC_API_KEY` | Paper2Proposal (Anthropic API) | Required for Stage 4 |
| `ANTHROPIC_BASE_URL` | Paper2Proposal proxy endpoint | Optional |

## External Dependencies

| Service | Purpose | Alternative |
|---------|---------|-------------|
| SoMark API | PDF→Markdown (what we used) | Any converter that writes `data/markdown/<arxiv_id>.md`, e.g. [marker](https://github.com/datalab-to/marker), [MinerU](https://github.com/opendatalab/MinerU), [docling](https://github.com/docling-project/docling), or arXiv's HTML version |
| Semantic Scholar | Paper metadata + citations | Free API keys available |
| Serper / Tavily | Web search for deep review and date-leak checks | Optional |
| Anthropic API | Paper2Proposal LLM backend | Required for Stage 4 |

## Makefile Targets

```bash
make help         # Show all available targets
make fetch        # Stage 0a
make pdf2md       # Stage 0b
make filter       # Stage 0c
make extract      # Stage 1
make cluster      # Stage 2a
make rebalance    # Stage 2b
make verify       # Stage 3a
make deep-review  # Stage 3b
make citations    # Stage 3c
make windows      # Stage 3d
make datecutoff   # Stage 3e
make merge        # Stage 3f
make proposals    # Stage 5
make all          # Full pipeline end-to-end
```

## Running the clustering (V3)

The current clustering pipeline lives in `scripts/run_taxoadapt_v3.py`. It differs
from the original `run_taxoadapt.py` in one key way: at each level the LLM first
discovers subtopics → human reviews/dedups → THEN papers are assigned globally
(multi-label) to the deduped leaves. This avoids "papers locked into a stale
subtree before dedup."

### Inputs (35,505 papers)

The clustering expects three per-paper files, all keyed by arXiv ID:

```
$SHARED_INPUTS_DIR/
├── arxiv_metadata.jsonl          # 35,505 papers (title, abstract, date, citations) — released as IdeaEval-Corpus
├── fields_extracted.jsonl        # research_problem, task_formulation   (Stage 1, run_field_extraction.py)
└── benchmarks_extracted.jsonl    # benchmarks/datasets per paper         (Stage 1, run_benchmark_extraction.py)
```

Download the corpus from Hugging Face (see the top-level README), run Stage 1 to produce the other two files, and point an env var at the directory:

```bash
export SHARED_INPUTS_DIR=$PWD/data/inputs
```

### Pipeline flow

```
Stage 0   Load papers
Stage 1a  Build initial L1 taxonomy by sampling 300 diverse papers and asking
          the LLM to propose ~`--initial-topics` broad areas
            ↳ skipped if --v2-checkpoint is supplied (warm-start)
Stage 1   Assign papers to L1 (global, multi-label, 1-3 leaves each)
            ↳ also acts as the density signal for the L1 dedup LLM
Stage 1D  L1 deduplication → writes audit_L1_dedup.json → HALTS for human review
            ↳ skipped if --v2-checkpoint is supplied
─────────  (edit audit_L1_dedup.json — see "Editing audit files" below) ─────────
Stage 2   (resume) Apply L1 dedup
Stage 3   Discover L2 subtopics in parallel per L1 (label/description only, no papers)
Stage 3D  L2 deduplication → writes audit_L2_dedup.json → HALTS for human review
─────────  (edit audit_L2_dedup.json — same format as L1) ─────────
Stage 3A  (resume) Apply L2 dedup → globally re-assign all papers to L2 leaves
Stage 4+  Density-based expansion L3...L6 (each leaf > max_leaf splits locally)
Stage 5   Reassign unassigned papers
Stage 6   Drop clusters < min_leaf → clusters_raw.json
Stage 7a  Generate research_question + research_question_detail per cluster
Stage 7b  Per-paper coherence verification (kick outliers); drop clusters < min_leaf
```

### Run command (from scratch — the default for new users)

```bash
# First run — builds L1, halts at audit_L1_dedup.json
uv run python scripts/run_taxoadapt_v3.py \
    --input       $SHARED_INPUTS_DIR/arxiv_metadata.jsonl \
    --fields      $SHARED_INPUTS_DIR/fields_extracted.jsonl \
    --benchmarks  $SHARED_INPUTS_DIR/benchmarks_extracted.jsonl \
    --output-dir  experiments/clustering_v3 \
    --initial-topics 20 \
    --min-leaf 10 --max-leaf 50 --max-depth 6 \
    --max-workers 100 \
    --max-parallel-leaves 50

# ... edit experiments/clustering_v3/audit_L1_dedup.json ...

# Re-run the same command — applies L1 merges, runs Stage 3, halts at audit_L2_dedup.json
uv run python scripts/run_taxoadapt_v3.py  --input ... --fields ... --benchmarks ...  --output-dir experiments/clustering_v3

# ... edit experiments/clustering_v3/audit_L2_dedup.json ...

# Re-run the same command — applies L2 merges and runs Stage 3A through Stage 7
uv run python scripts/run_taxoadapt_v3.py  --input ... --fields ... --benchmarks ...  --output-dir experiments/clustering_v3
```

### Warm-starting from a deduped L1 checkpoint

If you already have a curated L1 taxonomy (e.g. from a previous run),
pass `--v2-checkpoint` to skip Stage 1a + Stage 1D entirely and jump straight
to Stage 3 (L2 discovery + dedup):

```bash
uv run python scripts/run_taxoadapt_v3.py \
    --v2-checkpoint experiments/clustering_v2/checkpoint_normalized.json \
    --input ... --fields ... --benchmarks ... --output-dir experiments/clustering_v3
```

### Editing audit files

Each `audit_L{N}_dedup.json` contains `groups` of nodes the LLM thinks should
be merged. For each group:

- `action` — set to `"merge"` (default) or `"skip"`
- `node_ids` — the first ID is the primary (kept) node; secondaries are merged in
- `suggested_label` / `suggested_description` — edit freely

Unmodified groups are merged as-is on resume.

### Long-running

Wrap in `nohup` / `tmux` so it survives SSH disconnects:

```bash
nohup uv run python scripts/run_taxoadapt_v3.py \
    --input $SHARED_INPUTS_DIR/arxiv_metadata.jsonl \
    --fields $SHARED_INPUTS_DIR/fields_extracted.jsonl \
    --benchmarks $SHARED_INPUTS_DIR/benchmarks_extracted.jsonl \
    --output-dir experiments/clustering_v3 \
    > experiments/clustering_v3/run.log 2>&1 &
```

### Tuning concurrency (and avoiding 429s)

Two knobs control how many LLM calls fire at once:

| Flag | Default | Controls |
|---|---|---|
| `--max-workers` | 100 | Inner batch concurrency within one LLM stage (paper assign, pseudo-label, coherence verify, ...) |
| `--max-parallel-leaves` | 50 | Outer concurrency: how many L1/leaf nodes expand in parallel during L2 discovery and L3+ expansion |

Peak in-flight calls during L2 discovery / L3+ expansion ≈
`max_parallel_leaves × max_workers`, bounded above by the HTTP connection
pool (`OPENAI_MAX_CONNECTIONS`, default 512). If your endpoint enforces an
account-wide concurrency limit (e.g. ~500), peak runs will saturate it and
trigger 429 `rate_limit_error` responses.

Safe settings for a ~500-concurrent-account-limit endpoint:

```bash
# Modest scaling: peak ≈ 30 × 20 = 600 (clipped by connection pool)
--max-workers 30 --max-parallel-leaves 20

# More conservative: peak ≈ 10 × 10 = 100
--max-workers 10 --max-parallel-leaves 10
```

You can also cap the connection pool itself:

```bash
export OPENAI_MAX_CONNECTIONS=80
export OPENAI_MAX_KEEPALIVE_CONNECTIONS=40
```

### Optional secondary LLM for thinking-heavy calls

Stage 2 subtopic discovery, dedup grouping, and Stage 6 research-question
generation benefit from a stronger model. Set these env vars to route those
calls to a separate endpoint:

```
THINKING_API_KEY=...
THINKING_BASE_URL=...
THINKING_MODEL=...
```

### Key outputs in `experiments/clustering_v3/`

| File | Description |
|---|---|
| `checkpoint.json` | Tree with `_pipeline_stage` for resume |
| `audit_L1_dedup.json` | L1 human-review file (from-scratch path only) |
| `audit_L2_dedup.json` | L2 human-review file |
| `taxonomy_tree.json` | Final taxonomy with paper assignments |
| `clusters_raw.json` | Leaves with `n_papers >= min_leaf` |
| `clusters_labeled.json` | + `research_question` / `research_question_detail` |
| `clusters.json` | + Stage 7 coherence-filtered (outliers kicked) |
| `verification_results.jsonl` | Per-paper `belongs: true/false` audit log |
| `stats.json` | Cluster count, coverage, size distribution |

## Development

```bash
# All scripts support --help
uv run python scripts/run_taxoadapt_v3.py --help

# Use --limit N for small-scale testing
uv run python scripts/run_field_extraction.py --limit 50
```
