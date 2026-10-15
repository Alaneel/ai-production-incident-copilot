import copy
from datetime import UTC, datetime, timedelta

import pytest

from incident_copilot.models import Evidence, IncidentState, Remediation
from incident_copilot.recovery_verification import assess_recovery


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def fixture():
    context = {"tool": "restart_deployment", "service": "payment-service", "namespace": "incident-copilot",
               "cluster_context": "a" * 64, "deployment_uid": "observed-deployment-uid", "resource_version": "18",
               "deployment_template_sha256": "b" * 64, "revision": None, "replicaset_uid": None,
               "rollback_template_sha256": None}
    incident = IncidentState(
        question="Why is payment-service slow?", environment="incident-copilot", services=["payment-service"],
        remediation=Remediation(title="Restart deployment", tool="restart_deployment",
            arguments={"service": "payment-service", "namespace": "incident-copilot", "target_context": context},
            command_preview="kubectl -n incident-copilot rollout restart deploy/payment-service",
            risk="high", requires_approval=True, dry_run_passed=True, preflight_mode="kubernetes-server-dry-run",
            expected_result="Observe rollout and SLIs without assuming efficacy."),
        recovery={"simulation": False, "execution_acknowledged_at": (NOW - timedelta(seconds=90)).isoformat(),
                  "applied_generation": 3, "applied_template_sha256": "c" * 64},
    )
    health = {"source": "kubernetes_deployment_health", "read_only": True, "marker_status": "marker_matches",
              "cluster_context": context["cluster_context"], "deployment_uid": context["deployment_uid"],
              "service": context["service"], "namespace": context["namespace"],
              "generation": 3, "template_sha256": "c" * 64, "observed_generation": 3,
              "desired_replicas": 2, "total_replicas": 2, "updated_replicas": 2, "ready_replicas": 2, "available_replicas": 2,
              "unavailable_replicas": 0, "observed_at": (NOW - timedelta(seconds=5)).isoformat()}
    metrics = [metric(name) for name in ("request_latency_p95", "request_error_rate")]
    return incident, health, metrics


def metric(name, *, offsets=(50, 35, 20), value=None):
    if value is None:
        value = 750.0 if name == "request_latency_p95" else 0.01
    return Evidence(kind="metric", source="prometheus-via-telemetry-gateway", title=name, observation="Observed samples",
                    observed_at=NOW - timedelta(seconds=10), service="payment-service", score=0.95, query=name,
                    attributes={"data_origin": "prometheus_api", "unit": "milliseconds" if name == "request_latency_p95" else "ratio",
                                "discarded_samples": 0, "series": [
                                    {"timestamp": (NOW - timedelta(seconds=offset)).isoformat(), "value": value}
                                    for offset in offsets]})


def pending(incident, health, metrics, expected):
    result = assess_recovery(incident, health, metrics, now=NOW)
    assert result["status"] == "pending" and expected in result["reason_codes"]
    assert result["observation_window"] is None
    assert result["checked_at"] == NOW.isoformat()
    assert len(result["metric_evidence_ids"]) <= 32
    return result


def test_exact_applied_rollout_and_shared_sli_window_verify_without_claiming_causation():
    incident, health, metrics = fixture()
    before = copy.deepcopy((incident, health, metrics))
    result = assess_recovery(incident, health, metrics, now=NOW)
    assert result == {"status": "verified", "checked_at": NOW.isoformat(),
                      "reason_codes": ["rollout_and_sli_recovery_observed_not_causal_proof"],
                      "metric_evidence_ids": [item.id for item in metrics],
                      "observation_window": [(NOW - timedelta(seconds=50)).isoformat(), (NOW - timedelta(seconds=20)).isoformat()]}
    assert (incident, health, metrics) == before
    # The post-PATCH template differs from preflight because the operation
    # marker/restart annotation is part of the applied template identity.
    assert incident.remediation.arguments["target_context"]["deployment_template_sha256"] != health["template_sha256"]


