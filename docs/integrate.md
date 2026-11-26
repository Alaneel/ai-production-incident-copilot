# Connect OpsPilot to your own stack

This guide connects OpsPilot to an existing Prometheus, Loki and Tempo setup and to Alertmanager, without changing any code. The Agent, the MCP tools and the approval boundary are the same as in the demo; only the gateway's query mapping is specific to your environment.

What stays true in every setup:

- Diagnosis is read-only. Telemetry is reached only through the gateway's allow-listed, bounded queries.
- An alert can start a diagnosis and can lead to a *proposal*. Nothing is executed until a named human approves it in the console. Execution then repeats its scope and target checks.
- Signals your environment doesn't expose are recorded as **unavailable**, never as an observed zero.

## 1. Map your telemetry with a signal profile

The orchestrator reasons over a fixed set of canonical signals. A signal profile tells the gateway how to fetch each one from *your* Prometheus. A profile is a JSON file, and every query references `{{service}}`.

| Signal | Unit the query must return | Used to detect |
|---|---|---|
| `request_latency_p95` | milliseconds | latency regressions, recovery checks |
| `request_error_rate` | ratio (0–1) | error bursts, recovery checks |
| `db_pool_active_ratio` | ratio (0–1) | connection-pool exhaustion |
| `db_pool_active` | connections | pool saturation detail |
| `cpu_utilization` | ratio of limit | CPU saturation |
| `memory_working_set` | bytes | memory leaks |
| `kafka_consumer_lag` | messages | consumer lag |
| `retry_rate` | retries per second | retry storms |
| `cache_miss_rate` | misses per second | cache penetration |

Start from the preset closest to your stack in [`deploy/signal-profiles/`](../deploy/signal-profiles):

| Preset | For |
|---|---|
| [`opentelemetry-kubernetes.json`](../deploy/signal-profiles/opentelemetry-kubernetes.json) | OpenTelemetry HTTP/DB semantic conventions with a `service_name` label, plus cAdvisor/kube-state-metrics and kafka-exporter |
| [`spring-boot-micrometer.json`](../deploy/signal-profiles/spring-boot-micrometer.json) | Spring Boot Actuator/Micrometer with an `application` tag, HikariCP and Resilience4j |
| [`fault-lab.json`](../deploy/signal-profiles/fault-lab.json) | The built-in demo and fault-lab naming (the default when no profile is set) |

A minimal profile looks like this:

```json
{
  "name": "team-stack",
  "metrics": {
    "request_latency_p95": {
      "promql": "histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{service_name=\"{{service}}\"}[5m]))) * 1000",
      "unit": "milliseconds"
    },
    "request_error_rate": {
      "promql": "sum(rate(http_server_request_duration_seconds_count{service_name=\"{{service}}\",http_response_status_code=~\"5..\"}[5m])) / clamp_min(sum(rate(http_server_request_duration_seconds_count{service_name=\"{{service}}\"}[5m])), 0.000001)"
    }
  },
  "log_selector": "{app=\"{{service}}\"}",
  "trace_service_attribute": "resource.service.name",
  "dependencies": {
    "checkout": ["payments", "inventory"],
    "payments": ["ledger"]
  }
}
```

- **`metrics`**: configure what you have. Two signals (latency and errors) are enough to start.
- **`log_selector`**: one LogQL stream selector. The gateway appends the bounded text filter itself.
- **`trace_service_attribute`**: the TraceQL attribute that holds the service name.
- **`dependencies`** (optional): your declared call graph, keyed as caller → callees. Without it, live mode uses only dependencies observed in traces, plus the demo catalog. Declared edges carry no health data, and the API reports them as `operator_declared_catalog`.

The gateway validates the profile at startup and refuses to start on any of these:
- an unknown signal name;
- a declared unit that doesn't match;
- a query without `{{service}}`;
- a log pipeline instead of a selector;
- a malformed trace attribute;
- unknown fields.

Service names are restricted to lowercase letters, digits and hyphens before they reach a query.

## 2. Check what your telemetry answers

Run the same read-only queries a diagnosis would, for one service, before you rely on them:

```bash
cd services/telemetry-gateway
PROMETHEUS_URL=https://prometheus.internal LOKI_URL=https://loki.internal TEMPO_URL=https://tempo.internal \
TELEMETRY_SIGNAL_PROFILE=../../deploy/signal-profiles/opentelemetry-kubernetes.json \
go run ./cmd/gateway check --service checkout --window 1h
```

