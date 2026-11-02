#!/usr/bin/env bash
#
# Run the IdeaEval ideation benchmark (91 cohorts) for the "glm-5.2" model profile.
#
# Drives scripts/run_batch.py with N-way concurrency (default 20). The batch is
# resumable (re-running skips completed runs) and, by default, detaches into the
# background so it survives an SSH disconnect.
#
# Override via env vars; pass other run_batch flags as arguments:
#   scripts/benchmark/run-glm-5.2.sh                 # background, 20 concurrent
#   CONCURRENCY=10 scripts/benchmark/run-glm-5.2.sh  # 10 concurrent
#   SAMPLES=3 scripts/benchmark/run-glm-5.2.sh       # 3 replicates per input
#   scripts/benchmark/run-glm-5.2.sh --foreground    # attach / watch live
#   scripts/benchmark/run-glm-5.2.sh --force         # rerun successful runs
#
# Monitor:  uv run python scripts/run_batch.py status --name "${NAME:-v5-glm-5.2}"
# Score:    uv run python scripts/evaluate_runs.py --runs-dir runs/"${NAME:-v5-glm-5.2}"
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."   # repo root

PROFILE="glm-5.2"
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
