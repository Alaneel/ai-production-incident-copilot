import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

import run_graph_model_benchmark as benchmark  # noqa: E402
from incident_copilot.reasoning import OpenAIResponsesHypothesisGenerator  # noqa: E402


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "4096"])
def test_invalid_output_caps_fail_before_client_or_credentials(monkeypatch, cap):
    monkeypatch.setattr(benchmark, "dotenv_values", lambda *args: pytest.fail("must not read secrets"))
    with pytest.raises(ValueError, match="positive integer"):
        benchmark.plan(3, cap)
    with pytest.raises(ValueError, match="positive integer"):
        OpenAIResponsesHypothesisGenerator(api_key="fake", model="offline", max_output_tokens=cap)


def test_plan_reports_output_only_budget_without_prices_or_credential_access(monkeypatch):
    monkeypatch.setattr(benchmark, "dotenv_values", lambda *args: pytest.fail("must not read secrets"))
    manifest, _ = benchmark.plan(3, 2048)
    assert manifest["max_output_tokens_per_request"] == 2048
    assert manifest["maximum_requested_output_tokens"] == 6144
    assert manifest["maximum_api_requests"] == 3
    assert manifest["estimated_cost_usd"] is None
    assert "configured_model" not in manifest


@pytest.mark.parametrize("cap", [None, 128, 4096])
def test_real_adapter_transmits_optional_cap_without_changing_model(cap):
    async def scenario():
        captured = []

        def handle(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "completed", "output_text": '{"hypotheses": []}'})

        async with httpx.AsyncClient(base_url="https://offline.test/v1", transport=httpx.MockTransport(handle)) as client:
            generator = OpenAIResponsesHypothesisGenerator(api_key="fake", model="preserved-model",
                                                          max_output_tokens=cap, client=client)
            _, metadata = await generator.generate(question="test", incident_type=None, evidence=[])
        assert captured[0]["model"] == "preserved-model"
        assert metadata["requested_max_output_tokens"] == cap
        if cap is None:
            assert "max_output_tokens" not in captured[0]  # Preserve uncapped runtime callers.
        else:
            assert captured[0]["max_output_tokens"] == cap
    asyncio.run(scenario())


def test_incomplete_capped_response_retains_numeric_usage_and_stops_real_graph_batch(tmp_path):
    async def scenario():
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "status": "incomplete", "output_text": "PRIVATE-PROVIDER-OUTPUT",
                "usage": {"input_tokens": 100, "output_tokens": 128, "total_tokens": 228,
                          "private": "PRIVATE-USAGE-FIELD"},
            })

        manifest, cases = benchmark.plan(3, 128)
        async with httpx.AsyncClient(base_url="https://offline.test/v1", transport=httpx.MockTransport(handle)) as client:
            generator = OpenAIResponsesHypothesisGenerator(api_key="fake", model="offline", max_output_tokens=128,
                                                          max_attempts=1, client=client)
            path, summary = await benchmark.execute(manifest, cases, generator, tmp_path)
        assert not summary["ok"] and summary["cases_attempted"] == 1
        assert len(requests) == summary["model_requests_attempted"] == 1
        assert requests[0]["max_output_tokens"] == 128
        assert summary["total_tokens"] == 228  # Failed generation can still be billed.
        assert summary["actual_cost_usd"] is None
        rows = Path(summary["cases_artifact"]).read_text()
        record = json.loads(rows)
        assert record["error_type"] == "ModelGenerationError"
        assert record["model_usage"] == [{"input_tokens": 100, "output_tokens": 128, "total_tokens": 228}]
        assert record["model_root_cause_top1"] is None
        assert "PRIVATE" not in rows and "PRIVATE" not in path.read_text()
    asyncio.run(scenario())


@pytest.mark.parametrize("usage", [None, "PRIVATE", {"total_tokens": True}, {"total_tokens": -1},
                                 {"total_tokens": "PRIVATE", "input_tokens": 5}])
def test_invalid_or_missing_incomplete_usage_remains_unknown(usage):
    from incident_copilot.reasoning import ModelGenerationError

    class FailingGenerator:
        async def generate(self, **arguments):
            raise ModelGenerationError("incomplete", usage=usage)

    measured = benchmark.MeasuredGenerator(FailingGenerator())
    with pytest.raises(ModelGenerationError):
        asyncio.run(measured.generate())
    assert benchmark.measured_total_tokens([{"model_usage": measured.usage, "model_requests_attempted": 1}]) is None
    assert "PRIVATE" not in json.dumps(measured.usage)


def test_continuation_refuses_changed_output_cap(tmp_path):
    from test_graph_model_benchmark import LocalGenerator

    manifest, cases = benchmark.plan(3, 4096)
    manifest.update(configured_model="offline", provider_endpoint_sha256="0" * 64)
    path, _ = asyncio.run(benchmark.execute(manifest, cases, LocalGenerator(), tmp_path))
    changed, _ = benchmark.plan(3, 8192)
    changed.update(configured_model="offline", provider_endpoint_sha256="0" * 64)
    with pytest.raises(ValueError, match="revision/data"):
        benchmark.load_continuation(path, changed)


def test_cli_plan_passes_explicit_cap_and_does_not_need_key():
    result = subprocess.run([sys.executable, str(ROOT / "evals/run_graph_model_benchmark.py"),
                             "--plan", "--limit", "3", "--max-output-tokens", "2048"],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["maximum_requested_output_tokens"] == 6144
