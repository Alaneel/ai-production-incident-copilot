"""Actual compiled workflow tests over labels-isolated synthetic snapshots."""
import asyncio
import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from run_cross_service_benchmark import (  # noqa: E402
    CONTRACT, MultiServiceSnapshotTools, fingerprints, fixtures, run_case, save_report, score_record, summarize,
)
from incident_copilot.dependency_topology import ServiceTopology  # noqa: E402
from incident_copilot.mcp_client import McpToolError  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402


def test_fixtures_are_independent_structures_and_truth_is_only_in_scorer_envelope():
    cases = fixtures()
    assert len(cases) >= 24
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["structure"] for case in cases}) == len(cases)
    assert all("expectations" not in case["input"] and "ground_truth" not in json.dumps(case["input"]) for case in cases)
    families = {pair[1] for case in cases for pair in case["expectations"]["remote_candidate_pairs"]}
    assert families == {"cpu_saturation", "database_connection_pool_exhaustion", "memory_leak", "deployment_regression",
                        "kafka_consumer_lag", "retry_storm", "cache_penetration"}
    assert any(len(case["expectations"]["remote_candidate_pairs"]) > 1 for case in cases)
    assert sum(not case["expectations"]["remote_candidate_pairs"] for case in cases) >= 12


@pytest.mark.parametrize("case", fixtures(), ids=lambda case: case["id"])
def test_compiled_multiservice_workflow_contract_and_independent_localization(case):
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["ok"], {"case_id": case["id"], "failed": [key for key, value in record["checks"].items() if not value],
                          "predicted": record["predicted_remote_pairs"], "error": record["error_type"]}
    assert record["mutation_attempts"] == record["model_calls"] == record["new_api_cost_usd"] == 0
    assert record["state"]["approval_decision"] is None and record["state"]["remediation"]["tool"] == "none"
    assert not any(call["tool"] in {"restart_deployment", "rollback_release"} for call in record["tool_calls"])
    assert all(item["attributes"]["data_origin"] == "synthetic" for item in record["state"]["evidence"])
    assert record["tool_calls"] and record["state"]["trace"]
    assert "approval_receipt" not in record["state"]


def test_provider_is_multiservice_strict_typed_and_has_no_expected_labels():
    async def scenario():
        case = next(case for case in fixtures() if case["id"] == "direct-cpu_saturation")
        tools = MultiServiceSnapshotTools(case["input"], KnowledgeIndex.load_default())
        assert not any(key.startswith(("expected", "ground_truth")) for key in vars(tools))
        topology = await tools.get_service_topology("risk-engine")
        assert isinstance(topology, ServiceTopology) and topology.service == "risk-engine"
        assert topology.source == "service-catalog-via-telemetry-gateway"
        assert topology.topology_kind == "static_fixture_catalog"
        assert all("risk-engine" in (edge.source, edge.target) for edge in topology.edges)
        assert topology.observed.status == "available" and topology.observed.calls
        evidence = await tools.query_metrics("risk-engine", ("cpu_utilization",))
        assert evidence[0].service == "risk-engine" and evidence[0].attributes["series"][-1]["value"] == 0.95
        with pytest.raises(McpToolError, match="outside"):
            await tools.query_metrics("not-in-fixture", ("cpu_utilization",))
        with pytest.raises(McpToolError, match="bounded"):
            await tools.query_metrics("risk-engine", ("unbounded_metric",))
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_provider_forbids_even_destructive_preflight(tool, dry_run):
    tools = MultiServiceSnapshotTools(fixtures()[0]["input"], KnowledgeIndex.load_default())
    with pytest.raises(RuntimeError, match="forbids destructive preflight"):
        asyncio.run(getattr(tools, tool)(service="risk-engine", dry_run=dry_run))
    assert tools.mutation_attempts == 1
    assert tools.calls[-1]["tool"] == tool and tools.calls[-1]["error_type"] == "RuntimeError"