@pytest.mark.parametrize("change,reason", [
    ({"simulation": True}, "simulation_or_unknown_backend"),
    ({"simulation": 0}, "simulation_or_unknown_backend"),
    ({"simulation": None}, "simulation_or_unknown_backend"),
    ({"execution_acknowledged_at": None}, "execution_acknowledgement_invalid"),
    ({"execution_acknowledged_at": "2026-10-07T12:00:00"}, "execution_acknowledgement_invalid"),
    ({"execution_acknowledged_at": (NOW + timedelta(seconds=1)).isoformat()}, "execution_acknowledgement_invalid"),
    ({"execution_acknowledged_at": "x" * 65}, "execution_acknowledgement_invalid"),
    ({"applied_generation": None}, "applied_target_identity_missing"),
    ({"applied_generation": True}, "applied_target_identity_missing"),
    ({"applied_generation": 0}, "applied_target_identity_missing"),
    ({"applied_generation": "3"}, "applied_target_identity_missing"),
    ({"applied_template_sha256": None}, "applied_target_identity_missing"),
    ({"applied_template_sha256": "C" * 64}, "applied_target_identity_missing"),
])
def test_execution_acknowledgement_and_applied_identity_fail_closed(change, reason):
    incident, health, metrics = fixture()
    incident.recovery.update(change)
    pending(incident, health, metrics, reason)


def test_legacy_incident_without_recovery_record_is_pending():
    incident, health, metrics = fixture()
    incident.recovery = None
    pending(incident, health, metrics, "recovery_state_missing")


@pytest.mark.parametrize("change", [None, {}, {"extra": "not a normalized context"}, {"deployment_uid": None},
                                    {"resource_version": 18}, {"revision": "141"}])
def test_missing_or_malformed_approved_context_is_pending(change):
    incident, health, metrics = fixture()
    incident.remediation.arguments["target_context"] = change if change is None or not change else {
        **incident.remediation.arguments["target_context"], **change}
    pending(incident, health, metrics, "approved_target_context_invalid")


@pytest.mark.parametrize("change", [{"service": "other-service"}, {"namespace": "other-namespace"},
                                    {"tool": "rollback_release", "revision": "141", "replicaset_uid": "retained-rs-uid",
                                     "rollback_template_sha256": "d" * 64}])
def test_context_scope_must_still_match_approved_remediation(change):
    incident, health, metrics = fixture()
    incident.remediation.arguments["target_context"].update(change)
    pending(incident, health, metrics, "approved_target_scope_mismatch")


@pytest.mark.parametrize("change", [{"source": "fixture"}, {"read_only": False}, {"read_only": 1},
                                    {"marker_status": "not_observed"}, {"marker_status": "unverified_marker"}])
def test_provider_claim_of_healthy_cannot_replace_readonly_bound_marker_observation(change):
    incident, health, metrics = fixture()
    health.update({"healthy": True, **change})
    pending(incident, health, metrics, "deployment_observation_untrusted")


@pytest.mark.parametrize("change", [{"cluster_context": "d" * 64}, {"deployment_uid": "replacement-deployment-uid"},
                                    {"service": "other-service"}, {"namespace": "other-namespace"}])
def test_rollout_health_for_another_target_is_not_recovery(change):
    incident, health, metrics = fixture()
    health.update(change)
    pending(incident, health, metrics, "deployment_target_mismatch")


@pytest.mark.parametrize("change", [{"generation": 4}, {"generation": True}, {"generation": "3"},
                                    {"generation": 0}, {"template_sha256": "b" * 64}, {"template_sha256": None}])
def test_superseded_or_unknown_applied_template_and_generation_are_not_recovery(change):
    incident, health, metrics = fixture()
    health.update(change)
    pending(incident, health, metrics, "deployment_applied_target_mismatch")


@pytest.mark.parametrize("change", [{"observed_generation": 2}, {"observed_generation": True},
                                    {"desired_replicas": 0}, {"desired_replicas": True}, {"updated_replicas": 1},
                                    {"ready_replicas": 1}, {"available_replicas": 3}, {"unavailable_replicas": 1},
                                    {"unavailable_replicas": False}, {"ready_replicas": None}, {"total_replicas": 3},
                                    {"total_replicas": 1}, {"total_replicas": 0}, {"total_replicas": True},
                                    {"total_replicas": None}, {"total_replicas": "2"}])
def test_full_controller_observed_rollout_is_required(change):
    incident, health, metrics = fixture()
    health.update(change)
    pending(incident, health, metrics, "deployment_rollout_incomplete")


