PYTHON = uv run python

.PHONY: all fetch pdf2md filter extract cluster rebalance verify deep-review citations windows datecutoff merge proposals help

help:
	@echo "Ideation-Bench Pipeline Stages:"
	@echo ""
	@echo "  make fetch        - Stage 0a: Crawl ArXiv metadata"
	@echo "  make pdf2md       - Stage 0b: Download PDFs + convert to markdown"
	@echo "  make filter       - Stage 0c: Date + type filtering"
	@echo "  make extract      - Stage 1:  Field + benchmark extraction"
	@echo "  make cluster      - Stage 2a: TaxoAdapt clustering"
	@echo "  make rebalance    - Stage 2b: Rebalance cluster sizes"
	@echo "  make verify       - Stage 3a: RQ generation + coherence verification"
	@echo "  make deep-review  - Stage 3b: Full-text deep review"
	@echo "  make citations    - Stage 3c: Citation timeline fetching"
	@echo "  make windows      - Stage 3d: Build cluster windows (time slicing + citation stats)"
	@echo "  make datecutoff   - Stage 3e: Date-cutoff leak filtering"
	@echo "  make merge        - Stage 3f: Embedding-based cluster merging"
	@echo "  make proposals    - Stage 4:  Paper-to-proposal conversion"
	@echo ""
	@echo "See docs/PIPELINE.md for full documentation."

fetch:
	$(PYTHON) scripts/fetch_arxiv_s2_bulk.py

pdf2md:
	$(PYTHON) scripts/run_batch_pdf2md.py --workers 32

filter:
	$(PYTHON) scripts/run_filter_papers.py --workers 64

extract:
	$(PYTHON) scripts/run_field_extraction.py --workers 256
	$(PYTHON) scripts/run_benchmark_extraction.py --workers 256

cluster:
	$(PYTHON) scripts/run_taxoadapt.py \
		--input data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
		--output-dir experiments/clustering_results/ \
		--benchmarks data/benchmark_extraction/benchmarks_extracted.jsonl \
		--fields data/field_extraction/fields_extracted.jsonl \
		--min-leaf 30 --max-leaf 70

rebalance:
	$(PYTHON) scripts/run_rebalance.py \
		experiments/clustering_results/taxonomy_tree.json \
		experiments/clustering_results_rebalanced/

verify:
	$(PYTHON) scripts/run_verify.py \
		--clusters experiments/clustering_results_rebalanced/clusters.json \
		--input data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
		--output-dir experiments/verification/

deep-review:
	$(PYTHON) scripts/run_deep_review.py \
		--clusters experiments/verification/clusters_labeled.json \
		--output-dir experiments/deep_review/ \
		--markdown-dir data/markdown/

citations:
	$(PYTHON) scripts/fetch_citation_timelines.py

windows:
	$(PYTHON) scripts/build_cluster_windows.py \
		--clusters experiments/clustering_results_taxoadapt_rebalanced/clusters.json \
		--metadata data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl \
		--citations datasets/citation_timelines.jsonl \
		--fields data/field_extraction/fields_extracted.jsonl \
		--output datasets/cluster_windows_all.jsonl

datecutoff:
	$(PYTHON) scripts/filter_datecutoff_leaks.py --workers 8

merge:
	$(PYTHON) scripts/merge_clusters_embedding.py \
		--clusters experiments/clustering_results_rebalanced/clusters.json \
		--tree experiments/clustering_results/taxonomy_tree.json \
		--output-dir experiments/clustering_results_merged/ \
		--target-count 480 --max-size 100

proposals:
	uv run paper2proposal \
		--papers-dir data/markdown/ \
		--output-dir results/proposals/ \
		--workers 6

all: fetch pdf2md filter extract cluster rebalance verify deep-review citations windows datecutoff merge proposals
