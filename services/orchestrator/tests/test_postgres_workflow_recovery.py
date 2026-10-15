import asyncio
import os
import json
import select
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalRequest, Hypothesis, IncidentQuestion
from incident_copilot.store import PostgresIncidentRepository
from incident_copilot.tools import DemoTelemetryTools


PAUSED_PROCESS = """
import asyncio, json, os
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import IncidentQuestion, ApprovalRequest
from incident_copilot.store import PostgresIncidentRepository
from incident_copilot.tools import DemoTelemetryTools

class NonMutatingTools(DemoTelemetryTools):
    async def rollback_release(self, **arguments):
        if arguments.get("dry_run") is True:
            return await super().rollback_release(**arguments)
        print(json.dumps({"phase": "execution_started", "idempotency_key": arguments["idempotency_key"],
                          "side_effect_performed": False}), flush=True)
        await asyncio.Event().wait()

async def main():
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        workflow = LangGraphIncidentWorkflow(tools=NonMutatingTools(), checkpointer=saver,
            approval_signer=ApprovalReceiptSigner("isolated-recovery-test-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        repository = await PostgresIncidentRepository.connect(url)
        await repository.save(state)
        print(json.dumps(state.model_dump(mode="json")), flush=True)
        if os.getenv("RECOVERY_TEST_PHASE") == "executing":
            approval = ApprovalRequest(decision="approve", actor="simulation-only-operator",
                reason="Only test a non-mutating tool during process interruption",
                idempotency_key=f"{state.incident_id}-simulation-approval")
            assert await repository.claim_approval(state, approval)
            await workflow.resume_with_approval(state.incident_id, approval)
        await repository.close()
        await asyncio.Event().wait()
asyncio.run(main())
"""