@pytest.mark.parametrize("observed_at", [None, "2026-10-07T12:00:00", (NOW + timedelta(seconds=1)).isoformat(),
                                        (NOW - timedelta(seconds=31)).isoformat(), (NOW - timedelta(seconds=90)).isoformat()])
def test_health_observation_must_be_after_acknowledgement_and_current(observed_at):
    incident, health, metrics = fixture()
    health["observed_at"] = observed_at
    pending(incident, health, metrics, "deployment_observation_stale_or_invalid")


@pytest.mark.parametrize("attribute,value", [("source", "prometheus"), ("source", "synthetic-prometheus"),
                                            ("service", "another-service"), ("kind", "log")])
def test_wrong_metric_source_service_or_kind_cannot_verify(attribute, value):
    incident, health, metrics = fixture()
    metrics[0] = metrics[0].model_copy(update={attribute: value}) if attribute != "kind" else Evidence.model_validate({
        **metrics[0].model_dump(), attribute: value})
    pending(incident, health, metrics, "metric_observation_untrusted")


@pytest.mark.parametrize("origin", [None, "synthetic", "unknown", True, "prometheus"])
def test_source_string_alone_is_not_live_prometheus_origin(origin):
    incident, health, metrics = fixture()
    metrics[0].attributes["data_origin"] = origin
    pending(incident, health, metrics, "metric_observation_untrusted")


@pytest.mark.parametrize("index,unit", [(0, "seconds"), (0, None), (1, "percent"), (1, "milliseconds")])
def test_sli_units_must_be_exact(index, unit):
    incident, health, metrics = fixture()
    metrics[index].attributes["unit"] = unit
    pending(incident, health, metrics, "metric_units_mismatch")


@pytest.mark.parametrize("value", [True, None, "750", -1, float("nan"), float("inf"), 10**400])
def test_bad_values_inside_fixed_window_are_not_silently_dropped(value):
    incident, health, metrics = fixture()
    metrics[0].attributes["series"].append({"timestamp": (NOW - timedelta(seconds=10)).isoformat(), "value": value})
    pending(incident, health, metrics, "metric_sample_invalid")


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-10-07T11:59:50", "x" * 65])
def test_bad_timestamp_cannot_prove_a_safe_window(timestamp):
    incident, health, metrics = fixture()
    metrics[0].attributes["series"].append({"timestamp": timestamp, "value": 500})
    pending(incident, health, metrics, "metric_sample_invalid")


def test_future_metric_sample_is_not_dropped_as_outside_observation_window():
    incident, health, metrics = fixture()
    metrics[0].attributes["series"].append({"timestamp": (NOW + timedelta(seconds=1)).isoformat(), "value": 500})
    pending(incident, health, metrics, "metric_sample_future")


@pytest.mark.parametrize("index,value", [(0, 1000.01), (1, 0.050001)])
def test_any_abnormal_sample_inside_fixed_window_prevents_cherry_picking_green_tail(index, value):
    incident, health, metrics = fixture()
    metrics[index].attributes["series"].insert(0, {"timestamp": (NOW - timedelta(seconds=59)).isoformat(), "value": value})
    pending(incident, health, metrics, "metric_threshold_failed")


def test_error_ratio_outside_domain_is_invalid_not_a_healthy_threshold_sample():
    incident, health, metrics = fixture()
    metrics[1].attributes["series"][0]["value"] = 1.1
    pending(incident, health, metrics, "metric_sample_invalid")


@pytest.mark.parametrize("across_rows", [False, True])
def test_conflicting_duplicate_timestamp_inside_window_prevents_verification(across_rows):
    incident, health, metrics = fixture()
    duplicate = copy.deepcopy(metrics[0].attributes["series"][0])
    duplicate["value"] = 800
    if across_rows:
        another = metric("request_latency_p95")
        another.attributes["series"] = [duplicate]
        metrics.append(another)
    else:
        metrics[0].attributes["series"].append(duplicate)
    pending(incident, health, metrics, "metric_sample_conflict")


@pytest.mark.parametrize("discarded", [1, True, "0", -1, None])
def test_client_discarded_samples_cannot_hide_invalid_or_conflicting_input(discarded):
    incident, health, metrics = fixture()
    metrics[0].attributes["discarded_samples"] = discarded
    pending(incident, health, metrics, "metric_discarded_samples")


