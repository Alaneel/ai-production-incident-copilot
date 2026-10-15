"""One model-backed diagnosis on synthetic telemetry; never approve execution."""
from __future__ import annotations

import asyncio
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/orchestrator"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.models import IncidentQuestion  # noqa: E402
from incident_copilot.reasoning import OpenAIResponsesHypothesisGenerator  # noqa: E402
from incident_copilot.tools import DemoTelemetryTools  # noqa: E402


class DiagnosisOnlyTools(DemoTelemetryTools):
    """Refuse mutations even if a future workflow accidentally skips approval."""

    def __init__(self):
        super().__init__()
        self.execution_attempts = 0
        self.dry_run_calls = 0

    async def rollback_release(self, **arguments):
        self._check(arguments)
        return await super().rollback_release(**arguments)

    async def restart_deployment(self, **arguments):
        self._check(arguments)
        return await super().restart_deployment(**arguments)

    def _check(self, arguments):
        if arguments.get("dry_run") is not True:
            self.execution_attempts += 1
            raise RuntimeError("Diagnosis smoke forbids execution")
        self.dry_run_calls += 1


async def run_diagnosis(generator):
    tools = DiagnosisOnlyTools()
    workflow = LangGraphIncidentWorkflow(
        tools=tools,
        checkpointer=InMemorySaver(),
        approval_signer=ApprovalReceiptSigner("smoke-only-not-a-production-secret"),
        hypothesis_generator=generator,
    )
    started = time.perf_counter()
    incident = await workflow.start(IncidentQuestion(
        question="为什么 payment-service 今天延迟突然升高？"
    ))
    recovered = await workflow.get(incident.incident_id)
    checks = {
        "checkpoint_roundtrip": recovered.model_dump() == incident.model_dump(),
        "no_execution_attempts": tools.execution_attempts == 0,
        "no_approval": incident.approval_decision is None and incident.approval_actor is None,
        "destructive_proposal_paused": (
            incident.remediation is None
            or incident.remediation.tool not in {"restart_deployment", "rollback_release"}
            or (incident.status == "waiting_approval" and incident.remediation.requires_approval)
        ),
    }
    package = ROOT / "services/orchestrator/incident_copilot"
    sources = [Path(__file__), *sorted(package.glob("*.py"))]
    return {
        "ok": all(checks.values()),
        "run_at": datetime.now(UTC).isoformat(),
        "scope": "synthetic telemetry + configured hypothesis generator + in-memory LangGraph",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "production_actions_executed": False,
        "execution_attempts": tools.execution_attempts,
        "dry_run_calls": tools.dry_run_calls,
        "checks": checks,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sources},
        "semantic_groundedness": None,
        "limitations": ["Single synthetic case, not RCA accuracy", "Citation validation does not prove semantic entailment",
                        "In-memory checkpoint read, not process restart or PostgreSQL recovery"],
        "incident": incident.model_dump(mode="json"),
    }


async def main() -> None:
    config = {**os.environ, **dotenv_values(ROOT / ".env.local")}
    if not config.get("OPENAI_API_KEY") or not config.get("OPENAI_MODEL"):
        print(json.dumps({"ok": False, "error": "Missing OPENAI_API_KEY or OPENAI_MODEL"}))
        raise SystemExit(1)
    generator = OpenAIResponsesHypothesisGenerator(
        api_key=config["OPENAI_API_KEY"],
        model=config["OPENAI_MODEL"],
        base_url=config.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        max_attempts=1,
        timeout_seconds=55,
    )
    started = time.perf_counter()
    try:
        report = await run_diagnosis(generator)
        incident = report["incident"]
        target = ROOT / "evals/results" / ("diagnosis_smoke_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({
            "ok": report["ok"],
            "status": incident["status"],
            "duration_seconds": report["duration_seconds"],
            "hypotheses": [h["cause"] for h in incident["hypotheses"]],
            "evidence_count": len(incident["evidence"]),
            "remediation_tool": incident["remediation"]["tool"] if incident["remediation"] else None,
            "report": str(target),
        }, ensure_ascii=False))
        if not report["ok"]:
            raise SystemExit(1)
    except Exception as error:
        # Provider responses and transport exceptions can contain credentials/URLs.
        match = re.fullmatch(r"OpenAI Responses API rejected the request: (\d{3})", str(error))
        failure = {"ok": False, "error_type": type(error).__name__,
                   "http_status": int(match[1]) if match else None,
                   "cause_type": type(error.__cause__).__name__ if error.__cause__ else None,
                   "duration_seconds": round(time.perf_counter() - started, 3)}
        print(json.dumps(failure))
        raise SystemExit(1) from None
    finally:
        await generator.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-api-cost", action="store_true",
                        help="Authorize one paid model request; no production execution.")
    if not parser.parse_args().confirm_api_cost:
        parser.error("--confirm-api-cost is required before reading credentials or making a model request")
    asyncio.run(main())
