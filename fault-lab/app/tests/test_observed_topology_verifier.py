import asyncio
import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
VERIFIER = ROOT / "fault-lab/verify_observed_topology.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("live_topology_verifier", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scenario,live,database_url", [
    ("cpu-spike", True, "host=localhost dbname=test"),
    ("db-pool-exhaustion", False, "host=localhost dbname=test"),
    ("db-pool-exhaustion", True, None),
    ("db-pool-exhaustion", True, "host=remote.example dbname=test"),
])
def test_real_pool_probe_rejects_wrong_scope_before_children(scenario, live, database_url):
    verifier = load_verifier()
    with pytest.raises(ValueError):
        verifier.real_pool_parameters(True, scenario=scenario, live=live, database_url=database_url)


def test_real_pool_probe_is_explicit_opt_in_and_uses_strict_local_parameters():
    verifier = load_verifier()
    assert verifier.real_pool_parameters(False, scenario=None, live=False, database_url=None) is None
    params = verifier.real_pool_parameters(True, scenario="db-pool-exhaustion", live=True,
                                           database_url="host=localhost dbname=isolated_test")
    assert params["hostaddr"] == "127.0.0.1" and params["dbname"] == "isolated_test"


@pytest.mark.parametrize("backend,pids", [
    ("simulation", [1, 2, 3, 4]), ("postgres", [1, 1, 2, 3]),
    ("postgres", [1, 2, 3]), ("postgres", [True, 2, 3, 4]),
    ("postgres", [0, 2, 3, 4]),
])
def test_real_session_inspection_rejects_invalid_identity_before_database_access(backend, pids):
    verifier = load_verifier()
    response = MagicMock()
    response.json.return_value = {"backend": backend, "backend_pids": pids}
    client = SimpleNamespace(get=AsyncMock(return_value=response))
    with patch("psycopg.AsyncConnection.connect", new_callable=AsyncMock) as connect:
        with pytest.raises(ValueError):
            asyncio.run(verifier.inspect_real_pool(client, "payment-service", "local-inspection-token", {}))
        connect.assert_not_awaited()
    assert client.get.await_args.kwargs["headers"] == {"X-Fault-Token": "local-inspection-token"}


def log_evidence(*events):
    return [SimpleNamespace(attributes={"events": list(events)})]


def test_log_visibility_wait_retains_initial_miss_then_exact_match():
    verifier = load_verifier()
    event = {"trace_id": "trace", "span_id": "server", "request_id": "request"}
    adapter = SimpleNamespace(search_logs=AsyncMock(side_effect=[log_evidence(), log_evidence(event)]))
    with patch.object(verifier.asyncio, "sleep", new=AsyncMock()) as sleep:
        logs, lookup = asyncio.run(verifier.correlate_baseline_logs(adapter, {("payment-service", "server")}, "trace", "request"))
    assert lookup["complete"] is True
    assert [row["matched_pairs"] for row in lookup["attempts"]] == [0, 1]
    assert logs == [{"service": "payment-service", **event}]
    sleep.assert_awaited_once_with(1)


@pytest.mark.parametrize("field", ["trace_id", "span_id", "request_id"])
def test_log_visibility_never_accepts_identity_mismatch(field):
    verifier = load_verifier()
    event = {"trace_id": "trace", "span_id": "server", "request_id": "request", field: "wrong"}
    adapter = SimpleNamespace(search_logs=AsyncMock(return_value=log_evidence(event)))
    with patch.object(verifier.asyncio, "sleep", new=AsyncMock()) as sleep:
        logs, lookup = asyncio.run(verifier.correlate_baseline_logs(adapter, {("payment-service", "server")}, "trace", "request"))
    assert not logs and lookup["complete"] is False
    assert adapter.search_logs.await_count == 10
    assert sleep.await_count == 9
    assert lookup["missing_pairs"] == [{"service": "payment-service", "server_span_id": "server"}]


