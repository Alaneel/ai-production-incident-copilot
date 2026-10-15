import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify_compose_platform.py"
spec = importlib.util.spec_from_file_location("compose_platform_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def response(text="", code=0):
    return SimpleNamespace(stdout=text, stderr="private failure details", returncode=code)


def test_advisory_gate_uses_the_actual_remediation_tool_contract():
    state = {"status": "diagnosed", "approval_decision": None, "remediation": {
        "tool": "none", "requires_approval": False, "dry_run_passed": False}}
    assert verifier.advisory_only(state)
    for change in ({"tool": "rollback_release"}, {"requires_approval": True},
                   {"dry_run_passed": True}, {"tool": None}):
        altered = {**state, "remediation": {**state["remediation"], **change}}
        assert not verifier.advisory_only(altered)
    assert not verifier.advisory_only({**state, "status": "waiting_approval"})
    assert not verifier.advisory_only({**state, "approval_decision": "approve"})
    assert not verifier.advisory_only({"remediation": {"action": "none"}})
    assert not verifier.advisory_only({"remediation": None})


@pytest.mark.parametrize("published,allocated", [(True, False), (False, True)])
def test_preflight_refuses_ports_or_existing_project_without_startup_or_cleanup(published, allocated):
    commands = []

    def execute(args, **kwargs):
        commands.append(args)
        if "config" in args:
            return response(json.dumps({"services": {"fixture": {"image": "sha256:fixture", "ports": ["8080"] if published else []}}}))
        if "inspect" in args:
            return response("sha256:fixture\n")
        return response("existing\n" if allocated else "")

    with patch.object(verifier.subprocess, "run", side_effect=execute):
        with pytest.raises(ValueError):
            verifier.run({"orchestrator": "fixture"})
    assert not any("up" in cmd or "down" in cmd for cmd in commands)


@pytest.mark.parametrize("cleanup_listing_fails", [False, True])
def test_failed_startup_cleans_only_new_project_and_never_loads_model_credentials(monkeypatch, cleanup_listing_fails):
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "private-test-model")
    monkeypatch.setenv("POSTGRES_PASSWORD", "private-test-password")
    commands = []
    allocated = [False]

    def execute(args, **kwargs):
        commands.append(args)
        assert kwargs["env"]["OPENAI_API_KEY"] == kwargs["env"]["OPENAI_MODEL"] == ""
        assert kwargs["env"]["POSTGRES_PASSWORD"] != "private-test-password"
        if "config" in args:
            return response(json.dumps({"services": {"fixture": {"image": "sha256:fixture"}}}))
        if "inspect" in args:
            return response("sha256:fixture\n")
        if "up" in args:
            allocated[0] = True
            assert "--no-build" in args and args[-2:] == ["--pull", "never"]
            return response(code=1)
        if allocated[0] and "--filter" in args and cleanup_listing_fails:
            return response(code=1)
        return response()

    with patch.object(verifier.uuid, "uuid4", return_value=SimpleNamespace(hex="fixture")), patch.object(verifier.subprocess, "run", side_effect=execute):
        report = verifier.run({"orchestrator": "fixture"})
    assert not report["ok"]
    assert report["cleanup_complete"] is not cleanup_listing_fails
    assert report["error_type"] == "ComposeOperationFailure"
    assert report["operation_error"]["returncode"] == 1
    assert report["model_calls"] == report["production_actions"] == 0
    assert "private-test" not in str(report)
    down = next(cmd for cmd in commands if "down" in cmd)
    assert down[down.index("--project-name") + 1] == "copilot-compose-acceptance-fixture"
    assert down[down.index("--env-file") + 1] == "/dev/null"
    assert "--volumes" in down
    assert not any("prune" in cmd for cmd in commands)
