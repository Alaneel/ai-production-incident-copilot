import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/kubernetes"))
spec = importlib.util.spec_from_file_location("live_fault_verifier", ROOT / "deploy/kubernetes/verify_live_fault_lab.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def result(text="", code=0):
    return SimpleNamespace(stdout=text, stderr="private provider details", returncode=code)


def test_existing_cluster_cannot_be_reused_or_deleted():
    with patch.object(verifier.uuid, "uuid4", return_value=SimpleNamespace(hex="fixture")), patch.object(verifier, "execute", return_value=result("copilot-live-fixture\n")) as execute:
        with pytest.raises(ValueError):
            verifier.run({})
    assert execute.call_count == 1
    assert execute.call_args.args[0] == ["kind", "get", "clusters"]


def test_missing_image_fails_before_private_config_or_cluster_allocation():
    with patch.object(verifier, "names", return_value=[]), patch.object(verifier, "execute", side_effect=RuntimeError("fixture")), patch.object(verifier.tempfile, "mkdtemp") as directory:
        with pytest.raises(RuntimeError):
            verifier.run({"orchestrator": "missing"})
    directory.assert_not_called()


@pytest.mark.parametrize("delete_fails", [False, True])
def test_failure_after_creation_targets_only_the_owned_context_and_cluster(tmp_path, delete_fails):
    directory = tmp_path / "private"
    directory.mkdir()
    config = directory / "config"
    commands = []

    def execute(args, **kwargs):
        commands.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return result("sha256:fixture\n")
        if "kustomize" in args:
            return result()
        if args[:3] == ["kind", "create", "cluster"]:
            assert args[args.index("--kubeconfig") + 1] == str(config)
            config.write_text("fake private config")
        if args[:3] == ["kind", "load", "docker-image"]:
            raise RuntimeError("Synthetic load failure")
        if "kubectl" == args[0]:
            assert args[1:5] == ["--kubeconfig", str(config), "--context", "kind-copilot-live-fixture"]
            return result(json.dumps({"items": []}))
        if args[:3] == ["kind", "delete", "cluster"] and delete_fails:
            raise subprocess.TimeoutExpired(args, 1)
        return result()

    with patch.object(verifier.uuid, "uuid4", return_value=SimpleNamespace(hex="fixture")), patch.object(verifier.tempfile, "mkdtemp", return_value=str(directory)), patch.object(verifier, "names", return_value=[]), patch.object(verifier, "execute", side_effect=execute), patch.object(verifier, "render", return_value={"items": []}):
        report = verifier.run({"orchestrator": "fixture"})
    assert not report["ok"]
    assert report["last_phase"] == "load_images"
    assert report["cleanup_complete"] is not delete_fails
    assert config.exists() is delete_fails
    deletes = [args for args in commands if args[:3] == ["kind", "delete", "cluster"]]
    assert len(deletes) == 1 and deletes[0][deletes[0].index("--name") + 1] == "copilot-live-fixture"
    assert deletes[0][deletes[0].index("--kubeconfig") + 1] == str(config)
    assert report["model_calls"] == report["production_actions"] == 0
    assert "private provider details" not in str(report)


def test_advisory_boundary_does_not_accept_missing_or_destructive_remediation():
    assert not verifier.advisory_only({})
    assert not verifier.advisory_only({"status": "diagnosed", "remediation": {"tool": "rollback_release"}})
    state = {"status": "diagnosed", "approval_decision": None,
             "remediation": {"tool": "none", "requires_approval": False, "dry_run_passed": False}}
    assert verifier.advisory_only(state)


@pytest.mark.parametrize("delete_fails", [False, True])
def test_partial_create_failure_cleans_only_reserved_uuid_and_keeps_config_if_cleanup_fails(tmp_path, delete_fails):
    directory = tmp_path / "partial-private"
    directory.mkdir()
    config = directory / "config"
    commands = []

    def execute(args, **kwargs):
        commands.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return result("sha256:fixture\n")
        if "kustomize" in args:
            return result()
        if args[:3] == ["kind", "create", "cluster"]:
            config.write_text("synthetic partial private config")
            raise verifier.OperationFailure("kind", result(code=1))
        if args[:3] == ["kind", "delete", "cluster"] and delete_fails:
            raise subprocess.TimeoutExpired(args, 1)
        if args[0] == "kubectl":
            assert args[1:5] == ["--kubeconfig", str(config), "--context", "kind-copilot-live-fixture"]
            return result(json.dumps({"items": []}))
        return result()

    with patch.object(verifier.uuid, "uuid4", return_value=SimpleNamespace(hex="fixture")), patch.object(verifier.tempfile, "mkdtemp", return_value=str(directory)), patch.object(verifier, "names", return_value=[]), patch.object(verifier, "execute", side_effect=execute), patch.object(verifier, "render", return_value={"items": []}):
        report = verifier.run({"orchestrator": "fixture"})
    assert not report["ok"] and report["last_phase"] == "create_cluster"
    assert report["cleanup_complete"] is not delete_fails
    assert config.exists() is delete_fails
    deletion = [args for args in commands if args[:3] == ["kind", "delete", "cluster"]]
    assert len(deletion) == 1
    assert deletion[0][deletion[0].index("--name") + 1] == "copilot-live-fixture"
    assert deletion[0][deletion[0].index("--kubeconfig") + 1] == str(config)
    assert not any(args[:3] == ["kind", "load", "docker-image"] for args in commands)
    assert report["model_calls"] == report["production_actions"] == 0


def test_provider_errors_are_redacted_and_commands_are_bounded():
    with patch.object(verifier.subprocess, "run", return_value=result(code=1)) as run:
        with pytest.raises(RuntimeError) as error:
            verifier.execute(["kind", "get", "clusters"])
    assert str(error.value) == "Isolated test operation failed: kind"
    assert run.call_args.kwargs["timeout"] == 45


def test_retained_failure_diagnostics_scrub_generated_credentials(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir()

    def execute(args, **kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return result("sha256:fixture\n")
        if "kustomize" in args:
            return result()
        if args[:3] == ["kind", "load", "docker-image"]:
            raise verifier.OperationFailure("kind", SimpleNamespace(
                returncode=1, stdout="", stderr="Failure involving PRIVATE-TOKEN"))
        if args[0] == "kubectl":
            return result(json.dumps({"items": []}))
        return result()

    with patch.object(verifier.tempfile, "mkdtemp", return_value=str(directory)), patch.object(verifier.secrets, "token_urlsafe", return_value="PRIVATE-TOKEN"), patch.object(verifier, "names", return_value=[]), patch.object(verifier, "execute", side_effect=execute), patch.object(verifier, "render", return_value={"items": []}):
        report = verifier.run({"orchestrator": "fixture"})
    assert report["operation_error"] == {"returncode": 1, "stderr": "Failure involving [redacted]"}
    assert "PRIVATE-TOKEN" not in json.dumps(report)
    assert report["cleanup_complete"]


def pod(uid, *, ready=True, terminating=False):
    return {"metadata": {"name": "api-" + uid, "uid": uid,
                         **({"deletionTimestamp": "bounded-test-time"} if terminating else {})},
            "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def test_restart_probe_targets_new_ready_uid_not_old_terminating_pod():
    old, new = pod("old", terminating=True), pod("new")
    assert verifier.ready_new_pod({"items": [old, new]}, {"old"}) == new["metadata"]


@pytest.mark.parametrize("items", [[], [pod("old")], [pod("new", ready=False)],
                                    [pod("new", terminating=True)], [pod("a"), pod("b")]])
def test_ambiguous_or_unready_replacement_pods_are_not_recovery_proof(items):
    with pytest.raises(ValueError):
        verifier.ready_new_pod({"items": items}, {"old"})


def test_typed_empty_trace_envelope_is_not_runtime_telemetry_proof():
    state = {"evidence": [{"kind": kind, "attributes": {field: [1]}}
                          for kind, field in (("metric", "series"), ("log", "events"), ("trace", "traces"))]}
    assert verifier.telemetry_evidence_present(state)
    state["evidence"][-1]["attributes"]["traces"] = []
    assert not verifier.telemetry_evidence_present(state)
    assert not verifier.telemetry_evidence_present({})


def correlated_probe():
    return {"calls": [{"source": "checkout-api", "target": "payment-service", "trace_id": "trace",
                       "server_span_id": "server", "client_span_id": "client"}],
            "events": [{"trace_id": "trace", "request_id": "request", "span_id": "server", "message": "request completed"},
                       {"trace_id": "trace", "request_id": "request", "span_id": "inner",
                        "message": "database connection acquisition timed out", "level": "ERROR", "active": 4, "max": 4}],
            "traces": [{"trace_id": "trace"}]}


def test_exact_controlled_request_correlates_server_log_and_parentage():
    assert all(verifier.correlation_checks(correlated_probe(), "trace", "request").values())
    assert not any(verifier.correlation_checks({}, "trace", "request").values())


@pytest.mark.parametrize("field", ["trace_id", "request_id", "span_id"])
def test_server_log_identity_mismatch_cannot_pass(field):
    probe = correlated_probe()
    probe["events"][0][field] = "unrelated"
    assert not verifier.correlation_checks(probe, "trace", "request")["controlled_failure_server_log_identity"]


@pytest.mark.parametrize("field,value", [("trace_id", "other"), ("request_id", "other"), ("active", 3), ("max", 5), ("level", "INFO")])
def test_unrelated_or_non_saturated_error_is_not_pool_failure_proof(field, value):
    probe = correlated_probe()
    probe["events"][1][field] = value
    assert not verifier.correlation_checks(probe, "trace", "request")["controlled_failure_pool_log_identity"]


@pytest.mark.parametrize("field", ["source", "target", "trace_id", "client_span_id", "server_span_id"])
def test_missing_or_wrong_call_is_not_parentage_proof(field):
    probe = correlated_probe()
    probe["calls"][0][field] = "" if field.endswith("span_id") else "other"
    assert not verifier.correlation_checks(probe, "trace", "request")["controlled_failure_parentage"]


def test_probe_program_is_valid_read_only_and_does_not_embed_credentials():
    code = verifier.correlation_program("01" + "f" * 30, "probe-request")
    compile(code, "<controlled-mcp-probe>", "exec")
    assert "operator_token=''" in code
    assert "max_attempts=1" in code
    assert "rollback" not in code and "restart" not in code
    assert "query_traces" in code and "search_logs" in code and "get_service_topology" in code


@pytest.mark.parametrize("appears_after", [26, None])
def test_correlation_probe_observes_real_deadline_without_twelve_attempt_cutoff(monkeypatch, capsys, appears_after):
    clock, calls, closed = [0.0], [], []
    trace_id, request_id = "01" + "f" * 30, "controlled-late-trace"
    call = {"source": "checkout-api", "target": "payment-service", "trace_id": trace_id,
            "client_span_id": "c" * 16, "server_span_id": "d" * 16}

    class RecordingMcp:
        def __init__(self, **kwargs):
            assert kwargs["operator_token"] == "" and kwargs["max_attempts"] == 1

        async def get_service_topology(self, service):
            calls.append(service)
            visible = appears_after is not None and clock[0] >= appears_after
            return SimpleNamespace(observed=SimpleNamespace(calls=[SimpleNamespace(model_dump=lambda **kwargs: call)] if visible else []))

        async def search_logs(self, service, query):
            return [SimpleNamespace(attributes={"events": [{"trace_id": trace_id, "request_id": request_id,
                "span_id": call["server_span_id"], "message": "request completed"}]})]

        async def query_traces(self, service):
            visible = appears_after is not None and clock[0] >= appears_after
            return [SimpleNamespace(attributes={"traces": [{"trace_id": trace_id}] if visible else []})]

        async def close(self):
            closed.append(True)

    async def advance(seconds):
        clock[0] += seconds

    monkeypatch.setenv("MCP_READ_TOKEN", "controlled-test-token-not-a-credential")
    monkeypatch.setitem(sys.modules, "incident_copilot.mcp_client", SimpleNamespace(McpTelemetryTools=RecordingMcp))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", advance)
    exec(verifier.correlation_program(trace_id, request_id), {})
    report = json.loads(capsys.readouterr().out)
    assert len(report["attempts"]) > 12
    assert closed == [True]
    if appears_after is not None:
        assert clock[0] == appears_after
        assert report["attempts"][-1]["target_parentage_found"] is True
        assert report["attempts"][-1]["target_trace_found"] is True
        assert all(verifier.correlation_checks(report, trace_id, request_id)[key] for key in
                   ("controlled_failure_parentage", "controlled_failure_server_log_identity", "controlled_failure_trace_search"))
    else:
        assert clock[0] == 40
        assert len(report["attempts"]) == 20
        assert not report["attempts"][-1]["target_trace_found"]


def paused_state():
    context = {"tool": "restart_deployment", "service": "risk-engine", "namespace": "incident-copilot",
               "cluster_context": "a" * 64, "deployment_uid": "deployment-test-uid", "resource_version": "7",
               "deployment_template_sha256": "b" * 64, "revision": None, "replicaset_uid": None,
               "rollback_template_sha256": None}
    return {"incident_id": "test-incident", "status": "waiting_approval", "approval_decision": None,
            "remediation": {"id": "test-remediation", "tool": "restart_deployment", "requires_approval": True,
                            "preflight_mode": "kubernetes-server-dry-run", "dry_run_passed": True,
                            "arguments": {"service": "risk-engine", "namespace": "incident-copilot", "target_context": context}},
            "hypotheses": [{"evidence_ids": ["metric-cpu", "log-slow"]}],
            "trace": [{"event": "tool_result", "payload": {"tool": "restart_deployment", "dry_run": True,
                "accepted": True, "preflight_mode": "kubernetes-server-dry-run", "target_context": copy.deepcopy(context)}}]}


@pytest.mark.parametrize("scenario", sorted(verifier.SCENARIOS))
def test_every_canonical_scenario_has_an_explicit_bounded_fault_and_safety_contract(scenario):
    assert set(verifier.SCENARIOS) == set(verifier.EXPECTED) == set(verifier.FAULT_SCOPES)
    assert len(verifier.SCENARIOS) == 8
    assert verifier.SCENARIOS[scenario]["service"] in {"checkout-api", "payment-service", "ledger-service", "risk-engine"}
    state = paused_state()
    if scenario == "kafka-consumer-lag":
        state["remediation"]["arguments"]["service"] = "ledger-service"
        state["remediation"]["arguments"]["target_context"]["service"] = "ledger-service"
        state["trace"][0]["payload"]["target_context"]["service"] = "ledger-service"
    if verifier.EXPECTED[scenario][1] == "none":
        state.update(status="diagnosed", remediation={"tool": "none", "requires_approval": False, "dry_run_passed": False})
    assert verifier.safe_scenario_boundary(state, scenario)
    assert verifier.no_execution(state)


@pytest.mark.parametrize("field,value", [("status", "diagnosed"), ("approval_decision", "approve"),
                                        ("tool", "rollback_release"), ("requires_approval", False),
                                        ("dry_run_passed", False), ("dry_run_passed", 1)])
def test_approval_scenario_cannot_pass_from_missing_or_unapproved_boundary(field, value):
    state = paused_state()
    target = state if field in {"status", "approval_decision"} else state["remediation"]
    target[field] = value
    assert not verifier.safe_scenario_boundary(state, "cpu-spike")
    assert not verifier.safe_scenario_boundary({}, "cpu-spike")


@pytest.mark.parametrize("event", [{"step": "execute_or_simulate", "payload": {"executed": False}},
                                   {"step": "other", "payload": {"executed": True}}])
def test_any_execution_step_or_side_effect_invalidates_no_execution(event):
    state = paused_state()
    state["trace"] = [event]
    assert not verifier.no_execution(state)
    state = paused_state()
    state["approval_decision"] = "reject"
    assert not verifier.no_execution(state)


def checkpoint_probe(state):
    remediation = state["remediation"]
    return {"next": ["human_approval"], "interrupts": [{"id": "durable-interrupt", "value": {
        "incident_id": state["incident_id"], "remediation_id": remediation["id"],
        "tool": remediation["tool"], "arguments": copy.deepcopy(remediation["arguments"]),
        "dry_run_passed": True, "preflight_mode": remediation["preflight_mode"],
        "evidence_ids": state["hypotheses"][0]["evidence_ids"]}}]}


def test_checkpoint_requires_real_action_bound_interrupt_not_visible_status():
    state = paused_state()
    probe = checkpoint_probe(state)
    assert verifier.checkpoint_boundary(probe, state, "cpu-spike")
    assert not verifier.checkpoint_boundary({}, state, "cpu-spike")
    assert not verifier.checkpoint_boundary({"next": [], "interrupts": []}, state, "cpu-spike")
    assert verifier.checkpoint_boundary({"next": [], "interrupts": []}, state, "db-pool-exhaustion")
    assert not verifier.checkpoint_boundary(probe, state, "db-pool-exhaustion")


@pytest.mark.parametrize("field,value", [("incident_id", "other"), ("remediation_id", "other"),
                                        ("tool", "rollback_release"), ("arguments", {}),
                                        ("dry_run_passed", False), ("evidence_ids", []),
                                        ("preflight_mode", "simulation"), ("preflight_mode", None),
                                        ("evidence_ids", ["unrelated"])])
def test_checkpoint_interrupt_scope_and_evidence_must_match_retained_diagnosis(field, value):
    state = paused_state()
    probe = checkpoint_probe(state)
    probe["interrupts"][0]["value"][field] = value
    assert not verifier.checkpoint_boundary(probe, state, "cpu-spike")


@pytest.mark.parametrize("mutation", ["missing_id", "duplicate", "wrong_next"])
def test_missing_duplicate_or_wrong_checkpoint_pause_is_not_recovery_proof(mutation):
    state = paused_state()
    probe = checkpoint_probe(state)
    if mutation == "missing_id":
        probe["interrupts"][0]["id"] = ""
    elif mutation == "duplicate":
        probe["interrupts"] *= 2
    else:
        probe["next"] = ["execute_or_simulate"]
    assert not verifier.checkpoint_boundary(probe, state, "cpu-spike")


def test_checkpoint_program_is_database_enforced_read_only_and_has_no_tool_or_model_calls():
    code = verifier.checkpoint_program("quoted-'incident")
    compile(code, "<read-only-checkpoint>", "exec")
    assert "default_transaction_read_only=on" in code
    assert "SHOW transaction_read_only" in code
    assert "NoTools()" in code
    assert ".aget_state(" in code
    assert not any(word in code for word in (".setup(", ".ainvoke(", "resume_with_approval", "McpTelemetryTools", "OpenAIResponses", "API_KEY"))


def test_unknown_scenario_fails_before_image_or_cluster_inventory():
    with patch.object(verifier, "names") as names, patch.object(verifier, "execute") as execute:
        with pytest.raises(ValueError, match="Unknown canonical"):
            verifier.run({}, "unknown-fault")
    names.assert_not_called()
    execute.assert_not_called()


def snapshot():
    return {"service": "risk-engine", "namespace": "incident-copilot", "deployment_uid": "deployment-test-uid",
            "resource_version": "7", "generation": 1, "deployment_template_sha256": "b" * 64,
            "operation_annotations": {}}


def preflight_probe():
    target = paused_state()["remediation"]["arguments"]["target_context"]
    result = {"accepted": True, "executed": False, "dry_run": True, "mode": "dry-run", "backend": "kubernetes",
              "preflight_mode": "kubernetes-server-dry-run", "service": "risk-engine", "namespace": "incident-copilot",
              "target_context": target, "restarted_at": "2026-10-07T15:00:00+00:00"}
    retry = copy.deepcopy(result)
    retry["restarted_at"] = "2026-10-07T15:00:01+00:00"
    return {"before": snapshot(), "after": snapshot(), "result": result, "retry_result": retry,
            "expected_cluster_context": "a" * 64, "negative_results": {
                name: {"rejected": True, "error_type": "McpToolError"}
                for name in ("forbidden_namespace", "missing_deployment")}}


def test_real_server_preflight_and_same_key_fresh_retry_bind_independent_snapshots():
    probe = preflight_probe()
    assert all(verifier.server_preflight_checks(probe, "risk-engine", "incident-copilot").values())
    # Controller status updates are not pod-template changes or an action.
    probe["after"]["resource_version"] = "8"
    assert all(verifier.server_preflight_checks(probe, "risk-engine", "incident-copilot").values())
    assert verifier.diagnostic_preflight_binding(paused_state(), "cpu-spike", probe["before"], probe["after"])


@pytest.mark.parametrize("location,field,value", [
    ("result", "accepted", 1), ("result", "executed", True), ("result", "dry_run", "true"),
    ("result", "backend", "simulation"), ("result", "preflight_mode", "simulation"),
    ("result", "target_context", None), ("result", "service", "other-service"),
    ("retry_result", "restarted_at", "2026-10-07T15:00:00+00:00"),
    ("after", "deployment_uid", "replaced-uid"), ("after", "deployment_template_sha256", "d" * 64),
    ("after", "generation", 2), ("after", "generation", True), ("after", "resource_version", 7),
    ("after", "operation_annotations", {"incident-copilot.openai/idempotency-key": "unexpected-marker"}),
    ("before", "namespace", "wrong-namespace"),
])
def test_false_typed_stale_or_persisted_preflight_cannot_pass(location, field, value):
    probe = preflight_probe()
    probe[location][field] = value
    assert not all(verifier.server_preflight_checks(probe, "risk-engine", "incident-copilot").values())


@pytest.mark.parametrize("field,value", [("deployment_uid", "replacement"), ("deployment_template_sha256", "c" * 64),
                                         ("namespace", "other"), ("resource_version", False), ("revision", "141")])
def test_preflight_context_mismatch_is_not_live_approval_proof(field, value):
    state = paused_state()
    state["remediation"]["arguments"]["target_context"][field] = value
    assert not verifier.safe_scenario_boundary(state, "cpu-spike")
    assert not verifier.diagnostic_preflight_binding(state, "cpu-spike", snapshot(), snapshot())


def test_simulated_legacy_or_failed_latest_preflight_is_not_live_approval_proof():
    for change in ("simulation", "missing_context", "failed_trace", "missing_mode"):
        state = paused_state()
        if change == "simulation":
            state["remediation"]["preflight_mode"] = "simulation"
        elif change == "missing_context":
            state["remediation"]["arguments"].pop("target_context")
        elif change == "failed_trace":
            state["trace"].append({"event": "tool_result", "payload": {"tool": "restart_deployment", "dry_run": True, "accepted": False}})
        else:
            state["remediation"].pop("preflight_mode")
        assert not verifier.safe_scenario_boundary(state, "cpu-spike")


def test_preflight_probe_program_never_requests_execution_or_approval_and_hides_secrets():
    code = verifier.preflight_program("risk-engine", "incident-copilot", "probe-key", "missing-service")
    compile(code, "<server-preflight>", "exec")
    compile(verifier.cluster_context_program(), "<config-fingerprint>", "exec")
    assert "operator_token=''" in code and "'dry_run': True" in code
    assert "max_attempts=1" in code and "kube-system" in code
    assert not any(value in code for value in ("dry_run=False", "approval_receipt", "rollback_release", "MCP_OPERATOR_TOKEN", "MCP_APPROVAL_SECRET"))
