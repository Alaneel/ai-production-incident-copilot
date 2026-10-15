"""Pure cross-service attribution tests: no tools, models or database calls."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from incident_copilot.cross_service_rca import assess_root_candidates, dependency_paths
from incident_copilot.dependency_topology import (
    DependencyEdge,
    ObservedCall,
    ObservedDependencies,
    ServiceTopology,
    legacy_topology,
)
from incident_copilot.models import Evidence, IncidentState

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
COMPLAINT = "payment-service"
DOWNSTREAM = "ledger-service"


def catalog(service=COMPLAINT, edges=None):
    return ServiceTopology(
        service=service, source="service-catalog", catalog_status="available",
        topology_kind="static_fixture_catalog",
        edges=[DependencyEdge(source=source, target=target)
               for source, target in (edges if edges is not None else [(COMPLAINT, DOWNSTREAM)])],
    )


def observed(service=COMPLAINT, edges=None, *, age=30):
    calls = []
    for index, (source, target) in enumerate(edges if edges is not None else [(COMPLAINT, DOWNSTREAM)]):
        digest = sha256(f"{source}:{target}:{index}".encode()).hexdigest()
        calls.append(ObservedCall(source=source, target=target, trace_id=digest[:32],
                                  client_span_id=digest[32:48], server_span_id=digest[48:64],
                                  observed_at=NOW - timedelta(seconds=age)))
    return ServiceTopology(service=service, observed=ObservedDependencies(
        status="available", source="tempo_client_server_parentage", calls=calls,
        start=NOW - timedelta(minutes=5), end=NOW, coverage="sampled_not_exhaustive",
        traces_read=len(calls), traces_failed=0, calls_omitted=0,
    ))


def metric(service, name, values, *, ages=None, evidence_id=None, score=0.9):
    ages = ages if ages is not None else list(range(len(values) * 15, 0, -15))
    return Evidence(id=evidence_id or f"{service}:{name}", kind="metric", source="prometheus-via-telemetry-gateway",
                    service=service, title=name, observation="retained timestamped samples",
                    observed_at=NOW, score=score, query=name,
                    attributes={"series": [{"timestamp": (NOW - timedelta(seconds=age)).isoformat(), "value": value}
                                           for age, value in zip(ages, values)]})


def log(service, message, *, age=30, evidence_id=None):
    return Evidence(id=evidence_id or f"{service}:logs", kind="log", source="loki", service=service,
                    title="application logs", observation="timestamped event", observed_at=NOW, score=0.9,
                    attributes={"events": [{"timestamp": (NOW - timedelta(seconds=age)).isoformat(), "message": message}]})


def state(*evidence, topologies=None, complaint=COMPLAINT):
    return IncidentState(question="Why is payment-service slow?", environment="prod-sg",
                         complaint_service=complaint, services=[complaint, DOWNSTREAM],
                         service_topologies=topologies if topologies is not None else [observed()],
                         evidence=list(evidence))


def symptoms(*, service=COMPLAINT, ages=None):
    return metric(service, "request_latency_p95", [2200, 2300, 2400], ages=ages)


def remote(*evidence, topologies=None):
    return [candidate for candidate in assess_root_candidates(state(symptoms(), *evidence, topologies=topologies), now=NOW)
            if candidate.service != COMPLAINT]


def test_observed_direction_retains_truthful_parentage_not_causation():
    topology = observed()
    paths = dependency_paths(COMPLAINT, [topology], now=NOW)
    assert paths[COMPLAINT] == {"propagation_path": [COMPLAINT], "path_basis": "primary"}
    assert paths[DOWNSTREAM]["propagation_path"] == [COMPLAINT, DOWNSTREAM]
    assert paths[DOWNSTREAM]["path_basis"] == "observed_trace"
    expected = topology.observed.calls[0].model_dump(mode="json")
    expected["observed_at"] = topology.observed.calls[0].observed_at.isoformat()
    assert paths[DOWNSTREAM]["observed_calls"] == [expected]


def test_catalog_and_mixed_paths_do_not_upgrade_provenance():
    paths = dependency_paths(COMPLAINT, [catalog(), observed(DOWNSTREAM, [(DOWNSTREAM, "database-service")])], now=NOW)
    assert paths[DOWNSTREAM]["path_basis"] == "catalog"
    assert paths["database-service"]["path_basis"] == "mixed"
    assert paths["database-service"]["propagation_path"] == [COMPLAINT, DOWNSTREAM, "database-service"]


def test_explicit_two_hop_path_requires_second_topology_and_stops_before_third_hop():
    topologies = [catalog(), catalog(DOWNSTREAM, [(DOWNSTREAM, "database-service")]),
                  catalog("database-service", [("database-service", "storage-service")])]
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW)) == {COMPLAINT, DOWNSTREAM, "database-service"}
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW, max_hops=1)) == {COMPLAINT, DOWNSTREAM}
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW, max_hops=0)) == {COMPLAINT}


def test_observed_path_preferred_to_shorter_catalog_path_deterministically():
    topologies = [catalog(COMPLAINT, [(COMPLAINT, "database-service")]), observed(),
                  observed(DOWNSTREAM, [(DOWNSTREAM, "database-service")])]
    for sequence in (topologies, list(reversed(topologies)), [*topologies, *topologies]):
        path = dependency_paths(COMPLAINT, sequence, now=NOW)["database-service"]
        assert path["propagation_path"] == [COMPLAINT, DOWNSTREAM, "database-service"]
        assert path["path_basis"] == "observed_trace"


@pytest.mark.parametrize("topologies", [
    [legacy_topology(COMPLAINT, [DOWNSTREAM])],
    [catalog(DOWNSTREAM, [(DOWNSTREAM, COMPLAINT)])],
    [observed(DOWNSTREAM, [(DOWNSTREAM, COMPLAINT)])],
    [catalog(COMPLAINT, [("unrelated-service", DOWNSTREAM)])],
    [catalog().model_copy(update={"source": "unknown"})],
    [catalog().model_copy(update={"catalog_status": "unknown"})],
    [catalog().model_copy(update={"catalog_status": "unavailable"})],
])
def test_unknown_unrelated_and_incoming_relations_cannot_create_downstream_paths(topologies):
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW)) == {COMPLAINT}


@pytest.mark.parametrize("topologies", [
    [catalog(COMPLAINT, [(COMPLAINT, DOWNSTREAM), (DOWNSTREAM, COMPLAINT)])],
    [observed(), catalog(DOWNSTREAM, [(DOWNSTREAM, COMPLAINT)])],
    [catalog(), catalog(DOWNSTREAM, [(DOWNSTREAM, "database-service")]),
     catalog("database-service", [("database-service", COMPLAINT)])],
])
def test_bidirectional_or_cyclic_paths_are_not_attributed(topologies):
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW)) == {COMPLAINT}


def test_remote_feedback_loop_does_not_remove_independent_acyclic_branch():
    topologies = [catalog(COMPLAINT, [(COMPLAINT, DOWNSTREAM), (COMPLAINT, "risk-service")]),
                  catalog(DOWNSTREAM, [(DOWNSTREAM, "database-service")]),
                  catalog("database-service", [("database-service", DOWNSTREAM)])]
    assert set(dependency_paths(COMPLAINT, topologies, now=NOW)) == {COMPLAINT, "risk-service"}


@pytest.mark.parametrize("age", [301, -1, 3600])
def test_observed_calls_are_current_and_not_future(age):
    assert set(dependency_paths(COMPLAINT, [observed(age=age)], now=NOW)) == {COMPLAINT}


@pytest.mark.parametrize("update", [
    {"status": "unavailable"}, {"source": None}, {"coverage": None},
    {"traces_read": 0}, {"traces_read": True}, {"traces_read": 9},
    {"traces_failed": 1}, {"calls_omitted": -1}, {"calls_discarded": -1},
    {"start": NOW.replace(tzinfo=None)}, {"end": NOW + timedelta(seconds=10)},
    {"start": NOW - timedelta(minutes=31)}, {"end": NOW - timedelta(minutes=6)},
])
def test_malformed_observed_envelope_does_not_supply_paths(update):
    topology = observed()
    topology.observed = topology.observed.model_copy(update=update)
    assert set(dependency_paths(COMPLAINT, [topology], now=NOW)) == {COMPLAINT}


@pytest.mark.parametrize("update", [
    {"trace_id": "0" * 32}, {"trace_id": "a"}, {"client_span_id": "0" * 16},
    {"observed_at": NOW.replace(tzinfo=None)},
    {"source": "unrelated-service"}, {"source": "../secret"}, {"target": COMPLAINT},
])
def test_malformed_call_identity_or_scope_is_not_used(update):
    topology = observed()
    topology.observed.calls = [topology.observed.calls[0].model_copy(update=update)]
    assert set(dependency_paths(COMPLAINT, [topology], now=NOW)) == {COMPLAINT}


def test_duplicate_span_identity_is_ambiguous_not_a_second_observation():
    topology = observed()
    topology.observed.calls *= 2
    assert set(dependency_paths(COMPLAINT, [topology], now=NOW)) == {COMPLAINT}


def test_client_and_server_span_ids_must_differ():
    topology = observed()
    call = topology.observed.calls[0]
    topology.observed.calls = [call.model_copy(update={"server_span_id": call.client_span_id})]
    assert set(dependency_paths(COMPLAINT, [topology], now=NOW)) == {COMPLAINT}


def test_consistent_endpoint_views_deduplicate_one_physical_call():
    first = observed()
    second = observed(DOWNSTREAM)
    assert dependency_paths(COMPLAINT, [first, second], now=NOW)[DOWNSTREAM]["path_basis"] == "observed_trace"
    assert len(dependency_paths(COMPLAINT, [first, second], now=NOW)[DOWNSTREAM]["observed_calls"]) == 1


@pytest.mark.parametrize("update", [{"observed_at": NOW - timedelta(seconds=45)}, {"target": "risk-service"}])
def test_conflicting_endpoint_views_for_one_span_identity_are_not_guessed(update):
    first = observed()
    second = observed()
    second.observed.calls = [second.observed.calls[0].model_copy(update=update)]
    assert set(dependency_paths(COMPLAINT, [first, second], now=NOW)) == {COMPLAINT}
    assert set(dependency_paths(COMPLAINT, [second, first], now=NOW)) == {COMPLAINT}


def test_conflicting_span_views_may_not_upgrade_an_independent_catalog_path():
    first = observed()
    second = observed(age=45)
    path = dependency_paths(COMPLAINT, [first, second, catalog()], now=NOW)[DOWNSTREAM]
    assert path == {"propagation_path": [COMPLAINT, DOWNSTREAM], "path_basis": "catalog"}


@pytest.mark.parametrize("max_hops", [-1, 3, 100, True, None, "2"])
def test_hop_budget_cannot_be_coerced_or_expanded(max_hops):
    with pytest.raises(ValueError):
        dependency_paths(COMPLAINT, [catalog()], now=NOW, max_hops=max_hops)


def test_oversized_topology_container_fails_closed():
    assert set(dependency_paths(COMPLAINT, [catalog()] * 33, now=NOW)) == {COMPLAINT}
    assert set(dependency_paths(COMPLAINT, [catalog(COMPLAINT, [(COMPLAINT, f"service-{index}") for index in range(65)])], now=NOW)) == {COMPLAINT}


def test_root_candidate_is_service_scoped_and_never_mutates_incident():
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.95, 0.97])
    incident = state(symptoms(), cpu)
    before = incident.model_dump_json()
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.service == DOWNSTREAM
    assert candidate.incident_type == "cpu_saturation"
    assert candidate.local_evidence_ids == [cpu.id]
    assert candidate.symptom_evidence_ids == [symptoms().id]
    assert candidate.temporal_overlap is True
    assert candidate.causal_direction_verified is False
    assert candidate.path_basis == "observed_trace"
    assert candidate.propagation_path == [COMPLAINT, DOWNSTREAM]
    assert any("not failure propagation" in note for note in candidate.validation_notes)
    assert incident.model_dump_json() == before
    assert incident.remediation is None and incident.diagnosis_service is None


@pytest.mark.parametrize("profile,evidence", [
    ("database_connection_pool_exhaustion", metric(DOWNSTREAM, "db_pool_active_ratio", [0.95, 0.99])),
    ("database_connection_pool_exhaustion", log(DOWNSTREAM, "connection acquisition timed out")),
    ("cpu_saturation", metric(DOWNSTREAM, "cpu_utilization", [0.9, 0.91])),
    ("memory_leak", metric(DOWNSTREAM, "memory_working_set", [1000000, 2200000, 4000000])),
    ("deployment_regression", log(DOWNSTREAM, "invalid ledger response shape")),
    ("kafka_consumer_lag", metric(DOWNSTREAM, "kafka_consumer_lag", [200, 300])),
    ("retry_storm", metric(DOWNSTREAM, "retry_rate", [4, 5])),
    ("cache_penetration", metric(DOWNSTREAM, "cache_miss_rate", [4, 5])),
])
def test_each_specific_local_fault_can_be_a_reachable_candidate(profile, evidence):
    candidate, = remote(evidence)
    assert candidate.incident_type == profile
    assert candidate.local_evidence_ids == [evidence.id]


def test_downstream_timeout_alone_is_a_symptom_not_remote_origin():
    assert remote(log(DOWNSTREAM, "downstream request timed out")) == []
    local_log = log(COMPLAINT, "downstream request timed out")
    candidate, = assess_root_candidates(state(local_log), now=NOW)
    assert candidate.service == COMPLAINT
    assert candidate.incident_type == "downstream_timeout"
    assert candidate.path_basis == "primary"
    assert candidate.temporal_overlap is None


def test_primary_specific_fault_does_not_require_cross_service_sli_window():
    cpu = metric(COMPLAINT, "cpu_utilization", [0.9, 0.92])
    candidate, = assess_root_candidates(state(cpu), now=NOW)
    assert candidate.service == COMPLAINT and candidate.incident_type == "cpu_saturation"
    assert candidate.local_evidence_ids == [cpu.id]
    assert candidate.symptom_evidence_ids == []


@pytest.mark.parametrize("complaint_evidence", [
    [], [metric(COMPLAINT, "request_latency_p95", [200, 300])],
    [metric(COMPLAINT, "request_latency_p95", [2200])],
    [metric(COMPLAINT, "request_latency_p95", [2200, 300])],
    [metric(COMPLAINT, "request_latency_p95", [2200, 2300], ages=[400, 350])],
    [metric(COMPLAINT, "request_latency_p95", [2200, 2300], ages=[-2, -1])],
    [log(COMPLAINT, "downstream request timed out")],
])
def test_remote_origin_requires_current_sustained_complaint_sli(complaint_evidence):
    incident = state(*complaint_evidence, metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.93]))
    assert all(candidate.service == COMPLAINT for candidate in assess_root_candidates(incident, now=NOW))


def test_error_ratio_can_supply_complaint_sustained_symptom_window():
    error = metric(COMPLAINT, "request_error_rate", [0.1, 0.2])
    candidate, = assess_root_candidates(state(error, metric(DOWNSTREAM, "cpu_utilization", [0.9, 0.95])), now=NOW)
    assert candidate.symptom_evidence_ids == [error.id]


@pytest.mark.parametrize("evidence", [
    metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.92], ages=[290, 270]),
    metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.92], ages=[400, 350]),
    metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.92], ages=[-2, -1]),
    log(DOWNSTREAM, "invalid ledger response shape", age=290),
])
def test_nonoverlapping_stale_future_local_signal_cannot_be_remote_candidate(evidence):
    assert remote(evidence) == []


def test_old_anomaly_cannot_supply_overlap_after_metric_recovers():
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [0.95, 0.95, 0.1, 0.1])) == []
    assert remote(metric(DOWNSTREAM, "memory_working_set", [5000000, 5000000, 5000000])) == []


@pytest.mark.parametrize("value", [True, False, None, "0.99", "bad", float("nan"), float("inf"), -1, 2])
def test_invalid_cpu_values_do_not_supply_specific_fault(value):
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [value, value])) == []


def test_same_metric_in_different_services_cannot_overwrite_or_leak_into_primary():
    incident = state(symptoms(), metric(COMPLAINT, "cpu_utilization", [0.1, 0.2]),
                     metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    candidates = assess_root_candidates(incident, now=NOW)
    assert [(candidate.service, candidate.incident_type) for candidate in candidates] == [(DOWNSTREAM, "cpu_saturation")]
    incident.evidence.reverse()
    assert assess_root_candidates(incident, now=NOW) == candidates


def test_local_evidence_ids_exclude_normal_metrics_unrelated_logs_and_documents():
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.9, 0.91])
    incident = state(symptoms(), cpu, metric(DOWNSTREAM, "db_pool_active_ratio", [0.1, 0.2]),
                     metric(DOWNSTREAM, "request_latency_p95", [300, 400]),
                     log(DOWNSTREAM, "request completed"))
    incident.evidence.append(Evidence(id="doc", kind="runbook", service=DOWNSTREAM, source="docs",
                                     title="CPU", observation="cpu saturation", observed_at=NOW, score=1))
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.local_evidence_ids == [cpu.id]
    assert candidate.symptom_evidence_ids == [symptoms().id]


def test_profile_contribution_ids_are_profile_specific():
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94])
    pool = metric(DOWNSTREAM, "db_pool_active_ratio", [0.95, 0.96])
    pool_log = log(DOWNSTREAM, "acquisition timed out", evidence_id="pool-log")
    candidates = remote(cpu, pool, pool_log)
    by_profile = {candidate.incident_type: candidate for candidate in candidates}
    assert by_profile["cpu_saturation"].local_evidence_ids == [cpu.id]
    assert by_profile["database_connection_pool_exhaustion"].local_evidence_ids == sorted([pool.id, pool_log.id])


def test_two_real_evidence_items_can_contribute_independent_points_to_one_signal():
    first = metric(DOWNSTREAM, "cpu_utilization", [0.91], ages=[30], evidence_id="cpu-1")
    second = metric(DOWNSTREAM, "cpu_utilization", [0.94], ages=[15], evidence_id="cpu-2")
    candidate, = remote(first, second)
    assert candidate.local_evidence_ids == ["cpu-1", "cpu-2"]


def test_conflicting_duplicate_samples_fail_closed_instead_of_cherry_picking():
    first = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94, 0.95], evidence_id="cpu-1")
    conflict = metric(DOWNSTREAM, "cpu_utilization", [0.1], ages=[15], evidence_id="cpu-2")
    assert remote(first, conflict) == []
    assert remote(conflict, first) == []


def test_duplicate_evidence_id_is_not_accepted_for_either_service():
    symptom = symptoms()
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.9, 0.91], evidence_id=symptom.id)
    assert assess_root_candidates(state(symptom, cpu), now=NOW) == []


def test_excluded_foreign_response_cannot_be_candidate_even_when_service_is_reachable():
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94])
    incident = state(symptoms(), cpu)
    incident.diagnostic_excluded_evidence_ids = [cpu.id]
    assert assess_root_candidates(incident, now=NOW) == []
    assert cpu.id in {item.id for item in incident.evidence}


def test_excluded_complaint_response_cannot_supply_cross_service_symptom_window():
    symptom = symptoms()
    incident = state(symptom, metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    incident.diagnostic_excluded_evidence_ids = [symptom.id]
    assert assess_root_candidates(incident, now=NOW) == []


def test_excluded_ids_do_not_hide_independent_queried_valid_signal():
    rejected = metric(DOWNSTREAM, "cpu_utilization", [0.1, 0.2], evidence_id="foreign-response")
    queried = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94], evidence_id="queried-signal")
    incident = state(symptoms(), rejected, queried)
    incident.diagnostic_excluded_evidence_ids = [rejected.id]
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.local_evidence_ids == [queried.id]
    assert rejected.id not in candidate.local_evidence_ids


@pytest.mark.parametrize("topologies", [
    [], [legacy_topology(COMPLAINT, [DOWNSTREAM])],
    [catalog(COMPLAINT, [("checkout-api", COMPLAINT)])],
    [catalog(COMPLAINT, [(COMPLAINT, DOWNSTREAM), (DOWNSTREAM, COMPLAINT)])],
])
def test_specific_fault_without_unambiguous_directed_path_is_not_remote_candidate(topologies):
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]), topologies=topologies) == []


def test_catalog_candidate_is_explicitly_limited_not_a_causal_upgrade():
    candidate, = remote(metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]), topologies=[catalog()])
    assert candidate.path_basis == "catalog"
    assert candidate.causal_direction_verified is False
    assert any("static catalog" in note for note in candidate.validation_notes)


def test_two_hop_specific_fault_is_retained_but_three_hop_fault_is_not():
    topologies = [observed(), catalog(DOWNSTREAM, [(DOWNSTREAM, "database-service")]),
                  catalog("database-service", [("database-service", "storage-service")])]
    incident = state(symptoms(), metric("database-service", "db_pool_active_ratio", [0.95, 0.97]),
                     metric("storage-service", "cpu_utilization", [0.91, 0.94]), topologies=topologies)
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.service == "database-service"
    assert candidate.propagation_path == [COMPLAINT, DOWNSTREAM, "database-service"]
    assert candidate.path_basis == "mixed"
    assert candidate.causal_direction_verified is False
    assert any("static catalog" in note for note in candidate.validation_notes)


def test_only_generic_latency_and_error_signals_do_not_identify_remote_origin():
    assert remote(metric(DOWNSTREAM, "request_latency_p95", [2200, 2300]),
                  metric(DOWNSTREAM, "request_error_rate", [0.2, 0.3])) == []


def test_duplicated_equal_time_points_are_not_sustained_independent_samples():
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.91], ages=[15, 15])) == []
    complaint = metric(COMPLAINT, "request_latency_p95", [2200, 2200], ages=[15, 15])
    assert assess_root_candidates(state(complaint, metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94])), now=NOW) == []


def test_same_value_duplicate_time_is_deduplicated_without_fabricated_sample_count():
    first = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94], evidence_id="cpu-1")
    duplicate = metric(DOWNSTREAM, "cpu_utilization", [0.94], ages=[15], evidence_id="cpu-2")
    candidate, = remote(first, duplicate)
    assert candidate.local_evidence_ids == ["cpu-1", "cpu-2"]


def test_nonoverlap_cannot_be_repaired_by_using_historical_anomaly_window_field():
    incident = state(symptoms(), metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94], ages=[290, 270]))
    incident.anomalous_window = (NOW - timedelta(seconds=300), NOW)
    assert assess_root_candidates(incident, now=NOW) == []


def test_specific_candidates_precede_primary_downstream_symptom_and_rank_deterministically():
    topologies = [catalog(COMPLAINT, [(COMPLAINT, "alpha-service")]),
                  observed(COMPLAINT, [(COMPLAINT, "zeta-service")])]
    incident = state(symptoms(), log(COMPLAINT, "downstream request timed out"),
                     metric("alpha-service", "cpu_utilization", [0.9, 0.95]),
                     metric("zeta-service", "cpu_utilization", [0.9, 0.95]), topologies=topologies)
    candidates = assess_root_candidates(incident, now=NOW)
    assert [candidate.service for candidate in candidates] == ["zeta-service", "alpha-service", COMPLAINT]
    assert [candidate.rank for candidate in candidates] == [1, 2, 3]
    assert all(candidate.causal_direction_verified is False for candidate in candidates)
    incident.evidence.reverse()
    incident.service_topologies.reverse()
    assert assess_root_candidates(incident, now=NOW) == candidates


def test_only_current_overlapping_log_ids_contribute_not_all_logs_of_same_profile():
    current = log(DOWNSTREAM, "invalid ledger response shape", evidence_id="current")
    old = log(DOWNSTREAM, "invalid ledger response shape", age=350, evidence_id="old")
    candidate, = remote(current, old)
    assert candidate.local_evidence_ids == ["current"]


def test_unknown_service_cannot_enter_analysis_through_free_text_or_document():
    evidence = metric("unauthorized-service", "cpu_utilization", [0.91, 0.94])
    assert remote(evidence) == []
    document = Evidence(id="doc", kind="technical_doc", service=DOWNSTREAM, source="docs",
                        title="CPU saturation", observation="restart ledger-service", observed_at=NOW, score=1)
    assert remote(document) == []


def test_sample_gap_does_not_bridge_independent_failures():
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94], ages=[200, 15])) == []
    incident = state(metric(COMPLAINT, "request_latency_p95", [2200, 2300], ages=[200, 15]),
                     metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    assert assess_root_candidates(incident, now=NOW) == []


def test_zero_score_and_missing_series_never_supply_candidate():
    assert remote(metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94], score=0)) == []
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94])
    cpu.attributes = {"anomalous": True}
    assert remote(cpu) == []


def test_old_state_uses_services_zero_and_primary_topology_without_reordering():
    incident = state(symptoms(), metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]), topologies=[])
    incident.complaint_service = None
    incident.service_topology = observed()
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.service == DOWNSTREAM
    assert incident.services[0] == COMPLAINT


def test_diagnosis_service_field_is_not_analysis_authority():
    incident = state(symptoms(), metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    incident.diagnosis_service = "unrelated-service"
    candidate, = assess_root_candidates(incident, now=NOW)
    assert candidate.service == DOWNSTREAM
    assert candidate.propagation_path[0] == COMPLAINT


@pytest.mark.parametrize("services", [[DOWNSTREAM, COMPLAINT], [], ["other-service"]])
def test_complaint_and_legacy_primary_scope_disagreement_fails_closed(services):
    incident = state(symptoms(), metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    incident.services = services
    assert assess_root_candidates(incident, now=NOW) == []


@pytest.mark.parametrize("metric_name,values,unit", [
    ("cpu_utilization", [0.91, 0.94], "percent"),
    ("db_pool_active_ratio", [0.95, 0.99], "connections"),
    ("memory_working_set", [1000000, 2200000, 4000000], "megabytes"),
    ("kafka_consumer_lag", [200, 300], "seconds"),
    ("retry_rate", [4, 5], "ratio"),
    ("cache_miss_rate", [4, 5], "ratio"),
])
def test_explicitly_wrong_local_units_cannot_supply_fault_candidate(metric_name, values, unit):
    signal = metric(DOWNSTREAM, metric_name, values)
    signal.attributes["unit"] = unit
    assert remote(signal) == []


def test_wrong_complaint_units_do_not_supply_symptom_window():
    symptom = symptoms()
    symptom.attributes["unit"] = "seconds"
    assert assess_root_candidates(state(symptom, metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94])), now=NOW) == []


def test_cache_miss_rate_is_events_per_second_not_a_ratio():
    signal = metric(DOWNSTREAM, "cache_miss_rate", [8, 9])
    signal.attributes["unit"] = "misses_per_second"
    candidate, = remote(signal)
    assert candidate.incident_type == "cache_penetration"


def test_observed_path_remains_available_when_catalog_status_is_unknown():
    topology = observed()
    topology.source = "service-catalog"
    topology.catalog_status = "unknown"
    assert dependency_paths(COMPLAINT, [topology], now=NOW)[DOWNSTREAM]["path_basis"] == "observed_trace"


def test_oversized_evidence_and_sample_budgets_fail_closed():
    cpu = metric(DOWNSTREAM, "cpu_utilization", [0.91] * 2049, ages=[15] * 2049)
    assert remote(cpu) == []
    incident = state(symptoms(), metric(DOWNSTREAM, "cpu_utilization", [0.91, 0.94]))
    incident.evidence.extend(log(DOWNSTREAM, "normal", evidence_id=f"log-{index}") for index in range(511))
    assert assess_root_candidates(incident, now=NOW) == []


@pytest.mark.parametrize("now", [NOW.replace(tzinfo=None), None, "2026-10-07"])
def test_internal_clock_must_be_aware(now):
    with pytest.raises(ValueError):
        dependency_paths(COMPLAINT, [], now=now)
    with pytest.raises(ValueError):
        assess_root_candidates(state(symptoms()), now=now)