def test_ground_truth_change_cannot_change_workflow_candidate_output():
    original = fixtures()[0]
    changed = deepcopy(original)
    changed["expectations"].update(ground_truth_fault_service="ledger-db", ground_truth_fault_family="memory_leak",
                                  remote_candidate_pairs=[["ledger-db", "memory_leak"]], diagnosis_service="ledger-db")
    first = asyncio.run(run_case(original, KnowledgeIndex.load_default()))
    second = asyncio.run(run_case(changed, KnowledgeIndex.load_default()))
    assert first["predicted_remote_pairs"] == second["predicted_remote_pairs"]
    assert first["state"]["diagnosis_service"] == second["state"]["diagnosis_service"] == "risk-engine"
    assert first["state"]["incident_type"] == second["state"]["incident_type"]
    assert first["ok"] and not second["ok"]


@pytest.mark.parametrize("tamper", ["wrong_path", "local_citation_from_complaint", "no_overlap", "causal_claim", "fake_hypothesis_service"])
def test_scorer_independently_rejects_bad_attribution_not_only_green_state_flags(tamper):
    case = fixtures()[0]
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["ok"]
    candidate = next(item for item in record["state"]["root_cause_candidates"] if item["service"] == "risk-engine")
    if tamper == "wrong_path":
        candidate["propagation_path"] = ["risk-engine", "payment-service"]
    elif tamper == "local_citation_from_complaint":
        candidate["local_evidence_ids"] = candidate["symptom_evidence_ids"]
    elif tamper == "no_overlap":
        candidate["temporal_overlap"] = False
    elif tamper == "causal_claim":
        candidate["causal_direction_verified"] = True
    else:
        record["state"]["hypotheses"][0]["cause_service"] = "not-a-candidate"
    score = score_record(case, record)
    assert not score["ok"]


@pytest.mark.parametrize("tamper", ["stale_local", "future_local", "disjoint_local", "stale_symptom",
                                   "healthy_local", "healthy_symptom", "wrong_unit", "boolean_value",
                                   "nonfinite_value", "naive_time", "sparse_last_two", "false_path_basis",
                                   "duplicate_evidence_id", "excluded_citation"])
def test_independent_scorer_rejects_counterfeit_support_even_when_candidate_flags_stay_true(tamper):
    case = fixtures()[0]
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["ok"]
    candidate = next(item for item in record["state"]["root_cause_candidates"] if item["service"] == "risk-engine")
    evidence = {item["id"]: item for item in record["state"]["evidence"]}
    local = evidence[candidate["local_evidence_ids"][0]]
    symptom = evidence[candidate["symptom_evidence_ids"][0]]
    if tamper in {"stale_local", "future_local", "disjoint_local", "stale_symptom"}:
        selected = symptom if tamper == "stale_symptom" else local
        delta = {"stale_local": -900, "future_local": 120, "disjoint_local": -180, "stale_symptom": -900}[tamper]
        for point in selected["attributes"]["series"]:
            point["timestamp"] = (datetime.fromisoformat(point["timestamp"]) + timedelta(seconds=delta)).isoformat()
    elif tamper in {"healthy_local", "healthy_symptom", "boolean_value", "nonfinite_value"}:
        selected = symptom if tamper == "healthy_symptom" else local
        value = {"healthy_local": 0.1, "healthy_symptom": 100, "boolean_value": True, "nonfinite_value": float("nan")}[tamper]
        for point in selected["attributes"]["series"]:
            point["value"] = value
    elif tamper == "wrong_unit":
        local["attributes"]["unit"] = "bytes"
    elif tamper == "naive_time":
        for point in local["attributes"]["series"]:
            point["timestamp"] = datetime.fromisoformat(point["timestamp"]).replace(tzinfo=None).isoformat()
    elif tamper == "sparse_last_two":
        anchor = datetime.fromisoformat(record["snapshot_anchor"])
        local["attributes"]["series"] = [{"timestamp": (anchor - timedelta(seconds=offset)).isoformat(), "value": 0.95}
                                         for offset in (240, 0)]
    elif tamper == "false_path_basis":
        candidate["path_basis"] = "catalog"  # This fixture includes current sampled parentage on every hop.
    elif tamper == "duplicate_evidence_id":
        record["state"]["evidence"].append(deepcopy(local))
    else:
        record["state"]["diagnostic_excluded_evidence_ids"] = [local["id"]]
    assert candidate["temporal_overlap"] is True and candidate["causal_direction_verified"] is False
    scored = score_record(case, record)
    assert not scored["ok"]
    assert not all(scored["checks"][name] for name in (
        "retained_local_and_symptom_support_overlap", "path_basis_matches_independent_observations", "retained_evidence_ids_unique"))


