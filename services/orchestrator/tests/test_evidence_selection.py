from incident_copilot.incident_profiles import PROFILES
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import Evidence, IncidentState
from datetime import UTC, datetime
import pytest


def observation(id, kind="metric", query=None, score=0.95, service="payment-service", attributes=None):
    return Evidence(id=id, kind=kind, source="fixture", title=id,
                    observation="Observed signal", observed_at=datetime.now(UTC),
                    service=service, score=score, query=query, attributes=attributes or {})


def state(profile, evidence):
    return IncidentState(question="Why is payment-service slow?", environment="test",
                         services=["payment-service"], incident_type=profile, evidence=evidence)


@pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda profile: profile.key)
def test_profile_metric_beats_high_score_unrelated_signal(profile):
    items = [observation("decoy", query="unrelated_metric", score=1),
             observation("symptom", query=profile.metrics[1], score=0.99),
             observation("specific", query=profile.metrics[0], score=0.3)]
    selected = LangGraphIncidentWorkflow._supporting_evidence(state(profile.key, items))
    assert selected[0].id == "specific"


def test_invalid_profile_metric_is_not_cited_and_secondary_metric_can_be_used():
    items = [observation("empty_pool", query="db_pool_active_ratio", attributes={"series": []}),
             observation("latency", query="request_latency_p95")]
    selected = LangGraphIncidentWorkflow._supporting_evidence(state("database_connection_pool_exhaustion", items))
    assert [item.id for item in selected] == ["latency"]


def test_primary_service_excludes_unrelated_service_signals():
    items = [observation("other", query="db_pool_active_ratio", service="other-service", score=1),
             observation("primary", query="request_latency_p95"),
             observation("other_log", kind="log", service="other-service")]
    selected = LangGraphIncidentWorkflow._supporting_evidence(state("database_connection_pool_exhaustion", items))
    assert [item.id for item in selected] == ["primary"]


def test_log_signature_in_retained_events_beats_generic_higher_score_log():
    items = [observation("generic", kind="log", score=1),
             observation("pool_log", kind="log", score=0.5, attributes={"events": [{"message": "connection acquisition timed out"}]})]
    selected = LangGraphIncidentWorkflow._supporting_evidence(state("database_connection_pool_exhaustion", items))
    assert selected[0].id == "pool_log"


def test_deployment_regression_keeps_deploy_in_leading_four_before_trace():
    items = [observation("metric", query="request_error_rate"), observation("log", kind="log"),
             observation("trace", kind="trace", attributes={"traces": [{"trace_id": "t1"}]}),
             observation("runbook", kind="runbook"), observation("history", kind="past_incident"),
             observation("deployment", kind="deployment")]
    selected = LangGraphIncidentWorkflow._supporting_evidence(state("deployment_regression", items))
    assert [item.id for item in selected[:4]] == ["metric", "log", "deployment", "runbook"]


def test_equal_scores_are_stable_across_input_order():
    items = [observation("metric_a", query="db_pool_active_ratio"), observation("metric_b", query="db_pool_active_ratio")]
    for evidence in (items, list(reversed(items))):
        assert LangGraphIncidentWorkflow._supporting_evidence(state("database_connection_pool_exhaustion", evidence))[0].id == "metric_b"
