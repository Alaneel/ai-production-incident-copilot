"""Opt-in isolated merged-Compose pool-fault diagnosis acceptance; no model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("docker-compose.yml", "compose.fault-lab.yml", "compose.real-pool.yml", "compose.acceptance.yml")


class ComposeOperationFailure(RuntimeError):
    def __init__(self, result):
        super().__init__("Isolated Compose operation failed")
        self.returncode = result.returncode
        self.stderr = result.stderr


def advisory_only(state):
    remediation = state.get("remediation")
    return (isinstance(remediation, dict) and state.get("status") == "diagnosed"
            and remediation.get("tool") == "none"
            and remediation.get("requires_approval") is False
            and remediation.get("dry_run_passed") is False
            and state.get("approval_decision") is None)


def run(tags):
    env = os.environ.copy()
    for key in ("POSTGRES_PASSWORD", "INCIDENT_LAB_POSTGRES_PASSWORD", "FAULT_INJECTION_TOKEN",
                "MCP_READ_TOKEN", "MCP_OPERATOR_TOKEN", "MCP_APPROVAL_SECRET", "TELEMETRY_GATEWAY_TOKEN",
                "ORCHESTRATOR_READ_TOKEN", "ORCHESTRATOR_OPERATOR_TOKEN"):
        env[key] = secrets.token_urlsafe(32)
    env.update(OPENAI_API_KEY="", OPENAI_MODEL="", ORCHESTRATOR_AUTH_MODE="required",
               ORCHESTRATOR_OPERATOR_ACTOR="isolated-acceptance-operator", FAULT_LAB_RPS="3")
    project = "copilot-compose-acceptance-" + uuid.uuid4().hex
    prefix = ["docker", "compose", "--env-file", "/dev/null", "--project-name", project]
    for file in FILES:
        prefix.extend(["-f", str(ROOT / file)])

    def command(args, *, check=True, timeout=45):
        result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise ComposeOperationFailure(result)
        return result

    images = {}
    for role, ref in tags.items():
        images[role] = command(["docker", "image", "inspect", "--format", "{{.Id}}", ref]).stdout.strip()
        env["ACCEPTANCE_" + role.upper() + "_IMAGE"] = images[role]
    configuration = json.loads(command([*prefix, "config", "--format", "json"]).stdout)
    if any(service.get("ports") for service in configuration["services"].values()):
        raise ValueError("Acceptance cannot publish host ports")
    # Every service image must be cached before any allocation. Do not fetch images.
    configured_images = {}
    for name, service in configuration["services"].items():
        configured_images[name] = {
            "configured_reference": service["image"],
            "cached_image_id": command(["docker", "image", "inspect", "--format", "{{.Id}}", service["image"]]).stdout.strip(),
        }
    if command(["docker", "ps", "--all", "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}"] ).stdout.strip():
        raise ValueError("Acceptance project is already allocated")
    checks = {}
    report = {"contract": "isolated-compose-platform-acceptance-v1", "project": project,
              "started_at": datetime.now(UTC).isoformat(), "images": images, "checks": checks,
              "configured_service_images": configured_images,
              "model_calls": 0, "production_actions": 0, "kubernetes_acceptance": False,
              "source_sha256": {file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
                                for file in (*FILES, "container-images.lock.json", "fault-lab/verify_compose_platform.py")}}
    report["observability_config_sha256"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "fault-lab/observability").rglob("*")) if path.is_file()
    }
    started = time.monotonic()

    def request(service, path, *, method="GET", data=None, authenticated=False, timeout=10, extra_headers=None):
        port = 8080 if service == "orchestrator" else 8090
        headers = {}
        if authenticated:
            headers = {"Authorization": "Bearer " + env["ORCHESTRATOR_READ_TOKEN"]} if service == "orchestrator" else {"x-fault-token": env["FAULT_INJECTION_TOKEN"]}
        headers.update(extra_headers or {})
        code = ("import httpx,json; r=httpx.request(" + repr(method) + "," + repr(f"http://127.0.0.1:{port}" + path)
                + ",json=" + repr(data) + ",headers=" + repr(headers) + ",timeout=" + repr(timeout)
                + "); print(json.dumps({'status':r.status_code,'body':r.json(),'cors':r.headers.get('access-control-allow-origin')}))")
        return json.loads(command([*prefix, "exec", "-T", service, "python", "-c", code], timeout=timeout + 10).stdout)

    def stream_events(incident_id, after=0):
        # Real HTTP over the owned container network, never an in-process test client.
        path = f"http://127.0.0.1:8080/v1/incidents/{incident_id}/events?after={after}"
        code = ("import httpx,json; r=httpx.get(" + repr(path)
                + ",headers=" + repr({"Authorization": "Bearer " + env["ORCHESTRATOR_READ_TOKEN"]})
                + ",timeout=15); print(json.dumps({'status':r.status_code,'content_type':r.headers.get('content-type'),'text':r.text}))")
        result = json.loads(command([*prefix, "exec", "-T", "orchestrator", "python", "-c", code], timeout=25).stdout)
        frames = []
        for block in result["text"].replace("\r\n", "\n").split("\n\n"):
            frame = {}
            for line in block.splitlines():
                if line.startswith("id: "): frame["id"] = int(line[4:])
                elif line.startswith("event: "): frame["event"] = line[7:]
                elif line.startswith("data: "): frame["data"] = json.loads(line[6:])
            if "data" in frame: frames.append(frame)
        return result, frames

    def service_http(base, path, *, ui=False):
        # Only explicitly named services in this owned, unpublished-port network.
        if base not in {"http://prometheus:9090", "http://loki:3100", "http://tempo:3200", "http://grafana:3000", "http://prometheus-legacy-ui:9090"}:
            raise ValueError("Unexpected acceptance service")
        code = ("import httpx,json,re,hashlib; base=" + repr(base) + "; r=httpx.get(base+" + repr(path)
                + ",timeout=10,follow_redirects=True); out={'status':r.status_code,'content_type':r.headers.get('content-type'),'body':r.json() if 'application/json' in r.headers.get('content-type','') else r.text[:4096]}; "
                + ("m=re.search(r'<script[^>]+src=[\\\"\\\']([^\\\"\\\']+)',r.text); assert m and (m[1].startswith('/') or m[1].startswith('./')); p=m[1].removeprefix('.'); a=httpx.get(base+p,timeout=10); out['asset']={'path':p,'status':a.status_code,'bytes':len(a.content),'sha256':hashlib.sha256(a.content).hexdigest()}; " if ui else "")
                + "print(json.dumps(out))")
        return json.loads(command([*prefix, "exec", "-T", "orchestrator", "python", "-c", code], timeout=35).stdout)

    legacy_ui_container = None

    try:
        command([*prefix, "up", "--detach", "--no-build", "--pull", "never"], timeout=150)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                health = request("orchestrator", "/health")
                if health["status"] == 200:
                    break
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(1)
        else:
            raise TimeoutError("Packaged API readiness")
        report["runtime_modes"] = health["body"]
        versions = {}
        for service, port, path, expected in (("prometheus", 9090, "/api/v1/status/buildinfo", "3.15.0"),
                                              ("loki", 3100, "/loki/api/v1/status/buildinfo", "3.7.8"),
                                              ("tempo", 3200, "/api/status/buildinfo", "2.10.8"),
                                              ("grafana", 3000, "/api/health", "12.4.13")):
            response = service_http(f"http://{service}:{port}", path)
            body = response["body"].get("data", response["body"])
            checks[service + "_actual_version"] = response["status"] == 200 and body["version"] == expected
            versions[service] = body
        report["actual_sidecar_versions"] = versions
        current_ui = service_http("http://prometheus:9090", "/query", ui=True)
        checks["prometheus_current_ui_asset"] = current_ui["status"] == 200 and current_ui["asset"]["status"] == 200 and current_ui["asset"]["bytes"] > 1000
        legacy_ui_container = project + "-legacy-ui"
        command(["docker", "run", "--detach", "--name", legacy_ui_container, "--label", "com.docker.compose.project=" + project,
                 "--network", project + "_default", "--network-alias", "prometheus-legacy-ui", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 "--tmpfs", "/prometheus:rw,noexec,nosuid,size=64m,uid=65534,gid=65534", "--mount", "type=bind,src=" + str(ROOT / "fault-lab/observability/prometheus.yml") + ",dst=/etc/prometheus/prometheus.yml,readonly",
                 configured_images["prometheus"]["cached_image_id"], "--config.file=/etc/prometheus/prometheus.yml", "--enable-feature=old-ui", "--storage.tsdb.retention.time=1h"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                legacy_ui = service_http("http://prometheus-legacy-ui:9090", "/graph", ui=True)
                break
            except ComposeOperationFailure:
                time.sleep(1)
        else:
            raise TimeoutError("Owned legacy UI readiness")
        checks["prometheus_legacy_ui_asset"] = legacy_ui["status"] == 200 and legacy_ui["asset"]["status"] == 200 and legacy_ui["asset"]["bytes"] > 1000
        report["actual_prometheus_ui"] = {"current": current_ui["asset"], "legacy": legacy_ui["asset"]}
        command(["docker", "rm", "--force", "--volumes", legacy_ui_container])
        legacy_ui_container = None
        grafana_id = command([*prefix, "ps", "--quiet", "grafana"]).stdout.strip()
        grafana_config = json.loads(command(["docker", "inspect", grafana_id]).stdout)[0]["Config"]
        flags = {name: "false" for name in ("GF_ANALYTICS_REPORTING_ENABLED", "GF_ANALYTICS_CHECK_FOR_UPDATES", "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES")}
        flags["GF_PLUGINS_PREINSTALL_DISABLED"] = "true"
        actual_flags = dict(item.split("=", 1) for item in grafana_config["Env"] if item.split("=", 1)[0] in flags)
        checks["grafana_external_reporting_disabled"] = actual_flags == flags
        report["actual_grafana_reporting_flags"] = actual_flags
        log_conditions = {}
        for service in ("loki", "tempo", "otel-collector", "grafana"):
            container = command([*prefix, "ps", "--quiet", service]).stdout.strip()
            detail = json.loads(command(["docker", "inspect", container]).stdout)[0]
            names = sorted(item.split("=", 1)[0] for item in detail["Config"]["Env"] if item.startswith("OTEL_"))
            log_conditions[service] = {
                "image_id": detail["Image"], "otel_variable_names": names,
                "log_exporter_env_present": any(name == "OTEL_LOGS_EXPORTER" or name.startswith("OTEL_EXPORTER_OTLP_LOGS_") for name in names),
                "tls_exporter_env_present": any(name.startswith("OTEL_EXPORTER_") and ("CERTIFICATE" in name or name.endswith("_KEY")) for name in names),
            }
        report["actual_otel_log_exporter_conditions"] = log_conditions
        checks["no_env_only_tls_log_exporter_configured"] = all(not row["log_exporter_env_present"] and not row["tls_exporter_env_present"] for row in log_conditions.values())
        tempo_limit = service_http("http://tempo:3200", "/api/search?limit=262145")
        checks["tempo_oversized_query_limit_rejected"] = tempo_limit["status"] == 400
        report["tempo_query_limit_response"] = {"status": tempo_limit["status"], "body": tempo_limit["body"]}
        controls = {}
        for service in ("orchestrator", "mcp-server", "telemetry-gateway", "checkout-api", "payment-service", "ledger-service", "risk-engine", "traffic-generator"):
            container = command([*prefix, "ps", "--quiet", service]).stdout.strip()
            detail = json.loads(command(["docker", "inspect", container]).stdout)[0]
            host = detail["HostConfig"]
            controls[service] = {"user": detail["Config"]["User"], "cap_drop": host["CapDrop"], "security_opt": host["SecurityOpt"], "read_only_root": host["ReadonlyRootfs"]}
        report["actual_runtime_controls"] = controls
        checks["first_party_runtime_sandbox"] = all(row["user"].split(":")[0] not in {"", "0", "root"} and "ALL" in row["cap_drop"] and "no-new-privileges:true" in row["security_opt"] and row["read_only_root"] for row in controls.values())
        checks["postgres_checkpoint"] = health["body"]["checkpoint"] == "postgres"
        checks["redis_task_state"] = health["body"]["task_state"] == "redis"
        checks["mcp_tools"] = health["body"]["tools"] == "mcp"
        checks["no_model_reasoning"] = health["body"]["reasoning"] == "deterministic"
        checks["api_auth_required"] = health["body"]["api_auth"] == "required"
        checks["api_unauthorized_rejected"] = request("orchestrator", "/v1/incidents/missing")["status"] == 401
        checks["loopback_console_cors"] = request("orchestrator", "/v1/incidents/missing", extra_headers={"Origin": "http://127.0.0.1:3000"})["cors"] == "http://127.0.0.1:3000"
        checks["untrusted_origin_cors_denied"] = request("orchestrator", "/v1/incidents/missing", extra_headers={"Origin": "https://untrusted.invalid"})["cors"] is None
        baseline = request("payment-service", "/admin/pool", authenticated=True)["body"]
        checks["four_real_pool_connections"] = baseline["backend"] == "postgres" and baseline["maximum"] == 4 and len(set(baseline["backend_pids"])) == 4
        checks["baseline_request_healthy"] = request("payment-service", "/v1/work")["status"] == 200
        checks["fault_accepted"] = request("payment-service", "/admin/fault", method="POST", data={"mode": "db_pool_exhaustion", "duration_seconds": 180}, authenticated=True)["status"] == 200
        checks["pool_exhausted"] = request("payment-service", "/admin/pool", authenticated=True)["body"]["active"] == 4
        checks["real_timeout"] = request("payment-service", "/v1/work")["status"] == 503
        # Collect several 5-second scrapes and live traffic/log/spans before diagnosis.
        time.sleep(20)
        diagnosis = request("orchestrator", "/v1/incidents", method="POST", data={"question": "Why is payment-service latency suddenly high?"}, authenticated=True, timeout=45)
        checks["diagnosis_created"] = diagnosis["status"] == 201
        report["diagnosis"] = diagnosis["body"]
        state = diagnosis["body"]
        checks["pool_classification"] = state.get("incident_type") == "database_connection_pool_exhaustion"
        checks["hypothesis_present"] = bool(state.get("hypotheses"))
        kinds = {item["kind"] for item in state.get("evidence", [])}
        checks["metric_log_trace_evidence"] = {"metric", "log", "trace"} <= kinds
        checks["advisory_not_live_rollback"] = advisory_only(state)
        incident_id = state.get("incident_id")
        checks["incident_readback"] = request("orchestrator", "/v1/incidents/" + incident_id, authenticated=True)["body"] == state
        checks["sse_auth_required"] = request("orchestrator", f"/v1/incidents/{incident_id}/events")["status"] == 401
        checks["reader_cannot_approve"] = request("orchestrator", f"/v1/incidents/{incident_id}/approval", method="POST", data={}, authenticated=True)["status"] == 403
        response, frames = stream_events(incident_id)
        traces = [item for item in frames if item.get("event") == "trace"]
        checks["live_http_sse_complete_trace"] = (response["status"] == 200 and response["content_type"].startswith("text/event-stream")
            and [item["data"] for item in traces] == state["trace"])
        checks["live_http_sse_terminal_state"] = frames[-1].get("event") == "state" and frames[-1]["data"]["status"] == state["status"]
        cursor = state["trace"][len(state["trace"]) // 2]["sequence"]
        _, resumed_frames = stream_events(incident_id, cursor)
        resumed = [item for item in resumed_frames if item.get("event") == "trace"]
        checks["live_http_sse_resume_without_duplicate"] = [item["data"] for item in resumed] == [item for item in state["trace"] if item["sequence"] > cursor]
        report["sse_trace_frames"] = len(traces)
        report["sse_resumed_trace_frames"] = len(resumed)
        checks["fault_clear"] = request("payment-service", "/admin/fault", method="DELETE", authenticated=True)["status"] == 200
        checks["recovery_healthy"] = request("payment-service", "/v1/work")["status"] == 200
        checks["original_pg_sessions_retained"] = request("payment-service", "/admin/pool", authenticated=True)["body"]["backend_pids"] == baseline["backend_pids"]
        command([*prefix, "restart", "orchestrator"], timeout=35)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                restored = request("orchestrator", "/v1/incidents/" + incident_id, authenticated=True)
                if restored["status"] == 200:
                    break
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(1)
        else:
            raise TimeoutError("Restarted packaged API readiness")
        checks["postgres_state_after_api_restart"] = restored["body"] == state
        _, restarted_frames = stream_events(incident_id)
        checks["sse_after_api_restart"] = [item["data"] for item in restarted_frames if item.get("event") == "trace"] == state["trace"]
    except Exception as error:
        report["error_type"] = type(error).__name__
        def scrub(value):
            for key, secret in env.items():
                if secret and key.endswith(("TOKEN", "PASSWORD", "SECRET")):
                    value = value.replace(secret, "[redacted-owned-fixture]")
            return value
        if isinstance(error, ComposeOperationFailure):
            report["operation_error"] = {"returncode": error.returncode, "stderr": scrub(error.stderr)}
        try:
            report["owned_failure_logs"] = scrub(command([*prefix, "logs", "--no-color", "--tail", "80"], check=False).stdout)
        except Exception:
            report["owned_failure_logs_unavailable"] = True
    finally:
        if legacy_ui_container is not None:
            command(["docker", "rm", "--force", "--volumes", legacy_ui_container], check=False)
        command([*prefix, "down", "--volumes", "--timeout", "10"], check=False, timeout=80)
        remaining = {}
        for kind in ("container", "network", "volume"):
            args = ["docker", "ps", "--all"] if kind == "container" else ["docker", kind, "ls"]
            result = command([*args, "--filter", "label=com.docker.compose.project=" + project, "--format", "{{.ID}}" if kind == "container" else "{{.Name}}"], check=False)
            remaining[kind] = result.returncode != 0 or bool(result.stdout.strip())
        report["cleanup_complete"] = not any(remaining.values())
        report["remaining_resources"] = remaining
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = bool(checks) and all(checks.values()) and "error_type" not in report and report["cleanup_complete"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-integration", action="store_true", required=True)
    for role in ("orchestrator", "mcp", "gateway", "lab"):
        parser.add_argument("--" + role + "-image", required=True)
    args = parser.parse_args()
    report = run({role: getattr(args, role + "_image") for role in ("orchestrator", "mcp", "gateway", "lab")})
    output = ROOT / "fault-lab/results" / ("compose_platform_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8] + ".json")
    with output.open("x") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(output), "ok": report["ok"], "checks": report["checks"], "error_type": report.get("error_type"), "cleanup_complete": report["cleanup_complete"]}))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
