import asyncio
import json
import unittest
from datetime import UTC, datetime

import httpx

from incident_copilot.models import Evidence, EvidenceKind
from incident_copilot.reasoning import (
    ModelGenerationError,
    OpenAIResponsesHypothesisGenerator,
    REASONING_PROMPT_REVISION,
)


class OpenAIReasoningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = [
            Evidence(
                id="ev_metric",
                kind=EvidenceKind.METRIC,
                source="prometheus",
                title="Pool saturation",
                observation="Pool active ratio reached one.",
                observed_at=datetime.now(UTC),
                service="payment-service",
                score=0.96,
            ),
            Evidence(
                id="ev_log",
                kind=EvidenceKind.LOG,
                source="loki",
                title="Acquire timeout burst",
                observation="Database acquisition timeouts increased.",
                observed_at=datetime.now(UTC),
                service="payment-service",
                score=0.94,
            ),
        ]

    def test_structured_response_is_bound_to_retained_evidence(self) -> None:
        async def scenario() -> None:
            captured: list[dict] = []

            def handler(request: httpx.Request) -> httpx.Response:
                captured.append(json.loads(request.content))
                output = {
                    "hypotheses": [
                        {
                            "cause": "Database connection pool exhaustion",
                            "confidence": 0.91,
                            "evidence_ids": ["ev_metric", "ev_log"],
                            "conflicting_evidence_ids": [],
                            "validation_notes": [
                                "Independent metric and log evidence agree."
                            ],
                        }
                    ]
                }
                return httpx.Response(
                    200,
                    json={
                        "id": "resp_test_001",
                        "model": "configured-test-model",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(output),
                                    }
                                ],
                            }
                        ],
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 40,
                            "total_tokens": 160,
                        },
                    },
                )

            client = httpx.AsyncClient(
                base_url="https://api.openai.test/v1",
                transport=httpx.MockTransport(handler),
            )
            generator = OpenAIResponsesHypothesisGenerator(
                api_key="not-a-real-key",
                model="configured-test-model",
                client=client,
            )
            hypotheses, trace = await generator.generate(
                question="Why is payment-service slow?",
                incident_type="latency_regression",
                evidence=self.evidence,
            )
            self.assertEqual(hypotheses[0].evidence_ids, ["ev_metric", "ev_log"])
            self.assertEqual(trace["response_id"], "resp_test_001")
            self.assertEqual(trace["total_tokens"], 160)
            self.assertEqual(trace["prompt_revision"], REASONING_PROMPT_REVISION)
            instructions = captured[0]["input"][0]["content"][0]["text"]
            self.assertIn("For EVERY hypothesis, including alternatives", instructions)
            self.assertIn("two distinct evidence kinds", instructions)
            self.assertIn("Return fewer hypotheses", instructions)
            self.assertIn("Return hypotheses=[]", instructions)
            self.assertFalse(trace["abstained"])
            self.assertEqual(trace["hypothesis_count"], 1)
            context = json.loads(captured[0]["input"][1]["content"][0]["text"])
            self.assertTrue(all(e["usable_for_citation"] for e in context["evidence"]))
            self.assertEqual(context["evidence"][0]["service"], "payment-service")
            self.assertEqual(
                captured[0]["text"]["format"]["type"],
                "json_schema",
            )
            self.assertFalse(captured[0]["store"])
            candidate_schema = captured[0]["text"]["format"]["schema"]["$defs"]["HypothesisCandidate"]
            self.assertEqual(set(candidate_schema["required"]), set(candidate_schema["properties"]))
            self.assertFalse(candidate_schema["additionalProperties"])
            await client.aclose()

        asyncio.run(scenario())

    def test_unavailable_observations_are_marked_without_changing_ids(self) -> None:
        async def scenario() -> None:
            unavailable = self.evidence[0].model_copy(update={
                "id": "ev_empty", "attributes": {"series": []},
            })
            output = {"hypotheses": [{
                "cause": "Database connection pool exhaustion", "confidence": 0.8,
                "evidence_ids": ["ev_metric", "ev_log"],
                "conflicting_evidence_ids": [], "validation_notes": ["Provisional finding"],
            }]}

            def handler(request: httpx.Request) -> httpx.Response:
                payload = json.loads(request.content)
                context = json.loads(payload["input"][1]["content"][0]["text"])
                by_id = {e["id"]: e for e in context["evidence"]}
                self.assertEqual(set(by_id), {"ev_empty", "ev_metric", "ev_log"})
                self.assertFalse(by_id["ev_empty"]["usable_for_citation"])
                return httpx.Response(200, json={"output_text": json.dumps(output)})

            async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
                                        transport=httpx.MockTransport(handler)) as client:
                generator = OpenAIResponsesHypothesisGenerator(
                    api_key="not-a-real-key", model="configured-test-model", client=client,
                )
                await generator.generate(question="Why is payment-service slow?",
                                         incident_type=None, evidence=[unavailable, *self.evidence])

        asyncio.run(scenario())

    def test_unknown_evidence_id_is_rejected(self) -> None:
        async def scenario() -> None:
            output = {
                "hypotheses": [
                    {
                        "cause": "Unsupported diagnosis",
                        "confidence": 0.8,
                        "evidence_ids": ["ev_metric", "ev_hallucinated"],
                        "conflicting_evidence_ids": [],
                        "validation_notes": ["The second citation is invalid."],
                    }
                ]
            }

            def handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={"output_text": json.dumps(output)},
                )

            client = httpx.AsyncClient(
                base_url="https://api.openai.test/v1",
                transport=httpx.MockTransport(handler),
            )
            generator = OpenAIResponsesHypothesisGenerator(
                api_key="not-a-real-key",
                model="configured-test-model",
                client=client,
            )
            with self.assertRaises(ModelGenerationError):
                await generator.generate(
                    question="Why is payment-service slow?",
                    incident_type="latency_regression",
                    evidence=self.evidence,
                )
            await client.aclose()

        asyncio.run(scenario())

    def test_prompt_contract_does_not_bypass_workflow_grounding(self) -> None:
        from unittest.mock import AsyncMock

        from langgraph.checkpoint.memory import InMemorySaver
        from incident_copilot.approval import ApprovalReceiptSigner
        from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
        from incident_copilot.models import IncidentQuestion, IncidentStatus
        from incident_copilot.tools import DemoTelemetryTools

        async def scenario() -> None:
            for invalid_alternative in (False, True):
                with self.subTest(invalid_alternative=invalid_alternative):
                    requests = []

                    def handler(request: httpx.Request) -> httpx.Response:
                        payload = json.loads(request.content)
                        requests.append(payload)
                        context = json.loads(payload["input"][1]["content"][0]["text"])
                        metrics = [e["id"] for e in context["evidence"] if e["kind"] == "metric"]
                        log = next(e["id"] for e in context["evidence"] if e["kind"] == "log")
                        candidates = [{
                            "cause": "Database connection pool exhaustion", "confidence": 0.8,
                            "evidence_ids": [metrics[0], log], "conflicting_evidence_ids": [],
                            "validation_notes": ["Provisional finding, not proven causal direction"],
                        }]
                        if invalid_alternative:
                            candidates.append({**candidates[0], "cause": "Unverified alternative cause",
                                               "evidence_ids": metrics[:2]})
                        return httpx.Response(200, json={"output_text": json.dumps({"hypotheses": candidates})})

                    async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
                                                transport=httpx.MockTransport(handler)) as client:
                        generator = OpenAIResponsesHypothesisGenerator(
                            api_key="not-a-real-key", model="configured-test-model", client=client,
                        )
                        tools = DemoTelemetryTools()
                        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
                        tools.restart_deployment = AsyncMock()
                        workflow = LangGraphIncidentWorkflow(
                            tools=tools, checkpointer=InMemorySaver(),
                            approval_signer=ApprovalReceiptSigner("test-only-approval-secret"),
                            hypothesis_generator=generator,
                        )
                        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
                        self.assertEqual(len(requests), 1)
                        if invalid_alternative:
                            self.assertEqual(state.status, IncidentStatus.INSUFFICIENT_EVIDENCE)
                            self.assertEqual(state.hypotheses, [])
                            self.assertTrue(any(e.event == "hypothesis_citations_rejected" for e in state.trace))
                            tools.rollback_release.assert_not_awaited()
                        else:
                            self.assertEqual(state.status, IncidentStatus.WAITING_APPROVAL)
                            self.assertEqual(len(state.hypotheses), 1)
                            tools.rollback_release.assert_awaited_once()
                            self.assertTrue(tools.rollback_release.call_args.kwargs["dry_run"])
                        tools.restart_deployment.assert_not_awaited()
                        retained = await workflow.get(state.incident_id)
                        self.assertEqual(retained.status, state.status)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
