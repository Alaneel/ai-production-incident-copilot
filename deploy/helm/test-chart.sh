#!/usr/bin/env bash
# Render the chart under several value sets and check the safety-relevant
# output: default installs are read-only, RBAC is namespaced and minimal,
# and invalid combinations fail at render time. Needs only `helm`.
#
#   bash deploy/helm/test-chart.sh
set -euo pipefail
cd "$(dirname "$0")/opspilot"

failures=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; failures=$((failures + 1)); }
render() { helm template opspilot . --namespace opspilot "$@"; }
expect_contains() { if grep -Fq -- "$2" <<<"$3"; then pass "$1"; else fail "$1 (missing: $2)"; fi; }
expect_absent() { if grep -Fq -- "$2" <<<"$3"; then fail "$1 (unexpected: $2)"; else pass "$1"; fi; }
expect_failure() {
  local name="$1" message="$2"; shift 2
  local output
  if output="$(render "$@" 2>&1)"; then fail "$name (rendered without error)";
  elif grep -Fq -- "$message" <<<"$output"; then pass "$name";
  else fail "$name (wrong error: $output)"; fi
}

echo "lint"
lint() {
  local name="$1" output; shift
  if output="$(helm lint . --strict "$@" 2>&1)"; then pass "$name"; else fail "$name"; printf '%s\n' "$output" | grep -vE '^$|^==>' | sed 's/^/       /'; fi
}
lint "helm lint --strict"
lint "lint with remediation" --set discovery.enabled=true --set remediation.enabled=true --set 'targetNamespaces={shop}'

echo "default install is a read-only demo"
default="$(render)"
expect_absent "no Roles" "kind: Role" "$default"
expect_absent "no patch permission" '"patch"' "$default"
expect_absent "no live telemetry URLs" "PROMETHEUS_URL" "$default"
expect_contains "uses a built-in preset" "/etc/opspilot/signal-profiles/opentelemetry-kubernetes.json" "$default"
expect_contains "auth is required" "value: required" "$default"
expect_contains "remediation off" $'name: KUBERNETES_REMEDIATION_ENABLED\n              value: "false"' "$default"
expect_contains "bundled PostgreSQL" "kind: StatefulSet" "$default"
expect_contains "network policy" "kind: NetworkPolicy" "$default"
expect_contains "images from the registry" "image: ghcr.io/alaneel/opspilot-orchestrator:0.3.0" "$default"
if [ "$(grep -c 'automountServiceAccountToken: false' <<<"$default")" -ge 6 ]; then pass "no pod mounts a Kubernetes token"; else fail "no pod mounts a Kubernetes token"; fi

echo "discovery only: namespaced read access, still no patch"
discovery="$(render --set discovery.enabled=true --set 'targetNamespaces={shop,payments}')"
expect_contains "Role in shop" "namespace: shop" "$discovery"
expect_contains "Role in payments" "namespace: payments" "$discovery"
expect_absent "no patch permission" '"patch"' "$discovery"
expect_absent "no ClusterRole" "ClusterRole" "$discovery"
expect_contains "MCP token mounted" "automountServiceAccountToken: true" "$discovery"
smoke="$(render --set discovery.enabled=true --set 'targetNamespaces={shop,payments}' --show-only templates/tests/smoke.yaml)"
expect_contains "helm test diagnoses in the first target namespace" $'name: SMOKE_ENVIRONMENT, value: "shop"' "$smoke"

echo "remediation: patch only on Deployments in the listed namespaces"
remediation="$(render --set remediation.enabled=true --set 'targetNamespaces={shop}')"
expect_contains "patch on deployments" '["get", "patch"]' "$remediation"
expect_contains "remediation flag" $'name: KUBERNETES_REMEDIATION_ENABLED\n              value: "true"' "$remediation"
expect_contains "allowed namespaces" $'name: MCP_ALLOWED_NAMESPACES\n              value: "shop"' "$remediation"
expect_absent "no ClusterRole" "ClusterRole" "$remediation"

echo "live telemetry and custom profiles"
live="$(render --set telemetry.prometheusUrl=http://prom:9090 --set telemetry.lokiUrl=http://loki:3100 \
  --set telemetry.tempoUrl=http://tempo:3200 --set telemetry.signalProfile=spring-boot-micrometer)"