ABSTAINED_PROCESS = """
import asyncio, json, os
import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import IncidentQuestion
from incident_copilot.reasoning import OpenAIResponsesHypothesisGenerator
from incident_copilot.store import PostgresIncidentRepository
from incident_copilot.tools import DemoTelemetryTools

class NoMutationTools(DemoTelemetryTools):
    async def rollback_release(self, **arguments):
        raise AssertionError("Abstention must not even dry-run a mutation")
    async def restart_deployment(self, **arguments):
        raise AssertionError("Abstention must not even dry-run a mutation")

async def main():
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["text"]["format"]["schema"]["properties"]["hypotheses"].get("minItems", 0) == 0
        return httpx.Response(200, json={"status": "completed", "id": "offline-abstention-fixture",
            "output_text": '{"hypotheses": []}', "usage": {"total_tokens": 12}})
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
            transport=httpx.MockTransport(handler)) as client:
        generator = OpenAIResponsesHypothesisGenerator(api_key="fake-no-network",
            model="offline-test-model", client=client, max_attempts=1)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            workflow = LangGraphIncidentWorkflow(tools=NoMutationTools(), checkpointer=saver,
                hypothesis_generator=generator,
                approval_signer=ApprovalReceiptSigner("isolated-recovery-test-secret"))
            state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
            assert state.status == "insufficient_evidence"
            assert state.approval_decision is None
            repository = await PostgresIncidentRepository.connect(url)
            try:
                await repository.save(state)
            finally:
                await repository.close()
    print(json.dumps({"state": state.model_dump(mode="json"), "fixture_requests": len(requests)}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


@unittest.skipUnless(os.getenv("INCIDENT_TEST_DATABASE_URL"), "isolated PostgreSQL test URL required")
class PostgresWorkflowRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "SIGKILL recovery check requires POSIX")
    def test_model_abstention_survives_sigkill_and_fresh_read_only_process(self):
        root = Path(__file__).resolve().parents[3]
        # Validate the dedicated local test target before starting a writer.
        sys.path.insert(0, str(root / "fault-lab"))
        from verify_observed_topology import checkpoint_url
        checkpoint_url(True, True)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("OPENAI_") and key not in {
                           "DATABASE_URL", "REDIS_URL", "MCP_SERVER_URL"}}
        environment.update(PYTHONPATH=str(root / "services/orchestrator"), LANGGRAPH_STRICT_MSGPACK="true")
        writer = subprocess.Popen([sys.executable, "-c", ABSTAINED_PROCESS], cwd=root,
                                  env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([writer.stdout], [], [], 20)
            self.assertTrue(ready, "Writer did not report its committed abstention checkpoint.")
            line = writer.stdout.readline()
            self.assertTrue(line, "Writer stopped before its committed checkpoint.")
            report = json.loads(line)
            expected = report["state"]
            self.assertEqual(report["fixture_requests"], 1)
            self.assertEqual(expected["status"], "insufficient_evidence")
            self.assertEqual(expected["hypotheses"], [])
            self.assertEqual(expected["remediation"]["tool"], "none")
            self.assertIsNone(expected["approval_decision"])
            self.assertTrue(any(event["event"] == "model_hypothesis_abstained" for event in expected["trace"]))
            self.assertIsNone(writer.poll())
            writer.kill()
            self.assertEqual(writer.wait(timeout=5), -signal.SIGKILL)
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait(timeout=5)
            writer.stdout.close()
            writer.stderr.close()
        # This helper has no model generator and raises on any tool access.
        reader = subprocess.Popen([sys.executable, str(root / "fault-lab/restore_native_checkpoint.py"),
                                   "--inspect-existing-test-checkpoint"], cwd=root, env=environment,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            output, _ = reader.communicate(input=json.dumps(expected), timeout=30)
            self.assertNotEqual(reader.pid, writer.pid)
            self.assertEqual(reader.returncode, 0, "Fresh read-only recovery process failed.")
            restored = json.loads(output)
            self.assertTrue(restored["ok"])
            self.assertTrue(all(restored["checks"].values()))
            self.assertEqual(restored["incident"], expected)
            self.assertGreaterEqual(restored["history_checkpoint_count"], 2)
            self.assertIsNone(restored["incident"]["approval_actor"])
        finally:
            if reader.poll() is None:
                reader.kill()
                reader.wait(timeout=5)
            reader.stdin.close()
            reader.stdout.close()
            reader.stderr.close()

    @unittest.skipUnless(os.name == "posix", "SIGKILL recovery check requires POSIX")
    def test_sigkill_during_nonmutating_execution_keeps_duplicate_approval_locked(self):
        from incident_copilot.api import app
        root = Path(__file__).resolve().parents[3]
        environment = {**os.environ, "PYTHONPATH": str(root / "services/orchestrator"),
                       "LANGGRAPH_STRICT_MSGPACK": "true", "RECOVERY_TEST_PHASE": "executing"}
        process = subprocess.Popen([sys.executable, "-c", PAUSED_PROCESS], cwd=root,
                                   env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        try:
            def message():
                ready, _, _ = select.select([process.stdout], [], [], 20)
                self.assertTrue(ready, "Test subprocess did not report its recovery milestone.")
                line = process.stdout.readline()
                self.assertTrue(line, "Test subprocess stopped before the recovery milestone.")
                return json.loads(line)
            paused = message()
            started = message()
            self.assertEqual(started["phase"], "execution_started")
            self.assertFalse(started["side_effect_performed"])
            self.assertIsNone(process.poll())
            process.kill()
            self.assertEqual(process.wait(timeout=5), -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

        # Reconstruct the real API against the same isolated database, not an
        # in-memory checkpoint or an execution-result fixture.
        api_environment = {"DATABASE_URL": os.environ["INCIDENT_TEST_DATABASE_URL"],
                           "REDIS_URL": "", "MCP_SERVER_URL": "", "OPENAI_API_KEY": "",
                           "OPENAI_MODEL": "", "ORCHESTRATOR_AUTH_MODE": "disabled"}
        with patch.dict(os.environ, api_environment), TestClient(app) as client:
            incident_id = paused["incident_id"]
            retained = client.get(f"/v1/incidents/{incident_id}").json()
            self.assertEqual(retained["status"], "mitigating")
            self.assertEqual(retained["approval_actor"], "simulation-only-operator")
            self.assertEqual(retained["approval_decision"], "approve")
            self.assertEqual(retained["evidence"], paused["evidence"])
            intents = [event for event in retained["trace"] if event["event"] == "execution_intent_prepared"]
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0]["payload"]["idempotency_key"], started["idempotency_key"])
            self.assertEqual(intents[0]["payload"]["arguments"], paused["remediation"]["arguments"])
            self.assertEqual(intents[0]["payload"]["execution_outcome"], "unknown")
            self.assertNotIn("approval_receipt", str(retained))
            app.state.workflow.resume_with_approval = AsyncMock()
            for key in (started["idempotency_key"], f"{incident_id}-another-key"):
                response = client.post(f"/v1/incidents/{incident_id}/approval", json={
                    "decision": "approve", "actor": "simulation-only-operator", "reason": "Retry verification only",
                    "idempotency_key": key,
                })
                self.assertEqual(response.status_code, 409)
            app.state.workflow.resume_with_approval.assert_not_awaited()
            stored = client.portal.call(app.state.repository.get, incident_id)
            # Repository state predates execution; its stale paused snapshot
            # cannot reopen the already committed incident-operation claim.
            self.assertEqual(stored.status, "waiting_approval")
            claimed = client.portal.call(app.state.repository.claim_approval, stored, ApprovalRequest(
                decision="approve", actor="simulation-only-operator", reason="Retry verification only",
                idempotency_key=f"{incident_id}-stale-repository-retry",
            ))
            self.assertFalse(claimed)

    @unittest.skipUnless(os.name == "posix", "SIGKILL recovery check requires POSIX")
    def test_committed_approval_pause_survives_sigkill_of_owner_process(self):
        root = Path(__file__).resolve().parents[3]
        environment = {**os.environ, "PYTHONPATH": str(root / "services/orchestrator"),
                       "LANGGRAPH_STRICT_MSGPACK": "true", "RECOVERY_TEST_PHASE": "paused"}
        process = subprocess.Popen([sys.executable, "-c", PAUSED_PROCESS], cwd=root,
                                   env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 20)
            self.assertTrue(ready, "Test subprocess did not report a committed checkpoint in time.")
            line = process.stdout.readline()
            self.assertTrue(line, "Test subprocess stopped before reporting a committed checkpoint.")
            paused = json.loads(line)
            self.assertEqual(paused["status"], "waiting_approval")
            self.assertIsNone(process.poll())
            process.kill()
            self.assertEqual(process.wait(timeout=5), -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

        async def recover():
            url = os.environ["INCIDENT_TEST_DATABASE_URL"]
            repository = await PostgresIncidentRepository.connect(url)
            try:
                stored = await repository.get(paused["incident_id"])
                self.assertEqual(stored.model_dump(mode="json"), paused)
                async with AsyncPostgresSaver.from_conn_string(url) as saver:
                    workflow = self.workflow(saver)
                    retained = await workflow.get(paused["incident_id"])
                    self.assertEqual(retained.model_dump(mode="json"), paused)
                    approval = ApprovalRequest(decision="reject", actor="recovery-test-operator",
                                               reason="Verify process crash recovery without execution",
                                               idempotency_key=f"{retained.incident_id}-post-crash-rejection")
                    self.assertTrue(await repository.claim_approval(retained, approval))
                    final = await workflow.resume_with_approval(retained.incident_id, approval)
                    await repository.save(final)
                    self.assertEqual(final.status, "rejected")
                    self.assertEqual(final.evidence, retained.evidence)
                    self.assertEqual(final.trace[:len(retained.trace)], retained.trace)
                    workflow.tools.rollback_release.assert_not_awaited()
                    workflow.tools.restart_deployment.assert_not_awaited()
                    self.assertFalse(await repository.claim_approval(final, approval))
            finally:
                await repository.close()
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                final = await self.workflow(saver).get(paused["incident_id"])
                self.assertEqual(final.status, "rejected")
                self.assertEqual(final.approval_actor, "recovery-test-operator")
        asyncio.run(recover())

    def workflow(self, saver, generator=None):
        tools = DemoTelemetryTools()
        tools.restart_deployment = AsyncMock(wraps=tools.restart_deployment)
        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
        return LangGraphIncidentWorkflow(
            tools=tools, checkpointer=saver,
            approval_signer=ApprovalReceiptSigner("isolated-recovery-test-secret"),
            hypothesis_generator=generator,
        )

    def test_approval_interrupt_survives_fresh_workflow_and_connections(self):
        async def scenario():
            url = os.environ["INCIDENT_TEST_DATABASE_URL"]
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                await saver.setup()
                first = self.workflow(saver)
                paused = await first.start(IncidentQuestion(question="Why is payment-service slow today?"))
                self.assertEqual(paused.status, "waiting_approval")
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                recovered = self.workflow(saver)
                retained = await recovered.get(paused.incident_id)
                self.assertEqual(retained.model_dump(), paused.model_dump())
                result = await recovered.resume_with_approval(paused.incident_id, ApprovalRequest(
                    decision="reject", actor="test-operator", reason="Recovery verification only",
                    idempotency_key=f"{paused.incident_id}-reject",
                ))
                self.assertEqual(result.status, "rejected")
                recovered.tools.restart_deployment.assert_not_awaited()
                recovered.tools.rollback_release.assert_not_awaited()
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                final = await self.workflow(saver).get(paused.incident_id)
                self.assertEqual(final.status, "rejected")
                self.assertEqual(final.approval_actor, "test-operator")
                self.assertTrue(any(event.event == "human_decision" for event in final.trace))
        asyncio.run(scenario())

    def test_rejected_diagnosis_survives_fresh_saver_and_repository(self):
        async def scenario():
            url = os.environ["INCIDENT_TEST_DATABASE_URL"]
            generator = AsyncMock()
            generator.generate.return_value = ([Hypothesis(
                rank=1, cause="unvalidated output", confidence=0.99,
                evidence_ids=["missing", "also-missing"],
            )], {"model": "fixture"})
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                await saver.setup()
                workflow = self.workflow(saver, generator)
                failed = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
                self.assertEqual(failed.status, "insufficient_evidence")
                repository = await PostgresIncidentRepository.connect(url)
                try:
                    await repository.save(failed)
                finally:
                    await repository.close()
                workflow.tools.rollback_release.assert_not_awaited()
                workflow.tools.restart_deployment.assert_not_awaited()
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                retained = await self.workflow(saver).get(failed.incident_id)
                self.assertEqual(retained.model_dump(), failed.model_dump())
                repository = await PostgresIncidentRepository.connect(url)
                try:
                    stored = await repository.get(failed.incident_id)
                    self.assertEqual(stored.model_dump(), failed.model_dump())
                finally:
                    await repository.close()
                self.assertEqual(retained.hypotheses, [])
                self.assertTrue(any(event.event == "hypothesis_citations_rejected" for event in retained.trace))
                history = await self.workflow(saver).history(failed.incident_id)
                self.assertTrue(any(item.hypotheses for item in history))
                self.assertTrue(any(item.status == "insufficient_evidence" for item in history))
                generator.generate.assert_awaited_once()
        asyncio.run(scenario())
