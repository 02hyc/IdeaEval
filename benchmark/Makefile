PYTHON = uv run python
META = data/arxiv/arxiv_2025sep_2026jan_metadata_filtered.jsonl
INPUTS = data/inputs
TAXO = experiments/clustering_v3

.PHONY: help corpus features inputs taxonomy topics proposals calibration

help:
	@echo "IdeaEval benchmark pipeline (see docs/PIPELINE.md):"
	@echo ""
	@echo "  make corpus       - 1: filter papers, refresh citations, check date leaks, PDF -> Markdown"
	@echo "  make features     - 2: extract research fields and benchmarks"
	@echo "  make inputs       -    drop zero-citation papers, collect taxonomy inputs"
	@echo "  make taxonomy     - 3a: build the taxonomy (halts twice for human review; rerun to resume)"
	@echo "  make topics       - 3b-d: normalize labels, coherence review, topic-level clusters"
	@echo "  make proposals    - 5: paper-to-proposal conversion"
	@echo "  make calibration  - 6: citation-labeled calibration pairs + date-leak check"
	@echo ""
	@echo "Fetching arXiv (1a) and cohort curation (4) are run by hand; see docs/PIPELINE.md."

corpus:
	$(PYTHON) scripts/corpus/filter_papers.py --workers 64
	$(PYTHON) scripts/corpus/update_citation_counts.py --input $(META)
	$(PYTHON) scripts/corpus/check_date_leaks.py --metadata $(META) --papers-dir data/markdown --output-dir $(TAXO)
	$(PYTHON) scripts/corpus/pdf_to_markdown.py --workers 32

features:
	$(PYTHON) scripts/features/extract_fields.py --workers 128
	$(PYTHON) scripts/features/extract_benchmarks.py --workers 256

inputs:
	mkdir -p $(INPUTS)
	$(PYTHON) -c "import json,sys; [print(l, end='') for l in open(sys.argv[1]) if (json.loads(l).get('s2_citation_count') or 0) > 0]" \
		data/arxiv/arxiv_2025sep_2026jan_metadata_filtered_citation_updated.jsonl > $(INPUTS)/arxiv_metadata.jsonl
	cp data/field_extraction/fields_extracted.jsonl data/benchmark_extraction/benchmarks_extracted.jsonl $(INPUTS)/

taxonomy:
	$(PYTHON) scripts/taxonomy/build_taxonomy.py \
		--input $(INPUTS)/arxiv_metadata.jsonl \
		--fields $(INPUTS)/fields_extracted.jsonl \
		--benchmarks $(INPUTS)/benchmarks_extracted.jsonl \
		--output-dir $(TAXO) --min-leaf 10 --max-leaf 50 --max-depth 6

topics:
	$(PYTHON) scripts/taxonomy/normalize_labels.py --checkpoint $(TAXO)/checkpoint.json --output-dir $(TAXO)
	$(PYTHON) scripts/taxonomy/review_coherence.py --checkpoint $(TAXO)/checkpoint_normalized.json --output-dir $(TAXO) \
		--input $(INPUTS)/arxiv_metadata.jsonl --fields $(INPUTS)/fields_extracted.jsonl --benchmarks $(INPUTS)/benchmarks_extracted.jsonl
	$(PYTHON) scripts/taxonomy/build_topics.py --tree $(TAXO)/checkpoint_normalized.json \
		--clusters $(TAXO)/clusters_reviewed.json --output $(TAXO)/topic_level_clusters.json --max-topic-size 150

proposals:
	uv run paper2proposal --papers-dir data/markdown/ --output-dir results/proposals/ --workers 6

calibration:
	$(PYTHON) scripts/calibration/build_citation_pairs.py --clusters $(TAXO)/clusters_reviewed.json \
		--checkpoint $(TAXO)/checkpoint_normalized.json --metadata $(INPUTS)/arxiv_metadata.jsonl \
		--output $(TAXO)/ranker_pairs.json
	$(PYTHON) scripts/calibration/check_pair_date_leaks.py --pairs $(TAXO)/ranker_pairs.json \
		--metadata $(INPUTS)/arxiv_metadata.jsonl --output-dir $(TAXO)
