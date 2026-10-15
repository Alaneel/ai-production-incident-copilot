import subprocess
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from run_diagnosis_smoke import DiagnosisOnlyTools, run_diagnosis  # noqa: E402
from incident_copilot.models import Hypothesis  # noqa: E402


class LocalGenerator:
    def __init__(self, cause="Local test hypothesis; causality unconfirmed"):
        self.cause = cause

    async def generate(self, *, question, incident_type, evidence):
        metric = next(item for item in evidence if item.kind == "metric")
        deployment = next(item for item in evidence if item.kind == "deployment")
        return [Hypothesis(rank=1, cause=self.cause,
                           confidence=0.5, evidence_ids=[metric.id, deployment.id],
                           validation_notes=["Offline fixture, no model request"])], {"provider": "local-test"}


@pytest.mark.parametrize("cause,status,dry_runs", [
    ("Local test hypothesis; causality unconfirmed", "diagnosed", 0),
    ("Database connection pool exhaustion; causality unconfirmed", "waiting_approval", 1),
])
def test_offline_full_workflow_retains_safety_checks_and_provenance(cause, status, dry_runs):
    report = asyncio.run(run_diagnosis(LocalGenerator(cause)))
    assert report["ok"]
    assert all(report["checks"].values())
    assert report["incident"]["status"] == status
    assert report["execution_attempts"] == 0
    assert report["dry_run_calls"] == dry_runs
    if status == "diagnosed":
        assert report["incident"]["remediation"]["tool"] == "none"
        assert report["incident"]["hypotheses"][0]["cause"] == cause
    assert report["semantic_groundedness"] is None
    assert report["source_sha256"]
    assert all(len(value) == 64 for value in report["source_sha256"].values())


def test_safety_pass_does_not_claim_a_rejected_hypothesis_is_grounded():
    class UnsupportedGenerator:
        async def generate(self, **kwargs):
            return [Hypothesis(rank=1, cause="Unsupported local test hypothesis",
                               confidence=0.9, evidence_ids=["missing-1", "missing-2"])], {}

    report = asyncio.run(run_diagnosis(UnsupportedGenerator()))
    assert report["ok"]
    assert report["incident"]["status"] == "insufficient_evidence"
    assert report["incident"]["hypotheses"] == []
    assert report["semantic_groundedness"] is None
    assert report["dry_run_calls"] == 0


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
@pytest.mark.parametrize("dry_run", [False, None, "true", 1])
def test_smoke_refuses_every_non_boolean_dry_run_before_delegating(tool, dry_run):
    tools = DiagnosisOnlyTools()
    with pytest.raises(RuntimeError, match="forbids execution"):
        asyncio.run(getattr(tools, tool)(dry_run=dry_run))
    assert tools.execution_attempts == 1
    assert tools.dry_run_calls == 0


def test_smoke_requires_explicit_cost_confirmation_before_running():
    root = Path(__file__).resolve().parents[2]
    import sys

    result = subprocess.run(
        [sys.executable, str(root / "evals/run_diagnosis_smoke.py")],
        cwd=root, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "--confirm-api-cost is required" in result.stderr
    assert not result.stdout
