#!/usr/bin/env bash
# Run the same steps as .github/workflows/ci.yml before pushing.
#
#   bash scripts/ci-local.sh            # everything
#   bash scripts/ci-local.sh backend    # one stage: backend | evidence | frontend | gateway | chart
#
# Differences from CI, stated rather than hidden:
#   - CI starts an owned PostgreSQL fixture and rejects skipped tests. Locally
#     the PostgreSQL-only cases skip unless INCIDENT_TEST_DATABASE_URL is set.
#   - CI runs on Linux; this runs on your platform.
# The interpreter must be the exact release CPython (3.13.16). Set PYTHON to
# its path, or install it with `uv python install 3.13.16`.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
export WRANGLER_SEND_METRICS=false OPSPILOT_PUBLIC_BUILD=1
RELEASE_PYTHON=3.13.16
stage="${1:-all}"

step() { printf '\n== %s ==\n' "$1"; }

python_env() {
  if [ -x .venv/bin/python ] && [ "$(.venv/bin/python -c 'import platform; print(platform.python_version())')" = "$RELEASE_PYTHON" ]; then
    return
  fi
  local interpreter="${PYTHON:-}"
  if [ -z "$interpreter" ] && command -v uv >/dev/null 2>&1; then
    interpreter="$(uv python find "$RELEASE_PYTHON" 2>/dev/null || true)"
  fi
  if [ -z "$interpreter" ] || [ "$("$interpreter" -c 'import platform; print(platform.python_version())')" != "$RELEASE_PYTHON" ]; then
    echo "CPython $RELEASE_PYTHON is required. Set PYTHON=/path/to/python or run: uv python install $RELEASE_PYTHON" >&2
    exit 2
  fi
  step "Create .venv on CPython $RELEASE_PYTHON"
  rm -rf .venv
  "$interpreter" -m venv .venv
}

backend() {
  python_env
  step "Install exact artifact-verified Python graph"
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --require-hashes -r requirements-test.lock
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --no-deps --no-build-isolation \
    -e services/orchestrator -e services/mcp-server -e fault-lab/app
  .venv/bin/python -m pip check
  step "Backend suites"
  if [ -n "${INCIDENT_TEST_DATABASE_URL:-}" ]; then
    .venv/bin/python -B scripts/validate_ci_backend.py
  else
    echo "INCIDENT_TEST_DATABASE_URL is not set: PostgreSQL-only cases will skip (CI runs them)."
    .venv/bin/python -B -m pytest services/orchestrator/tests services/mcp-server/tests evals/tests \
      fault-lab/app/tests deploy/kubernetes/tests tests/test_portfolio_demo_offline.py \
      --rootdir=. -q -rs -p no:cacheprovider
  fi
}

evidence() {
  python_env
  step "Offline entry point and evidence provenance"
  .venv/bin/python -B scripts/portfolio_demo.py >/dev/null
  # Fails when retained runs no longer match the source. Re-run both
  # benchmarks (evals/README.md) and update the renderer's artifact paths.
  .venv/bin/python -B scripts/render_portfolio_evidence.py
  if ! git diff --quiet -- docs/assets/evidence.svg docs/assets/evidence.json; then
    echo "Rendered evidence differs from the committed files; commit the regenerated assets." >&2
    exit 1
  fi
}

frontend() {
  step "Exact frontend build and behavioural suite"
  npm ci --ignore-scripts --no-audit --no-fund
  npm test
  npm run test:stream-contract
}

gateway() {
  step "Gateway validation"
  (
    cd services/telemetry-gateway
    go version
    go vet ./...
    go test -race ./...
    go mod verify
    go build -trimpath -o /dev/null ./cmd/gateway
  )
}

chart() {
  step "Helm chart render and policy checks (the kind install runs in CI)"
  if ! command -v helm >/dev/null 2>&1; then
    echo "helm is not installed; skipping chart checks (CI runs them in the helm chart workflow)."
    return
  fi
  bash deploy/helm/test-chart.sh
}

case "$stage" in
  all) backend; evidence; frontend; gateway; chart ;;
  backend|evidence|frontend|gateway|chart) "$stage" ;;
  *) echo "Unknown stage: $stage (use backend, evidence, frontend, gateway, chart or no argument)" >&2; exit 2 ;;
esac
printf '\nAll requested stages passed.\n'