def test_log_visibility_requires_all_spans_even_for_same_target_service():
    verifier = load_verifier()
    first = {"trace_id": "trace", "span_id": "first", "request_id": "request"}
    second = {**first, "span_id": "second"}
    adapter = SimpleNamespace(search_logs=AsyncMock(side_effect=[log_evidence(first), log_evidence(second)]))
    with patch.object(verifier.asyncio, "sleep", new=AsyncMock()):
        logs, lookup = asyncio.run(verifier.correlate_baseline_logs(adapter, {("payment-service", "first"), ("payment-service", "second")}, "trace", "request"))
    assert lookup["complete"] is True and len(logs) == 2
    assert [row["matched_pairs"] for row in lookup["attempts"]] == [1, 2]


def test_log_query_failure_is_retained_and_redacted_not_replaced_by_success():
    verifier = load_verifier()
    adapter = SimpleNamespace(search_logs=AsyncMock(side_effect=RuntimeError("secret-provider-payload")))
    with patch.object(verifier.asyncio, "sleep", new=AsyncMock()):
        logs, lookup = asyncio.run(verifier.correlate_baseline_logs(adapter, {("payment-service", "server")}, "trace", "request"))
    assert not logs and lookup["complete"] is False
    assert len(lookup["attempts"]) == 10
    assert all(row["errors"] == {"payment-service": "RuntimeError"} for row in lookup["attempts"])
    assert "secret-provider-payload" not in json.dumps(lookup)


def test_log_visibility_cancellation_propagates_without_retry():
    verifier = load_verifier()
    adapter = SimpleNamespace(search_logs=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(verifier.correlate_baseline_logs(adapter, {("payment-service", "server")}, "trace", "request"))
    assert adapter.search_logs.await_count == 1


def test_no_observed_span_pairs_cannot_pass_log_correlation():
    verifier = load_verifier()
    adapter = SimpleNamespace(search_logs=AsyncMock())
    logs, lookup = asyncio.run(verifier.correlate_baseline_logs(adapter, set(), "trace", "request"))
    assert not logs and lookup["complete"] is False
    adapter.search_logs.assert_not_awaited()


@pytest.mark.parametrize("options", [
    {}, {"checkpoint_postgres": True, "live_telemetry": True},
    {"live_telemetry": True, "scenario": "db-pool-exhaustion"},
])
def test_api_crash_probe_requires_dedicated_database_and_live_scenario_before_launch(options):
    verifier = load_verifier()
    with patch.dict(verifier.os.environ, {"INCIDENT_TEST_DATABASE_URL": "postgresql://127.0.0.1/test"}), patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="API crash probe requires"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_api_crash=True, **options))
    launch.assert_not_called()


def test_api_adapter_uses_create_and_read_endpoints_never_approvals():
    verifier = load_verifier()
    state = verifier.IncidentState(question="Why is payment slow?", environment="fault-lab")
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path, request.headers.get("authorization")))
        return verifier.httpx.Response(200, json=[state.model_dump(mode="json")] if request.url.path.endswith("/history") else state.model_dump(mode="json"))

    async def exercise():
        async with verifier.httpx.AsyncClient(transport=verifier.httpx.MockTransport(handle)) as client:
            api = verifier.ApiIncidentWorkflow(client, "test-api-token")
            assert await api.start(verifier.IncidentQuestion(question=state.question)) == state
            assert await api.get(state.incident_id) == state
            assert await api.history(state.incident_id) == [state]
    asyncio.run(exercise())
    assert requests == [
        ("POST", "/v1/incidents", "Bearer test-api-token"),
        ("GET", f"/v1/incidents/{state.incident_id}", "Bearer test-api-token"),
        ("GET", f"/v1/incidents/{state.incident_id}/history", "Bearer test-api-token"),
    ]


def test_api_adapter_propagates_authorization_failure_without_retry():
    verifier = load_verifier()
    calls = []

    def handle(request):
        calls.append(request)
        return verifier.httpx.Response(401)

    async def exercise():
        async with verifier.httpx.AsyncClient(transport=verifier.httpx.MockTransport(handle)) as client:
            with pytest.raises(verifier.httpx.HTTPStatusError):
                await verifier.ApiIncidentWorkflow(client, "test-api-token").get("INC-TEST")
    asyncio.run(exercise())
    assert len(calls) == 1


