#!/usr/bin/env bash
#
# Run the IdeaEval ideation benchmark (91 cohorts) for the "gpt-5.5" model profile.
#
# Drives scripts/run_batch.py with N-way concurrency (default 20). The batch is
# resumable (re-running skips completed runs) and, by default, detaches into the
# background so it survives an SSH disconnect.
#
# COST: gpt-5.5 is the most expensive profile. Consider setting a per-run cap in the
# config first (budgets.max_cost_usd in the --config file), and/or a smaller
# CONCURRENCY, before launching the full 91-input benchmark.
#
# Override via env vars; pass other run_batch flags as arguments:
#   scripts/benchmark/run-gpt-5.5.sh                 # background, 20 concurrent
#   CONCURRENCY=8 scripts/benchmark/run-gpt-5.5.sh   # 8 concurrent
#   SAMPLES=3 scripts/benchmark/run-gpt-5.5.sh       # 3 replicates per input
#   scripts/benchmark/run-gpt-5.5.sh --foreground    # attach / watch live
#   scripts/benchmark/run-gpt-5.5.sh --force         # rerun successful runs
#
# Monitor:  uv run python scripts/run_batch.py status --name "${NAME:-v5-gpt-5.5}"
# Score:    uv run python scripts/evaluate_runs.py --runs-dir runs/"${NAME:-v5-gpt-5.5}"
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."   # repo root

PROFILE="gpt-5.5"
CONCURRENCY="${CONCURRENCY:-20}"
SAMPLES="${SAMPLES:-1}"
CONFIG="${CONFIG:-configs/scientific-hypothesis-generator/base.yaml}"
INPUT="${INPUT:-inputs/ideaeval_bench_inputs.json}"
NAME="${NAME:-v5-${PROFILE}}"

echo "Benchmark: profile=${PROFILE}  concurrency=${CONCURRENCY}  samples=${SAMPLES}  name=${NAME}"
exec uv run python scripts/run_batch.py run \
  --config "${CONFIG}" \
  --model-profile "${PROFILE}" \
  --input "${INPUT}" \
  --name "${NAME}" \
  --concurrency "${CONCURRENCY}" \
  --samples "${SAMPLES}" \
  "$@"
