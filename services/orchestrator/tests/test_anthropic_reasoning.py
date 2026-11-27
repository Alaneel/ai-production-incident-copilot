import asyncio
import json
import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import anthropic
import httpx2
from fastapi.testclient import TestClient

from incident_copilot.anthropic_reasoning import (
    DEFAULT_ANTHROPIC_MODEL,
    AnthropicMessagesHypothesisGenerator,
)
from incident_copilot.models import Evidence, EvidenceKind
from incident_copilot.reasoning import (
    REASONING_INSTRUCTIONS,
    REASONING_PROMPT_REVISION,
    ModelGenerationError,
    evidence_context,
)

PROVIDER_SECRET_TEXT = "provider-private-detail"


def message(*, text=None, stop_reason="end_turn", thinking=True, usage=None):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "", "signature": "sig"})
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "id": "msg_test_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 812, "output_tokens": 240},
    }


def candidate(**overrides):
    return {
        "cause": "Database connection pool exhaustion",
        "confidence": 0.86,
        "evidence_ids": ["ev_metric", "ev_log"],
        "conflicting_evidence_ids": [],
        "validation_notes": ["Metric and log agree; causal direction is unproven."],
        **overrides,
    }


class ClaudeTransport:
    """Records requests and answers from a queue of (status, body) pairs."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx2.Response(status, json=body)

    def payload(self, index=0):
        return json.loads(self.requests[index].content)


def generator(transport, **options):
    client = anthropic.AsyncAnthropic(
        api_key="not-a-real-key",
        base_url="https://claude.api.test",
        max_retries=options.pop("max_retries", 0),
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(transport)),
    )
    return AnthropicMessagesHypothesisGenerator(client=client, **options), client


class AnthropicReasoningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = [
            Evidence(
                id="ev_metric", kind=EvidenceKind.METRIC, source="prometheus",
                title="Pool saturation", observation="Pool active ratio reached one.",
                observed_at=datetime.now(UTC), service="payment-service", score=0.96,
            ),
            Evidence(
                id="ev_log", kind=EvidenceKind.LOG, source="loki",
                title="Acquire timeout burst", observation="Database acquisition timeouts increased.",
                observed_at=datetime.now(UTC), service="payment-service", score=0.94,
            ),
        ]

    def run_generate(self, transport, **options):
        async def scenario():
            subject, client = generator(transport, **options)
            try:
                return await subject.generate(
                    question="Why is payment-service slow?",
                    incident_type="db_pool_exhaustion",
                    evidence=self.evidence,
                )
            finally:
                await client.close()

        return asyncio.run(scenario())

    def test_structured_response_is_bound_to_retained_evidence(self) -> None:
        transport = ClaudeTransport((200, message(text=json.dumps({"hypotheses": [candidate()]}))))
        hypotheses, trace = self.run_generate(transport)

        self.assertEqual(len(hypotheses), 1)
        self.assertEqual(hypotheses[0].rank, 1)
        self.assertEqual(hypotheses[0].evidence_ids, ["ev_metric", "ev_log"])
        self.assertEqual(trace["provider"], "anthropic")
        self.assertEqual(trace["prompt_revision"], REASONING_PROMPT_REVISION)
        self.assertEqual(trace["model"], "claude-opus-5-5")
        self.assertEqual(trace["response_id"], "msg_test_1")
        self.assertEqual((trace["input_tokens"], trace["output_tokens"], trace["total_tokens"]), (812, 240, 1052))
        self.assertFalse(trace["abstained"])

    def test_request_carries_the_same_contract_as_the_openai_adapter(self) -> None:
        transport = ClaudeTransport((200, message(text=json.dumps({"hypotheses": []}))))
        self.run_generate(transport)

        request = transport.requests[0]
        self.assertEqual(request.url.path, "/v1/messages")
        self.assertEqual(request.headers["authorization"], "Bearer not-a-real-key")
        payload = transport.payload()
        self.assertEqual(payload["model"], DEFAULT_ANTHROPIC_MODEL)
        self.assertEqual(payload["system"], REASONING_INSTRUCTIONS)
        self.assertEqual(payload["messages"], [{
            "role": "user",
            "content": evidence_context(
                question="Why is payment-service slow?",
                incident_type="db_pool_exhaustion",
                evidence=self.evidence,
            ),
        }])
        schema = payload["output_config"]["format"]
        self.assertEqual(schema["type"], "json_schema")
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertNotIn("effort", payload["output_config"])
        # Claude is never offered tools, and no sampling knobs are sent.
        for absent in ("tools", "tool_choice", "temperature", "top_p", "top_k"):
            self.assertNotIn(absent, payload)

    def test_effort_is_sent_only_when_configured(self) -> None:
        transport = ClaudeTransport((200, message(text=json.dumps({"hypotheses": []}))))
        _, trace = self.run_generate(transport, effort="high")
        self.assertEqual(transport.payload()["output_config"]["effort"], "high")
        self.assertEqual(trace["requested_effort"], "high")

    def test_empty_list_is_an_abstention(self) -> None:
        transport = ClaudeTransport((200, message(text=json.dumps({"hypotheses": []}))))
        hypotheses, trace = self.run_generate(transport)
        self.assertEqual(hypotheses, [])
        self.assertTrue(trace["abstained"])

    def test_unknown_evidence_id_is_rejected(self) -> None:
        body = {"hypotheses": [candidate(evidence_ids=["ev_metric", "ev_invented"])]}
        transport = ClaudeTransport((200, message(text=json.dumps(body))))
        with self.assertRaisesRegex(ModelGenerationError, "outside the retained"):
            self.run_generate(transport)

    def test_contract_limits_are_enforced_client_side(self) -> None:
        # The API schema cannot express minItems=2; one citation must still fail.
        for bad in (candidate(evidence_ids=["ev_metric"]), candidate(confidence=1.4), candidate(cause="x")):
            with self.subTest(bad=bad):
                transport = ClaudeTransport((200, message(text=json.dumps({"hypotheses": [bad]}))))
                with self.assertRaises(ModelGenerationError) as caught:
                    self.run_generate(transport)
                self.assertEqual(caught.exception.usage_metadata["input_tokens"], 812)

    def test_incomplete_responses_fail_without_provider_text(self) -> None:
        cases = {
            "refusal": message(text=PROVIDER_SECRET_TEXT, stop_reason="refusal"),
            "max_tokens": message(text='{"hypotheses": [', stop_reason="max_tokens"),
            "no_text": message(text=None),
        }
        for name, body in cases.items():
            with self.subTest(name):
                with self.assertRaises(ModelGenerationError) as caught:
                    self.run_generate(ClaudeTransport((200, body)))
                self.assertNotIn(PROVIDER_SECRET_TEXT, str(caught.exception))
                self.assertEqual(caught.exception.usage_metadata["output_tokens"], 240)

    def test_api_errors_are_reported_by_status_only(self) -> None:
        error = {"type": "error", "error": {"type": "invalid_request_error", "message": PROVIDER_SECRET_TEXT}}
        transport = ClaudeTransport((400, error))
        with self.assertRaisesRegex(ModelGenerationError, "rejected the request: 400") as caught:
            self.run_generate(transport, max_retries=1)
        self.assertNotIn(PROVIDER_SECRET_TEXT, str(caught.exception))
        self.assertEqual(len(transport.requests), 1)  # client errors are not retried

    def test_overload_is_retried_once_then_reported(self) -> None:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        transport = ClaudeTransport((529, overloaded), (529, overloaded))
        with self.assertRaisesRegex(ModelGenerationError, "529"):
            self.run_generate(transport, max_retries=1)
        self.assertEqual(len(transport.requests), 2)

    def test_connection_failure_is_a_model_generation_error(self) -> None:
        def refuse(request):
            raise httpx2.ConnectError("unreachable", request=request)

        with self.assertRaisesRegex(ModelGenerationError, "request failed"):
            self.run_generate(refuse)

    def test_constructor_rejects_unsafe_configuration(self) -> None:
        with self.assertRaises(ValueError):
            AnthropicMessagesHypothesisGenerator(api_key="k", effort="extreme")
        with self.assertRaises(ValueError):
            AnthropicMessagesHypothesisGenerator(api_key="k", max_tokens=0)
        with self.assertRaises(ValueError):
            AnthropicMessagesHypothesisGenerator(api_key="", model=DEFAULT_ANTHROPIC_MODEL)

    def test_workflow_abstention_and_grounding_with_claude(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver
        from incident_copilot.approval import ApprovalReceiptSigner
        from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
        from incident_copilot.models import IncidentQuestion, IncidentStatus
        from incident_copilot.tools import DemoTelemetryTools

        async def scenario(answer):
            def handler(request):
                context = json.loads(json.loads(request.content)["messages"][0]["content"])
                metric = next(e["id"] for e in context["evidence"] if e["kind"] == "metric")
                log = next(e["id"] for e in context["evidence"] if e["kind"] == "log")
                return httpx2.Response(200, json=message(text=json.dumps(answer(metric, log))))

            subject, client = generator(handler)
            tools = DemoTelemetryTools()
            tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
            tools.restart_deployment = AsyncMock()
            workflow = LangGraphIncidentWorkflow(
                tools=tools, checkpointer=InMemorySaver(),
                approval_signer=ApprovalReceiptSigner("test-only-approval-secret"),
                hypothesis_generator=subject,
            )
            try:
                state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
            finally:
                await client.close()
            # Nothing is executed: at most one dry-run preflight before approval.
            for call in tools.rollback_release.await_args_list:
                self.assertTrue(call.kwargs["dry_run"])
            tools.restart_deployment.assert_not_awaited()
            return state, tools

        cited, _ = asyncio.run(scenario(lambda metric, log: {"hypotheses": [
            candidate(evidence_ids=[metric, log])]}))
        self.assertEqual(cited.status, IncidentStatus.WAITING_APPROVAL)
        self.assertEqual(cited.hypotheses[0].cause, "Database connection pool exhaustion")
        trace = next(e for e in cited.trace if e.event == "model_generation")
        self.assertEqual(trace.payload["provider"], "anthropic")

        abstained, tools = asyncio.run(scenario(lambda metric, log: {"hypotheses": []}))
        self.assertEqual(abstained.status, IncidentStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(abstained.hypotheses, [])
        tools.rollback_release.assert_not_awaited()


class AnthropicStartupTests(unittest.TestCase):
    BASE = {"DATABASE_URL": "", "REDIS_URL": "", "MCP_SERVER_URL": "",
            "OPENAI_API_KEY": "", "OPENAI_MODEL": "", "ORCHESTRATOR_AUTH_MODE": "disabled"}

    def health(self, **env):
        from incident_copilot.api import app

        with patch.dict(os.environ, {**self.BASE, **env}):
            with TestClient(app) as client:
                return client.get("/health").json()["reasoning"]

    def test_key_alone_does_not_enable_paid_reasoning(self) -> None:
        self.assertEqual(self.health(ANTHROPIC_API_KEY="not-a-real-key", ANTHROPIC_MODEL=""), "deterministic")

    def test_key_and_model_enable_claude(self) -> None:
        self.assertEqual(
            self.health(ANTHROPIC_API_KEY="not-a-real-key", ANTHROPIC_MODEL="claude-opus-5-5"),
            "anthropic:claude-opus-5-5",
        )

    def test_two_configured_providers_refuse_to_start(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Both OpenAI and Anthropic"):
            self.health(ANTHROPIC_API_KEY="k", ANTHROPIC_MODEL="claude-opus-5-5",
                        OPENAI_API_KEY="k", OPENAI_MODEL="some-model")

    def test_invalid_effort_refuses_to_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "effort"):
            self.health(ANTHROPIC_API_KEY="k", ANTHROPIC_MODEL="claude-opus-5-5", ANTHROPIC_EFFORT="turbo")


if __name__ == "__main__":
    unittest.main()
