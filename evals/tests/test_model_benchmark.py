import argparse
import asyncio
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "services" / "mcp-server"))

from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from run_model_benchmark import (  # noqa: E402
    ModelPrediction,
    ResponsesClient,
    _load_completed,
    aggregate,
    build_context,
    print_plan,
    to_prediction,
)
from normalize_queued_latency import recover_fifo_request_latencies  # noqa: E402


class ModelBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        dataset = ROOT / "evals" / "dataset" / "incidents.jsonl"
        self.case = json.loads(dataset.read_text().splitlines()[0])
        self.index = KnowledgeIndex.load_default()

    def test_single_prompt_context_does_not_leak_labels(self) -> None:
        context, retrieved = build_context(
            self.case,
            "single_prompt",
            self.index,
        )
        self.assertEqual(context, {"question": self.case["question"]})
        self.assertEqual(retrieved, [])
        self.assertNotIn(self.case["root_cause"], json.dumps(context))

    def test_responses_request_is_structured_and_not_stored(self) -> None:
        async def scenario() -> None:
            payloads: list[dict] = []

            def handler(request: httpx.Request) -> httpx.Response:
                payloads.append(json.loads(request.content))
                output = {
                    "service": self.case["service"],
                    "ranked_causes": [self.case["root_cause"]],
                    "selected_tools": self.case["required_tools"],
                    "cited_evidence": self.case["relevant_evidence"],
                    "action": self.case["recommended_action"],
                    "requires_human_approval": self.case["requires_approval"],
                }
                return httpx.Response(
                    200,
                    json={
                        "id": "resp_eval_test",
                        "status": "completed",
                        "output_text": json.dumps(output),
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 25,
                            "total_tokens": 125,
                        },
                    },
                )

            http_client = httpx.AsyncClient(
                base_url="https://api.openai.test/v1",
                transport=httpx.MockTransport(handler),
            )
            client = ResponsesClient(
                api_key="not-a-real-key",
                model="configured-test-model",
                base_url="https://api.openai.test/v1",
                concurrency=1,
                client=http_client,
            )
            context, retrieved = build_context(
                self.case,
                "validated_agent",
                self.index,
            )
            output, usage = await client.predict(
                variant="validated_agent",
                context=context,
            )
            prediction = to_prediction(output, retrieved)
            self.assertTrue(prediction.grounded)
            self.assertEqual(usage["response_id"], "resp_eval_test")
            self.assertEqual(
                usage["latency_measurement"],
                "direct_request_elapsed_v2",
            )
            self.assertFalse(payloads[0]["store"])
            self.assertEqual(
                payloads[0]["text"]["format"]["type"],
                "json_schema",
            )
            await http_client.aclose()

        asyncio.run(scenario())

    def test_strict_response_schema_requires_every_property(self) -> None:
        schema = ModelPrediction.model_json_schema()
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_fifo_latency_recovery_removes_semaphore_queue_time(self) -> None:
        recovered = recover_fifo_request_latencies(
            [2_000.0, 3_000.0, 5_500.0, 7_000.0],
            concurrency=2,
        )
        self.assertEqual(recovered, [2_000.0, 3_000.0, 3_500.0, 4_000.0])

    def test_aggregate_uses_explicit_token_prices(self) -> None:
        prediction = {
            "service": self.case["service"],
            "ranked_causes": [self.case["root_cause"]],
            "selected_tools": self.case["required_tools"],
            "cited_evidence": self.case["relevant_evidence"],
            "retrieved_evidence": self.case["relevant_evidence"],
            "action": self.case["recommended_action"],
            "requires_human_approval": self.case["requires_approval"],
            "grounded": True,
        }
        result = aggregate(
            variant="validated_agent",
            records=[
                {
                    "case": self.case,
                    "prediction": prediction,
                    "usage": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 1_000_000,
                        "latency_ms": 100,
                    },
                }
            ],
            input_price=2.0,
            output_price=8.0,
        )
        self.assertEqual(result["token_cost_usd"], 10.0)
        self.assertEqual(result["token_cost_per_incident_usd"], 10.0)
        self.assertEqual(result["root_cause_top1"], 1.0)
        self.assertEqual(result["citation_faithfulness_rate"], 1.0)
        self.assertEqual(result["destructive_action_guardrail_rate"], 1.0)
        self.assertEqual(result["unguarded_destructive_actions"], 0)

    def test_aggregate_exposes_unguarded_destructive_predictions(self) -> None:
        case = dict(self.case)
        case["recommended_action"] = "none"
        case["requires_approval"] = False
        prediction = {
            "service": case["service"],
            "ranked_causes": [case["root_cause"]],
            "selected_tools": case["required_tools"],
            "cited_evidence": case["relevant_evidence"],
            "retrieved_evidence": case["relevant_evidence"],
            "action": "restart_deployment",
            "requires_human_approval": False,
            "grounded": True,
        }
        result = aggregate(
            variant="validated_agent",
            records=[
                {
                    "case": case,
                    "prediction": prediction,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "latency_ms": 100,
                    },
                }
            ],
            input_price=None,
            output_price=None,
        )
        self.assertEqual(result["destructive_action_guardrail_rate"], 0.0)
        self.assertEqual(result["unguarded_destructive_actions"], 1)
        self.assertEqual(result["remediation_action_accuracy"], 0.0)

    def test_plan_reports_calls_without_api_credentials(self) -> None:
        arguments = argparse.Namespace(
            limit=2,
            variants=["single_prompt", "rag"],
            input_usd_per_million=None,
            output_usd_per_million=None,
        )
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            print_plan(arguments)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["mode"], "plan_only_no_api_calls")
        self.assertEqual(plan["planned_api_calls"], 4)
        self.assertEqual(plan["checkpointed_calls"], 0)
        self.assertEqual(plan["remaining_api_calls"], 4)
        self.assertIsNone(plan["estimated_remaining_cost_usd"])

    def test_resume_only_loads_matching_model_and_revision(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            records = [
                {
                    "model": "target-model",
                    "benchmark_revision": "revision-a",
                    "variant": "rag",
                    "case": {"id": "eval-001"},
                },
                {
                    "model": "different-model",
                    "benchmark_revision": "revision-a",
                    "variant": "rag",
                    "case": {"id": "eval-002"},
                },
                {
                    "model": "target-model",
                    "benchmark_revision": "old-revision",
                    "variant": "rag",
                    "case": {"id": "eval-003"},
                },
            ]
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            completed = _load_completed(
                cases_path=path,
                model="target-model",
                benchmark_revision="revision-a",
            )
        self.assertEqual(list(completed), [("rag", "eval-001")])


if __name__ == "__main__":
    unittest.main()