@pytest.mark.parametrize("tamper", ["signature_removed", "stale_event", "future_event"])
def test_log_based_candidate_requires_retained_current_signature_not_an_observation_summary(tamper):
    case = next(case for case in fixtures() if case["id"] == "direct-deployment_regression")
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["ok"]
    candidate = next(item for item in record["state"]["root_cause_candidates"] if item["service"] == "payment-service")
    cited = next(item for item in record["state"]["evidence"] if item["id"] in candidate["local_evidence_ids"])
    assert cited["kind"] == "log"
    event = cited["attributes"]["events"][0]
    if tamper == "signature_removed":
        event["message"] = "request completed"
    else:
        event["timestamp"] = (datetime.fromisoformat(event["timestamp"])
                              + timedelta(seconds=-900 if tamper == "stale_event" else 120)).isoformat()
    # Keep the summary and purported overlap untouched: only retained events
    # are admissible support, not the workflow's pre-computed green flags.
    assert candidate["temporal_overlap"] is True
    scored = score_record(case, record)
    assert not scored["ok"] and not scored["checks"]["retained_local_and_symptom_support_overlap"]


def test_report_is_exclusive_uuid_bound_and_covers_all_source_packages_and_corpus(tmp_path):
    sources = fingerprints()
    assert "evals/run_cross_service_benchmark.py" in sources
    assert "services/orchestrator/incident_copilot/cross_service_rca.py" in sources
    assert "services/mcp-server/incident_mcp/knowledge_corpus.json" in sources
    for package in ("services/orchestrator/incident_copilot", "services/mcp-server/incident_mcp"):
        for path in (ROOT / package).rglob("*.py"):
            assert sources[str(path.relative_to(ROOT))] == hashlib.sha256(path.read_bytes()).hexdigest()
    records = [{"case_id": "one", "state": {"trace": []}, "tool_calls": []}]
    summary, cases_path = save_report(records, {"contract": CONTRACT}, tmp_path, run_id="test-exclusive-run")
    report = json.loads(summary.read_text())
    assert report["case_records_sha256"] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    assert report["case_records_file"] == cases_path.name
    original = (summary.read_bytes(), cases_path.read_bytes())
    with pytest.raises(FileExistsError):
        save_report([], {}, tmp_path, run_id="test-exclusive-run")
    assert original == (summary.read_bytes(), cases_path.read_bytes())
    with pytest.raises(ValueError):
        save_report(records, {}, tmp_path, run_id="../overwrite")
    next_summary, _ = save_report(records, {}, tmp_path)
    assert next_summary != summary


def test_summary_metrics_cannot_be_read_as_model_causal_accuracy_or_faithfulness():
    case = fixtures()[0]
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    result = summarize([case], [record], fingerprints())
    assert result["metric_scope"] == "synthetic candidate service localization, not model causal accuracy or faithfulness"
    assert result["model_calls"] == result["new_api_cost_usd"] == result["mutation_attempts"] == 0
    assert all(result[key] is None for key in ("live_root_cause_accuracy", "model_causal_accuracy", "semantic_faithfulness", "causal_action_efficacy"))
    assert result["metric_case_counts"] == {"service_family_top1": 1, "service_family_top3": 1,
                                            "unique_diagnosis_service": 1, "negative_remote_abstention": 0,
                                            "all_contract_checks": 1}
