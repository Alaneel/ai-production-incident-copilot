import asyncio
import os
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from incident_copilot.api import app
from incident_copilot.models import Hypothesis, IncidentState, IncidentStatus, Remediation
from incident_copilot.notifications import NotificationSettings, build_notification, send_notification
from incident_copilot.store import InMemoryIncidentStore, PostgresIncidentRepository, incident_summary


def state(incident_id, status=IncidentStatus.WAITING_APPROVAL, *, minutes=0, cause="Pool exhausted after v142"):
    moment = datetime(2026, 11, 25, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)
    value = IncidentState(incident_id=incident_id, question="Why is checkout slow?", environment="prod-sg",
                          status=status, services=["checkout", "payments"], severity="SEV-2",
                          created_at=moment, updated_at=moment)
    if cause:
        value.hypotheses = [Hypothesis(rank=1, cause=cause, confidence=0.87, evidence_ids=["ev_1"])]
    return value


class IncidentListTests(unittest.TestCase):
    def test_summary_is_a_bounded_projection_without_evidence_or_commands(self):
        summary = incident_summary(state("INC-AAA111", cause="x" * 900))
        self.assertEqual(set(summary), {"incident_id", "status", "environment", "service", "severity", "question",
                                        "leading_cause", "proposed_action", "created_at", "updated_at"})
        self.assertEqual(summary["service"], "checkout")
        self.assertEqual(len(summary["leading_cause"]), 240)

    def test_memory_store_lists_newest_first_with_status_filter_and_limit(self):
        async def scenario():
            store = InMemoryIncidentStore()
            await store.save(state("INC-OLD001", IncidentStatus.DIAGNOSED, minutes=0))
            await store.save(state("INC-MID002", minutes=5))
            await store.save(state("INC-NEW003", IncidentStatus.DIAGNOSED, minutes=9))
            return (await store.list_recent(limit=2), await store.list_recent(status="waiting_approval"))
        newest, waiting = asyncio.run(scenario())
        self.assertEqual([item["incident_id"] for item in newest], ["INC-NEW003", "INC-MID002"])
        self.assertEqual([item["incident_id"] for item in waiting], ["INC-MID002"])

    def test_list_endpoint_returns_summaries_and_validates_its_bounds(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/incidents").json(), {"incidents": [], "count": 0})
            created = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
            body = client.get("/v1/incidents").json()
            self.assertEqual(body["count"], 1)
            row = body["incidents"][0]
            self.assertEqual(row["incident_id"], created["incident_id"])
            self.assertEqual(row["status"], created["status"])
            self.assertNotIn("evidence", row)
            self.assertNotIn("trace", row)
            self.assertEqual(client.get("/v1/incidents", params={"status": "resolved"}).json()["count"], 0)
            self.assertEqual(client.get("/v1/incidents", params={"status": created["status"]}).json()["count"], 1)
            for bad in ({"limit": 0}, {"limit": 101}, {"status": "nonsense"}):
                self.assertEqual(client.get("/v1/incidents", params=bad).status_code, 422)

    def test_list_requires_a_credential_when_auth_is_required(self):
        read = "r" * 40
        environment = {"ORCHESTRATOR_AUTH_MODE": "required", "ORCHESTRATOR_READ_TOKEN": read,
                       "ORCHESTRATOR_OPERATOR_TOKEN": "o" * 40, "ORCHESTRATOR_OPERATOR_ACTOR": "oncall-operator"}
        with patch.dict(os.environ, environment), TestClient(app) as client:
            self.assertEqual(client.get("/v1/incidents").status_code, 401)
            self.assertEqual(client.get("/v1/incidents", headers={"Authorization": f"Bearer {read}"}).status_code, 200)


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_postgres_list_matches_memory_ordering_and_filter():
    async def scenario():
        repository = await PostgresIncidentRepository.connect(os.environ["INCIDENT_TEST_DATABASE_URL"])
        marker = f"{time.time_ns() % 10**8:08d}"
        ids = [f"INC-L{marker}{suffix}" for suffix in "ABC"]
        try:
            # Far-future timestamps keep these rows first regardless of other tests' data.
            await repository.save(state(ids[0], IncidentStatus.DIAGNOSED, minutes=60 * 24 * 365 * 50))
            await repository.save(state(ids[1], minutes=60 * 24 * 365 * 50 + 5))
            await repository.save(state(ids[2], IncidentStatus.DIAGNOSED, minutes=60 * 24 * 365 * 50 + 9))
            newest = await repository.list_recent(limit=2)
            waiting = await repository.list_recent(limit=100, status="waiting_approval")
            return ids, newest, waiting
        finally:
            async with repository.pool.connection() as connection:
                await connection.execute("DELETE FROM incidents WHERE incident_id = ANY(%s)", (ids,))
            await repository.close()
    ids, newest, waiting = asyncio.run(scenario())
    assert [item["incident_id"] for item in newest] == [ids[2], ids[1]]
    assert ids[1] in [item["incident_id"] for item in waiting]
    assert all(item["status"] == "waiting_approval" for item in waiting)


class NotificationTests(unittest.TestCase):
    def test_message_points_to_the_console_and_cannot_form_markup_or_mentions(self):
        incident = state("INC-NOTE01", cause="<!channel> pool exhausted @here & <https://evil.test|click>")
        incident.remediation = Remediation(title="Rollback checkout to v141", tool="rollback_release", arguments={},
                                           command_preview="kubectl rollout undo deployment/checkout",
                                           expected_result="Restores v141", risk="high",
                                           requires_approval=True, dry_run_passed=True)
        note = build_notification(incident, alertname="HighLatency", console_url="https://ops.example.test/?incident=INC-NOTE01")
        self.assertTrue(note["requires_approval"])
        self.assertIn("INC-NOTE01 for checkout needs a human decision", note["text"])
        self.assertIn("Proposed (not executed): Rollback checkout to v141", note["text"])
        self.assertIn("Review: https://ops.example.test/?incident=INC-NOTE01", note["text"])
        hypothesis_line = next(line for line in note["text"].splitlines() if line.startswith("Leading hypothesis"))
        self.assertNotIn("<", hypothesis_line)
        self.assertNotIn("@here", hypothesis_line)
        # The message is a pointer: no command, evidence or credential fields.
        self.assertNotIn("kubectl", str(note))
        self.assertEqual(set(note), {"event", "incident_id", "status", "service", "environment", "alertname",
                                     "requires_approval", "console_url", "text"})

    def test_only_states_that_need_attention_notify(self):
        self.assertIsNone(build_notification(state("INC-RUN001", IncidentStatus.INVESTIGATING), alertname=None, console_url=None))
        self.assertIsNone(build_notification(state("INC-DONE01", IncidentStatus.RESOLVED), alertname=None, console_url=None))
        unknown = build_notification(state("INC-NOEV01", IncidentStatus.INSUFFICIENT_EVIDENCE, cause=None), alertname=None, console_url=None)
        self.assertIn("could not be diagnosed", unknown["text"])
        self.assertFalse(unknown["requires_approval"])
        self.assertIn("Review it in the OpsPilot console.", unknown["text"])

    def test_settings_reject_unsafe_receivers(self):
        self.assertIsNone(NotificationSettings.from_environment({}).url)
        for url in ("ftp://hooks.example.test/x", "https://user:secret@hooks.example.test/x", "hooks.example.test"):
            with self.assertRaises(ValueError):
                NotificationSettings.from_environment({"NOTIFY_WEBHOOK_URL": url})
        with self.assertRaises(ValueError):
            NotificationSettings.from_environment({"NOTIFY_WEBHOOK_URL": "https://hooks.example.test/x", "NOTIFY_WEBHOOK_FORMAT": "xml"})

    def test_delivery_formats_and_failures_are_contained(self):
        note = build_notification(state("INC-SEND01"), alertname="HighLatency", console_url=None)
        seen = []

        def receiver(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"Location": "https://elsewhere.example.test/"})
            if request.url.path == "/down":
                raise httpx.ConnectError("refused")
            return httpx.Response(200)

        async def scenario():
            transport = httpx.MockTransport(receiver)
            slack = NotificationSettings("https://hooks.example.test/slack", "slack")
            generic = NotificationSettings("https://hooks.example.test/json", "json")
            return [await send_notification(slack, note, transport=transport),
                    await send_notification(generic, note, transport=transport),
                    await send_notification(NotificationSettings("https://hooks.example.test/redirect", "json"), note, transport=transport),
                    await send_notification(NotificationSettings("https://hooks.example.test/down", "json"), note, transport=transport),
                    await send_notification(NotificationSettings(None, "json"), note, transport=transport)]
        self.assertEqual(asyncio.run(scenario()), [True, True, False, False, False])
        import json
        self.assertEqual(json.loads(seen[0].content), {"text": note["text"]})
        self.assertEqual(json.loads(seen[1].content)["incident_id"], "INC-SEND01")
        # The redirect was reported as undelivered and never followed.
        self.assertEqual([request.url.host for request in seen], ["hooks.example.test"] * 4)

    def test_alert_diagnosis_notifies_once_after_saving_and_survives_delivery_failure(self):
        payload = {"alerts": [{"status": "firing", "fingerprint": "notify-1",
                               "labels": {"alertname": "HighLatency", "service": "payment-service"},
                               "annotations": {"summary": "p95 above 2s"}}]}
        environment = {"NOTIFY_WEBHOOK_URL": "https://hooks.example.test/x", "CONSOLE_URL": "https://ops.example.test"}
        with patch.dict(os.environ, environment), \
                patch("incident_copilot.api.send_notification", new=AsyncMock(return_value=False)) as sender, \
                TestClient(app) as client:
            accepted = client.post("/v1/incidents/alerts/alertmanager", json=payload).json()["accepted"][0]
            deadline = time.monotonic() + 20
            while sender.await_count == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(sender.await_count, 1)
            note = sender.await_args.args[1]
            self.assertEqual(note["incident_id"], accepted["incident_id"])
            self.assertEqual(note["alertname"], "HighLatency")
            self.assertEqual(note["console_url"], accepted["console_url"])
            # The incident was already saved, and an unacknowledged delivery changes nothing.
            stored = client.get(f"/v1/incidents/{accepted['incident_id']}").json()
            self.assertEqual(stored["status"], note["status"])
            self.assertIn(accepted["incident_id"], [row["incident_id"] for row in client.get("/v1/incidents").json()["incidents"]])


if __name__ == "__main__":
    unittest.main()