def test_redis_stream_mode_requires_api_crash_before_launch():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="requires API crash"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_redis_stream=True))
    launch.assert_not_called()


def test_redis_loss_mode_cannot_target_an_external_redis():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="requires owned Redis"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_redis_loss=True))
    launch.assert_not_called()


def test_redis_stall_mode_requires_owned_redis_before_launch():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="requires owned Redis"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_redis_stall=True))
    launch.assert_not_called()


def test_cache_publish_probe_requires_both_owned_failure_modes():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="requires owned Redis stall and loss"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_cache_publish=True))
    launch.assert_not_called()


def test_sse_inspection_preserves_read_only_replay_source_without_changing_events():
    verifier = load_verifier()
    source = {"source": "workflow_checkpoint", "checkpoint_backend": "postgres", "reason": "task_store_unavailable", "read_only": True}
    payload = "event: replay_source\ndata: " + json.dumps(source) + "\n\n" + trace_frame(1)
    events, states, sources = verifier.parse_trace_stream(payload, include_sources=True)
    assert [event.sequence for event in events] == [1]
    assert sources == [source] and not states


def test_redis_stream_mode_missing_binary_refuses_before_launch():
    verifier = load_verifier()
    with patch.object(verifier.shutil, "which", return_value=None), patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="local redis-server"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_api_crash=True, verify_redis_stream=True))
    launch.assert_not_called()


def trace_frame(sequence, *, envelope=None):
    return f"id: {sequence if envelope is None else envelope}\nevent: trace\ndata: " + json.dumps({
        "sequence": sequence, "step": "classify_incident", "event": "step_started",
        "occurred_at": "2026-10-03T00:00:00Z", "payload": {},
    }) + "\n\n"


def test_sse_inspection_preserves_exact_events_state_and_heartbeat():
    verifier = load_verifier()
    payload = ": keep-alive\n\n" + trace_frame(1) + trace_frame(2) + 'event: state\ndata: {"status":"waiting_approval"}\n\n'
    events, states = verifier.parse_trace_stream(payload.replace("\n", "\r\n"))
    assert [event.sequence for event in events] == [1, 2]
    assert states == [{"status": "waiting_approval"}]


@pytest.mark.parametrize("payload", [
    trace_frame(1, envelope=2), trace_frame(2) + trace_frame(1),
    trace_frame(1) + trace_frame(1), "event: trace\nevent: state\ndata: {}\n\n",
    "x" * (1024 * 1024 + 1),
])
def test_sse_inspection_rejects_wrong_ids_duplicate_or_unbounded_data(payload):
    verifier = load_verifier()
    with pytest.raises(ValueError):
        verifier.parse_trace_stream(payload)


def test_api_sse_cursor_uses_authenticated_read_only_request():
    verifier = load_verifier()
    requests = []

    def handle(request):
        requests.append(request)
        return verifier.httpx.Response(200, text=trace_frame(3), headers={"content-type": "text/event-stream"})

    async def exercise():
        async with verifier.httpx.AsyncClient(transport=verifier.httpx.MockTransport(handle)) as client:
            events, states = await verifier.ApiIncidentWorkflow(client, "test-api-token").events("INC-TEST", after=2)
            assert [event.sequence for event in events] == [3] and not states
    asyncio.run(exercise())
    request, = requests
    assert request.method == "GET" and request.url.params["after"] == "2"
    assert request.headers["authorization"] == "Bearer test-api-token"


