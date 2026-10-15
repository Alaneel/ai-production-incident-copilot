import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/kubernetes"))
spec = importlib.util.spec_from_file_location("fault_matrix", ROOT / "deploy/kubernetes/verify_fault_matrix.py")
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


@pytest.fixture
def source_snapshot(monkeypatch):
    # Freeze only the read-only fingerprint lookup; no Docker, network or tools.
    snapshot = matrix.current_source_sha256()
    monkeypatch.setattr(matrix, "current_source_sha256", lambda: copy.deepcopy(snapshot))
    return snapshot


def case(scenario, sources):
    primary = matrix.SCENARIOS[scenario]["service"]
    question = f"Why is {primary} suddenly slow?"
    action = matrix.EXPECTED[scenario][1]
    snapshot = {"service": primary, "namespace": "incident-copilot", "deployment_uid": "deployment-" + primary,
                "resource_version": "5", "generation": 1,
                "deployment_template_sha256": matrix.digest((primary + "template").encode()),
                "operation_annotations": {}}
    context = {"tool": "restart_deployment", "service": primary, "namespace": "incident-copilot",
               "cluster_context": matrix.digest(b"configured-api-origin-and-ca"),
               "deployment_uid": snapshot["deployment_uid"], "resource_version": snapshot["resource_version"],
               "deployment_template_sha256": snapshot["deployment_template_sha256"],
               "revision": None, "replicaset_uid": None, "rollback_template_sha256": None}
    preflight = {"service": primary, "namespace": "incident-copilot", "action": "restart", "mode": "dry-run",
                 "accepted": True, "executed": False, "dry_run": True, "backend": "kubernetes",
                 "preflight_mode": "kubernetes-server-dry-run", "target_context": copy.deepcopy(context),
                 "kubernetes_resource_version": "5", "restarted_at": "2026-10-07T14:00:00.000001+00:00"}
    state = {
        "incident_id": "incident-" + scenario, "trace_id": "workflow-" + scenario,
        "question": question, "environment": "incident-copilot",
        "services": [primary], "incident_type": matrix.EXPECTED[scenario][0],
        "status": "diagnosed" if action == "none" else "waiting_approval",
        "approval_decision": None,
        "remediation": {"id": "remediation-" + scenario, "tool": action,
                        "requires_approval": action != "none", "dry_run_passed": action != "none",
                        "preflight_mode": None if action == "none" else "kubernetes-server-dry-run",
                        "arguments": {"service": primary, "namespace": "incident-copilot"}},
        "trace": [{"sequence": 1, "step": "validate_evidence", "event": "evidence_validated",
                   "occurred_at": "2026-10-07T14:00:00+00:00", "duration_ms": None, "payload": {}}],
        "evidence": [
            {"id": "metric", "kind": "metric", "service": primary,
             "attributes": {"series": [{"timestamp": "2026-10-07T14:00:00Z", "value": 0.95}]}},
            {"id": "log", "kind": "log", "service": primary,
             "attributes": {"events": [{"timestamp": "2026-10-07T14:00:00Z", "message": "request completed"}]}},
            {"id": "trace", "kind": "trace", "service": primary,
             "attributes": {"traces": [{"trace_id": "1" * 32}]}},
        ],
        "hypotheses": [{"cause": "Deterministic hypothesis", "evidence_ids": ["metric", "log"]}],
    }
    if action != "none":
        state["remediation"]["arguments"]["target_context"] = copy.deepcopy(context)
        state["trace"].append({"sequence": 2, "step": "propose_remediation", "event": "tool_result",
                               "occurred_at": "2026-10-07T14:00:01+00:00", "duration_ms": None,
                               "payload": {"tool": action, "dry_run": True, "accepted": True,
                                           "preflight_mode": "kubernetes-server-dry-run",
                                           "target_context": copy.deepcopy(context), "error_type": None}})
    checkpoint = {"read_only": True, "checkpoint_id": "checkpoint-" + scenario,
                  "state": copy.deepcopy(state), "next": [], "interrupts": []}
    if action != "none":
        checkpoint.update(next=["human_approval"], interrupts=[{
            "id": "interrupt-" + scenario,
            "value": {"incident_id": state["incident_id"], "remediation_id": state["remediation"]["id"],
                      "tool": action, "arguments": copy.deepcopy(state["remediation"]["arguments"]),
                      "preflight_mode": "kubernetes-server-dry-run",
                      "dry_run_passed": True, "evidence_ids": state["hypotheses"][0]["evidence_ids"]}}])
    is_pool = scenario == "db-pool-exhaustion"
    cluster = "copilot-live-" + matrix.digest(scenario.encode())[:20]
    report = {
        "contract": matrix.CONTRACT, "scenario": scenario, "primary_service": primary,
        "fault_scope": matrix.FAULT_SCOPES[scenario], "ok": True, "cleanup_complete": True,
        "full_fault_matrix": False, "last_phase": "complete", "model_calls": 0, "production_actions": 0,
        "controlled_log_span_correlation_attempted": is_pool,
        "checks": dict.fromkeys(matrix.COMMON_CHECKS | (matrix.POOL_CHECKS if is_pool else set()), True),
        "source_sha256": copy.deepcopy(sources),
        "images": {role: "sha256:" + matrix.digest(role.encode()) for role in matrix.IMAGE_ROLES},
        "manifest_sha256": matrix.digest((scenario + "manifest").encode()),
        "runtime_modes": {"status": "ok", "api_auth": "required", "checkpoint": "postgres",
                          "task_state": "redis", "tools": "mcp", "reasoning": "deterministic"},
        "cluster": cluster, "image_tag": "kind-" + cluster,
        "started_at": "2026-10-07T14:00:00+00:00", "elapsed_seconds": 160.5,
        "pool_baseline": {"backend": "postgres", "maximum": 4, "backend_pids": [71, 72, 73, 74]},
        "generic_question": question, "diagnosis_http_status": 201, "diagnosis": state,
        "checkpoint_before_restart": copy.deepcopy(checkpoint), "checkpoint_after_restart": copy.deepcopy(checkpoint),
        "pre_restart_api_pod_uids": ["old-uid"], "post_restart_api_pod": {"name": "api-new", "uid": "new-uid"},
        "server_preflight": {"before": copy.deepcopy(snapshot), "after": copy.deepcopy(snapshot),
                             "expected_cluster_context": context["cluster_context"],
                             "result": copy.deepcopy(preflight),
                             "retry_result": {**copy.deepcopy(preflight), "restarted_at": "2026-10-07T14:00:00.000002+00:00"},
                             "negative_results": {name: {"rejected": True, "error_type": "McpToolError"}
                                                  for name in ("forbidden_namespace", "missing_deployment")}},
        "diagnosis_target_before": copy.deepcopy(snapshot), "diagnosis_target_after": copy.deepcopy(snapshot),
    }
    if is_pool:
        trace_id, request_id = "01" + "a" * 30, "correlation-request"
        report["controlled_failure"] = {"trace_id": trace_id, "request_id": request_id,
                                        "response": {"status": 200, "body": {"downstream": {"status": "failed"}}}}
        report["failure_correlation"] = {
            "calls": [{"source": "checkout-api", "target": "payment-service", "trace_id": trace_id,
                       "client_span_id": "1" * 16, "server_span_id": "2" * 16}],
            "events": [{"trace_id": trace_id, "request_id": request_id, "span_id": "2" * 16,
                        "message": "request completed"},
                       {"trace_id": trace_id, "request_id": request_id, "span_id": "3" * 16,
                        "message": "database connection acquisition timed out", "level": "ERROR", "active": 4, "max": 4}],
            "traces": [{"trace_id": trace_id}],
        }
    return report


