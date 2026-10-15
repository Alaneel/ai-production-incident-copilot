"""Opt-in full matrix or selected native fault with owned disposable Docker stores."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "fault-lab/results"
SCENARIOS = tuple(json.loads((ROOT / "fault-lab/scenarios.json").read_text()))
IMAGES = {
    "tempo": "grafana/tempo:2.10.5", "prometheus": "prom/prometheus:v3.12.0",
    "loki": "grafana/loki:3.7.0", "collector": "otel/opentelemetry-collector-contrib:0.147.0",
}
PORTS = (8191, 8192, 8193, 8194, 8081, 8003, 9090, 3100, 3200, 4318)
REQUIRED_CHECKS = {
    "no_causal_upgrade", "stopped_before_execution", "baseline_parentage_verified",
    "checkpoint_observations_retained", "live_metrics", "live_logs", "live_traces",
    "service_identification", "incident_classification", "hypothesis_present",
    "remediation_selection", "approval_boundary",
}
RECOVERY_CHECKS = {
    "fresh_process_checkpoint_matches", "fresh_process_repository_matches",
    "checkpoint_history_retained", "approval_interrupt_retained",
    "fresh_process_recovery_succeeded", "baseline_log_server_span_correlated",
}
REAL_POOL_CHECKS = {
    "real_postgres_sessions", "actual_pool_exhausted", "actual_pool_recovered",
    "real_pool_metric_in_incident", "real_pool_timeout_log_in_incident",
}


def selected_cases(scenario, *, checkpoint_postgres, real_postgres_pool):
    if scenario is not None and scenario not in SCENARIOS:
        raise ValueError("Unknown fault scenario")
    if real_postgres_pool and (scenario != "db-pool-exhaustion" or not checkpoint_postgres):
        raise ValueError("Real pool requires a single pool scenario and isolated PostgreSQL checkpoints")
    return (scenario,) if scenario is not None else SCENARIOS


def validate_database():
    sys.path.insert(0, str(ROOT / "fault-lab"))
    from verify_observed_topology import checkpoint_url
    checkpoint_url(True, True)


def docker(*arguments, check=True):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=40)
    if check and result.returncode:
        raise RuntimeError("Local Docker operation failed")
    return result


def preflight(binary):
    if not binary.is_file():
        raise ValueError("Build the Go gateway binary first")
    for port in PORTS:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    return {name: docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
            for name, image in IMAGES.items()}


class OwnedStack:
    """Never starts/stops/reuses an existing user container or network."""

    def __init__(self):
        self.network = "copilot-matrix-" + uuid.uuid4().hex
        self.network_created = False
        self.names = []

    def start(self):
        docker("network", "create", self.network)
        self.network_created = True
        configurations = (
            ("tempo", 3200, "/var/tempo:uid=10001,gid=10001,mode=0770", "tempo.yml", "/etc/tempo.yml", "-config.file=/etc/tempo.yml"),
            ("prometheus", 9090, "/prometheus:uid=65534,gid=65534,mode=0770", "prometheus-native.yml", "/etc/prometheus/prometheus.yml", "--config.file=/etc/prometheus/prometheus.yml"),
            ("loki", 3100, "/tmp/loki:uid=10001,gid=10001,mode=0770", "loki.yml", "/etc/loki/local-config.yaml", "-config.file=/etc/loki/local-config.yaml"),
            ("collector", 4318, None, "otel-collector.yml", "/etc/otelcol/config.yml", "--config=/etc/otelcol/config.yml"),
        )
        for role, port, tmpfs, config, destination, argument in configurations:
            name = self.network + "-" + role
            # Record the newly generated identity before a possibly ambiguous
            # Docker response; cleanup still targets only this owned name.
            self.names.append(name)
            command = ["run", "--detach", "--rm", "--pull=never", "--name", name,
                       "--network", self.network, "--network-alias", role,
                       "--publish", f"127.0.0.1:{port}:{port}", "--mount",
                       f"type=bind,source={ROOT / 'fault-lab/observability' / config},target={destination},readonly"]
            if tmpfs:
                command += ["--tmpfs", tmpfs]
            docker(*command, IMAGES[role], argument)

    def close(self):
        clean = True
        for name in reversed(self.names):
            docker("stop", "--time", "10", name, check=False)
            remaining = docker("container", "inspect", "--format", "{{.State.Running}}", name, check=False)
            if remaining.returncode == 0 and remaining.stdout.strip() == "true":
                clean = False
        if self.network_created:
            clean = docker("network", "rm", self.network, check=False).returncode == 0 and clean
        if not clean:
            raise RuntimeError("Owned observation stack cleanup incomplete")


def stop_probe(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    # The parent can exit before native children finish their shutdown.
    # This process group was created exclusively for this owned probe.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate(timeout=10)


def probe_case(binary, scenario, *, checkpoint_postgres=False, real_postgres_pool=False):
    selected_cases(scenario, checkpoint_postgres=checkpoint_postgres, real_postgres_pool=real_postgres_pool)
    command = [sys.executable, str(ROOT / "fault-lab/verify_observed_topology.py"),
               "--run-local-integration", "--live-telemetry", "--scenario", scenario,
               "--gateway-binary", str(binary)]
    if checkpoint_postgres:
        command += ["--checkpoint-postgres", "--verify-log-correlation"]
    if real_postgres_pool:
        command += ["--real-postgres-pool"]
    # Dedicated session makes timeout cleanup specific to this probe and its
    # native children, never the caller's shell/process group.
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=360)
    except subprocess.TimeoutExpired:
        stop_probe(process)
        return {"scenario": scenario, "ok": False, "error_type": "ProbeTimeout"}
    finally:
        if process.poll() is None:
            stop_probe(process)
    report_path, error_type = None, None
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict) and isinstance(message.get("report"), str):
            candidate = Path(message["report"]).resolve()
            if candidate.parent == RESULTS and candidate.name.startswith(f"native_scenario_{scenario}_"):
                report_path = candidate
        if isinstance(message, dict) and message.get("error_type") in {"OSError", "TimeoutError", "RuntimeError", "ValueError", "McpToolError"}:
            error_type = message["error_type"]
    if report_path is None:
        return {"scenario": scenario, "ok": False, "error_type": error_type or "MissingCaseArtifact",
                "artifact_error": "MissingCaseArtifact", "exit_code": process.returncode}
    report = json.loads(report_path.read_text())
    checks = report.get("checks", {})
    required = REQUIRED_CHECKS | RECOVERY_CHECKS if checkpoint_postgres else REQUIRED_CHECKS
    if real_postgres_pool:
        required |= REAL_POOL_CHECKS
    ok = (process.returncode == 0 and report.get("ok") is True and isinstance(checks, dict)
          and required <= checks.keys() and all(value is True for value in checks.values())
          and report.get("scenario") == scenario and isinstance(report.get("model_calls"), int)
          and not isinstance(report.get("model_calls"), bool) and report.get("model_calls") == 0
          and report.get("production_actions_executed") is False
          and (not checkpoint_postgres or (report.get("checkpoint_backend") == "postgresql"
               and report.get("fresh_process_recovery", {}).get("ok") is True))
          and (not real_postgres_pool or report.get("db_pool_backend") == "postgres"))
    return {"scenario": scenario, "ok": ok, "checks": checks, "report": str(report_path.relative_to(ROOT)),
            "artifact_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "diagnostic_seconds": report.get("diagnostic_seconds"), "duration_seconds": report.get("duration_seconds"),
            "model_calls": report.get("model_calls"), "production_actions_executed": report.get("production_actions_executed"),
            "checkpoint_backend": report.get("checkpoint_backend"),
            "db_pool_backend": report.get("db_pool_backend"),
            "fresh_process_recovery": report.get("fresh_process_recovery"),
            "status": report.get("incident", {}).get("status"), "exit_code": process.returncode}


def run(binary, *, checkpoint_postgres=False, scenario=None, real_postgres_pool=False):
    cases = selected_cases(scenario, checkpoint_postgres=checkpoint_postgres, real_postgres_pool=real_postgres_pool)
    if checkpoint_postgres:
        validate_database()  # Refuse unsafe/missing database before any Docker work.
    image_ids = preflight(binary)
    sources = [Path(__file__), ROOT / "fault-lab/verify_observed_topology.py", ROOT / "fault-lab/scenarios.json",
               ROOT / "fault-lab/app/incident_lab/app.py", ROOT / "services/orchestrator/incident_copilot/langgraph_workflow.py"]
    sources += [ROOT / "fault-lab/restore_native_checkpoint.py"]
    sources += sorted((ROOT / "fault-lab/app/incident_lab").glob("*.py"))
    sources += sorted((ROOT / "fault-lab/observability").glob("*.yml"))
    sources += sorted((ROOT / "services/orchestrator/incident_copilot").glob("*.py"))
    sources += sorted((ROOT / "services/mcp-server/incident_mcp").glob("*.py"))
    sources += [ROOT / "services/mcp-server/incident_mcp/knowledge_corpus.json"]
    report = {"ok": False, "run_at": datetime.now(UTC).isoformat(), "scenarios": [], "image_ids": image_ids,
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
              "gateway_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "model_calls": None, "production_actions_executed": None,
              "checkpoint_backend": "postgresql" if checkpoint_postgres else "memory",
              "selection_mode": "single_case" if scenario is not None else "full_matrix",
              "selected_scenarios": list(cases), "full_matrix_completed": False,
              "db_pool_backend": "postgres" if real_postgres_pool else "simulation",
              "required_checks": sorted((REQUIRED_CHECKS | RECOVERY_CHECKS if checkpoint_postgres else REQUIRED_CHECKS)
                                        | (REAL_POOL_CHECKS if real_postgres_pool else set())),
              "limitations": [("Deterministic workflow, PostgreSQL fresh-process inspection; no model, Redis, approval/resume or live API crash recovery"
                               if checkpoint_postgres else "Deterministic workflow, in-memory checkpoint; no model or PostgreSQL/Redis recovery"),
                              ("Four real application-pool leases, not server-wide PostgreSQL max_connections exhaustion; catalog/deployments/knowledge remain fixtures"
                               if real_postgres_pool else "Bounded simulated faults, fixture catalog/deployments/knowledge; no real database/Kafka exhaustion"),
                              "Sampled call parentage and structural citations do not prove semantic faithfulness or production causality"]}
    prefix = "native_scenario_single_" if scenario is not None else "native_scenario_matrix_"
    target = RESULTS / (prefix + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8] + ".json")
    with target.open("x") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    started = time.monotonic()
    for scenario in cases:
        stack = OwnedStack()
        result = {"scenario": scenario, "ok": False}
        interrupted = False
        print(json.dumps({"event": "case_started", "scenario": scenario}), flush=True)
        try:
            stack.start()
            options = {"checkpoint_postgres": True} if checkpoint_postgres else {}
            if real_postgres_pool:
                options["real_postgres_pool"] = True
            result = probe_case(binary, scenario, **options)
        except KeyboardInterrupt:
            result["error_type"] = "ProbeInterrupted"
            interrupted = True
        except Exception as error:
            result["error_type"] = type(error).__name__  # never echo provider text
        finally:
            try:
                stack.close()
                result["cleanup_complete"] = True
            except Exception as error:
                result.update(ok=False, cleanup_complete=False, cleanup_error_type=type(error).__name__)
        report["scenarios"].append(result)
        calls = [row.get("model_calls") for row in report["scenarios"]]
        actions = [row.get("production_actions_executed") for row in report["scenarios"]]
        report["model_calls"] = sum(calls) if all(isinstance(value, int) and not isinstance(value, bool) for value in calls) else None
        report["production_actions_executed"] = any(actions) if all(isinstance(value, bool) for value in actions) else None
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["ok"] = len(report["scenarios"]) == len(cases) and all(row["ok"] for row in report["scenarios"])
        report["full_matrix_completed"] = report["ok"] and report["selection_mode"] == "full_matrix"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"event": "case_finished", **result}), flush=True)
        if interrupted or not result["cleanup_complete"]:
            break  # No further experiment while owned resources may remain.
    print(json.dumps({"ok": report["ok"], "cases": len(report["scenarios"]), "report": str(target)}), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-integration", action="store_true")
    parser.add_argument("--gateway-binary", type=Path, required=True)
    parser.add_argument("--checkpoint-postgres", action="store_true", help="Require dedicated local test PostgreSQL and fresh-process recovery for every case.")
    parser.add_argument("--scenario", choices=SCENARIOS, help="Run only this scenario with fresh owned stores; omission runs all eight.")
    parser.add_argument("--real-postgres-pool", action="store_true", help="Require --scenario db-pool-exhaustion and --checkpoint-postgres; reserve four real local test database leases.")
    args = parser.parse_args()
    if not args.run_local_integration:
        parser.error("--run-local-integration is required before creating owned Docker stores or injecting faults")
    try:
        raise SystemExit(run(args.gateway_binary.resolve(), checkpoint_postgres=args.checkpoint_postgres,
                             scenario=args.scenario, real_postgres_pool=args.real_postgres_pool))
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
