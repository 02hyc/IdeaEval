"""Smoke test: runs a single pair through the full ranker pipeline with real LLM.

Verifies:
- Pipeline completes without errors
- Per-agent workspace structure is created correctly
- Each agent has its own log, messages.json, and workspace/ directory
- Reports are extracted and saved
- Judge produces parseable JSON output

Requires real API keys and the search service, so it is skipped by default.

Usage:
    IDEAEVAL_LIVE_TESTS=1 python -m pytest tests/test_ranker_smoke.py -v -s
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import tempfile
from pathlib import Path

import pytest

from runtime.secret_bootstrap import bootstrap_secrets

bootstrap_secrets()

from runtime.rank_runner import RankRunConfig, run_ranker_task


PROPOSAL_A = """\
Title: Adaptive Gradient Clipping for Sparse Transformers

We propose a method that dynamically adjusts gradient clipping thresholds based on
the sparsity pattern of attention masks in transformer models. Current fixed-threshold
clipping fails to account for the highly variable gradient magnitudes that arise when
attention is concentrated on few tokens. Our approach monitors the effective sparsity
ratio per layer and scales the clipping bound inversely with sparsity, preventing both
gradient explosion in sparse layers and unnecessary gradient suppression in dense layers.

Experiment Plan:
- Compare against fixed clipping (0.5, 1.0, 2.0) and no clipping on GPT-2 medium
- Datasets: WikiText-103, C4 subset
- Metrics: perplexity, training stability (loss variance), wall-clock time
- Ablation: remove per-layer adaptation, use global sparsity estimate only
"""

PROPOSAL_B = """\
Title: Cross-Lingual Knowledge Distillation via Shared Subword Embeddings

We propose distilling a large English-only language model into a multilingual student
by aligning shared subword embeddings across languages before distillation. The key
insight is that many subword tokens (numbers, punctuation, borrowed words) are shared
across scripts, and these provide natural anchor points for alignment. We first identify
shared tokens, align their embedding spaces via Procrustes rotation, then perform
standard knowledge distillation with the aligned representations as initialization.

Experiment Plan:
- Teacher: LLaMA-7B (English). Student: 1.3B params, multilingual tokenizer
- Target languages: French, German, Japanese, Hindi (varying script overlap)
- Baselines: standard KD without alignment, mBERT initialization, random init
- Metrics: downstream task accuracy (XNLI, XQuAD), BLEU on translation
- Ablation: vary % of shared anchors, compare Procrustes vs learned alignment
"""


@pytest.fixture(scope="module")
def task_dir():
    with tempfile.TemporaryDirectory(prefix="ranker_smoke_") as d:
        yield d


@pytest.mark.skipif(
    os.getenv("IDEAEVAL_LIVE_TESTS") != "1",
    reason="live LLM test; set IDEAEVAL_LIVE_TESTS=1 to run",
)
@pytest.mark.asyncio
async def test_ranker_smoke(task_dir):
    config = RankRunConfig(
        proposal_a=PROPOSAL_A,
        proposal_b=PROPOSAL_B,
        cutoff_date="2025-01-01",
        max_tool_calls_per_turn=4,
        judge_max_tool_calls_per_turn=3,
        task_root=task_dir,
    )

    result = await run_ranker_task(config)

    # Pipeline completed
    assert result.exit_code == 0, f"Pipeline failed: {result.error}"

    # Per-agent workspace structure
    root = Path(task_dir)
    for agent_name in ("research_a", "research_b", "judge"):
        agent_dir = root / "agents" / agent_name
        assert agent_dir.exists(), f"Missing agent dir: {agent_dir}"
        assert (agent_dir / "messages.json").exists(), f"Missing messages.json for {agent_name}"
        assert (agent_dir / "agent.log").exists(), f"Missing agent.log for {agent_name}"
        assert (agent_dir / "workspace").is_dir(), f"Missing workspace/ for {agent_name}"

    # Reports extracted
    assert (root / "reports" / "report_a.md").exists()
    assert (root / "reports" / "report_b.md").exists()
    assert len(result.report_a) > 100, "Report A too short"
    assert len(result.report_b) > 100, "Report B too short"

    # Judge output is valid
    assert result.ranking is not None
    assert "result" in result.ranking, f"Missing 'result' in ranking: {result.ranking}"
    assert result.ranking["result"] in ("A > B", "A < B", "A ≈ B")

    # Ranking result files
    assert (root / "ranking_result.json").exists()
    assert (root / "full_ranking_result.json").exists()


if __name__ == "__main__":
    bootstrap_secrets()
    with tempfile.TemporaryDirectory(prefix="ranker_smoke_") as d:
        print(f"Task dir: {d}")
        result = asyncio.run(run_ranker_task(RankRunConfig(
            proposal_a=PROPOSAL_A,
            proposal_b=PROPOSAL_B,
            cutoff_date="2025-01-01",
            max_tool_calls_per_turn=4,
            judge_max_tool_calls_per_turn=3,
            task_root=d,
        )))
        print(f"\nExit code: {result.exit_code}")
        if result.ranking:
            print(f"Result: {result.ranking.get('result')}")
            print(f"Confidence: {result.ranking.get('confidence')}")
        if result.error:
            print(f"Error: {result.error}")
