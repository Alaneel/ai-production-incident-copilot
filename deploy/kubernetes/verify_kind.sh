#!/usr/bin/env bash
set -euo pipefail

cluster_name="${KIND_CLUSTER_NAME:-incident-copilot}"
namespace="incident-copilot"
image_tag="kind-${cluster_name}"
port_forward_pid=""
result_path="${KIND_RESULT_PATH:-}"
created_cluster="false"
private_kubeconfig_dir=""
private_kubeconfig=""

# Every API request is bound to this invocation's explicit cluster. Never let
# a caller's default kubeconfig/current-context decide a mutation target.
cluster_kubectl() {
  command kubectl --kubeconfig "$private_kubeconfig" --context "kind-$cluster_name" "$@"
}

cleanup() {
  if [[ -n "$port_forward_pid" ]]; then
    kill "$port_forward_pid" 2>/dev/null || true
  fi
  if [[ "$created_cluster" == "true" && "${KEEP_KIND_CLUSTER:-false}" != "true" ]]; then
    if ! KUBECONFIG="$private_kubeconfig" kind delete cluster --name "$cluster_name"; then
      echo "Cluster deletion failed; private kubeconfig retained at $private_kubeconfig" >&2
      return
    fi
    # Preserve credentials if deletion cannot be confirmed.
    local remaining_clusters
    remaining_clusters="$(kind get clusters)" || return
    if grep -Fxq "$cluster_name" <<<"$remaining_clusters"; then
      echo "Test cluster remains; private kubeconfig retained at $private_kubeconfig" >&2
      return
    fi
    rm -f "$private_kubeconfig"
    rmdir "$private_kubeconfig_dir" 2>/dev/null || true
  elif [[ -n "$private_kubeconfig_dir" ]]; then
    echo "Private test kubeconfig retained at $private_kubeconfig; inspect partial/kept resources manually." >&2
  fi
}
trap cleanup EXIT

for command_name in docker kind kubectl curl jq; do
  command -v "$command_name" >/dev/null || {
    echo "missing required command: $command_name" >&2
    exit 1
  }
done

if [[ ${#cluster_name} -gt 63 || ! "$cluster_name" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
  echo "KIND_CLUSTER_NAME must be a lowercase DNS label." >&2
  exit 1
fi
if [[ -n "$result_path" && -e "$result_path" ]]; then
  echo "Result path already exists; verification reports are not overwritten." >&2
  exit 1
fi

existing_clusters="$(kind get clusters)"
if grep -Fxq "$cluster_name" <<<"$existing_clusters"; then
  echo "Existing Kind cluster '$cluster_name' will not be replaced; choose a different KIND_CLUSTER_NAME." >&2
  exit 1
fi

# Inspect cached exact database inputs before allocating the owned cluster.
docker image inspect opspilot-postgres:17.11-security-20261013 >/dev/null
docker image inspect opspilot-redis:7.4.11-security-20261013 >/dev/null
private_kubeconfig_dir="$(mktemp -d /private/tmp/copilot-kind-kubeconfig.XXXXXX)"
private_kubeconfig="$private_kubeconfig_dir/config"
kind create cluster --name "$cluster_name" --kubeconfig "$private_kubeconfig" \
  --image kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5 --wait 120s
created_cluster="true"

docker build -f services/orchestrator/Dockerfile -t "incident-copilot/orchestrator:$image_tag" .
docker build -f services/mcp-server/Dockerfile -t "incident-copilot/mcp-server:$image_tag" .
docker build -f services/telemetry-gateway/Dockerfile -t "incident-copilot/telemetry-gateway:$image_tag" .
kind load docker-image --name "$cluster_name" \
  "incident-copilot/orchestrator:$image_tag" \
  "incident-copilot/mcp-server:$image_tag" \
  "incident-copilot/telemetry-gateway:$image_tag" \
  opspilot-postgres:17.11-security-20261013 \
  opspilot-redis:7.4.11-security-20261013

cluster_kubectl apply -f deploy/kubernetes/base/namespace.yaml
cluster_kubectl -n "$namespace" create secret generic incident-copilot-secrets \
  --from-literal=postgres-user=incident \
  --from-literal=postgres-password=local-kind-password \
  --from-literal=database-url=postgresql://incident:local-kind-password@postgres:5432/incident_copilot \
  --from-literal=telemetry-gateway-token=local-development-token \
  --from-literal=mcp-read-token=local-read-token \
  --from-literal=mcp-operator-token=local-operator-token \
  --from-literal=orchestrator-read-token=kind-read-verification-only-000000000001 \
  --from-literal=orchestrator-operator-token=kind-operator-verification-only-0000001 \
  --from-literal=orchestrator-operator-actor=kind-verifier@opspilot.local \
  --from-literal=mcp-approval-secret=local-kind-approval-secret
# Rewrite only the three local application image tags in the rendered overlay.
cluster_kubectl kustomize deploy/kubernetes/overlays/local-kind \
  | sed "s|\(image: incident-copilot/[^ :]*:\)kind$|\1$image_tag|" \
  | cluster_kubectl apply -f -

cluster_kubectl -n "$namespace" rollout status statefulset/postgres --timeout=180s
cluster_kubectl -n "$namespace" rollout status deployment/redis --timeout=180s
cluster_kubectl -n "$namespace" rollout status deployment/telemetry-gateway --timeout=180s
cluster_kubectl -n "$namespace" rollout status deployment/mcp-server --timeout=180s
cluster_kubectl -n "$namespace" rollout status deployment/orchestrator --timeout=180s

allowed="$({
  cluster_kubectl auth can-i patch deployments \
    --as=system:serviceaccount:${namespace}:mcp-remediator \
    -n "$namespace"
} | tr -d '\r')"
denied="$({
  cluster_kubectl auth can-i patch deployments \
    --as=system:serviceaccount:${namespace}:mcp-remediator \
    -n kube-system || true
} | tr -d '\r')"
[[ "$allowed" == "yes" ]]
[[ "$denied" == "no" ]]

