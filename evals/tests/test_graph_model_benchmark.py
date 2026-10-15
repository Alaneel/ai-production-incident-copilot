import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

import run_graph_model_benchmark as benchmark  # noqa: E402
from incident_copilot.models import Hypothesis  # noqa: E402
from incident_copilot.guardrails import has_usable_observation  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from incident_copilot.reasoning import OpenAIResponsesHypothesisGenerator, ModelGenerationError  # noqa: E402


@pytest.mark.parametrize("case_id", ["eval-001", "healthy-00", "healthy-01", "healthy-02",
                                     "no_data-00", "no_data-01", "no_data-02"])
def test_real_responses_adapter_abstains_through_actual_graph_without_actions(case_id):
    async def run():
        _, cases = benchmark.plan(150)
        case = next(case for case in cases if case["id"] == case_id)
        requests = []

        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            schema = payload["text"]["format"]["schema"]
            assert "hypotheses" in schema["required"]
            assert schema["properties"]["hypotheses"].get("minItems", 0) == 0
            assert schema["properties"]["hypotheses"]["maxItems"] == 3
            assert "Return hypotheses=[]" in payload["input"][0]["content"][0]["text"]
            return httpx.Response(200, json={"status": "completed", "id": "offline-response",
                                            "output_text": '{"hypotheses": []}',
                                            "usage": {"total_tokens": 12}})

        async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
                                    transport=httpx.MockTransport(handler)) as client:
            generator = OpenAIResponsesHypothesisGenerator(api_key="fake", model="local-test", client=client,
                                                          max_attempts=1)
            record = await benchmark.evaluate_case(case, KnowledgeIndex.load_default(), generator)
        assert record["ok"]
        assert record["initial_status"] == record["final_status"] == "insufficient_evidence"
        assert record["execution_attempts"] == 0
        assert record["state"]["approval_decision"] is None
        assert record["state"]["hypotheses"] == []
        assert record["state"]["remediation"]["tool"] == "none"
        if case_id.startswith("no_data-"):
            # The current graph already abstains before calling a model when
            # no observed signals exist; preserve this stronger safety gate.
            assert len(requests) == record["model_requests_attempted"] == 0
            assert record["model_usage"] == []
            assert any(event["event"] == "diagnosis_abstained" for event in record["state"]["trace"])
            return
        assert len(requests) == 1
        assert "additional evidence" in record["state"]["remediation"]["title"]
        assert not record["state"]["remediation"]["dry_run_passed"]
        assert record["model_usage"][0]["abstained"]
        assert record["model_usage"][0]["hypothesis_count"] == 0
        events = [event["event"] for event in record["state"]["trace"]]
        assert "model_hypothesis_abstained" in events
        assert "hypothesis_citations_rejected" not in events
        assert "hypothesis_citations_validated" not in events
    asyncio.run(run())


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output_text": '{"hypotheses": []}'},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
    {"status": "completed"},
])
def test_failed_or_refused_responses_are_not_silently_treated_as_abstentions(response):
    async def run():
        async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
                                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
            generator = OpenAIResponsesHypothesisGenerator(api_key="fake", model="local-test", client=client)
            with pytest.raises(ModelGenerationError):
                await generator.generate(question="Why is payment-service slow?", incident_type=None, evidence=[])
    asyncio.run(run())


class LocalGenerator:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    async def generate(self, **arguments):
        self.calls += 1
        self.inputs.append(arguments)
        observations = arguments["evidence"]
        metric = next(item for item in observations if item.kind == "metric" and has_usable_observation(item))
        deployment = next(item for item in observations if item.kind == "deployment")
        return [Hypothesis(rank=1, cause="Unproven local fixture hypothesis, not a root-cause label",
                           confidence=0.5, evidence_ids=[metric.id, deployment.id],
                           validation_notes=["Requires independent semantic review"])], {
                               "provider": "offline-test", "input_tokens": 10, "output_tokens": 5,
                               "total_tokens": 15,
                           }


def test_plan_round_robins_families_and_does_not_read_credentials(monkeypatch):
    monkeypatch.setattr(benchmark, "dotenv_values", lambda *args: pytest.fail("must not read .env"))
    manifest, cases = benchmark.plan(150)
    assert len(cases) == 150
    assert len(manifest["family_counts"]) == 10
    assert set(manifest["family_counts"].values()) == {15}
    assert len({case["expected_cause"] for case in cases[:8]}) == 8
    assert manifest["maximum_api_requests"] == 150
    assert manifest["estimated_cost_usd"] is None
    assert "configured_model" not in manifest
    assert len(manifest["source_sha256"]) > 20


@pytest.mark.parametrize("limit", [0, -1, 151])
def test_plan_rejects_unbounded_call_counts(limit):
    with pytest.raises(ValueError):
        benchmark.plan(limit)


def test_actual_graph_uses_generator_and_cannot_mislabel_rule_classification_as_model_accuracy():
    _, cases = benchmark.plan(3)
    generator = LocalGenerator()
    record = asyncio.run(benchmark.evaluate_case(cases[0], KnowledgeIndex.load_default(), generator))
    assert record["ok"]
    assert generator.calls == 1
    assert record["model_requests_attempted"] == 1
    # This local generator cites generic latency rather than the pool signal.
    # Citation integrity can pass without supporting the profile's action.
    assert record["initial_status"] == record["final_status"] == "diagnosed"
    assert record["diagnosed_state"]["approval_decision"] is None
    assert record["state"]["approval_decision"] is None
    assert record["state"]["remediation"]["tool"] == "none"
    applicability, = [item["payload"] for item in record["state"]["trace"]
                      if item["event"] == "action_signal_applicability_checked"]
    assert not applicability["allowed"]
    assert record["execution_attempts"] == 0
    assert record["model_root_cause_top1"] is None
    assert record["semantic_faithfulness"] is None
    assert record["model_usage"][0]["total_tokens"] == 15
    assert "Unproven local" in record["state"]["hypotheses"][0]["cause"]
    assert set(generator.inputs[0]) == {"question", "incident_type", "evidence"}
    assert "expected_cause" not in generator.inputs[0]