```text
OpsPilot telemetry check · profile opentelemetry-kubernetes · service checkout · last 1h0m0s

SIGNAL                SOURCE      STATUS  DETAIL
request_latency_p95   prometheus  ok      240 samples, latest 182 milliseconds
request_error_rate    prometheus  empty   no samples: check the metric name and the label that selects checkout
                                          ↳ sum(rate(http_server_request_duration_seconds_count{service_name="checkout",...
logs                  loki        ok      20 results
traces                tempo       ok      5 results
dependencies          catalog     ok      2 edges from profile dependencies
```

Each row that isn't `ok` shows the rendered query, so you can paste it into Grafana Explore and adjust the profile. The check exits with code 1 on backend or query errors, which makes it usable in CI. Use `--json` for a machine-readable report and `-v` to print every query.

With Docker, the same check runs from the gateway image:

```bash
docker compose -f docker-compose.yml -f compose.connect.yml run --rm telemetry-gateway check --service checkout
```

## 3. Run the stack against your backends

On Kubernetes, use the [Helm chart](kubernetes.md) instead: set `telemetry.prometheusUrl`, `lokiUrl`, `tempoUrl` and `signalProfile`. The rest of this section describes the Compose route.

```bash
PROMETHEUS_URL=https://prometheus.internal LOKI_URL=https://loki.internal TEMPO_URL=https://tempo.internal \
SIGNAL_PROFILE=opentelemetry-kubernetes CONSOLE_URL=https://opspilot.internal \
docker compose -f docker-compose.yml -f compose.connect.yml up --build
```

[`compose.connect.yml`](../compose.connect.yml) mounts `deploy/signal-profiles` read-only. Set `SIGNAL_PROFILE_DIR` to use profiles kept in your own repository.

On Kubernetes, ship the profile as a ConfigMap and point the gateway at it. The [base](../deploy/kubernetes/base/telemetry-gateway.yaml) already reads `PROMETHEUS_URL`, `LOKI_URL` and `TEMPO_URL`:

```yaml
# kustomization.yaml in your overlay
configMapGenerator:
  - name: opspilot-signal-profile
    files: [profile.json]
patches:
  - target: {kind: Deployment, name: telemetry-gateway}
    patch: |-
      - op: add
        path: /spec/template/spec/volumes/-
        value: {name: signal-profile, configMap: {name: opspilot-signal-profile}}
      - op: add
        path: /spec/template/spec/containers/0/volumeMounts/-
        value: {name: signal-profile, mountPath: /etc/opspilot, readOnly: true}
      - op: add
        path: /spec/template/spec/containers/0/env/-
        value: {name: TELEMETRY_SIGNAL_PROFILE, value: /etc/opspilot/profile.json}
```

Deployment history and approved restart/rollback use the Kubernetes API through the MCP server; see the [Kubernetes guide](../deploy/kubernetes/README.md) for its namespace-scoped RBAC.

## 4. Start diagnoses from Alertmanager

Point an Alertmanager receiver at the orchestrator. When the API runs with `ORCHESTRATOR_AUTH_MODE=required`, use the **reader** token: the webhook can start diagnoses but can never approve one.

```yaml
# alertmanager.yml
route:
  routes:
    - matchers: [severity=~"page|critical"]
      receiver: opspilot
      continue: true          # keep your existing paging route
receivers:
  - name: opspilot
    webhook_configs:
      - url: https://opspilot-api.internal/v1/incidents/alerts/alertmanager
        send_resolved: false
        max_alerts: 20
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/opspilot-reader-token
```

For each firing alert, the orchestrator:

1. **Finds the service.** It takes the first usable label from `ALERT_SERVICE_LABELS` (default: `service, service_name, app_kubernetes_io_name, app, job`) and normalises it, so `Payments_API` becomes `payments-api`. Alerts without a usable label are skipped and the reason is reported.
2. **Builds the question.** It combines the alert name, severity and `summary`/`description` annotation into a question. Annotations are untrusted text: they are length-bounded, stripped of control characters and screened by the same input policy as console questions.
3. **Starts a background diagnosis.** It responds `202` straight away with an incident ID for each accepted alert, and a console link when `CONSOLE_URL` is set:

```json
{
  "accepted": [{"incident_id": "INC-4F2A91", "alertname": "HighLatency", "service": "checkout",
                "environment": "prod-sg", "console_url": "https://opspilot.internal/?incident=INC-4F2A91"}],
  "skipped": [{"alertname": "DiskFull", "reason": "no usable service label (looked for service, ...)"}]
}
```