@pytest.mark.parametrize("series,reason", [(None, "metric_series_missing_or_invalid"), ([], "metric_samples_missing"),
                                         ([{"timestamp": (NOW - timedelta(seconds=20)).isoformat(), "value": 700}] * 3, "metric_samples_missing")])
def test_three_copies_of_one_sample_or_missing_series_are_not_three_independent_samples(series, reason):
    incident, health, metrics = fixture()
    metrics[0].attributes["series"] = series
    pending(incident, health, metrics, reason)


@pytest.mark.parametrize("offsets,reason", [((29, 14, 0), "metric_window_insufficient"),
                                          ((60, 15, 0), "metric_window_insufficient"),
                                          ((60, 45, 31), "metric_window_stale")])
def test_sustained_contiguous_current_window_is_required(offsets, reason):
    incident, health, metrics = fixture()
    metrics[0] = metric("request_latency_p95", offsets=offsets)
    pending(incident, health, metrics, reason)


def test_individually_healthy_nonoverlapping_metric_windows_are_not_joint_recovery():
    incident, health, metrics = fixture()
    metrics[0] = metric("request_latency_p95", offsets=(60, 45, 30))
    metrics[1] = metric("request_error_rate", offsets=(45, 30, 15))
    pending(incident, health, metrics, "metric_shared_window_insufficient")


def test_acknowledgement_boundary_samples_are_not_post_execution_observations():
    incident, health, metrics = fixture()
    incident.recovery["execution_acknowledged_at"] = (NOW - timedelta(seconds=50)).isoformat()
    pending(incident, health, metrics, "metric_samples_missing")


def test_old_post_ack_failures_and_pre_ack_history_do_not_block_current_fixed_healthy_window():
    incident, health, metrics = fixture()
    for item in metrics:
        item.attributes["series"][:0] = [
            {"timestamp": (NOW - timedelta(seconds=200)).isoformat(), "value": -100},
            {"timestamp": (NOW - timedelta(seconds=70)).isoformat(), "value": 2000},
        ]
    assert assess_recovery(incident, health, metrics, now=NOW)["status"] == "verified"


def test_exact_sli_thresholds_and_maximum_gap_are_accepted():
    incident, health, metrics = fixture()
    health["observed_at"] = (NOW - timedelta(seconds=30)).isoformat()
    metrics = [metric("request_latency_p95", offsets=(60, 30, 0), value=1000),
               metric("request_error_rate", offsets=(60, 30, 0), value=0.05)]
    assert assess_recovery(incident, health, metrics, now=NOW)["status"] == "verified"


def test_provider_healthy_flag_is_not_needed_when_raw_rollout_and_sli_observations_are_verified():
    incident, health, metrics = fixture()
    health["healthy"] = False
    assert assess_recovery(incident, health, metrics, now=NOW)["status"] == "verified"


def test_evidence_and_series_parsing_budgets_fail_closed():
    incident, health, metrics = fixture()
    pending(incident, health, metrics * 17, "metric_evidence_budget_exceeded")
    metrics[0].attributes["series"] *= 683
    pending(incident, health, metrics, "metric_series_budget_exceeded")


def test_invalid_metric_envelope_and_duplicate_evidence_ids_do_not_verify():
    incident, health, metrics = fixture()
    pending(incident, health, [*metrics, object()], "metric_observation_invalid")
    pending(incident, health, [*metrics, metrics[0]], "metric_evidence_identity_invalid")


@pytest.mark.parametrize("observed_at", [NOW.replace(tzinfo=None), NOW + timedelta(seconds=1), NOW - timedelta(seconds=31)])
def test_metric_envelope_must_also_be_aware_and_fresh(observed_at):
    incident, health, metrics = fixture()
    metrics[0].observed_at = observed_at
    pending(incident, health, metrics, "metric_observation_stale_or_invalid")


def test_naive_verification_clock_is_a_programming_error_never_success():
    incident, health, metrics = fixture()
    with pytest.raises(ValueError, match="aware clock"):
        assess_recovery(incident, health, metrics, now=NOW.replace(tzinfo=None))