def retained(tmp_path, report, name="case"):
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps(report))
    return path


def synchronized_diagnosis(report, change):
    change(report["diagnosis"])
    for name in ("checkpoint_before_restart", "checkpoint_after_restart"):
        report[name]["state"] = copy.deepcopy(report["diagnosis"])


def rejected(tmp_path, report, expected):
    result = matrix.aggregate([retained(tmp_path, report)])
    assert not result["accepted"] and not result["full_fault_matrix"]
    assert expected in str(result["errors"])
    return result


def test_source_helper_retains_current_runner_corpus_and_package_inputs(source_snapshot):
    assert len(source_snapshot) == 142
    for context in ("postgres", "redis", "collector", "tempo", "prometheus", "loki", "grafana"):
        assert f"deploy/images/{context}-security/Dockerfile" in source_snapshot
    for context in ("collector", "tempo", "prometheus", "loki"):
        assert f"deploy/images/{context}-security/go.mod" in source_snapshot
        assert f"deploy/images/{context}-security/go.sum" in source_snapshot
    assert {"container-images.lock.json", "requirements-runtime.lock", "requirements-build.lock", "requirements-fault-lab.lock"} <= source_snapshot.keys()
    assert "services/orchestrator/incident_copilot/cross_service_rca.py" in source_snapshot
    assert "services/orchestrator/incident_copilot/recovery_verification.py" in source_snapshot
    assert "services/mcp-server/incident_mcp/target_context.py" in source_snapshot
    assert source_snapshot["deploy/kubernetes/verify_live_fault_lab.py"] == matrix.digest(
        (ROOT / "deploy/kubernetes/verify_live_fault_lab.py").read_bytes())
    assert matrix.CORPUS in source_snapshot
    assert "services/orchestrator/incident_copilot/approval.py" in source_snapshot
    assert all(matrix.hash_value(value) for value in source_snapshot.values())


