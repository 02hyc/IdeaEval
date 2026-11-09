# IdeaEval Benchmark Construction

The pipeline used to build the IdeaEval benchmarks: arXiv corpus → research-question taxonomy → time-windowed cohorts → pre-experiment proposals, plus citation-labeled pairs for calibrating the evaluator.

> The outputs are on Hugging Face (corpus, the 91-cohort ideation benchmark, calibration pairs); see the [top-level README](../README.md#data). You only need this pipeline to rebuild the benchmark or apply it to new papers.

## Setup

```bash
# Python 3.12+ and uv (https://docs.astral.sh/uv/)
uv sync
cp .env.example .env                 # API keys
cp config.example.yaml config.yaml   # paths and model settings
```

## Layout

```
scripts/
├── corpus/
│   ├── fetch_arxiv.py              # arXiv API: submissions by category and date range
│   ├── filter_papers.py            # drop pure benchmark/dataset/survey/report papers (LLM)
│   ├── update_citation_counts.py   # refresh Semantic Scholar citation counts
│   ├── check_date_leaks.py         # find papers public before their arXiv date
│   └── pdf_to_markdown.py          # download PDFs and convert to Markdown
├── features/
│   ├── extract_fields.py           # research problem, task formulation, domain (LLM)
│   └── extract_benchmarks.py       # benchmarks and datasets used (LLM)
├── taxonomy/
│   ├── build_taxonomy.py           # modified TaxoAdapt, with human review of dedup merges
│   ├── normalize_labels.py         # short labels for internal nodes + HTML view
│   ├── review_coherence.py         # relaxed per-paper membership check
│   └── build_topics.py             # leaves → topics (subtrees of ≤150 papers)
└── calibration/
    ├── build_citation_pairs.py     # same-topic pairs labeled by citation gap
    └── check_pair_date_leaks.py    # date-leak check on the pairs
src/ideaeval_benchmark/             # shared library: config, LLM client, arXiv/SoMark clients,
                                    #   benchmark extraction, date-leak detection, TaxoAdapt core
paper2proposal/                     # paper → proposal converter (uv workspace member)
docs/PIPELINE.md                    # the full pipeline, stage by stage
```

## Running

**[`docs/PIPELINE.md`](docs/PIPELINE.md)** walks through every stage with the exact commands, the human-review step in the taxonomy build, and the cohort-curation rules. The Makefile wraps the scripted stages:

```bash
make help
make corpus features inputs taxonomy   # taxonomy halts for review; edit the audit file and rerun
make topics proposals calibration
```

Two steps were done with ad-hoc code that is not included: merging the arXiv fetches with their Semantic Scholar matches (1b), and turning topics into the final cohorts (4: topic selection, 31-day windows, LLM-judged cleaning, leak resolution, refills). `PIPELINE.md` documents the rules applied in each, and their outputs are released on Hugging Face.

## Configuration

Settings resolve as CLI argument > environment variable > `config.yaml`, with paths relative to `benchmark/`.

| Variable | Used for |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` | All LLM calls in stages 1–3 (any OpenAI-compatible endpoint; we used DeepSeek-V4-Pro) |
| `THINKING_API_KEY`, `THINKING_BASE_URL`, `THINKING_MODEL` | Optional: a separate endpoint for the reasoning-heavy taxonomy calls |
| `S2_API_KEYS` / `S2_API_KEY`, `S2_API_BASE` | Semantic Scholar (citation refresh, date leaks) |
| `SOMARK_AUTH_USER`, `SOMARK_AUTH_PASSWORD` | Optional: SoMark PDF→Markdown. marker, MinerU or docling work too; put `<arxiv_id>.md` files in `data/markdown/` |
| `ANTHROPIC_API_KEY` | paper2proposal |
