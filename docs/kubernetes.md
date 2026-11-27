# Install OpsPilot on Kubernetes

The Helm chart in [`deploy/helm/opspilot`](../deploy/helm/opspilot) installs the orchestrator, the MCP server, the telemetry gateway and, optionally, a bundled PostgreSQL. The orchestrator also serves the console, so the API and the page people approve in share one address. **The default install is read-only.** It uses demo telemetry and gets no permission to change anything in your cluster.

## Try it on kind in five minutes

You need Docker, [kind](https://kind.sigs.k8s.io), kubectl and Helm 3.14 or later (CI uses Helm 4.3.1).

```bash
bash deploy/helm/kind-quickstart.sh
```

The script:

1. Creates a cluster named `opspilot-quickstart`. It refuses to reuse an existing one.
2. Builds the three images from your checkout.
3. Installs the chart with demo telemetry.
4. Runs `helm test`, which starts a real diagnosis through the MCP endpoint and checks that the reader credential **can't** approve it.
5. Prints how to open the console and the `claude mcp add` command. Ask why `payment-service` is slow. The demo diagnosis cites connection-pool exhaustion. Because the demo's deployment history is synthetic, OpsPilot won't propose a rollback; it asks for live revision history first. Enable `discovery` on a namespace with real Deployments to see proposals.

Once images are published, use `OPSPILOT_IMAGE_TAG=0.3.0` to skip the local build. Remove everything with:

```bash
kind delete cluster --name opspilot-quickstart
```

## Install on your cluster

From a release, once the chart is published:

```bash
helm install opspilot oci://ghcr.io/alaneel/charts/opspilot --version 0.3.0 \
  --namespace opspilot --create-namespace
```

From a checkout, after pushing the images to a registry your cluster can pull from:

```bash
helm install opspilot deploy/helm/opspilot --namespace opspilot --create-namespace \
  --set image.registry=registry.example.com/opspilot
```

Then check it:

```bash
helm test opspilot -n opspilot --logs
```

## Open the console

The console is part of the orchestrator image and is served at the API's own address:

```bash
kubectl -n opspilot port-forward svc/opspilot-orchestrator 8080:8080
kubectl -n opspilot get secret opspilot-credentials -o jsonpath='{.data.orchestrator-read-token}' | base64 -d
```

Open `http://localhost:8080`, expand **API connection**, confirm the backend and paste the reader token. That is enough to ask questions, open the incident inbox and read evidence. To approve or reject, also enter the operator token (`orchestrator-operator-token`) and the actor named by `auth.operatorActor`.

For shared use, put it behind your Ingress with TLS:

```yaml
ingress:
  enabled: true
  className: nginx
  host: opspilot.example.com
  tls: [{hosts: [opspilot.example.com], secretName: opspilot-tls}]
```

With the Ingress on, links in alerts, notifications and MCP results point at that host unless you set `orchestrator.consoleUrl`. What to know before you expose it:

- **The page is public; the API is not.** Anyone who can reach the address can load the page. It holds no credential, and every incident request still needs a token.
- **Tokens stay in the tab.** They are kept in page memory and never written to browser storage or a URL. The console sends them only to the address that served it, and only over HTTPS or a loopback port-forward. Opened over plain HTTP on a network, it refuses to send anything.
- **Nothing is loaded from elsewhere.** Fonts and scripts come from the image. The page's Content-Security-Policy allows scripts only from its own origin or matching the hash of an exported inline script.
- **The bundle is verified.** At startup the orchestrator checks every console file against the manifest written at build time and refuses to start if one was changed.
- **Tokens are not identities.** Put an identity-aware proxy in front for per-person access. `orchestrator.console.enabled: false` turns the page off and leaves the API and MCP endpoint unchanged.

## Point it at your telemetry

Set all three URLs, and pick the [signal profile](integrate.md#1-map-your-telemetry-with-a-signal-profile) closest to your naming:

```yaml
# values-prod.yaml
telemetry:
  prometheusUrl: http://prometheus.monitoring.svc:9090
  lokiUrl: http://loki-gateway.monitoring.svc
  tempoUrl: http://tempo.monitoring.svc:3200
  signalProfile: opentelemetry-kubernetes   # or spring-boot-micrometer
orchestrator:
  consoleUrl: https://opspilot.example.com
  alerts:
    defaultEnvironment: prod
networkPolicy:
  ingressNamespaces: [monitoring, ingress-nginx]   # Alertmanager and your ingress
```

```bash
helm upgrade --install opspilot deploy/helm/opspilot -n opspilot -f values-prod.yaml
```

A custom profile goes inline as `telemetry.signalProfileJson`. Before relying on a profile, check which queries return data:

```bash
kubectl -n opspilot exec deploy/opspilot-telemetry-gateway -- /gateway check --service checkout
```

Setting only some of the three URLs fails at render time. It never falls back to a mix of live and fixture data.

## Let it read, then act, in chosen namespaces

| Values | What OpsPilot can do in each `targetNamespaces` entry |
|---|---|
| (default) | Nothing. No Kubernetes token is mounted in any pod. |
| `discovery.enabled: true` | `get` Deployments, `get`/`list` ReplicaSets, `list` Events: rollout history for diagnosis |
| `remediation.enabled: true` | Also `patch` Deployments, so an **approved** restart or rollback can run |

```yaml
targetNamespaces: [checkout, payments]
discovery:
  enabled: true
remediation:
  enabled: true   # only after you've tried discovery
```

Every grant is a namespaced Role bound to the MCP server's service account. The chart never creates a ClusterRole, and the orchestrator has no Kubernetes access at all. Even with `remediation.enabled`, each execution needs a named human approval with the operator credential, and a signed receipt bound to the exact Deployment identity that a server-side dry run verified.

OpsPilot reads and acts in the namespace named by the incident's environment. With discovery on, it reads real rollout history, so a service with no Deployment in that namespace ends as *insufficient evidence* rather than a guess. Set `orchestrator.alerts.defaultEnvironment`, or the alert's `environment` label, to the service's namespace, and list that namespace in `targetNamespaces`.

## Credentials

Without `auth.existingSecret`, the chart generates random credentials once in `opspilot-credentials` and keeps them on upgrade and uninstall. CI checks that an upgrade doesn't rotate them.

| Key | Who uses it |
|---|---|
| `orchestrator-read-token` | Console readers, Alertmanager, MCP clients. Can start and read diagnoses, can't approve. |
| `orchestrator-operator-token` | On-call engineers. Approves as `auth.operatorActor`. |
| `mcp-*`, `telemetry-gateway-token` | Service-to-service only |
| `anthropic-api-key` or `openai-api-key` | Optional model reasoning; only with `auth.existingSecret` |

To manage the credentials yourself, create a Secret with the keys from [`secret.example.yaml`](../deploy/kubernetes/secret.example.yaml), including `database-url`, and set `auth.existingSecret`.

### Model reasoning (optional)

Diagnoses are deterministic until you name a model. To let Claude write the hypotheses, add `anthropic-api-key` to your Secret and set:

```yaml
orchestrator:
  model:
    provider: anthropic
    name: claude-opus-5-5
    effort: ""        # or low | medium | high | xhigh | max
```

`provider: openai` (the default) uses `openai-api-key` and the OpenAI Responses API instead. Either way, the model sees only the retained evidence, may cite only evidence IDs it was given, and never proposes or runs tools; approvals still go to a named human. An unknown provider or effort fails at render time.

## Persistence

| Values | Storage |
|---|---|
| `postgres.enabled: true` (default) | A bundled single-instance PostgreSQL 17 with a 10 GiB volume |
| `postgres.enabled: false`, `externalDatabase.url: postgresql://…` | Your managed PostgreSQL (recommended for production) |
| `postgres.enabled: false` and no URL | In memory. The orchestrator is pinned to one replica, and incidents are lost on restart. |

`redis.enabled: true` adds an event-delivery cache. Without it, event streams replay from checkpoints, which is slower but equally correct.

## How the chart is tested

[`deploy/helm/test-chart.sh`](../deploy/helm/test-chart.sh) renders the chart under a dozen value sets. It checks that the default has no Roles, no `patch` and no mounted token. It checks that RBAC stays namespaced, and that invalid combinations fail.

The [`helm chart` workflow](../.github/workflows/chart.yml) builds the three images and installs the demo chart on a fresh kind cluster. It then:

- runs `helm test`;
- checks inside the cluster that a demo diagnosis reaches *connection-pool exhaustion* with cited evidence, proposes no rollback from synthetic deployment history, and that the reader token gets 403 when it tries to approve;
- fetches every console file from the running API and compares it with the build manifest, checks that the script policy carries the exported hashes, and that the incident API still answers 401 without a token;
- upgrades to `discovery.enabled` for one namespace and confirms the upgrade keeps the credentials;
- checks with `kubectl auth can-i` that the MCP server can read but not patch Deployments in that namespace, and has no access elsewhere.

Version tags publish multi-arch images and the chart to GHCR with build provenance ([`release.yml`](../.github/workflows/release.yml)).

## Not covered yet

- The console uses the scoped tokens, not per-user sign-in. SSO for the console is not implemented.
- The bundled PostgreSQL uses the upstream `postgres:17.11-alpine` image pinned by digest, not the patched derivative the Compose stack builds locally. Use a managed database for production.
- Tested on kind (Kubernetes 1.37) only. Managed clusters (EKS, GKE, AKS) and their storage classes are untested.
