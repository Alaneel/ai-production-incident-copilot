import asyncio
import os
import unittest

from incident_copilot.models import IncidentState, ApprovalRequest, ApprovalDecision
from incident_copilot.store import ApprovalConflictError, InMemoryIncidentStore, PostgresIncidentRepository


async def verify_claims(test, repositories):
    incident = IncidentState(question="Investigate payment-service", environment="prod-sg")
    other = IncidentState(question="Investigate ledger-service", environment="prod-sg")
    await repositories[0].save(incident)
    await repositories[0].save(other)
    keys = [f"{incident.incident_id}-operator-{index}" for index in range(20)]
    outcomes = await asyncio.gather(*(
        repositories[index % len(repositories)].claim_idempotency_key(
            incident.incident_id, key, "remediation_approval",
        ) for index, key in enumerate(keys)
    ))
    test.assertEqual(sum(outcomes), 1)
    winner = keys[outcomes.index(True)]
    loser = keys[outcomes.index(False)]
    await repositories[0].release_idempotency_key(loser)
    test.assertFalse(await repositories[-1].claim_idempotency_key(
        incident.incident_id, "another-operator", "remediation_approval",
    ))
    test.assertFalse(await repositories[-1].claim_idempotency_key(
        other.incident_id, winner, "remediation_approval",
    ))
    test.assertTrue(await repositories[-1].claim_idempotency_key(
        incident.incident_id, f"{incident.incident_id}-other-operation", "report_export",
    ))
    await repositories[0].release_idempotency_key(winner)
    test.assertTrue(await repositories[-1].claim_idempotency_key(
        incident.incident_id, f"{incident.incident_id}-replacement", "remediation_approval",
    ))


class MemoryClaimTests(unittest.TestCase):
    def test_concurrent_operators_share_one_incident_operation_claim(self):
        asyncio.run(verify_claims(self, [InMemoryIncidentStore()]))

    def test_approval_key_cannot_change_actor_after_release(self):
        asyncio.run(verify_binding(self, InMemoryIncidentStore()))


async def verify_binding(test, repository):
    state = IncidentState(question="Investigate payment-service", environment="prod-sg")
    await repository.save(state)
    approval = ApprovalRequest(
        decision=ApprovalDecision.APPROVE, actor="first-operator",
        reason="Reviewed evidence", idempotency_key=f"{state.incident_id}-decision-key",
    )
    test.assertTrue(await repository.claim_approval(state, approval))
    await repository.release_idempotency_key(approval.idempotency_key)
    with test.assertRaises(ApprovalConflictError):
        await repository.claim_approval(state, approval.model_copy(update={"actor": "second-operator"}))
    with test.assertRaises(ApprovalConflictError):
        await repository.claim_approval(state, approval.model_copy(update={"decision": ApprovalDecision.REJECT}))
    test.assertTrue(await repository.claim_approval(state, approval))


@unittest.skipUnless(os.getenv("INCIDENT_TEST_DATABASE_URL"), "isolated PostgreSQL test URL required")
class PostgresClaimTests(unittest.TestCase):
    def test_approval_binding_survives_claim_release(self):
        async def scenario():
            repository = await PostgresIncidentRepository.connect(os.environ["INCIDENT_TEST_DATABASE_URL"])
            try:
                await verify_binding(self, repository)
            finally:
                await repository.close()
        asyncio.run(scenario())

    def test_audit_insert_failure_rolls_back_both_claims(self):
        async def scenario():
            from psycopg.errors import NotNullViolation
            repository = await PostgresIncidentRepository.connect(os.environ["INCIDENT_TEST_DATABASE_URL"])
            try:
                state = IncidentState(question="Investigate payment-service", environment="prod-sg")
                await repository.save(state)
                approval = ApprovalRequest(
                    decision=ApprovalDecision.APPROVE, actor="operator",
                    reason="Reviewed evidence", idempotency_key=f"{state.incident_id}-atomic-key",
                )
                with self.assertRaises(NotNullViolation):
                    await repository.claim_approval(state, approval.model_copy(update={"actor": None}))
                self.assertTrue(await repository.claim_approval(state, approval))
            finally:
                await repository.close()
        asyncio.run(scenario())

    def test_independent_repositories_share_one_database_claim(self):
        async def scenario():
            first = await PostgresIncidentRepository.connect(os.environ["INCIDENT_TEST_DATABASE_URL"])
            second = await PostgresIncidentRepository.connect(os.environ["INCIDENT_TEST_DATABASE_URL"])
            try:
                await verify_claims(self, [first, second])
            finally:
                await first.close()
                await second.close()
        asyncio.run(scenario())
