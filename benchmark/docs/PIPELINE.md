# Pipeline Execution Guide

This document describes the full Ideation-Bench construction pipeline, from raw ArXiv data to structured research proposals.

## Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/) installed
- API keys configured in `.env` (see `.env.example`)
- `config.yaml` configured (see `config.example.yaml`)

## Stage 0: Data Acquisition

### 0a. Fetch ArXiv metadata + Semantic Scholar enrichment

```bash
uv run python scripts/fetch_arxiv_s2_bulk.py \
  --categories cs.LG cs.AI cs.CL cs.CV stat.ML \
  --start-date 2025-09-01 --end-date 2026-01-31
```

**Output**: `data/arxiv/submissions.jsonl`

### 0b. Batch PDF download + Markdown conversion

Uses the SoMark API (set `somark.api_url` and `SOMARK_AUTH_*`). Alternatively convert PDFs with any open-source tool (marker, MinerU, docling) and place `<arxiv_id>.md` files in `data/markdown/`, then skip this step.

```bash
uv run python scripts/run_batch_pdf2md.py --workers 32
```

**Output**: `data/markdown/*.md`

### 0c. Filter papers (date + type classification)

```bash
uv run python scripts/run_filter_papers.py --workers 64
```

**Output**: `data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl`

---

## Stage 1: Feature Extraction

### 1a. Extract research problem + task formulation

```bash
uv run python scripts/run_field_extraction.py --workers 256
```

**Output**: `data/field_extraction/fields_extracted.jsonl`

### 1b. Extract benchmarks/datasets

```bash
uv run python scripts/run_benchmark_extraction.py --workers 256
```

**Output**: `data/benchmark_extraction/benchmarks_extracted.jsonl`

---

## Stage 2: Clustering (TaxoAdapt)

### 2a. Density-driven hierarchical clustering

```bash
uv run python scripts/run_taxoadapt.py \
  --input data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
  --output-dir experiments/clustering_results/ \
  --benchmarks data/benchmark_extraction/benchmarks_extracted.jsonl \
  --fields data/field_extraction/fields_extracted.jsonl \
  --min-leaf 30 --max-leaf 70
```

**Output**: `experiments/clustering_results/taxonomy_tree.json`, `clusters.json`

### 2b. Rebalance cluster sizes

```bash
uv run python scripts/run_rebalance.py \
  experiments/clustering_results/taxonomy_tree.json \
  experiments/clustering_results_rebalanced/
```

**Output**: `experiments/clustering_results_rebalanced/clusters.json`

---

## Stage 3: Post-Processing

### 3a. RQ generation + coherence verification

```bash
uv run python scripts/run_verify.py \
  --clusters experiments/clustering_results_rebalanced/clusters.json \
  --input data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
  --output-dir experiments/verification/
```

### 3b. Deep review (full-text per-paper judgment)

```bash
uv run python scripts/run_deep_review.py \
  --clusters experiments/verification/clusters_labeled.json \
  --output-dir experiments/deep_review/ \
  --markdown-dir data/markdown/
```

### 3c. Citation timeline fetching

```bash
uv run python scripts/fetch_citation_timelines.py
```

**Output**: `datasets/citation_timelines.jsonl`

### 3d. Build cluster windows (sliding time windows + citation stats)

Joins clusters + metadata + citations into 30-day sliding windows with citation rungs/ladder computation. This is the key data structure for downstream benchmark pool construction.

```bash
uv run python scripts/build_cluster_windows.py \
  --clusters experiments/clustering_results_taxoadapt_rebalanced/clusters.json \
  --metadata data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
  --citations datasets/citation_timelines.jsonl \
  --fields data/field_extraction/fields_extracted.jsonl \
  --output datasets/cluster_windows_all.jsonl
```

**Output**: `datasets/cluster_windows_all.jsonl` (one row per cluster×window cohort)

### 3e. Date-cutoff leak filtering

```bash
uv run python scripts/filter_datecutoff_leaks.py --workers 8
```

**Output**: `experiments/clustering_results_taxoadapt_rebalanced_datecutoff_filtered/`

### 3f. Embedding-based cluster merging

```bash
uv run python scripts/merge_clusters_embedding.py \
  --clusters experiments/clustering_results_taxoadapt_rebalanced/clusters.json \
  --tree experiments/clustering_results_taxoadapt/taxonomy_tree.json \
  --output-dir experiments/clustering_results_merged/ \
  --target-count 480 --max-size 100
```

---

## Stage 4: Paper-to-Proposal Generation

Uses the `paper2proposal` workspace package.

```bash
uv run paper2proposal \
  --papers-dir data/markdown/ \
  --output-dir results/proposals/ \
  --workers 6
```

Or with cluster-aware scheduling (smallest clusters first):

```bash
uv run paper2proposal \
  --clusters-jsonl datasets/cluster_windows_all.jsonl \
  --papers-dir data/markdown/ \
  --output-dir results/proposals/ \
  --workers 6
```

---

## Stage Dependencies (DAG)

```
fetch_arxiv  ──→  run_filter_papers  ──→  run_field_extraction  ──┐
                                     ──→  run_benchmark_extraction ─┤
                                                                    ↓
run_batch_pdf2md  ──────────────────────────────→  run_taxoadapt
                                                        ↓
                                                   run_rebalance
                                                        ↓
                                                   run_verify
                                                        ↓
                                                   run_deep_review
                                                        ↓
                                            fetch_citation_timelines
                                                        ↓
                                            build_cluster_windows
                                                        ↓
                                            filter_datecutoff_leaks
                                                        ↓
                                            merge_clusters_embedding
                                                        ↓
                                                  paper2proposal
```

## Configuration Resolution Order

For all path-related settings, values are resolved in this priority:

1. **CLI argument** (e.g., `--markdown-dir ./custom/path`)
2. **Environment variable** (e.g., `MARKDOWN_DIR=./custom/path`)
3. **config.yaml `paths:` section**
4. **Sensible relative defaults** (relative to project root)

## External Dependencies

| Service | Required for | Notes |
|---------|-------------|-------|
| SoMark API | Stage 0b (PDF→MD) | Optional; marker, MinerU or docling also work — just provide your own markdowns |
| Semantic Scholar API | Stages 0a, 3c, 3d | Free tier available; multiple keys recommended |
| Serper API | Stage 3b (web search) | Optional; improves deep review accuracy |
| Tavily API | Stage 3d (date-cutoff) | Optional web search for leak detection |
| LLM endpoint | Stages 0c, 1, 2, 3a-b | OpenAI-compatible API |
| Claude Code CLI | Stage 4 (paper2proposal) | Requires `claude` command available |