def test_eight_unique_current_cases_are_required_for_full_matrix(tmp_path, source_snapshot):
    paths = [retained(tmp_path, case(scenario, source_snapshot), scenario) for scenario in sorted(matrix.SCENARIOS)]
    originals = {path: path.read_bytes() for path in paths}
    result = matrix.aggregate(paths)
    assert result["accepted"] and result["full_fault_matrix"]
    assert result["coverage"]["accepted_count"] == 8
    assert result["coverage"]["missing_scenarios"] == []
    assert result["source_sha256"] == source_snapshot
    assert result["knowledge_corpus_sha256"] == source_snapshot[matrix.CORPUS]
    assert result["aggregator_sha256"] == matrix.digest(Path(matrix.__file__).read_bytes())
    assert len({item["manifest_sha256"] for item in result["cases"]}) == 8
    assert all(item["report_sha256"] == matrix.digest(originals[Path(item["path"])]) for item in result["cases"])
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert any("not independent" in limitation for limitation in result["limitations"])


def test_valid_subset_is_accepted_without_full_matrix(tmp_path, source_snapshot):
    result = matrix.aggregate([retained(tmp_path, case("cpu-spike", source_snapshot))])
    assert result["accepted"] and not result["full_fault_matrix"]
    assert result["coverage"]["accepted_scenarios"] == ["cpu-spike"]
    assert len(result["coverage"]["missing_scenarios"]) == 7


def test_v2_requires_seven_preflight_checks_and_does_not_upgrade_v1_counts(tmp_path, source_snapshot):
    assert len(matrix.COMMON_CHECKS) == 35
    assert len(matrix.COMMON_CHECKS | matrix.POOL_CHECKS) == 42
    report = case("cpu-spike", source_snapshot)
    for key in ("server_preflight_validated", "preflight_target_matches_snapshot", "preflight_did_not_persist",
                "preflight_fresh_retry", "preflight_forbidden_namespace_rejected",
                "preflight_missing_deployment_rejected", "diagnostic_preflight_binding"):
        del report["checks"][key]
    assert len(report["checks"]) == 28
    rejected(tmp_path, report, "Required checks")


@pytest.mark.parametrize("scenario", ["cpu-spike", "memory-leak"])
@pytest.mark.parametrize("field", ["server_preflight", "diagnosis_target_before", "diagnosis_target_after"])
def test_green_flags_require_actual_retained_probe_and_diagnostic_snapshots(tmp_path, source_snapshot, scenario, field):
    report = case(scenario, source_snapshot)
    del report[field]
    rejected(tmp_path, report, "preflight")