cluster_kubectl -n "$namespace" port-forward service/orchestrator 18080:8080 \
  >/tmp/incident-copilot-kind-port-forward.log 2>&1 &
port_forward_pid="$!"
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18080/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:18080/health >/dev/null
[[ "$(curl --silent http://127.0.0.1:18080/health | jq -r '.api_auth')" == "required" ]]
[[ "$(curl --silent -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/v1/incidents/unknown)" == "401" ]]
[[ "$(curl --silent -o /dev/null -w '%{http_code}' -X POST \
  -H 'Authorization: Bearer kind-read-verification-only-000000000001' \
  http://127.0.0.1:18080/v1/incidents/unknown/approval)" == "403" ]]

if ! incident="$({
  curl --fail-with-body --silent --show-error \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer kind-read-verification-only-000000000001' \
    -d '{"question":"为什么 payment-service 今天延迟突然升高？","environment":"incident-copilot"}' \
    http://127.0.0.1:18080/v1/incidents
})"; then
  printf 'Incident creation failed: %s\n' "$incident" >&2
  exit 1
fi
incident_id="$(jq -er '.incident_id' <<<"$incident")"
[[ "$(jq -r '.status' <<<"$incident")" == "waiting_approval" ]]
[[ "$(jq -r '.remediation.requires_approval' <<<"$incident")" == "true" ]]

rejected="$({
  curl --fail --silent \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer kind-operator-verification-only-0000001' \
    -d '{"decision":"reject","actor":"kind-verifier@opspilot.local","reason":"Kubernetes verification never executes production changes.","idempotency_key":"kind-verification-reject-0001"}' \
    "http://127.0.0.1:18080/v1/incidents/${incident_id}/approval"
})"
[[ "$(jq -r '.status' <<<"$rejected")" == "rejected" ]]
[[ "$(jq '[.trace[] | select(.step == "execute_or_simulate")] | length' <<<"$rejected")" == "0" ]]

result="$({
  jq -n \
  --arg verified_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg cluster "$cluster_name" \
  --arg kind_version "$(kind version)" \
  --arg kubernetes_version "$(cluster_kubectl version -o json | jq -r '.serverVersion.gitVersion')" \
  --arg incident_id "$incident_id" \
  --arg allowed "$allowed" \
  --arg denied "$denied" \
  '{
    verified_at: $verified_at,
    cluster: $cluster,
    kind_version: $kind_version,
    kubernetes_version: $kubernetes_version,
    rollouts: "passed",
    incident_id: $incident_id,
    rejected_before_execution: true,
    api_auth: "required",
    unauthenticated_denied: true,
    reader_approval_denied: true,
    remediation_rbac_in_namespace: $allowed,
    remediation_rbac_kube_system: $denied
  }'
})"
if [[ -n "$result_path" ]]; then
  mkdir -p "$(dirname "$result_path")"
  (set -o noclobber; printf '%s\n' "$result" >"$result_path")
fi
printf '%s\n' "$result"