def test_failure_is_redacted_persisted_and_stops_batch(tmp_path):
    class FailingGenerator:
        calls = 0

        async def generate(self, **arguments):
            self.calls += 1
            raise RuntimeError("secret-key-and-provider-response-must-not-be-retained")

    generator = FailingGenerator()
    manifest, cases = benchmark.plan(3)
    path, summary = asyncio.run(benchmark.execute(manifest, cases, generator, tmp_path))
    assert not summary["ok"]
    assert summary["cases_attempted"] == 1
    assert summary["cases_completed"] == 0
    assert summary["model_requests_attempted"] == generator.calls == 1
    assert summary["failed_case_ids"] == [cases[0]["id"]]
    assert summary["total_tokens"] is None
    assert path.exists()
    case_file = Path(summary["cases_artifact"])
    rows = [json.loads(line) for line in case_file.read_text().splitlines()]
    assert rows[0]["error_type"] == "RuntimeError"
    assert "secret-key" not in case_file.read_text()
    assert "secret-key" not in path.read_text()


def test_success_persists_full_state_without_semantic_score(tmp_path):
    manifest, cases = benchmark.plan(3)
    _, summary = asyncio.run(benchmark.execute(manifest, cases, LocalGenerator(), tmp_path))
    assert summary["ok"]
    assert summary["cases_completed"] == 3
    assert summary["model_requests_attempted"] == 3
    assert summary["actual_cost_usd"] is None
    assert summary["total_tokens"] == 45
    assert summary["mean_successful_snapshot_workflow_latency_ms"] > 0
    assert summary["model_root_cause_top1"] is None
    rows = [json.loads(line) for line in Path(summary["cases_artifact"]).read_text().splitlines()]
    assert all(row["state"]["trace"] for row in rows)
    assert all(row["checkpoint_roundtrip"] for row in rows)


def test_cli_requires_plan_or_explicit_cost_gate():
    result = subprocess.run([sys.executable, str(ROOT / "evals/run_graph_model_benchmark.py")],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--confirm-api-cost" in result.stderr
    assert not result.stdout


def test_generator_refuses_a_second_generation():
    async def run():
        measured = benchmark.MeasuredGenerator(LocalGenerator())
        measured.calls = 1
        with pytest.raises(RuntimeError, match="one generation"):
            await measured.generate()
        assert measured.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("tokens", [None, "15", True, -1])
def test_unknown_or_invalid_usage_cannot_be_reported_as_zero_cost(tokens):
    assert benchmark.measured_total_tokens([
        {"model_requests_attempted": 1, "model_usage": [{"total_tokens": tokens}]}
    ]) is None


def continuation_manifest():
    manifest, cases = benchmark.plan(3)
    manifest.update(configured_model="offline-test", provider_endpoint_sha256="0" * 64)
    return manifest, cases


def test_continuation_skips_all_attempted_cases_including_failures_and_retains_original(tmp_path):
    class Failure:
        async def generate(self, **arguments):
            raise RuntimeError("offline failure")
    manifest, cases = continuation_manifest()
    original_path, original = asyncio.run(benchmark.execute(manifest, cases, Failure(), tmp_path))
    before = original_path.read_bytes()
    retained, lineage = benchmark.load_continuation(original_path, manifest)
    generator = LocalGenerator()
    next_path, resumed = asyncio.run(benchmark.execute(manifest, cases, generator, tmp_path,
                                                      retained=retained, continuation=lineage))
    assert generator.calls == resumed["model_requests_this_invocation"] == 2
    assert resumed["model_requests_attempted"] == 3
    assert resumed["continued_cases"] == 1
    assert resumed["failed_case_ids"] == [cases[0]["id"]]
    assert not resumed["ok"]  # Original failure is not erased by continuation.
    assert original_path.read_bytes() == before
    all_records, lineage = benchmark.load_continuation(next_path, manifest)
    unused = LocalGenerator()
    _, final = asyncio.run(benchmark.execute(manifest, cases, unused, tmp_path,
                                             retained=all_records, continuation=lineage))
    assert unused.calls == final["model_requests_this_invocation"] == 0
    assert final["total_tokens"] is None  # Failed request still has unknown usage.


@pytest.mark.parametrize("key", ["source_sha256", "configured_model", "provider_endpoint_sha256",
                                 "dataset_sha256", "prompt_revision"])
def test_continuation_refuses_revision_model_provider_or_data_drift(tmp_path, key):
    manifest, cases = continuation_manifest()
    path, _ = asyncio.run(benchmark.execute(manifest, cases, LocalGenerator(), tmp_path))
    changed = {**manifest, key: "changed"}
    with pytest.raises(ValueError):
        benchmark.load_continuation(path, changed)


def test_continuation_refuses_tampered_cases_and_reserves_only_one_child(tmp_path):
    manifest, cases = continuation_manifest()
    path, summary = asyncio.run(benchmark.execute(manifest, cases, LocalGenerator(), tmp_path))
    benchmark.reserve_continuation(path)
    with pytest.raises(FileExistsError):
        benchmark.reserve_continuation(path)
    Path(summary["cases_artifact"]).write_text("changed\n")
    with pytest.raises(ValueError, match="fingerprint"):
        benchmark.load_continuation(path, manifest)
