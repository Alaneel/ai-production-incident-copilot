import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import verify_agent_scenarios as verifier  # noqa: E402


def incident():
    return {"incident_id": "INC-VERIFIER", "services": ["payment-service"],
            "evidence": [], "hypotheses": [], "remediation": None,
            "status": "insufficient_evidence", "incident_type": None, "trace": []}


def test_abstention_is_failed_measurement_not_verifier_crash(monkeypatch):
    monkeypatch.setattr(verifier, "request_json", lambda *args, **kwargs: incident())
    result = verifier.diagnose("db-pool-exhaustion")
    assert result["passed"] is False
    assert result["leading_cause"] is None
    assert result["confidence"] is None
    assert result["action"] is None
    assert result["checks"]["cause"] is False
    assert result["checks"]["status"] is False


def test_dependency_metrics_cannot_overwrite_primary_signal_report(monkeypatch):
    state = incident()
    state["evidence"] = [
        {"id": "primary", "service": "payment-service", "kind": "metric", "source": "test",
         "query": "request_latency_p95", "attributes": {"series": [{"value": 100}]}},
        {"id": "dependency", "service": "checkout-api", "kind": "metric", "source": "test",
         "query": "request_latency_p95", "attributes": {"series": [{"value": 9000}]}},
    ]
    state["service_impacts"] = [{"service": "checkout-api", "status": "observed_anomaly"}]
    monkeypatch.setattr(verifier, "request_json", lambda *args, **kwargs: state)
    result = verifier.diagnose("db-pool-exhaustion")
    assert result["metric_signals"]["request_latency_p95"]["latest"] == 100
    assert result["metric_queries"] == ["request_latency_p95"]
    assert result["service_impacts"] == state["service_impacts"]


def test_competing_candidates_and_query_failures_survive_failed_live_measurement(monkeypatch):
    state = incident()
    state["candidate_incident_types"] = ["cpu_saturation", "database_connection_pool_exhaustion"]
    state["trace"] = [{"event": "tool_error", "payload": {"tool": "search_logs", "error_type": "TimeoutError"}}]
    monkeypatch.setattr(verifier, "request_json", lambda *args, **kwargs: state)
    result = verifier.diagnose("db-pool-exhaustion")
    assert result["candidate_incident_types"] == state["candidate_incident_types"]
    assert result["query_failures"][0]["error_type"] == "TimeoutError"
    assert result["passed"] is False


def test_required_verifier_uses_separate_scopes_and_bound_actor_without_reporting_tokens(monkeypatch):
    read = "reader-" + "r" * 32
    operator = "operator-" + "o" * 32
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "required")
    monkeypatch.setenv("ORCHESTRATOR_READ_TOKEN", read)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_TOKEN", operator)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_ACTOR", "bound-verifier")
    state = incident()
    state.update(status="waiting_approval", incident_type="database_connection_pool_exhaustion",
                 hypotheses=[{"cause": "Database connection pool exhaustion", "confidence": 0.8}],
                 remediation={"tool": "rollback_release"})
    calls = []
    def request(method, url, **kwargs):
        calls.append(kwargs)
        return state if len(calls) == 1 else {"status": "rejected", "trace": []}
    monkeypatch.setattr(verifier, "request_json", request)
    result = verifier.diagnose("db-pool-exhaustion")
    assert calls[0]["headers"]["Authorization"] == f"Bearer {read}"
    assert calls[1]["headers"]["Authorization"] == f"Bearer {operator}"
    assert calls[1]["body"]["actor"] == "bound-verifier"
    assert calls[1]["body"]["decision"] == "reject"
    assert read not in str(result) and operator not in str(result)


def test_missing_required_credentials_stop_before_fault_injection(monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "required")
    monkeypatch.delenv("ORCHESTRATOR_READ_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["verify_agent_scenarios.py"])
    calls = []
    monkeypatch.setattr(verifier, "inject", lambda *args: calls.append(args))
    assert verifier.main() == 2
    assert calls == []
    assert "ORCHESTRATOR_READ_TOKEN" in capsys.readouterr().err


def test_verifier_error_reports_never_echo_transport_response_secrets(monkeypatch, capsys):
    secret = "private-token-" + "s" * 32
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    monkeypatch.setattr(sys, "argv", ["verify_agent_scenarios.py"])
    monkeypatch.setattr(verifier, "SCENARIOS", {"db-pool-exhaustion": {"service": "payment-service"}})
    monkeypatch.setattr(verifier, "inject", lambda *args: None)
    monkeypatch.setattr(verifier, "clear", lambda *args: None)
    monkeypatch.setattr(verifier.time, "sleep", lambda *args: None)
    def fail(*args):
        raise RuntimeError(f"response echoed Authorization: Bearer {secret}")
    monkeypatch.setattr(verifier, "exercise", fail)
    assert verifier.main() == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output