expect_contains "Prometheus URL" "value: \"http://prom:9090\"" "$live"
expect_contains "Spring preset" "/etc/opspilot/signal-profiles/spring-boot-micrometer.json" "$live"
custom="$(render --set-json 'telemetry.signalProfileJson="{\"name\":\"team\",\"metrics\":{}}"')"
expect_contains "custom profile ConfigMap" "profile.json:" "$custom"
expect_contains "custom profile path" "/etc/opspilot/custom-profile/profile.json" "$custom"

echo "persistence"
memory="$(render --set postgres.enabled=false --set orchestrator.replicas=3)"
expect_absent "no DATABASE_URL without a database" "DATABASE_URL" "$memory"
memory_orchestrator="$(render --set postgres.enabled=false --set orchestrator.replicas=3 --show-only templates/orchestrator.yaml)"
expect_contains "orchestrator forced to 1 replica" "replicas: 1" "$memory_orchestrator"
expect_absent "requested replicas ignored" "replicas: 3" "$memory_orchestrator"
scaled="$(render --set orchestrator.replicas=3 --show-only templates/orchestrator.yaml)"
expect_contains "replicas honoured with a database" "replicas: 3" "$scaled"
expect_contains "PodDisruptionBudget when scaled" "kind: PodDisruptionBudget" "$scaled"
external="$(render --set postgres.enabled=false --set externalDatabase.url=postgresql://u:p@db:5432/x)"
expect_contains "external DATABASE_URL" "DATABASE_URL" "$external"
expect_absent "no bundled StatefulSet" "kind: StatefulSet" "$external"
existing="$(render --set auth.existingSecret=team-opspilot)"
expect_absent "no generated Secret with existingSecret" "kind: Secret" "$existing"
expect_contains "uses the existing Secret" "name: team-opspilot" "$existing"

echo "model reasoning is off by default and needs an explicit model"
expect_absent "no model env by default" "_MODEL" "$default"
claude="$(render --set orchestrator.model.provider=anthropic --set orchestrator.model.name=claude-opus-5-5 --set orchestrator.model.effort=high)"
expect_contains "Claude model" $'name: ANTHROPIC_MODEL\n              value: "claude-opus-5-5"' "$claude"
expect_contains "Claude key from the secret" "key: anthropic-api-key" "$claude"
expect_contains "Claude effort" $'name: ANTHROPIC_EFFORT\n              value: "high"' "$claude"
expect_absent "no OpenAI env with Claude" "OPENAI_" "$claude"
openai="$(render --set orchestrator.model.name=some-model)"
expect_contains "OpenAI stays the default provider" "key: openai-api-key" "$openai"
expect_absent "no Claude env with OpenAI" "ANTHROPIC_" "$openai"

echo "bundled console: on by default, one switch turns it off"
expect_absent "image default serves the console" "CONSOLE_STATIC_DIR" "$default"
expect_contains "helm test expects the console" $'name: SMOKE_CONSOLE, value: "bundled"' "$(render --show-only templates/tests/smoke.yaml)"
no_console="$(render --set orchestrator.console.enabled=false)"
expect_contains "console turned off" $'name: CONSOLE_STATIC_DIR\n              value: ""' "$no_console"
expect_contains "helm test expects no console" $'name: SMOKE_CONSOLE, value: "not_bundled"' "$no_console"
ingress="$(render --set ingress.enabled=true --set ingress.host=opspilot.example.com --set 'ingress.tls[0].hosts[0]=opspilot.example.com')"
expect_contains "console links default to the TLS Ingress host" $'name: CONSOLE_URL\n              value: "https://opspilot.example.com"' "$ingress"
explicit="$(render --set ingress.enabled=true --set ingress.host=opspilot.example.com --set orchestrator.consoleUrl=https://console.example.com)"
expect_contains "an explicit console URL wins" $'name: CONSOLE_URL\n              value: "https://console.example.com"' "$explicit"
expect_contains "no console link without an Ingress host" $'name: CONSOLE_URL\n              value: ""' "$default"

echo "invalid combinations fail at render time"
expect_failure "partial telemetry" "must be set together" --set telemetry.prometheusUrl=http://prom:9090
expect_failure "remediation without namespaces" "need at least one entry in targetNamespaces" --set remediation.enabled=true
expect_failure "unknown preset" "telemetry.signalProfile must be" --set telemetry.signalProfile=nope
expect_failure "invalid profile JSON" "not valid JSON" --set-json 'telemetry.signalProfileJson="{oops"'
expect_failure "unknown model provider" "provider must be openai or anthropic" --set orchestrator.model.name=m --set orchestrator.model.provider=gemini
expect_failure "unknown effort" "effort must be one of" --set orchestrator.model.provider=anthropic --set orchestrator.model.name=m --set orchestrator.model.effort=turbo
expect_failure "effort with OpenAI" "effort applies only to provider anthropic" --set orchestrator.model.name=m --set orchestrator.model.effort=high

if [ "$failures" -gt 0 ]; then
  echo "$failures chart check(s) failed" >&2
  exit 1
fi
echo "All chart checks passed."