Repeat notifications for the same alert fingerprint return the existing incident instead of starting another run. Limits are configurable:

| Variable | Default | Meaning |
|---|---|---|
| `ALERT_DEDUPE_SECONDS` | 3600 | How long a fingerprint maps to its incident |
| `ALERT_MAX_DIAGNOSES_PER_WEBHOOK` | 5 | Diagnoses started per notification (1–20) |
| `ALERT_MAX_CONCURRENT_DIAGNOSES` | 2 | Diagnoses running at once (1–8) |
| `ALERT_DEFAULT_ENVIRONMENT` | `prod-sg` | Used when the alert has no `environment`/`env` label |

Opening the console link loads the incident read-only. Restore it with your reader credentials, review the cited evidence, and approve or reject the proposal there.

### Find alert-started incidents

Nobody is watching a diagnosis that an alert started, so there are two ways to reach it afterwards.

**The console inbox.** "Incidents" in the top bar (or <kbd>⌘K</kbd> → "Recent incidents") lists the 20 most recently updated incidents with their status, service and leading hypothesis, and shows how many are waiting for approval. Opening one is a single read-only GET.

**The list API.** The same data is available to scripts and bots with a reader token:

```bash
curl -sS "https://opspilot-api.internal/v1/incidents?status=waiting_approval&limit=50" \
  -H "Authorization: Bearer $OPSPILOT_READER_TOKEN"
```

The list returns bounded summaries only: no evidence, traces or commands. `status` and `limit` (1–100, default 20) are optional.

### Notify a channel when a human is needed

Set `NOTIFY_WEBHOOK_URL` and the orchestrator posts one message when an alert-started diagnosis pauses for approval or finishes:

```text
OpsPilot INC-4F2A91 for checkout needs a human decision.
Alert: HighLatency
Leading hypothesis (87%): Database connection pool exhaustion
Proposed (not executed): Restore checkout container images from revision 141
Review: https://opspilot.internal/?incident=INC-4F2A91
```

| Variable | Default | Meaning |
|---|---|---|
| `NOTIFY_WEBHOOK_URL` | unset (off) | Receiver URL: http(s), no embedded credentials |
| `NOTIFY_WEBHOOK_FORMAT` | `json` | `slack` sends `{"text": …}` for Slack-compatible incoming webhooks; `json` sends the text plus `incident_id`, `status`, `service`, `requires_approval` and `console_url` |

The message is a pointer, not a control: it carries no evidence, command or approval capability, and approving still happens in the console. Text taken from the diagnosis is escaped so it cannot form mentions, links or markup. Delivery is best effort with a 5-second timeout, redirects are not followed, and a failed delivery never changes the incident. Only alert-started diagnoses notify; a question typed in the console already has someone watching.

## 5. Ask about a specific service

The API also accepts an explicit service, for chat-ops bots, runbook buttons or service names without a hyphen:

```bash
curl -sS https://opspilot-api.internal/v1/incidents \
  -H "Authorization: Bearer $OPSPILOT_READER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"question": "Checkout p95 doubled after the 14:00 deploy. Why?", "service": "checkout"}'
```

Without `service`, the first hyphenated name in the question is used, as before.

## 6. Let your agents ask OpsPilot

Claude Code, Cursor and other MCP clients can connect to the orchestrator's `/mcp` endpoint with the reader token. From there they can diagnose incidents and read the evidence, but they can't approve or execute anything:

```bash
claude mcp add --transport http opspilot https://opspilot-api.internal/mcp \
  --header "Authorization: Bearer $OPSPILOT_READER_TOKEN"
```

See [Use OpsPilot from your coding or ops agent](agents.md) for the tools and the safety properties.

## Current limits

- **Failure families**: the diagnosis profiles cover eight: connection-pool exhaustion, downstream timeout, CPU saturation, memory leak, deployment regression, consumer lag, retry storm and cache penetration. Other failure modes end as *insufficient evidence* rather than a guess.
- **Tenancy**: one gateway serves one profile and one telemetry tenant. Namespace/tenant scoping is on the [roadmap](project-reference.md#remaining-roadmap).
- **Identity**: API tokens are scoped credentials, not per-user SSO. Put the console behind your identity proxy for shared use.
- **Notifications**: one attempt per incident, with no retry queue. Treat the inbox or the list API as the source of truth.
- **Alert deduplication**: deduplication is in memory, per orchestrator process. Behind several replicas, route the webhook to one of them or rely on Alertmanager grouping.