def test_live_verifier_requires_explicit_local_gate_before_any_launch():
    result = subprocess.run([sys.executable, str(VERIFIER), "--gateway-binary", "/nonexistent/test-gateway"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--run-local-integration is required" in result.stderr
    assert not result.stdout


def test_occupied_port_refuses_before_launching_children():
    verifier = load_verifier()
    probe = MagicMock()
    probe.__enter__.return_value.bind.side_effect = OSError("test port occupied")
    # Construct the real event-loop self-pipe before mocking the verifier socket.
    with asyncio.Runner() as runner:
        with patch.object(verifier.socket, "socket", return_value=probe), patch.object(verifier.subprocess, "Popen") as launch:
            with pytest.raises(OSError):
                runner.run(verifier.run(Path("/nonexistent/test-gateway")))
            launch.assert_not_called()
            probe.__enter__.return_value.setsockopt.assert_called_once_with(verifier.socket.SOL_SOCKET, verifier.socket.SO_REUSEADDR, 1)


def test_failed_child_readiness_closes_only_verifier_owned_processes():
    verifier = load_verifier()
    binary, process = MagicMock(), MagicMock()
    binary.is_file.return_value = True
    process.poll.return_value = None
    process.wait.return_value = 0
    ready = AsyncMock(side_effect=[None, RuntimeError("simulated child readiness failure")])
    # Construct the real event-loop self-pipe before mocking the verifier socket.
    with asyncio.Runner() as runner:
        with patch.object(verifier.socket, "socket"), patch.object(verifier.subprocess, "Popen", return_value=process) as launch, patch.object(verifier, "ready", ready):
            with pytest.raises(RuntimeError, match="simulated child readiness failure"):
                runner.run(verifier.run(binary))
            launch.assert_called_once()
            assert launch.call_args.kwargs["env"]["KUBERNETES_REMEDIATION_ENABLED"] == "false"
            assert "OPENAI_API_KEY" not in launch.call_args.kwargs["env"]
            assert "DATABASE_URL" not in launch.call_args.kwargs["env"]
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=8)
            process.kill.assert_not_called()


def test_cpu_fault_mode_requires_live_telemetry_before_any_process_launch():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="CPU probe requires live telemetry"):
            asyncio.run(verifier.run(Path("/nonexistent/test-gateway"), fault_cpu_spike=True))
        launch.assert_not_called()


def test_log_correlation_refuses_demo_telemetry_before_any_launch():
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="Log correlation verification requires live telemetry"):
            asyncio.run(verifier.run(Path("/nonexistent"), verify_log_correlation=True))
        launch.assert_not_called()


@pytest.mark.parametrize("url", [
    "", "postgresql://remote.example/test", "postgresql:///test", "https://127.0.0.1/test",
    "postgresql://localhost/test?host=remote.example",
    "postgresql://localhost/test?hostaddr=192.0.2.1",
    "postgresql://localhost/test?service=production",
])
def test_checkpoint_probe_rejects_unscoped_database_before_launch(url):
    verifier = load_verifier()
    with patch.dict(verifier.os.environ, {"INCIDENT_TEST_DATABASE_URL": url}), patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError, match="explicit local INCIDENT_TEST_DATABASE_URL"):
            asyncio.run(verifier.run(Path("/nonexistent"), live_telemetry=True, checkpoint_postgres=True))
        launch.assert_not_called()


def test_checkpoint_probe_requires_live_mode_and_keeps_url_private():
    verifier = load_verifier()
    url = "postgresql:///postgres?host=/private/tmp/owned-test-db&port=55439"
    with patch.dict(verifier.os.environ, {"INCIDENT_TEST_DATABASE_URL": url}):
        assert verifier.checkpoint_url(True, True) == url
        assert verifier.checkpoint_url(False, False) is None
        with pytest.raises(ValueError):
            verifier.checkpoint_url(True, False)


def test_fresh_checkpoint_reader_requires_explicit_gate():
    result = subprocess.run([sys.executable, str(ROOT / "fault-lab/restore_native_checkpoint.py")],
                            input="{}", capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"ok": False, "error_type": "ValueError"}


