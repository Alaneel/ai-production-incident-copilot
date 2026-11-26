#!/usr/bin/env bash
# Try OpsPilot on a throwaway kind cluster in about five minutes.
#
#   bash deploy/helm/kind-quickstart.sh          # build images from this checkout
#   OPSPILOT_IMAGE_TAG=0.3.0 bash deploy/helm/kind-quickstart.sh   # use published images
#
# Creates a kind cluster named opspilot-quickstart (refuses to touch an
# existing one), installs the chart with demo telemetry, runs `helm test`,
# and prints how to reach the API. Delete it with:
#   kind delete cluster --name opspilot-quickstart
set -euo pipefail
cd "$(dirname "$0")/../.."

cluster=opspilot-quickstart
context="kind-$cluster"
tag="${OPSPILOT_IMAGE_TAG:-}"
for command_name in docker kind kubectl helm; do
  command -v "$command_name" >/dev/null || { echo "missing required command: $command_name" >&2; exit 1; }
done
if kind get clusters | grep -Fxq "$cluster"; then
  echo "A kind cluster named $cluster already exists; delete it first or reuse it with helm directly." >&2
  exit 1
fi

kind create cluster --name "$cluster" --wait 120s
# Every command below names this cluster's context explicitly.
k() { kubectl --context "$context" "$@"; }

image_args=()
if [ -z "$tag" ]; then
  tag=local
  for component in orchestrator mcp-server telemetry-gateway; do
    docker build --file "services/$component/Dockerfile" --tag "ghcr.io/alaneel/opspilot-$component:$tag" .
    kind load docker-image --name "$cluster" "ghcr.io/alaneel/opspilot-$component:$tag"
  done
  image_args=(--set image.pullPolicy=Never)
fi

k create namespace shop
helm install opspilot deploy/helm/opspilot --kube-context "$context" \
  --namespace opspilot --create-namespace --set image.tag="$tag" ${image_args[@]+"${image_args[@]}"} \
  --set discovery.enabled=true --set 'targetNamespaces={shop}' --wait --timeout 6m
helm test opspilot --kube-context "$context" --namespace opspilot --logs --timeout 3m

cat <<EOF

OpsPilot is running on kind (demo telemetry, read-only).

  kubectl --context $context -n opspilot port-forward svc/opspilot-orchestrator 8080:8080
  export OPSPILOT_READER_TOKEN=\$(kubectl --context $context -n opspilot get secret opspilot-credentials -o jsonpath='{.data.orchestrator-read-token}' | base64 -d)
  claude mcp add --transport http opspilot http://localhost:8080/mcp --header "Authorization: Bearer \$OPSPILOT_READER_TOKEN"

Remove everything with: kind delete cluster --name $cluster
EOF
