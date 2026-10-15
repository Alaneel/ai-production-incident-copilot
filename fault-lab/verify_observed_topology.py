"""Opt-in localhost telemetry/fault probes, no model requests or approvals."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import time
import tempfile
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/orchestrator"))
sys.path.insert(0, str(ROOT / "fault-lab/app"))

SCENARIOS = json.loads((ROOT / "fault-lab/scenarios.json").read_text())
EXPECTED = {
    "db-pool-exhaustion": ("database_connection_pool_exhaustion", "rollback_release"),
    "downstream-timeout": ("downstream_timeout", "none"),
    "cpu-spike": ("cpu_saturation", "restart_deployment"),
    "memory-leak": ("memory_leak", "rollback_release"),
    "deployment-regression": ("deployment_regression", "rollback_release"),
    "kafka-consumer-lag": ("kafka_consumer_lag", "restart_deployment"),
    "retry-storm": ("retry_storm", "none"),
    "cache-penetration": ("cache_penetration", "none"),
}
SERVICE_PORTS = {"checkout-api": 8191, "payment-service": 8192, "ledger-service": 8193, "risk-engine": 8194}
CHAIN = (("checkout-api", "payment-service"), ("payment-service", "ledger-service"), ("ledger-service", "risk-engine"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.mcp_client import McpTelemetryTools  # noqa: E402
from incident_copilot.models import IncidentQuestion  # noqa: E402
from incident_copilot.models import IncidentState  # noqa: E402
from incident_copilot.models import TraceEvent  # noqa: E402
from incident_copilot.tools import DemoTelemetryTools  # noqa: E402


async def ready(client, url, process=None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("Local verification child stopped before readiness")
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("Local verification service was not ready")


def select_fault(*, live_telemetry, fault_cpu_spike, scenario):
    if scenario is not None:
        if scenario not in SCENARIOS or scenario not in EXPECTED:
            raise ValueError("Unknown fault scenario")
        if not live_telemetry or fault_cpu_spike:
            raise ValueError("Scenario requires live telemetry and excludes the legacy CPU flag")
        return SCENARIOS[scenario]
    if fault_cpu_spike and not live_telemetry:
        raise ValueError("CPU probe requires live telemetry")
    return {"service": "payment-service", "mode": "cpu_spike"} if fault_cpu_spike else None


async def exercise(client, *, duration_seconds, counts=None):
    """Keep emitting telemetry even when an injected fault returns HTTP errors."""
    counts = {} if counts is None else counts
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        response = await client.post("http://127.0.0.1:8191/v1/work")
        if response.status_code not in (200, 500, 502, 503, 504):
            raise RuntimeError("Unexpected workload HTTP status")
        key = str(response.status_code)
        counts[key] = counts.get(key, 0) + 1
        await asyncio.sleep(1)
    return counts


async def diagnose_with_traffic(workflow, request, client, counts):
    traffic = asyncio.create_task(exercise(client, duration_seconds=90, counts=counts))
    try:
        await asyncio.sleep(0)  # Start real traffic before querying current signals.
        if traffic.done():
            await traffic  # Refuse to diagnose after an immediate workload failure.
        return await workflow.start(request)
    finally:
        traffic.cancel()
        try:
            await traffic
        except asyncio.CancelledError:
            pass


def diagnosis_checks(incident, *, scenario, service):
    expected_type, expected_action = EXPECTED[scenario]
    # This live MCP deployment endpoint remains synthetic. Fault recognition
    # does not make its fixed revision safe as a remote rollback target.
    unverified_rollback = expected_action == "rollback_release" and not any(
        item.kind.value == "deployment" and item.attributes.get("data_origin") == "kubernetes_api"
        and item.attributes.get("namespace") == incident.environment for item in incident.evidence)
    if unverified_rollback:
        expected_action = "none"
    remediation = incident.remediation
    checks = {
        "service_identification": bool(incident.services) and incident.services[0] == service,
        "incident_classification": incident.incident_type == expected_type,
        "hypothesis_present": bool(incident.hypotheses),
        "remediation_selection": remediation is not None and remediation.tool == expected_action,
        "approval_boundary": (remediation is not None and (
            incident.status.value == "diagnosed" and not remediation.requires_approval
            if expected_action == "none" else
            incident.status.value == "waiting_approval" and remediation.requires_approval
        )),
    }
    if unverified_rollback:
        checks["unverified_rollback_target_blocked"] = any(event.event == "rollback_target_unverified" for event in incident.trace) and not any(
            event.payload.get("tool") == "rollback_release" for event in incident.trace)
    return checks


def checkpoint_url(enabled, live):
    if not enabled:
        return None
    raw = os.getenv("INCIDENT_TEST_DATABASE_URL", "")
    parsed = urlsplit(raw)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    hosts = parameters.get("host", [])
    loopback = {"localhost", "127.0.0.1", "::1"}
    query_local = len(hosts) == 1 and (hosts[0] in loopback or hosts[0].startswith(("/private/tmp/", "/tmp/")))
    local = (parsed.hostname in loopback or parsed.hostname is None and query_local) and (not hosts or query_local)
    local = local and not parameters.keys() & {"service", "servicefile", "hostaddr"}
    if not live or parsed.scheme not in {"postgres", "postgresql"} or not local:
        raise ValueError("PostgreSQL probe requires live telemetry and an explicit local INCIDENT_TEST_DATABASE_URL")
    return raw


async def correlate_baseline_logs(adapter, pairs, trace_id, request_id):
    """Bounded visibility wait; retain exact identities and failed attempts."""
    matched = {}
    history = []
    deadline = time.monotonic() + 15
    for attempt in range(10):
        counts, errors = {}, {}
        for target in sorted({target for target, _ in pairs}):
            try:
                logs = await adapter.search_logs(target, "request completed")
            except Exception as error:
                errors[target] = type(error).__name__  # no provider/credential text
                continue
            events = [event for item in logs for event in item.attributes.get("events", [])]
            counts[target] = len(events)
            for event in events:
                identity = (target, event.get("span_id"))
                if (event.get("trace_id") == trace_id and event.get("request_id") == request_id
                        and identity in pairs):
                    matched[identity] = {"service": target, **event}
        complete = bool(pairs) and pairs <= matched.keys()
        history.append({"attempt": attempt + 1, "event_counts": counts,
                        "matched_pairs": len(matched), "errors": errors})
        if complete or not pairs or time.monotonic() >= deadline:
            break
        if attempt < 9:
            await asyncio.sleep(1)
    return list(matched.values()), {"complete": complete, "attempts": history,
        "required_pairs": len(pairs), "matched_pairs": len(matched),
        "missing_pairs": [{"service": service, "server_span_id": span} for service, span in sorted(pairs - matched.keys())]}


def real_pool_parameters(enabled, *, scenario, live, database_url):
    if not enabled:
        return None
    if scenario != "db-pool-exhaustion" or not live or not database_url:
        raise ValueError("Real pool probe requires the pool scenario, live telemetry and isolated PostgreSQL checkpoints")
    from incident_lab.postgres_pool import local_parameters
    return local_parameters(database_url)


async def inspect_real_pool(client, service, token, parameters):
    import psycopg
    response = await client.get(f"http://127.0.0.1:{SERVICE_PORTS[service]}/admin/pool",
                                headers={"X-Fault-Token": token})
    response.raise_for_status()
    snapshot = response.json()
    pids = snapshot.get("backend_pids", [])
    if snapshot.get("backend") != "postgres" or len(set(pids)) != 4 or any(not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in pids):
        raise ValueError("Owned service did not expose four real PostgreSQL sessions")
    # Inspect exact owned connection identities, never unrelated server sessions.
    async with await psycopg.AsyncConnection.connect(autocommit=True, **parameters) as probe:
        async with probe.cursor() as cursor:
            await cursor.execute("SELECT pid, state, application_name FROM pg_stat_activity WHERE pid = ANY(%s)", (pids,))
            rows = await cursor.fetchall()
    snapshot["postgres_sessions"] = [{"pid": pid, "state": state, "application_name": name}
                                      for pid, state, name in rows]
    return snapshot


class ApiIncidentWorkflow:
    """Only create/read endpoints; this verifier cannot submit approvals."""

    def __init__(self, client, token):
        self.client = client
        self.headers = {"Authorization": "Bearer " + token}

    async def start(self, question):
        response = await self.client.post("http://127.0.0.1:8004/v1/incidents",
            json=question.model_dump(mode="json"), headers=self.headers, timeout=60)
        response.raise_for_status()
        self.last_delivery = {"http_status": response.status_code,
            "mode": response.headers.get("X-Incident-Event-Delivery"),
            "reason": response.headers.get("X-Incident-Event-Delivery-Reason")}
        return IncidentState.model_validate(response.json())

    async def get(self, incident_id):
        response = await self.client.get(f"http://127.0.0.1:8004/v1/incidents/{incident_id}", headers=self.headers)
        response.raise_for_status()
        return IncidentState.model_validate(response.json())

    async def history(self, incident_id):
        response = await self.client.get(f"http://127.0.0.1:8004/v1/incidents/{incident_id}/history", headers=self.headers)
        response.raise_for_status()
        return [IncidentState.model_validate(row) for row in response.json()]

    async def events(self, incident_id, after=0, include_sources=False):
        response = await self.client.get(f"http://127.0.0.1:8004/v1/incidents/{incident_id}/events",
            params={"after": after}, headers=self.headers, timeout=10)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            raise ValueError("Expected SSE stream")
        return parse_trace_stream(response.text, include_sources=include_sources)


def parse_trace_stream(payload, *, include_sources=False):
    if len(payload.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Stream exceeds inspection budget")
    events, states, sources = [], [], []
    for block in payload.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":" ) or not line:
                continue
            key, separator, value = line.partition(":")
            if separator:
                if key in fields:
                    raise ValueError("Duplicate SSE inspection field")
                fields[key] = value.lstrip(" ")
        if fields.get("event") == "trace":
            event = TraceEvent.model_validate_json(fields["data"])
            if int(fields.get("id", "-1")) != event.sequence or (events and event.sequence <= events[-1].sequence):
                raise ValueError("SSE identity or sequence mismatch")
            events.append(event)
        elif fields.get("event") == "state":
            states.append(json.loads(fields["data"]))
        elif fields.get("event") == "replay_source":
            sources.append(json.loads(fields["data"]))
    return (events, states, sources) if include_sources else (events, states)


async def ready_redis(url, process):
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
    redis = Redis.from_url(url, decode_responses=True)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned Redis stopped before readiness")
            try:
                if await redis.ping():
                    return
            except RedisError:
                pass
            await asyncio.sleep(0.25)
        raise TimeoutError("Owned Redis readiness timeout")
    finally:
        await redis.aclose()


async def inspect_degraded_publish(workflow, question, database_url, expected_status, phase):
    from incident_copilot.store import PostgresIncidentRepository
    started = time.monotonic()
    incident = await workflow.start(question)
    elapsed = time.monotonic() - started
    delivery = workflow.last_delivery
    checkpoint = await workflow.get(incident.incident_id)
    repository = await PostgresIncidentRepository.connect(database_url)
    try:
        stored = await repository.get(incident.incident_id)
    finally:
        await repository.close()
    reasons = {"cache-timeout"} if phase == "stall" else {"cache-unavailable", "cache-timeout"}
    prefix = "cache_publish_" + phase
    return {"incident": incident.model_dump(mode="json"), "delivery": delivery,
        "diagnosis_seconds": round(elapsed, 3), "checks": {
            prefix + "_response": delivery["http_status"] == 201 and delivery["mode"] == "checkpoint-fallback" and delivery["reason"] in reasons,
            prefix + "_repository": stored is not None and stored.model_dump() == incident.model_dump(),
            prefix + "_checkpoint": checkpoint.model_dump() == incident.model_dump(),
            prefix + "_status": incident.status == expected_status and incident.approval_decision is None,
            prefix + "_no_execution": not any(event.step.value in ("prepare_execution", "execute_or_simulate") for event in incident.trace),
        }}


async def run(gateway_binary: Path, *, live_telemetry: bool = False, fault_cpu_spike: bool = False, scenario: str | None = None, verify_log_correlation: bool = False, checkpoint_postgres: bool = False, verify_api_crash: bool = False, verify_redis_stream: bool = False, verify_redis_loss: bool = False, verify_redis_stall: bool = False, verify_cache_publish: bool = False, real_postgres_pool: bool = False):
    if verify_cache_publish and not (verify_redis_stall and verify_redis_loss):
        raise ValueError("Cache publish probe requires owned Redis stall and loss modes")
    if verify_redis_stall and not verify_redis_stream:
        raise ValueError("Redis stall probe requires owned Redis stream mode")
    if verify_redis_loss and not verify_redis_stream:
        raise ValueError("Redis loss probe requires owned Redis stream mode")
    if verify_redis_stream and not verify_api_crash:
        raise ValueError("Redis stream probe requires API crash mode")
    redis_binary = shutil.which("redis-server") if verify_redis_stream else None
    if verify_redis_stream and redis_binary is None:
        raise ValueError("Redis stream probe requires a local redis-server binary")
    database_url = checkpoint_url(checkpoint_postgres, live_telemetry)
    pool_parameters = real_pool_parameters(real_postgres_pool, scenario=scenario, live=live_telemetry, database_url=database_url)
    pool_inspections = {}
    if verify_api_crash and (not checkpoint_postgres or not live_telemetry or scenario is None):
        raise ValueError("API crash probe requires PostgreSQL, live telemetry and a canonical scenario")
    if verify_log_correlation and not live_telemetry:
        raise ValueError("Log correlation verification requires live telemetry")
    fault = select_fault(live_telemetry=live_telemetry, fault_cpu_spike=fault_cpu_spike, scenario=scenario)
    service = fault["service"] if fault else "payment-service"
    ports = (8191, 8192, 8193, 8081, 8003) + ((8194,) if scenario else ())
    if verify_api_crash:
        ports += (8004,)
    for port in ports:
        with socket.socket() as probe:
            # Match uvicorn's restart semantics: TIME_WAIT is not a listener.
            # SO_REUSEPORT is deliberately not enabled; active binds still fail.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    if not gateway_binary.is_file():
        raise ValueError("Build the Go gateway binary before running verification")
    children = []
    redis_directory = None
    gateway_token, read_token, operator_token, approval_secret = (secrets.token_urlsafe(32) for _ in range(4))
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("OPENAI_API_KEY", "OPENAI_MODEL", "DATABASE_URL", "REDIS_URL", "INCIDENT_TEST_DATABASE_URL", "INCIDENT_LAB_POSTGRES_URL")}
    environment["INCIDENT_LAB_DB_POOL_BACKEND"] = "simulation"
    environment["KUBERNETES_DEPLOYMENT_EVENTS_ENABLED"] = "false"
    environment.update({"PYTHONPATH": str(ROOT / "fault-lab/app") + ":" + str(ROOT / "services/mcp-server"),
                        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
                        "LOKI_PUSH_URL": "http://127.0.0.1:3100/loki/api/v1/push" if live_telemetry else "",
                        "KUBERNETES_REMEDIATION_ENABLED": "false", "KUBERNETES_DEPLOYMENT_DISCOVERY_ENABLED": "false", "MCP_READ_TOKEN": read_token,
                        "MCP_OPERATOR_TOKEN": operator_token, "MCP_APPROVAL_SECRET": approval_secret,
                        "FAULT_INJECTION_TOKEN": secrets.token_urlsafe(32)})

    def launch(command, extra):
        process = subprocess.Popen(command, cwd=ROOT, env={**environment, **extra},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(process)
        return process

    started = time.monotonic()
    try:
        async with AsyncExitStack() as persistence, httpx.AsyncClient(timeout=5, trust_env=False) as client:
            saver = InMemorySaver()
            if database_url and not verify_api_crash:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                saver = await persistence.enter_async_context(AsyncPostgresSaver.from_conn_string(database_url))
                await saver.setup()
            await ready(client, "http://127.0.0.1:3200/ready")
            if live_telemetry:
                await ready(client, "http://127.0.0.1:9090/-/ready")
                await ready(client, "http://127.0.0.1:3100/ready")
            services = (
                ("ledger-service", 8193, ""), ("payment-service", 8192, "http://127.0.0.1:8193"),
                ("checkout-api", 8191, "http://127.0.0.1:8192"),
            )
            if scenario:
                services = (("risk-engine", 8194, ""), ("ledger-service", 8193, "http://127.0.0.1:8194")) + services[1:]
            for child_service, port, downstream in services:
                pool_config = {"INCIDENT_LAB_DB_POOL_BACKEND": "postgres", "INCIDENT_LAB_POSTGRES_URL": database_url} if real_postgres_pool and child_service == service else {}
                process = launch([sys.executable, "-m", "uvicorn", "incident_lab.app:app", "--host", "127.0.0.1", "--port", str(port)],
                                 {"SERVICE_NAME": child_service, "DOWNSTREAM_URL": downstream, **pool_config})
                await ready(client, f"http://127.0.0.1:{port}/health", process)
            if real_postgres_pool:
                pool_inspections["baseline"] = await inspect_real_pool(client, service, environment["FAULT_INJECTION_TOKEN"], pool_parameters)
            process = launch([str(gateway_binary)], {
                "TELEMETRY_GATEWAY_TOKEN": gateway_token, "TELEMETRY_GATEWAY_ADDRESS": "127.0.0.1:8081",
                "PROMETHEUS_URL": "http://127.0.0.1:9090", "LOKI_URL": "http://127.0.0.1:3100", "TEMPO_URL": "http://127.0.0.1:3200",
            })
            await ready(client, "http://127.0.0.1:8081/health", process)
            process = launch([sys.executable, "-m", "uvicorn", "incident_mcp.server:app", "--host", "127.0.0.1", "--port", "8003"],
                             {"TELEMETRY_GATEWAY_URL": "http://127.0.0.1:8081", "TELEMETRY_GATEWAY_TOKEN": gateway_token})
            await ready(client, "http://127.0.0.1:8003/health", process)
            # Canonical scenarios always exercise Tempo's leading-zero search
            # encoding while keeping 120 random ID bits and a nonzero trace ID.
            trace_id = "01" + secrets.token_hex(15) if scenario else secrets.token_hex(16)
            external_parent = secrets.token_hex(8)
            request_id = "probe-" + secrets.token_hex(8)
            response = await client.post("http://127.0.0.1:8191/v1/work", headers={"traceparent": f"00-{trace_id}-{external_parent}-01", "x-request-id": request_id})
            response.raise_for_status()
            adapter = McpTelemetryTools(url="http://127.0.0.1:8003/mcp", read_token=read_token, operator_token=operator_token, client=client)
            tools = adapter if live_telemetry else DemoTelemetryTools()
            if not live_telemetry:
                tools.get_service_topology = adapter.get_service_topology
            # Prepare the graph before fault traffic; setup/cold-start latency
            # must not silently turn a growing workload into a stale plateau.
            api_process, api_config = None, None
            if verify_api_crash:
                api_token, api_operator = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                api_config = {"PYTHONPATH": environment["PYTHONPATH"] + ":" + str(ROOT / "services/orchestrator"),
                    "DATABASE_URL": database_url, "MCP_SERVER_URL": "http://127.0.0.1:8003/mcp",
                    "ORCHESTRATOR_AUTH_MODE": "required", "ORCHESTRATOR_READ_TOKEN": api_token,
                    "ORCHESTRATOR_OPERATOR_TOKEN": api_operator, "ORCHESTRATOR_OPERATOR_ACTOR": "native-verification-operator"}
                if verify_redis_stream:
                    redis_directory = tempfile.TemporaryDirectory(prefix="copilot-redis-", dir="/private/tmp")
                    redis_socket = str(Path(redis_directory.name) / "redis.sock")
                    redis_process = launch([redis_binary, "--port", "0", "--unixsocket", redis_socket,
                        "--unixsocketperm", "700", "--save", "", "--appendonly", "no", "--dir", redis_directory.name], {})
                    redis_url = "unix://" + redis_socket
                    await ready_redis(redis_url, redis_process)
                    api_config["REDIS_URL"] = redis_url
                api_command = [sys.executable, "-m", "uvicorn", "incident_copilot.api:app", "--host", "127.0.0.1", "--port", "8004"]
                api_process = launch(api_command, api_config)
                await ready(client, "http://127.0.0.1:8004/health", api_process)
                workflow = ApiIncidentWorkflow(client, api_token)
            else:
                workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
                                                     approval_signer=ApprovalReceiptSigner(approval_secret))
            topology = None
            expected = {edge for edge in CHAIN if service in edge and (scenario or "risk-engine" not in edge)}
            deadline, attempts = time.monotonic() + 40, 0
            while time.monotonic() < deadline:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("Local verification child stopped during trace retrieval")
                attempts += 1
                topology = await adapter.get_service_topology(service)
                matching = [call for call in topology.observed.calls if call.trace_id == trace_id]
                if expected <= {(call.source, call.target) for call in matching}:
                    break
                await asyncio.sleep(1)
            else:
                failure = {
                    "ok": False, "stage": "baseline", "checks": {"baseline_parentage_verified": False},
                    "run_at": datetime.now(UTC).isoformat(), "duration_seconds": round(time.monotonic() - started, 3),
                    "scenario": scenario, "primary_service": service, "trace_id": trace_id,
                    "retrieval_attempts": attempts, "topology": topology.model_dump(mode="json") if topology else None,
                    "fault_injected": None, "model_calls": 0, "production_actions_executed": False,
                }
                prefix = f"native_scenario_{scenario}_" if scenario else "observed_topology_live_"
                target = ROOT / "fault-lab/results" / (prefix + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_baseline_failed.json")
                target.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"ok": False, "stage": "baseline", "report": str(target)}))
                raise RuntimeError("Expected live trace parentage did not reach MCP before the deadline")
            correlated_logs = []
            log_lookup = None
            if verify_log_correlation:
                pairs = {(call.target, call.server_span_id) for call in topology.observed.calls if call.trace_id == trace_id}
                correlated_logs, log_lookup = await correlate_baseline_logs(adapter, pairs, trace_id, request_id)
                counters = {}
                for target in sorted({target for target, _ in pairs}):
                    response = await client.get(f"http://127.0.0.1:{SERVICE_PORTS[target]}/metrics")
                    response.raise_for_status()
                    counters[target] = [line for line in response.text.splitlines()
                                        if line.startswith(("incident_log_dropped_total{", "incident_log_delivered_total{", "incident_log_retry_total{"))]
                log_lookup["shipper_counters"] = counters
            workload, injected_at = {}, None
            if live_telemetry:
                # Obtain multiple actual scrapes and shipped request logs before diagnosis.
                for _ in range(12):
                    (await client.post("http://127.0.0.1:8191/v1/work")).raise_for_status()
                    await asyncio.sleep(1)
                if fault:
                    injected_at = datetime.now(UTC).isoformat()
                    response = await client.post(f"http://127.0.0.1:{SERVICE_PORTS[service]}/admin/fault",
                        headers={"X-Fault-Token": environment["FAULT_INJECTION_TOKEN"]},
                        json={"mode": fault["mode"], "intensity": 1.0 if scenario else 0.95,
                              "duration_seconds": 120 if scenario else 45})
                    response.raise_for_status()
                    if real_postgres_pool:
                        pool_inspections["exhausted"] = await inspect_real_pool(client, service, environment["FAULT_INJECTION_TOKEN"], pool_parameters)
                        direct = await client.post(f"http://127.0.0.1:{SERVICE_PORTS[service]}/v1/work")
                        pool_inspections["exhausted"]["direct_work_status"] = direct.status_code
                    if scenario:
                        workload = await exercise(client, duration_seconds=30)
                    else:
                        for _ in range(12):
                            (await client.post("http://127.0.0.1:8191/v1/work")).raise_for_status()
                            await asyncio.sleep(1)
            diagnostic_started = time.monotonic()
            question = IncidentQuestion(question=f"Why is {service} slow today?")
            incident = (await diagnose_with_traffic(workflow, question, client, workload)
                        if scenario else await workflow.start(question))
            diagnostic_seconds = round(time.monotonic() - diagnostic_started, 3)
            restored = await workflow.get(incident.incident_id)
            api_recovery = None
            redis_recovery = None
            redis_loss = None
            redis_stall = None
            cache_publish = {}
            if verify_api_crash:
                if verify_redis_stream:
                    from redis.asyncio import Redis
                    redis = Redis.from_url(redis_url, decode_responses=True)
                    try:
                        prior_task = await redis.hgetall(f"incident:{incident.incident_id}:task")
                    finally:
                        await redis.aclose()
                original_pid = api_process.pid
                api_process.kill()  # Only the child created by launch above.
                api_process.wait(timeout=10)
                killed_code = api_process.returncode
                api_process = launch(api_command, api_config)
                await ready(client, "http://127.0.0.1:8004/health", api_process)
                restored = await workflow.get(incident.incident_id)
                api_history = await workflow.history(incident.incident_id)
                health = (await client.get("http://127.0.0.1:8004/health")).json()
                api_recovery = {"writer_pid": original_pid, "recovered_pid": api_process.pid,
                    "writer_exit_code": killed_code, "history_checkpoint_count": len(api_history),
                    "runtime_modes": health, "checks": {
                        "api_writer_killed": killed_code == -9,
                        "api_new_process": original_pid != api_process.pid,
                        "api_complete_state_recovered": restored.model_dump() == incident.model_dump(),
                        "api_history_recovered": len(api_history) >= 2 and any(row.model_dump() == incident.model_dump() for row in api_history),
                        "api_recovery_runtime_modes": all(health.get(key) == value for key, value in {
                            "checkpoint": "postgres", "tools": "mcp", "reasoning": "deterministic", "api_auth": "required",
                            "task_state": "redis" if verify_redis_stream else "memory"}.items()),
                    }}
                if verify_redis_stream:
                    redis = Redis.from_url(redis_url, decode_responses=True)
                    try:
                        recovered_task = await redis.hgetall(f"incident:{incident.incident_id}:task")
                        ttl = await redis.ttl(f"incident:{incident.incident_id}:events")
                    finally:
                        await redis.aclose()
                    stream, states = await workflow.events(incident.incident_id)
                    cursor = incident.trace[len(incident.trace) // 2].sequence
                    resumed, resumed_states = await workflow.events(incident.incident_id, after=cursor)
                    expected_task = {"status": incident.status.value, "current_step": incident.current_step.value,
                        "trace_id": incident.trace_id, "trace_length": str(len(incident.trace)), "updated_at": incident.updated_at.isoformat()}
                    expected_terminal = [{"status": incident.status.value, "current_step": incident.current_step.value}]
                    redis_recovery = {"event_ttl_seconds": ttl, "full_event_count": len(stream),
                        "cursor": cursor, "cursor_event_count": len(resumed), "checks": {
                            "redis_task_state_recovered": recovered_task == prior_task == expected_task,
                            "redis_full_stream_recovered": stream == incident.trace and ttl > 0,
                            "redis_cursor_replay_exact": resumed == [event for event in incident.trace if event.sequence > cursor],
                            "redis_stream_terminal_state_correct": states == resumed_states == expected_terminal,
                        }}
                    if verify_redis_stall:
                        redis_process.send_signal(signal.SIGSTOP)
                        try:
                            read_started = time.monotonic()
                            stalled, stalled_states, stalled_sources = await workflow.events(incident.incident_id, include_sources=True)
                            full_seconds = time.monotonic() - read_started
                            read_started = time.monotonic()
                            stalled_cursor, stalled_cursor_states, stalled_cursor_sources = await workflow.events(incident.incident_id, after=cursor, include_sources=True)
                            cursor_seconds = time.monotonic() - read_started
                            if verify_cache_publish:
                                cache_publish["stall"] = await inspect_degraded_publish(workflow, question, database_url, incident.status, "stall")
                            still_alive = redis_process.poll() is None
                        finally:
                            if redis_process.poll() is None:
                                redis_process.send_signal(signal.SIGCONT)
                        healthy, healthy_states, healthy_sources = await workflow.events(incident.incident_id, include_sources=True)
                        timeout_source = [{"source": "workflow_checkpoint", "checkpoint_backend": "postgres",
                            "reason": "task_store_timeout", "read_only": True}]
                        redis_stall = {"full_replay_seconds": round(full_seconds, 3),
                            "cursor_replay_seconds": round(cursor_seconds, 3), "sources": stalled_sources,
                            "full_event_count": len(stalled), "cursor_event_count": len(stalled_cursor), "checks": {
                                "owned_redis_stalled_not_dead": still_alive,
                                "redis_stall_full_replay_bounded": stalled == incident.trace and stalled_states == expected_terminal and 1.5 <= full_seconds < 6,
                                "redis_stall_cursor_replay_bounded": stalled_cursor == [event for event in incident.trace if event.sequence > cursor] and stalled_cursor_states == expected_terminal and 1.5 <= cursor_seconds < 6,
                                "redis_stall_timeout_provenance": stalled_sources == stalled_cursor_sources == timeout_source,
                                "redis_stall_normal_delivery_resumed": healthy == incident.trace and healthy_states == expected_terminal and not healthy_sources,
                            }}
                    if verify_redis_loss:
                        redis_process.kill()  # Only the Redis child launched in this run.
                        redis_process.wait(timeout=10)
                        if verify_cache_publish:
                            cache_publish["loss"] = await inspect_degraded_publish(workflow, question, database_url, incident.status, "loss")
                        lost_events, lost_states, sources = await workflow.events(incident.incident_id, include_sources=True)
                        cursor_events, cursor_states, cursor_sources = await workflow.events(incident.incident_id, after=cursor, include_sources=True)
                        expected_source = [{"source": "workflow_checkpoint", "checkpoint_backend": "postgres",
                            "reason": "task_store_unavailable", "read_only": True}]
                        redis_loss = {"exit_code": redis_process.returncode, "full_event_count": len(lost_events),
                            "cursor_event_count": len(cursor_events), "sources": sources, "checks": {
                                "owned_redis_killed": redis_process.returncode == -9,
                                "redis_loss_full_trace_replayed": lost_events == incident.trace and lost_states == expected_terminal,
                                "redis_loss_cursor_replay_exact": cursor_events == [event for event in incident.trace if event.sequence > cursor] and cursor_states == expected_terminal,
                                "redis_loss_read_only_provenance": sources == cursor_sources == expected_source,
                            }}
            recovery = None
            if database_url:
                if not verify_api_crash:
                    from incident_copilot.store import PostgresIncidentRepository
                    repository = await PostgresIncidentRepository.connect(database_url)
                    try:
                        await repository.save(incident)
                    finally:
                        await repository.close()
                await persistence.aclose()  # Close original checkpoint connection before recovery.
                result = subprocess.run([sys.executable, str(ROOT / "fault-lab/restore_native_checkpoint.py"), "--inspect-existing-test-checkpoint"],
                    input=incident.model_dump_json(), capture_output=True, text=True, timeout=30,
                    env={**environment, "INCIDENT_TEST_DATABASE_URL": database_url})
                recovery = json.loads(result.stdout)
                if recovery.get("incident"):
                    restored = IncidentState.model_validate(recovery.pop("incident"))
            retained = [call for call in restored.service_topology.observed.calls if live_telemetry or call.trace_id == trace_id]
            checks = {
                "no_causal_upgrade": not any(impact["causal_direction_verified"] for impact in incident.service_impacts),
                "stopped_before_execution": (live_telemetry or incident.status.value == "waiting_approval") and not any(event.step.value in ("prepare_execution", "execute_or_simulate") for event in incident.trace),
            }
            if recovery is not None:
                checks.update(recovery.get("checks", {}))
                checks["fresh_process_recovery_succeeded"] = recovery.get("ok") is True
            if api_recovery is not None:
                checks.update(api_recovery["checks"])
            if redis_recovery is not None:
                checks.update(redis_recovery["checks"])
            if redis_loss is not None:
                checks.update(redis_loss["checks"])
            if redis_stall is not None:
                checks.update(redis_stall["checks"])
            for inspection in cache_publish.values():
                checks.update(inspection["checks"])
            if verify_log_correlation:
                checks["baseline_log_server_span_correlated"] = log_lookup["complete"] is True
            if scenario:
                # A failure can prevent an outgoing call entirely. Do not demand
                # healthy baseline edges in the post-fault bounded trace sample.
                checks["baseline_parentage_verified"] = expected <= {(call.source, call.target) for call in topology.observed.calls if call.trace_id == trace_id}
                checks["checkpoint_observations_retained"] = restored.service_topology == incident.service_topology
            else:
                checks["live_parentage_retained"] = expected <= {(call.source, call.target) for call in retained}
            if live_telemetry:
                metrics = [item for item in incident.evidence if item.kind.value == "metric" and item.service == service and item.attributes.get("series")]
                logs = [item for item in incident.evidence if item.kind.value == "log" and item.service == service and item.attributes.get("events")]
                traces = [item for item in incident.evidence if item.kind.value == "trace" and item.attributes.get("traces")]
                checks.update(live_metrics=bool(metrics), live_logs=bool(logs), live_traces=bool(traces))
                if fault_cpu_spike:
                    checks["cpu_diagnosis"] = incident.incident_type == "cpu_saturation" and incident.status.value == "waiting_approval"
                if scenario:
                    checks.update(diagnosis_checks(incident, scenario=scenario, service=service))
            if real_postgres_pool:
                response = await client.delete(f"http://127.0.0.1:{SERVICE_PORTS[service]}/admin/fault",
                    headers={"X-Fault-Token": environment["FAULT_INJECTION_TOKEN"]})
                response.raise_for_status()
                response = await client.post(f"http://127.0.0.1:{SERVICE_PORTS[service]}/v1/work")
                pool_inspections["recovered"] = await inspect_real_pool(client, service, environment["FAULT_INJECTION_TOKEN"], pool_parameters)
                pool_inspections["recovered"]["direct_work_status"] = response.status_code
                before, during, after = (pool_inspections[phase] for phase in ("baseline", "exhausted", "recovered"))
                checks.update(real_postgres_sessions=all(len(row["postgres_sessions"]) == 4 and all(session["application_name"] == "incident-fault-lab-pool" for session in row["postgres_sessions"]) for row in (before, during, after)),
                    actual_pool_exhausted=before["active"] == 0 and during["active"] == during["maximum"] == 4 and during["direct_work_status"] == 503,
                    actual_pool_recovered=after["active"] == 0 and after["direct_work_status"] == 200 and before["backend_pids"] == after["backend_pids"])
                checks["real_pool_metric_in_incident"] = any(item.service == service and item.query in {"db_pool_utilization", "db_pool_active_ratio"} and any(point["value"] == 1.0 for point in item.attributes.get("series", [])) for item in incident.evidence)
                checks["real_pool_timeout_log_in_incident"] = any(item.service == service and any(event.get("message") == "database connection acquisition timed out" and event.get("active") == event.get("max") == 4 for event in item.attributes.get("events", [])) for item in incident.evidence)
            report = {
                "ok": all(checks.values()), "checks": checks, "run_at": datetime.now(UTC).isoformat(), "duration_seconds": round(time.monotonic() - started, 3),
                "scope": ("actual native fault-lab HTTP services -> live Prometheus/Loki/Tempo -> actual Go gateway -> actual MCP HTTP -> deterministic LangGraph memory checkpoint"
                          if live_telemetry else "actual native fault-lab HTTP services -> OTLP collector -> live Tempo -> actual Go gateway -> actual MCP HTTP -> LangGraph memory checkpoint"),
                "limitations": ["No PostgreSQL recovery, model, or real remediation", "Sampled synchronous call parentage is not incident causation",
                    "Fault-lab CPU gauge reports configured fault intensity, not measured host CPU utilization",
                    "Catalog/deployment metadata and knowledge remain fixture-backed"] if live_telemetry else ["Metrics/logs/reasoning use DemoTelemetryTools", "No fault injection, PostgreSQL recovery, model, or real remediation", "Sampled synchronous call parentage is not incident causation"],
                "live_metrics_logs": live_telemetry, "fault_injected": fault["mode"] if fault else None,
                "scenario": scenario, "primary_service": service, "fault_injected_at": injected_at,
                "workload_status_counts": workload, "diagnostic_seconds": diagnostic_seconds,
                "workload_continued_through_diagnosis": scenario is not None,
                "trace_id": trace_id, "retrieval_attempts": attempts, "topology": topology.model_dump(mode="json"),
                "topology_phase": "baseline",
                "baseline_request_id": request_id, "correlated_baseline_logs": correlated_logs,
                "baseline_log_lookup": log_lookup,
                "incident": incident.model_dump(mode="json"), "model_calls": 0, "production_actions_executed": False,
                "checkpoint_backend": "postgresql" if database_url else "memory", "fresh_process_recovery": recovery,
                "api_crash_recovery": api_recovery,
                "redis_stream_recovery": redis_recovery,
                "redis_loss_replay": redis_loss,
                "redis_stall_replay": redis_stall,
                "cache_publish_degradation": cache_publish,
                "db_pool_backend": "postgres" if real_postgres_pool else "simulation",
                "real_pool_inspections": pool_inspections,
                "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__), *sorted((ROOT / "fault-lab/app/incident_lab").glob("*.py")), *sorted((ROOT / "services/orchestrator/incident_copilot").glob("*.py")), *sorted((ROOT / "services/mcp-server/incident_mcp").glob("*.py"))]},
                "gateway_sha256": hashlib.sha256(gateway_binary.read_bytes()).hexdigest(),
            }
            if database_url:
                report["scope"] = report["scope"].replace("memory checkpoint", "PostgreSQL checkpoint/repository + fresh-process recovery")
                report["limitations"][0] = "No model, real remediation, Redis or live API-server crash recovery"
            if verify_api_crash:
                report["scope"] += "; authenticated real API creation, SIGKILL and fresh API HTTP recovery"
                report["limitations"][0] = "No model, Redis, approval/resume, in-flight diagnosis crash or remediation execution crash recovery"
            if verify_redis_stream:
                report["scope"] += "; owned Unix-only Redis survives API crash, exact SSE full/cursor replay"
                report["limitations"][0] = "No model, Redis process crash/disk durability, approval/resume, in-flight diagnosis or execution crash recovery"
            if verify_redis_loss:
                report["scope"] += "; owned Redis SIGKILL followed by read-only checkpoint SSE full/cursor replay"
                report["limitations"][0] = "No model, Redis restart/disk recovery, approval/resume, in-flight diagnosis or execution crash recovery"
            if verify_redis_stall:
                report["scope"] += "; owned Redis SIGSTOP, bounded checkpoint SSE fallback, SIGCONT normal stream recovery"
                report["limitations"][0] = "No model, total request/DB deadline, Redis restart/disk recovery, approval/resume or in-flight/execution crash recovery"
            if verify_cache_publish:
                report["scope"] += "; additional API diagnoses remain committed/available during owned Redis stall and loss"
            if real_postgres_pool:
                report["scope"] += "; four actual PostgreSQL application-pool leases exhausted and explicitly cleared, fixed SELECT 1 requests recover"
                report["limitations"].append("Application pool acquisition exhaustion, not PostgreSQL server-wide max_connections exhaustion; other fault modes remain simulated")
            prefix = f"native_scenario_{scenario}_" if scenario else "native_live_telemetry_" if live_telemetry else "observed_topology_live_"
            target = ROOT / "fault-lab/results" / (prefix + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"ok": report["ok"], "checks": checks, "duration_seconds": report["duration_seconds"], "retained_call_pairs": len(retained),
                              "status": incident.status.value, "report": str(target)}, ensure_ascii=False))
            if not report["ok"]:
                raise SystemExit(1)
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
        for process in reversed(children):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if redis_directory is not None:
            redis_directory.cleanup()  # Only this freshly owned temporary directory.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-integration", action="store_true")
    parser.add_argument("--gateway-binary", type=Path, required=True)
    parser.add_argument("--live-telemetry", action="store_true", help="Require live Prometheus and Loki; do not use Demo telemetry.")
    parser.add_argument("--fault-cpu-spike", action="store_true", help="Inject bounded CPU fault only into the owned temporary payment service.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="Run one of the eight canonical faults using four owned services and live telemetry.")
    parser.add_argument("--verify-log-correlation", action="store_true", help="Verify actual Loki/MCP log identities against observed Tempo SERVER spans.")
    parser.add_argument("--checkpoint-postgres", action="store_true", help="Use an explicitly configured isolated local INCIDENT_TEST_DATABASE_URL and verify fresh-process recovery.")
    parser.add_argument("--real-postgres-pool", action="store_true", help="Only for db-pool-exhaustion with live telemetry and isolated PostgreSQL checkpoints; hold four real application-pool leases.")
    parser.add_argument("--verify-api-crash", action="store_true", help="Create via owned authenticated API, kill its process after diagnosis and read state/history from a fresh API.")
    parser.add_argument("--verify-redis-stream", action="store_true", help="Create a Unix-only temporary Redis and verify task state and SSE replay across the API crash.")
    parser.add_argument("--verify-redis-loss", action="store_true", help="After Redis recovery inspection, kill only the owned Redis and verify read-only checkpoint SSE fallback.")
    parser.add_argument("--verify-redis-stall", action="store_true", help="Pause only the owned Redis, verify bounded checkpoint SSE fallback and then resume normal delivery.")
    parser.add_argument("--verify-cache-publish", action="store_true", help="Create additional diagnoses during owned Redis stall/loss and inspect committed state and degraded-delivery headers.")
    args = parser.parse_args()
    if not args.run_local_integration:
        parser.error("--run-local-integration is required before creating traffic or starting services")
    if args.fault_cpu_spike and not args.live_telemetry:
        parser.error("--fault-cpu-spike requires --live-telemetry")
    if args.scenario and (not args.live_telemetry or args.fault_cpu_spike):
        parser.error("--scenario requires --live-telemetry and excludes --fault-cpu-spike")
    if args.verify_log_correlation and not args.live_telemetry:
        parser.error("--verify-log-correlation requires --live-telemetry")
    try:
        asyncio.run(run(args.gateway_binary.resolve(), live_telemetry=args.live_telemetry, fault_cpu_spike=args.fault_cpu_spike, scenario=args.scenario, verify_log_correlation=args.verify_log_correlation, checkpoint_postgres=args.checkpoint_postgres, verify_api_crash=args.verify_api_crash, verify_redis_stream=args.verify_redis_stream, verify_redis_loss=args.verify_redis_loss, verify_redis_stall=args.verify_redis_stall, verify_cache_publish=args.verify_cache_publish, real_postgres_pool=args.real_postgres_pool))
    except Exception as error:
        # Never print provider responses or environment values.
        checks = {
            "Local verification child stopped before readiness": "child_startup_failed",
            "Local verification child stopped during trace retrieval": "child_stopped_during_trace_read",
            "Expected live trace parentage did not reach MCP before the deadline": "trace_parentage_timeout",
        }
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "check": checks.get(str(error))}))
        raise SystemExit(1) from None