def test_cli_refuses_fault_injection_with_demo_telemetry():
    result = subprocess.run([sys.executable, str(VERIFIER), "--run-local-integration", "--fault-cpu-spike",
                             "--gateway-binary", "/nonexistent/test-gateway"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--fault-cpu-spike requires --live-telemetry" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("scenario", [
    "db-pool-exhaustion", "downstream-timeout", "cpu-spike", "memory-leak",
    "deployment-regression", "kafka-consumer-lag", "retry-storm", "cache-penetration",
])
def test_all_canonical_scenarios_use_actual_services_and_require_diagnosis(scenario):
    verifier = load_verifier()
    from incident_copilot.incident_profiles import PROFILES

    fault = verifier.select_fault(live_telemetry=True, fault_cpu_spike=False, scenario=scenario)
    assert fault == verifier.SCENARIOS[scenario]
    assert fault["service"] in verifier.SERVICE_PORTS
    expected_type, action = verifier.EXPECTED[scenario]
    assert PROFILES[expected_type].action == action
    incident = SimpleNamespace(
        services=[fault["service"]], incident_type=expected_type, hypotheses=[object()],
        remediation=SimpleNamespace(tool=action, requires_approval=action != "none"),
        status=SimpleNamespace(value="diagnosed" if action == "none" else "waiting_approval"),
        environment="prod-sg", trace=[], evidence=[SimpleNamespace(kind=SimpleNamespace(value="deployment"),
            attributes={"data_origin": "kubernetes_api", "namespace": "prod-sg"})],
    )
    assert all(verifier.diagnosis_checks(incident, scenario=scenario, service=fault["service"]).values())
    incident.hypotheses = []
    assert not verifier.diagnosis_checks(incident, scenario=scenario, service=fault["service"])["hypothesis_present"]
    incident.status.value = "resolved"
    assert not verifier.diagnosis_checks(incident, scenario=scenario, service=fault["service"])["approval_boundary"]


def test_native_fixture_catalog_requires_advisory_and_explicit_no_rollback_trace():
    verifier = load_verifier()
    incident = SimpleNamespace(services=["payment-service"], incident_type="database_connection_pool_exhaustion",
        hypotheses=[object()], environment="prod-sg", evidence=[],
        remediation=SimpleNamespace(tool="none", requires_approval=False), status=SimpleNamespace(value="diagnosed"),
        trace=[SimpleNamespace(event="rollback_target_unverified", payload={})])
    assert all(verifier.diagnosis_checks(incident, scenario="db-pool-exhaustion", service="payment-service").values())
    incident.trace.append(SimpleNamespace(event="tool_call_started", payload={"tool": "rollback_release"}))
    assert not verifier.diagnosis_checks(incident, scenario="db-pool-exhaustion", service="payment-service")["unverified_rollback_target_blocked"]


@pytest.mark.parametrize("live,legacy,scenario", [
    (False, False, "memory-leak"), (True, True, "cpu-spike"), (True, False, "unknown"),
])
def test_invalid_scenario_mode_refuses_before_launch(live, legacy, scenario):
    verifier = load_verifier()
    with patch.object(verifier.subprocess, "Popen") as launch:
        with pytest.raises(ValueError):
            asyncio.run(verifier.run(Path("/nonexistent"), live_telemetry=live, fault_cpu_spike=legacy, scenario=scenario))
        launch.assert_not_called()


def test_expected_fault_http_error_does_not_stop_bounded_traffic():
    verifier = load_verifier()
    client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=503)))
    with patch.object(verifier, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[0, 0, 2]))), patch.object(verifier.asyncio, "sleep", AsyncMock()):
        assert asyncio.run(verifier.exercise(client, duration_seconds=1)) == {"503": 1}
    client.post.assert_awaited_once_with("http://127.0.0.1:8191/v1/work")


def test_unexpected_workload_status_fails_without_unbounded_retry():
    verifier = load_verifier()
    client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=401)))
    with patch.object(verifier, "time", SimpleNamespace(monotonic=MagicMock(side_effect=[0, 0]))):
        with pytest.raises(RuntimeError, match="Unexpected workload HTTP status"):
            asyncio.run(verifier.exercise(client, duration_seconds=1))
    client.post.assert_awaited_once()


