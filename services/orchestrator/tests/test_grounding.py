import unittest
from datetime import UTC, datetime

from incident_copilot.guardrails import GuardrailViolation, validate_grounding
from incident_copilot.models import Evidence, EvidenceKind, Hypothesis, IncidentState


class GroundingTests(unittest.TestCase):
    def test_invalid_alternative_cannot_hide_behind_valid_leading_hypothesis(self):
        metric = self.evidence(EvidenceKind.METRIC)
        log = self.evidence(EvidenceKind.LOG)
        runbook = self.evidence(EvidenceKind.RUNBOOK)
        deployment = self.evidence(EvidenceKind.DEPLOYMENT)
        invalid = self.evidence(EvidenceKind.METRIC, attributes={"series": []})
        for ids, error in (
            ([metric.id, "missing"], "retained evidence"),
            ([metric.id], "retained evidence"),
            ([runbook.id, deployment.id], "metric or log"),
            ([metric.id, invalid.id], "unavailable or invalid"),
        ):
            with self.subTest(ids=ids):
                state = self.state([metric, log, runbook, deployment, invalid])
                state.hypotheses[0].evidence_ids = [metric.id, log.id]
                state.hypotheses.append(Hypothesis(rank=2, cause="alternative", confidence=0.3, evidence_ids=ids))
                with self.assertRaisesRegex(GuardrailViolation, f"rank 2.*{error}"):
                    validate_grounding(state)

    def test_valid_alternatives_are_checked_independently(self):
        metric = self.evidence(EvidenceKind.METRIC)
        log = self.evidence(EvidenceKind.LOG)
        runbook = self.evidence(EvidenceKind.RUNBOOK)
        state = self.state([metric, log, runbook])
        state.hypotheses.append(Hypothesis(rank=2, cause="alternative", confidence=0.3,
                                          evidence_ids=[log.id, runbook.id]))
        validate_grounding(state)

    def test_duplicate_retained_id_cannot_satisfy_grounding(self):
        metric = self.evidence(EvidenceKind.METRIC)
        log = self.evidence(EvidenceKind.LOG)
        duplicate = metric.model_copy(update={"observation": "contradicting retained observation"})
        with self.assertRaisesRegex(GuardrailViolation, "ambiguous"):
            validate_grounding(self.state([metric, log, duplicate]))

    def evidence(self, kind, **changes):
        return Evidence(
            kind=kind, source="test", title="retained evidence",
            observation="bounded observation", observed_at=datetime.now(UTC),
            service="payment-service", score=changes.get("score", 0.9),
            attributes=changes.get("attributes", {}),
        )

    def state(self, evidence):
        return IncidentState(
            question="Why is payment-service slow?", environment="prod-sg",
            services=["payment-service"], evidence=evidence,
            hypotheses=[Hypothesis(
                rank=1, cause="candidate cause", confidence=0.8,
                evidence_ids=[item.id for item in evidence],
            )],
        )

    def test_unavailable_evidence_cannot_satisfy_citation_guardrail(self):
        runbook = self.evidence(EvidenceKind.RUNBOOK)
        cases = [
            self.evidence(EvidenceKind.METRIC, score=0),
            self.evidence(EvidenceKind.METRIC, attributes={"series": []}),
            self.evidence(EvidenceKind.METRIC, attributes={"series": [{"value": "NaN"}]}),
            self.evidence(EvidenceKind.METRIC, attributes={"series": [{"value": True}]}),
            self.evidence(EvidenceKind.LOG, attributes={"events": []}),
            self.evidence(EvidenceKind.LOG, attributes={"events": [{"message": " "}]}),
            self.evidence(EvidenceKind.TRACE, attributes={"traces": []}),
        ]
        for item in cases:
            with self.subTest(kind=item.kind, attributes=item.attributes):
                with self.assertRaisesRegex(GuardrailViolation, "unavailable or invalid"):
                    validate_grounding(self.state([item, runbook]))

    def test_knowledge_and_deployment_alone_cannot_prove_root_cause(self):
        for kind in (EvidenceKind.INCIDENT, EvidenceKind.DEPLOYMENT, EvidenceKind.TRACE):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(GuardrailViolation, "metric or log"):
                    validate_grounding(self.state([
                        self.evidence(kind), self.evidence(EvidenceKind.RUNBOOK),
                    ]))

    def test_valid_metric_with_independent_knowledge_is_allowed(self):
        validate_grounding(self.state([
            self.evidence(EvidenceKind.METRIC, attributes={"series": [{"value": 0.95}]}),
            self.evidence(EvidenceKind.RUNBOOK),
        ]))

    def test_valid_logs_with_independent_knowledge_are_allowed(self):
        validate_grounding(self.state([
            self.evidence(EvidenceKind.LOG, attributes={"events": [{"message": "acquisition timed out"}]}),
            self.evidence(EvidenceKind.RUNBOOK),
        ]))
