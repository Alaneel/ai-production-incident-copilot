import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_dependency_benchmark import DependencyTools, fixtures, run_case  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402


@pytest.mark.parametrize("case", fixtures(), ids=lambda case: case["id"])
def test_dependency_benchmark_checks_observations_not_causal_claims(case):
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["actual_status"] == record["expected_status"]
    assert record["actual_overlap"] == record["expected_overlap"]
    assert record["primary_classification_preserved"]
    assert record["checkpoint_roundtrip"]
    assert record["no_causal_direction_claim"]
    assert record["execution_attempts"] == 0
    assert "synthetic private" not in str(record["state"])


def test_dependency_provider_receives_no_expected_labels():
    case = fixtures()[0]
    tools = DependencyTools(service=case["service"], dependency=case["dependency"],
                            observations=case["observations"], index=KnowledgeIndex.load_default())
    assert not hasattr(tools, "expected_status")
    assert not hasattr(tools, "expected_overlap")