def test_scenario_readiness_failure_cleans_every_owned_child():
    verifier = load_verifier()
    binary = MagicMock()
    binary.is_file.return_value = True
    children = [MagicMock(), MagicMock()]
    for process in children:
        process.poll.return_value = None
        process.wait.return_value = 0
    ready = AsyncMock(side_effect=[None] * 4 + [RuntimeError("scenario readiness failed")])
    # Construct the real event-loop self-pipe before mocking the verifier socket.
    with asyncio.Runner() as runner:
        with patch.object(verifier.socket, "socket"), patch.object(verifier.subprocess, "Popen", side_effect=children) as launch, patch.object(verifier, "ready", ready):
            with pytest.raises(RuntimeError, match="scenario readiness failed"):
                runner.run(verifier.run(binary, live_telemetry=True, scenario="cpu-spike"))
            assert launch.call_count == 2
            assert launch.call_args_list[0].kwargs["env"]["SERVICE_NAME"] == "risk-engine"
            assert launch.call_args_list[1].kwargs["env"]["SERVICE_NAME"] == "ledger-service"
            for process in children:
                process.terminate.assert_called_once()
                process.wait.assert_called_once_with(timeout=8)
                process.kill.assert_not_called()


def test_baseline_timeout_saves_failure_before_fault_injection(capsys):
    verifier = load_verifier()
    from incident_copilot.dependency_topology import ServiceTopology

    binary, context, process = MagicMock(), MagicMock(), MagicMock()
    binary.is_file.return_value = True
    process.poll.return_value = None
    process.wait.return_value = 0
    response = MagicMock()
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    adapter = SimpleNamespace(get_service_topology=AsyncMock(return_value=ServiceTopology(service="payment-service")))
    clock = SimpleNamespace(monotonic=MagicMock(side_effect=[0, 0, 0, 41, 41]))
    # Construct the real event-loop self-pipe before mocking the verifier socket.
    with asyncio.Runner() as runner:
        with patch.object(verifier.socket, "socket"), patch.object(verifier.subprocess, "Popen", return_value=process), patch.object(verifier, "ready", AsyncMock()), patch.object(verifier.httpx, "AsyncClient", return_value=context), patch.object(verifier, "McpTelemetryTools", return_value=adapter), patch.object(verifier, "time", clock), patch.object(verifier.asyncio, "sleep", AsyncMock()), patch.object(Path, "write_text") as save, patch.object(verifier, "LangGraphIncidentWorkflow") as graph:
            with pytest.raises(RuntimeError, match="Expected live trace parentage"):
                runner.run(verifier.run(binary, live_telemetry=True, scenario="memory-leak"))
            saved = json.loads(save.call_args.args[0])
            assert saved["stage"] == "baseline"
            assert saved["checks"] == {"baseline_parentage_verified": False}
            assert saved["fault_injected"] is None
            assert saved["model_calls"] == 0
            assert saved["production_actions_executed"] is False
            assert saved["trace_id"].startswith("01")
            graph.return_value.start.assert_not_called()
            client.post.assert_awaited_once()  # healthy probe only, never /admin/fault
            process.terminate.assert_called()
    assert "baseline" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [False, True])
def test_live_workload_runs_during_diagnosis_and_is_cancelled_on_every_exit(failure):
    verifier = load_verifier()

    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def traffic(_client, *, duration_seconds, counts):
            assert duration_seconds == 90
            counts["200"] = 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def diagnose(_request):
            assert started.is_set() and not stopped.is_set()
            if failure:
                raise RuntimeError("diagnosis failed")
            return "incident"

        counts = {}
        workflow = SimpleNamespace(start=AsyncMock(side_effect=diagnose))
        with patch.object(verifier, "exercise", side_effect=traffic):
            if failure:
                with pytest.raises(RuntimeError, match="diagnosis failed"):
                    await verifier.diagnose_with_traffic(workflow, "question", object(), counts)
            else:
                assert await verifier.diagnose_with_traffic(workflow, "question", object(), counts) == "incident"
        assert stopped.is_set() and counts == {"200": 1}

    asyncio.run(scenario())


def test_immediate_live_workload_failure_prevents_diagnosis():
    verifier = load_verifier()
    workflow = SimpleNamespace(start=AsyncMock())
    with patch.object(verifier, "exercise", AsyncMock(side_effect=RuntimeError("workload failed"))):
        with pytest.raises(RuntimeError, match="workload failed"):
            asyncio.run(verifier.diagnose_with_traffic(workflow, "question", object(), {}))
    workflow.start.assert_not_awaited()
