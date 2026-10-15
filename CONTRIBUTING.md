# Contributing

Start with the [source tour](README.md#source-tour), [architecture](docs/architecture.md) and [evidence limits](docs/evidence.md). Preserve the approval interrupt, receipt and target checks, explicit stream completion, and independent recovery observation. Use synthetic fixtures in tests. Do not add production configuration or client data.

The tested release tools are CPython 3.13.16, Node 22.23.3 / npm 10.9.9 and Go 1.26.9. From a clean checkout:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-test.lock
python -m pip install --no-deps --no-build-isolation -e services/orchestrator -e services/mcp-server -e fault-lab/app
python -m pip check
python -B -m pytest services/orchestrator/tests services/mcp-server/tests evals/tests fault-lab/app/tests deploy/kubernetes/tests tests/test_portfolio_demo_offline.py --rootdir=. -q -rs -p no:cacheprovider
npm ci --ignore-scripts --no-audit --no-fund
OPSPILOT_PUBLIC_BUILD=1 npm test
npm run test:stream-contract
cd services/telemetry-gateway
GOTOOLCHAIN=local go vet ./...
GOTOOLCHAIN=local go test -race ./...
GOTOOLCHAIN=local go mod verify
GOTOOLCHAIN=local go build -trimpath ./cmd/gateway
```

The Python suite requires local Docker Compose/kubectl render tools and a Redis executable for its socket-only startup check. The 27 PostgreSQL tests require an explicitly owned local database via `INCIDENT_TEST_DATABASE_URL`; unset it to see clearly reported skips. Never point that variable at an existing or production database.

`requirements-runtime.lock` and `requirements-fault-lab.lock` pin platform-marked runtime distributions and SHA256 artifacts; `requirements-build.lock` pins build tools; `requirements-test.lock` unions all three applications and test/build tooling. `constraints-release.txt` keeps intentionally chosen compatible versions during lock regeneration. Install first-party code with `--no-deps --no-build-isolation` after the locks so build isolation cannot resolve a second graph. The human-edited service manifests retain bounds for library consumers; the release paths always use the locks.

Use `npm run build:public` for a reviewable neutral Workers package. `npm start` serves that compiled target in the real local workerd runtime on 127.0.0.1:3000; it requires a completed build. The secure Vinext release delegates its Node server separately and cannot directly load a Cloudflare-target module, so the local Workers command is intentional. `.openai/hosting.local.json` is an ignored local deployment override and must never be published. The Docker build context is the repository root with a strict `.dockerignore` allowlist; individual service-directory build commands from older notes are superseded. See [release validation](docs/release-verification-2026-10-13.md) for exact tested images and commands.

Contributions are distributed under the root MIT license. Retain the third-party notices and package license files. The SHA-pinned GitHub Actions workflow is prepared but has not been executed on GitHub; its local equivalents are recorded separately. It uses no provider credentials and does not deploy.
