import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("scenario,expected_deletes", [
    ("existing", 0), ("inventory_failure", 0), ("create_failure", 0), ("build_failure", 1), ("keep_created", 0),
    ("apply_failure", 1),
    ("delete_failure", 1), ("delete_inventory_failure", 1),
    ("existing_report", 0), ("missing_cached_image", 0),
])
def test_kind_verifier_only_cleans_up_a_cluster_it_successfully_created(tmp_path, scenario, expected_deletes):
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls.jsonl"
    # Fake every external mutation command: the real Docker/Kubernetes tools
    # cannot be reached, and no cluster is created or removed by this test.
    stub = f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["OWNERSHIP_TEST_LOG"], "a") as log:
    log.write(json.dumps([command, *args]) + "\\n")
scenario = os.environ["OWNERSHIP_TEST_SCENARIO"]
if command == "kind" and args == ["get", "clusters"]:
    calls = [json.loads(line) for line in Path(os.environ["OWNERSHIP_TEST_LOG"]).read_text().splitlines()]
    if scenario == "delete_inventory_failure" and any(call[:3] == ["kind", "delete", "cluster"] for call in calls):
        sys.exit(2)
    if scenario == "inventory_failure":
        sys.exit(2)
    if scenario == "missing_cached_image":
        assert not any(call[:3] == ["kind", "create", "cluster"] for call in calls)
        assert not any(call[:2] == ["docker", "build"] for call in calls)
    if scenario == "existing":
        print("ownership-test-cluster")
elif command == "kind" and args[:2] == ["create", "cluster"]:
    target = Path(args[args.index("--kubeconfig") + 1])
    target.write_text("private fake test kubeconfig")
    sys.exit(2 if scenario == "create_failure" else 0)
elif command == "docker":
    if args[:2] == ["image", "inspect"]:
        sys.exit(3 if scenario == "missing_cached_image" else 0)
    sys.exit(0 if scenario == "apply_failure" else 3)
elif command == "kubectl":
    sys.exit(4)
elif command == "kind" and args[:2] == ["delete", "cluster"]:
    sys.exit(2 if scenario == "delete_failure" else 0)
'''
    for name in ("docker", "kind", "kubectl", "curl", "jq"):
        path = commands / name
        path.write_text(stub)
        path.chmod(0o755)
    environment = {**os.environ, "PATH": f"{commands}:/usr/bin:/bin",
                   "KIND_CLUSTER_NAME": "ownership-test-cluster",
                   "OWNERSHIP_TEST_LOG": str(log), "OWNERSHIP_TEST_SCENARIO": scenario,
                   "KEEP_KIND_CLUSTER": "true" if scenario == "keep_created" else "false",
                   "KIND_RESULT_PATH": ""}
    previous_report = tmp_path / "previous-report.json"
    previous_report.write_text('{"historical":true}')
    if scenario == "existing_report":
        environment["KIND_RESULT_PATH"] = str(previous_report)
    caller_config = tmp_path / "caller-config"
    caller_config.write_text("unrelated production context sentinel")
    environment["KUBECONFIG"] = str(caller_config)
    result = subprocess.run(["/bin/bash", str(ROOT / "deploy/kubernetes/verify_kind.sh")],
                            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert previous_report.read_text() == '{"historical":true}'
    if scenario == "existing_report":
        assert not calls
        assert "not overwritten" in result.stderr
    assert caller_config.read_text() == "unrelated production context sentinel"
    creates = [call for call in calls if call[:3] == ["kind", "create", "cluster"]]
    if creates:
        private = Path(creates[0][creates[0].index("--kubeconfig") + 1])
        assert private != caller_config
        assert private.parent.name.startswith("copilot-kind-kubeconfig.")
        api_calls = [call for call in calls if call[0] == "kubectl"]
        for call in api_calls:
            assert call[1:5] == ["--kubeconfig", str(private), "--context", "kind-ownership-test-cluster"]
        if scenario == "apply_failure":
            assert api_calls
        if expected_deletes and scenario not in {"delete_failure", "delete_inventory_failure"}:
            assert not private.exists()
        else:
            assert private.exists()
            private.unlink()
            private.parent.rmdir()
    for call in calls:
        if call[:2] == ["docker", "build"]:
            assert call[call.index("-t") + 1].endswith(":kind-ownership-test-cluster")
    deletes = [call for call in calls if call[:3] == ["kind", "delete", "cluster"]]
    assert len(deletes) == expected_deletes
    if deletes:
        assert deletes[0] == ["kind", "delete", "cluster", "--name", "ownership-test-cluster"]
    if scenario == "missing_cached_image":
        assert not any(call[:3] == ["kind", "create", "cluster"] for call in calls)
        assert not any(call[:2] == ["docker", "build"] for call in calls)
    if scenario == "existing":
        assert "will not be replaced" in result.stderr
        assert not any(call[0] == "docker" or call[:3] == ["kind", "create", "cluster"] for call in calls)
    if scenario == "inventory_failure":
        assert not any(call[0] == "docker" or call[:3] == ["kind", "create", "cluster"] for call in calls)