@pytest.mark.parametrize("field,value", [
    ("service", "other-service"), ("namespace", "kube-system"), ("deployment_uid", None),
    ("deployment_uid", True), ("resource_version", 5), ("resource_version", " "),
    ("generation", True), ("generation", 1.0), ("generation", 0),
    ("deployment_template_sha256", "A" * 64), ("operation_annotations", []),
    ("operation_annotations", {"foreign.example/annotation": "ignored"}),
    ("operation_annotations", {"incident-copilot.openai/idempotency-key": False}),
])
def test_preflight_snapshot_scope_and_types_are_independently_checked(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    report["server_preflight"]["before"][field] = value
    rejected(tmp_path, report, "server preflight snapshot")


@pytest.mark.parametrize("field,value", [
    ("accepted", 1), ("executed", 0), ("executed", True), ("dry_run", 1),
    ("backend", "simulation"), ("mode", "execute"), ("preflight_mode", "simulation"),
    ("service", "other-service"), ("namespace", "other-namespace"), ("action", "rollback"),
    ("kubernetes_resource_version", 5), ("target_context", None),
])
def test_server_preflight_response_cannot_impersonate_real_admission(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    report["server_preflight"]["result"][field] = value
    rejected(tmp_path, report, "Unverified server preflight")


@pytest.mark.parametrize("field,value", [
    ("tool", "rollback_release"), ("resource_version", 5), ("resource_version", " "),
    ("revision", "141"), ("replicaset_uid", "foreign-rs"), ("rollback_template_sha256", "b" * 64),
    ("deployment_template_sha256", "Z" * 64), ("cluster_context", True),
])
def test_restart_target_context_is_complete_typed_and_has_no_rollback_identity(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    report["server_preflight"]["result"]["target_context"][field] = value
    rejected(tmp_path, report, "Unverified server preflight")


@pytest.mark.parametrize("field", ["revision", "namespace", "resource_version", "cluster_context"])
def test_target_context_missing_fields_cannot_be_reconstructed_by_offline_verifier(tmp_path, source_snapshot, field):
    report = case("cpu-spike", source_snapshot)
    del report["server_preflight"]["result"]["target_context"][field]
    rejected(tmp_path, report, "Unverified server preflight")


@pytest.mark.parametrize("field", ["deployment_uid", "deployment_template_sha256"])
def test_matching_result_and_retry_still_must_match_target_snapshots(tmp_path, source_snapshot, field):
    report = case("cpu-spike", source_snapshot)
    value = "replacement-deployment" if field == "deployment_uid" else "c" * 64
    for result in ("result", "retry_result"):
        report["server_preflight"][result]["target_context"][field] = value
    rejected(tmp_path, report, "target differs from resource snapshots")


@pytest.mark.parametrize("change", ["wrong", "missing", "coordinated_results"])
def test_preflight_cluster_binding_requires_independent_configuration_observation(tmp_path, source_snapshot, change):
    report = case("cpu-spike", source_snapshot)
    probe = report["server_preflight"]
    if change == "missing":
        del probe["expected_cluster_context"]
    elif change == "wrong":
        probe["expected_cluster_context"] = "f" * 64
    else:
        for result in ("result", "retry_result"):
            probe[result]["target_context"]["cluster_context"] = "f" * 64
    rejected(tmp_path, report, "independent configuration observation")


@pytest.mark.parametrize("field,value", [
    ("deployment_uid", "replacement-deployment"), ("generation", 2),
    ("deployment_template_sha256", "d" * 64),
    ("operation_annotations", {"incident-copilot.openai/idempotency-key": "unexpected-operation"}),
])
def test_preflight_cannot_hide_a_persisted_change_behind_true_flags(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    report["server_preflight"]["after"][field] = value
    rejected(tmp_path, report, "persisted a target change")


@pytest.mark.parametrize("time", [None, True, "not-a-timestamp", "2026-10-07T14:00:00", "same"])
def test_fresh_retry_requires_distinct_typed_timestamp_not_cached_success(tmp_path, source_snapshot, time):
    report = case("cpu-spike", source_snapshot)
    probe = report["server_preflight"]
    probe["retry_result"]["restarted_at"] = probe["result"]["restarted_at"] if time == "same" else time
    rejected(tmp_path, report, "not a fresh observation")


@pytest.mark.parametrize("name", ["forbidden_namespace", "missing_deployment"])
@pytest.mark.parametrize("change", ["missing", "accepted", "bool_coercion", "wrong_error", "contradictory_success"])
def test_negative_preflight_probes_cannot_be_accepted_or_ambiguous(tmp_path, source_snapshot, name, change):
    report = case("cpu-spike", source_snapshot)
    negative = report["server_preflight"]["negative_results"]
    if change == "missing":
        del negative[name]
    elif change == "accepted":
        negative[name]["rejected"] = False
    elif change == "bool_coercion":
        negative[name]["rejected"] = 1
    elif change == "wrong_error":
        negative[name]["error_type"] = "ValueError"
    else:
        negative[name]["accepted"] = True
    rejected(tmp_path, report, "server preflight probe")


def test_controller_resource_version_drift_is_recorded_without_claiming_a_mutation(tmp_path, source_snapshot):
    report = case("cpu-spike", source_snapshot)
    report["server_preflight"]["after"]["resource_version"] = "7"
    report["server_preflight"]["retry_result"]["target_context"]["resource_version"] = "6"
    report["diagnosis_target_after"]["resource_version"] = "8"
    result = matrix.aggregate([retained(tmp_path, report)])
    assert result["accepted"] and not result["full_fault_matrix"]


@pytest.mark.parametrize("scenario", ["cpu-spike", "memory-leak"])
@pytest.mark.parametrize("field,value", [
    ("namespace", "kube-system"), ("service", "foreign-service"), ("generation", True),
    ("deployment_uid", "replacement-deployment"), ("generation", 2),
    ("deployment_template_sha256", "e" * 64),
    ("operation_annotations", {"incident-copilot.openai/restarted-at": "unexpected-operation"}),
])
def test_diagnostic_snapshot_binding_is_required_even_for_advisory_cases(tmp_path, source_snapshot, scenario, field, value):
    report = case(scenario, source_snapshot)
    report["diagnosis_target_after"][field] = value
    rejected(tmp_path, report, "preflight")


@pytest.mark.parametrize("field,value", [
    ("deployment_uid", "replacement-deployment"), ("deployment_template_sha256", "d" * 64),
    ("cluster_context", "f" * 64),
])
def test_coordinated_diagnostic_context_and_trace_tampering_cannot_escape_snapshots(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    def change(state):
        state["remediation"]["arguments"]["target_context"][field] = value
        state["trace"][-1]["payload"]["target_context"][field] = value
    synchronized_diagnosis(report, change)
    for name in ("checkpoint_before_restart", "checkpoint_after_restart"):
        report[name]["interrupts"][0]["value"]["arguments"] = copy.deepcopy(report["diagnosis"]["remediation"]["arguments"])
    rejected(tmp_path, report, "Diagnostic preflight target differs")


@pytest.mark.parametrize("change", ["simulation", "missing_context", "missing_trace", "unaccepted_trace", "wrong_trace_context", "trace_error"])
def test_diagnostic_preflight_requires_real_context_and_successful_matching_trace(tmp_path, source_snapshot, change):
    report = case("cpu-spike", source_snapshot)
    def mutation(state):
        if change == "simulation":
            state["remediation"]["preflight_mode"] = "simulation"
        elif change == "missing_context":
            del state["remediation"]["arguments"]["target_context"]
        elif change == "missing_trace":
            state["trace"].pop()
        elif change == "unaccepted_trace":
            state["trace"][-1]["payload"]["accepted"] = False
        elif change == "wrong_trace_context":
            state["trace"][-1]["payload"]["target_context"]["deployment_uid"] = "different-target"
        else:
            state["trace"][-1]["payload"]["error_type"] = "McpToolError"
    synchronized_diagnosis(report, mutation)
    rejected(tmp_path, report, "")


@pytest.mark.parametrize("mode", [None, "simulation"])
def test_interrupt_must_retain_real_preflight_mode_not_only_waiting_status(tmp_path, source_snapshot, mode):
    report = case("cpu-spike", source_snapshot)
    for name in ("checkpoint_before_restart", "checkpoint_after_restart"):
        report[name]["interrupts"][0]["value"]["preflight_mode"] = mode
    rejected(tmp_path, report, "flow boundary")


def test_diagnostic_preflight_verification_is_independent_of_boundary_green_result(tmp_path, source_snapshot, monkeypatch):
    report = case("cpu-spike", source_snapshot)
    synchronized_diagnosis(report, lambda state: state["trace"][-1]["payload"].update(error_type="McpToolError"))
    monkeypatch.setattr(matrix, "safe_scenario_boundary", lambda state, scenario: True)
    rejected(tmp_path, report, "successful trace record")


def test_empty_input_and_eight_empty_artifacts_cannot_pass(tmp_path, source_snapshot):
    assert not matrix.aggregate([])["accepted"]
    result = matrix.aggregate([retained(tmp_path, {}, str(index)) for index in range(8)])
    assert not result["accepted"] and not result["full_fault_matrix"]
    assert result["coverage"]["accepted_count"] == 0


def test_duplicate_and_unknown_scenarios_fail(tmp_path, source_snapshot):
    report = case("cpu-spike", source_snapshot)
    result = matrix.aggregate([retained(tmp_path, report, "first"), retained(tmp_path, report, "second")])
    assert not result["accepted"] and "Duplicate canonical scenario" in str(result["errors"])
    report["scenario"] = "made-up"
    rejected(tmp_path, report, "Unknown canonical scenario")


@pytest.mark.parametrize("contract", ["kind-live-fault-acceptance-v1", "kind-live-pool-acceptance-v2", "kind-live-pool-acceptance-v1", None])
def test_historical_pool_contracts_are_never_upgraded(tmp_path, source_snapshot, contract):
    report = case("db-pool-exhaustion", source_snapshot)
    report["contract"] = contract
    rejected(tmp_path, report, "Unsupported report contract")


@pytest.mark.parametrize("mutation", ["missing", "false", "integer", "unknown"])
def test_all_required_checks_must_be_present_and_exact_booleans(tmp_path, source_snapshot, mutation):
    report = case("cpu-spike", source_snapshot)
    if mutation == "missing":
        del report["checks"]["checkpoint_state_exact"]
    elif mutation == "unknown":
        report["checks"]["invented_success"] = True
    else:
        report["checks"]["checkpoint_state_exact"] = False if mutation == "false" else 1
    rejected(tmp_path, report, "Required checks")


@pytest.mark.parametrize("counter", ["model_calls", "production_actions"])
@pytest.mark.parametrize("value", [False, True, 0.0, "0", None, 1])
def test_missing_or_boolean_or_nonzero_counters_do_not_mean_zero(tmp_path, source_snapshot, counter, value):
    report = case("cpu-spike", source_snapshot)
    if value is None:
        del report[counter]
    else:
        report[counter] = value
    rejected(tmp_path, report, counter)


@pytest.mark.parametrize("mutation", ["missing", "changed", "extra", "empty"])
def test_exact_current_source_set_and_values_are_required(tmp_path, source_snapshot, mutation):
    report = case("cpu-spike", source_snapshot)
    if mutation == "missing":
        del report["source_sha256"][matrix.CORPUS]
    elif mutation == "changed":
        report["source_sha256"][matrix.CORPUS] = "0" * 64
    elif mutation == "extra":
        report["source_sha256"]["deleted-source.py"] = "0" * 64
    else:
        report["source_sha256"] = {}
    rejected(tmp_path, report, "source fingerprints")


def test_worktree_deletion_and_change_during_aggregation_fail_closed(tmp_path, source_snapshot, monkeypatch):
    report = case("cpu-spike", source_snapshot)
    deleted = dict(source_snapshot)
    del deleted["services/orchestrator/incident_copilot/approval.py"]
    monkeypatch.setattr(matrix, "current_source_sha256", lambda: deleted)
    rejected(tmp_path, report, "source fingerprints")
    responses = iter([source_snapshot, deleted])
    monkeypatch.setattr(matrix, "current_source_sha256", lambda: next(responses))
    rejected(tmp_path, report, "changed during aggregation")


def test_source_helper_discovers_deleted_added_and_changed_files(tmp_path):
    helper = matrix.current_source_sha256
    original = helper()
    for relative in original:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    assert helper(tmp_path) == original
    removed = "services/orchestrator/incident_copilot/approval.py"
    (tmp_path / removed).unlink()
    assert set(helper(tmp_path)) == set(original) - {removed}
    added = "services/orchestrator/incident_copilot/new-module.py"
    (tmp_path / added).write_text("fixture")
    assert set(helper(tmp_path)) == set(original) - {removed} | {added}
    (tmp_path / matrix.CORPUS).write_text("fixture drift")
    assert helper(tmp_path)[matrix.CORPUS] != original[matrix.CORPUS]


def test_missing_fixed_source_file_fails_without_report_discovery(tmp_path, monkeypatch):
    def missing():
        raise FileNotFoundError("fixture")
    monkeypatch.setattr(matrix, "current_source_sha256", missing)
    result = matrix.aggregate([tmp_path / "never-read.json"])
    assert not result["accepted"] and not result["full_fault_matrix"]
    assert result["cases"] == []
    assert "Cannot fingerprint" in str(result["errors"])


def test_cross_case_image_drift_is_rejected(tmp_path, source_snapshot):
    first, second = case("cpu-spike", source_snapshot), case("memory-leak", source_snapshot)
    second["images"]["orchestrator"] = "sha256:" + "0" * 64
    result = matrix.aggregate([retained(tmp_path, first, "one"), retained(tmp_path, second, "two")])
    assert not result["accepted"] and "Image IDs differ" in str(result["errors"])


@pytest.mark.parametrize("mutation", ["classification", "service", "no_approval_key", "approval", "action", "execution"])
def test_recomputed_diagnosis_and_safety_reject_altered_state(tmp_path, source_snapshot, mutation):
    report = case("cpu-spike", source_snapshot)
    def change(state):
        if mutation == "classification":
            state["incident_type"] = "retry_storm"
        elif mutation == "service":
            state["services"] = ["payment-service"]
        elif mutation == "no_approval_key":
            del state["approval_decision"]
        elif mutation == "approval":
            state["approval_decision"] = "approve"
        elif mutation == "action":
            state["remediation"]["tool"] = "rollback_release"
        else:
            state["trace"].append({"step": "execute_or_simulate", "payload": {"executed": False}})
    synchronized_diagnosis(report, change)
    rejected(tmp_path, report, {"classification": "classification", "service": "service identification",
                                "no_approval_key": "approval decision", "approval": "approval decision",
                                "action": "Unsafe remediation", "execution": "Execution"}[mutation])


@pytest.mark.parametrize("kind", ["metric", "log", "trace"])
@pytest.mark.parametrize("mutation", ["empty", "neighbor", "malformed"])
def test_nonempty_primary_telemetry_cannot_be_inferred_from_envelopes_or_neighbors(tmp_path, source_snapshot, kind, mutation):
    report = case("cpu-spike", source_snapshot)
    def change(state):
        item = next(item for item in state["evidence"] if item["kind"] == kind)
        field = {"metric": "series", "log": "events", "trace": "traces"}[kind]
        if mutation == "neighbor":
            item["service"] = "payment-service"
        else:
            item["attributes"][field] = [] if mutation == "empty" else [{"arbitrary": "not telemetry"}]
    synchronized_diagnosis(report, change)
    rejected(tmp_path, report, "payload for primary service")


@pytest.mark.parametrize("mutation", ["state", "id", "read_only", "boundary", "interrupt", "new_uid", "old_uids"])
def test_checkpoint_identity_boundary_and_new_process_are_recomputed(tmp_path, source_snapshot, mutation):
    report = case("cpu-spike", source_snapshot)
    after = report["checkpoint_after_restart"]
    if mutation == "state":
        after["state"]["incident_type"] = "retry_storm"
    elif mutation == "id":
        after["checkpoint_id"] = "different-checkpoint"
    elif mutation == "read_only":
        after["read_only"] = 1
    elif mutation == "boundary":
        after["next"] = []
    elif mutation == "interrupt":
        after["interrupts"][0]["value"]["arguments"]["namespace"] = "other"
    elif mutation == "new_uid":
        report["post_restart_api_pod"]["uid"] = "old-uid"
    else:
        report["pre_restart_api_pod_uids"] = []
    rejected(tmp_path, report, {"state": "Checkpoint state", "id": "checkpoint recovery drift",
                                "read_only": "read-only graph checkpoint", "boundary": "flow boundary",
                                "interrupt": "flow boundary", "new_uid": "replacement API Pod",
                                "old_uids": "original API Pod"}[mutation])


def test_checkpoint_json_equality_does_not_treat_false_as_zero(tmp_path, source_snapshot):
    report = case("memory-leak", source_snapshot)
    report["checkpoint_before_restart"]["state"]["remediation"]["dry_run_passed"] = 0
    report["checkpoint_after_restart"] = copy.deepcopy(report["checkpoint_before_restart"])
    rejected(tmp_path, report, "Checkpoint state differs")


@pytest.mark.parametrize("mutation", ["request", "trace", "span", "pool", "search", "dependency", "checks"])
def test_pool_case_requires_real_controlled_correlation_not_green_flags(tmp_path, source_snapshot, mutation):
    report = case("db-pool-exhaustion", source_snapshot)
    probe = report["failure_correlation"]
    if mutation == "request":
        probe["events"][0]["request_id"] = "other"
    elif mutation == "trace":
        probe["calls"][0]["trace_id"] = "b" * 32
    elif mutation == "span":
        probe["events"][0]["span_id"] = "4" * 16
    elif mutation == "pool":
        probe["events"][1]["active"] = 3
    elif mutation == "search":
        probe["traces"][0]["trace_id"] = "b" * 32
    elif mutation == "dependency":
        report["controlled_failure"]["response"]["body"]["downstream"]["status"] = "ok"
    else:
        del report["checks"]["controlled_failure_parentage"]
    result = rejected(tmp_path, report, "")
    assert result["coverage"]["accepted_count"] == 0


@pytest.mark.parametrize("mutation", ["bad_span", "float_counter", "different_call_log"])
def test_pool_correlation_cannot_join_valid_shape_with_unrelated_invalid_identity(tmp_path, source_snapshot, mutation):
    report = case("db-pool-exhaustion", source_snapshot)
    probe = report["failure_correlation"]
    if mutation == "bad_span":
        probe["calls"][0]["client_span_id"] = True
    elif mutation == "float_counter":
        probe["events"][1]["active"] = 4.0
        unrelated = copy.deepcopy(probe["events"][1])
        unrelated.update(active=4, level="INFO")
        probe["events"].append(unrelated)
    else:
        invalid_call = copy.deepcopy(probe["calls"][0])
        invalid_call.update(client_span_id=True, server_span_id="4" * 16)
        probe["calls"].append(invalid_call)
        probe["events"][0]["span_id"] = "4" * 16
    rejected(tmp_path, report, "")


@pytest.mark.parametrize("field,value", [("cleanup_complete", False), ("ok", 1), ("last_phase", "diagnose"),
                                        ("full_fault_matrix", True), ("controlled_log_span_correlation_attempted", None)])
def test_incomplete_or_misrepresented_report_is_rejected(tmp_path, source_snapshot, field, value):
    report = case("cpu-spike", source_snapshot)
    report[field] = value
    rejected(tmp_path, report, "")


@pytest.mark.parametrize("text", ['{} trailing', '{"contract":1,"contract":2}', '{"value":NaN}',
                                 '{"value":1e999}', '[]', '[' * 2000 + ']' * 2000])
def test_malformed_and_ambiguous_json_cannot_pass(tmp_path, source_snapshot, text):
    path = tmp_path / "malformed.json"
    path.write_text(text)
    result = matrix.aggregate([path])
    assert not result["accepted"] and not result["full_fault_matrix"]


def test_cli_output_is_explicit_exclusive_and_cannot_overwrite_inputs(tmp_path, source_snapshot, capsys):
    path = retained(tmp_path, case("cpu-spike", source_snapshot))
    output = tmp_path / "new-matrix.json"
    assert matrix.main([str(path), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["accepted"]
    assert json.loads(capsys.readouterr().out)["report"] == str(output.resolve())
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        matrix.main([str(path), "--output", str(output)])
    assert output.read_bytes() == before
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        matrix.main([str(path), "--output", str(path)])
    assert path.read_bytes() == before


def test_cli_without_output_is_offline_stdout_only(tmp_path, source_snapshot, capsys):
    path = retained(tmp_path, case("cpu-spike", source_snapshot))
    before = set(tmp_path.iterdir())
    assert matrix.main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"]
    assert set(tmp_path.iterdir()) == before
