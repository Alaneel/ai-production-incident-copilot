import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_matrix():
    spec = importlib.util.spec_from_file_location("native_matrix_verifier", ROOT / "fault-lab/verify_native_matrix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_requires_opt_in_before_docker_or_fault_work():
    result = subprocess.run([sys.executable, str(ROOT / "fault-lab/verify_native_matrix.py"),
                             "--gateway-binary", "/nonexistent"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--run-local-integration is required" in result.stderr
    assert not result.stdout


def test_preflight_occupied_port_refuses_before_docker():
    matrix = load_matrix()
    binary, probe = MagicMock(), MagicMock()
    binary.is_file.return_value = True
    probe.__enter__.return_value.bind.side_effect = OSError("occupied")
    with patch.object(matrix.socket, "socket", return_value=probe), patch.object(matrix, "docker") as docker:
        with pytest.raises(OSError):
            matrix.preflight(binary)
        docker.assert_not_called()
        probe.__enter__.return_value.setsockopt.assert_called_once_with(matrix.socket.SOL_SOCKET, matrix.socket.SO_REUSEADDR, 1)


def test_matrix_preflight_reuses_closed_socket_addresses_without_reuseport():
    matrix = load_matrix()
    binary, probe = MagicMock(), MagicMock()
    binary.is_file.return_value = True
    with patch.object(matrix.socket, "socket", return_value=probe), patch.object(matrix, "docker", return_value=SimpleNamespace(stdout="cached image")):
        assert len(matrix.preflight(binary)) == 4
    assert probe.__enter__.return_value.setsockopt.call_count == len(matrix.PORTS)
    assert all(call.args == (matrix.socket.SOL_SOCKET, matrix.socket.SO_REUSEADDR, 1)
               for call in probe.__enter__.return_value.setsockopt.call_args_list)


def test_owned_stack_has_no_user_container_volume_or_image_cleanup():
    matrix = load_matrix()
    stack = matrix.OwnedStack()
    replies = SimpleNamespace(returncode=0, stdout="false")
    with patch.object(matrix, "docker", return_value=replies) as docker:
        stack.start()
        stack.close()
    calls = [call.args for call in docker.call_args_list]
    runs = [call for call in calls if call[0] == "run"]
    assert len(runs) == 4
    for call in runs:
        assert "--rm" in call and "--pull=never" in call
        assert call[call.index("--name") + 1].startswith(stack.network + "-")
        assert call[call.index("--publish") + 1].startswith("127.0.0.1:")
        assert "--volume" not in call
        assert call[call.index("--mount") + 1].endswith(",readonly")
    stops = [call[-1] for call in calls if call[0] == "stop"]
    assert stops == list(reversed(stack.names))
    assert calls[-1] == ("network", "rm", stack.network)
    assert not any(call[0] in ("rmi", "volume", "system", "prune") for call in calls)


def test_partial_launch_cleans_only_new_names():
    matrix = load_matrix()
    stack = matrix.OwnedStack()
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "run":
            raise RuntimeError("startup failed")
        return SimpleNamespace(returncode=0, stdout="false")

    with patch.object(matrix, "docker", side_effect=docker):
        with pytest.raises(RuntimeError):
            stack.start()
        stack.close()
    assert len(stack.names) == 1
    assert [args[-1] for args in calls if args[0] == "stop"] == stack.names


def test_matrix_stops_after_failed_cleanup_and_saves_checkpoint(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    stack = MagicMock()
    stack.close.side_effect = RuntimeError("cleanup failed")
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "preflight", return_value={}), patch.object(matrix, "OwnedStack", return_value=stack), patch.object(matrix, "probe_case", return_value={"scenario": matrix.SCENARIOS[0], "ok": True}) as probe:
        assert matrix.run(binary) == 1
        probe.assert_called_once()
    report, = tmp_path.glob("native_scenario_matrix_*.json")
    saved = json.loads(report.read_text())
    assert saved["ok"] is False
    assert saved["scenarios"][0]["cleanup_complete"] is False
    assert saved["scenarios"][0]["ok"] is False
    assert saved["source_sha256"] and saved["gateway_sha256"]


def test_probe_artifact_without_all_checks_is_not_success(tmp_path):
    matrix = load_matrix()
    scenario = matrix.SCENARIOS[0]
    report = tmp_path / f"native_scenario_{scenario}_test.json"
    report.write_text(json.dumps({"ok": True, "scenario": scenario, "checks": {"one": True},
                                  "model_calls": 0, "production_actions_executed": False}))
    process = MagicMock()
    process.communicate.return_value = (json.dumps({"report": str(report)}), "")
    process.returncode = 0
    with patch.object(matrix, "ROOT", tmp_path), patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix.subprocess, "Popen", return_value=process) as launch:
        result = matrix.probe_case(Path("/test-gateway"), scenario)
    assert result["ok"] is False
    assert launch.call_args.kwargs["start_new_session"] is True
    assert result["artifact_sha256"]


def test_probe_rejects_artifact_outside_results_directory(tmp_path):
    matrix = load_matrix()
    process = MagicMock()
    process.returncode = 0
    process.communicate.return_value = (json.dumps({"report": "/outside/native_scenario_db-pool-exhaustion_test.json"}), "")
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix.subprocess, "Popen", return_value=process), patch.object(Path, "read_text") as read:
        result = matrix.probe_case(Path("/test-gateway"), matrix.SCENARIOS[0])
    assert result["ok"] is False and result["error_type"] == "MissingCaseArtifact"
    read.assert_not_called()


def test_timeout_cleanup_targets_only_owned_process_group():
    matrix = load_matrix()
    process = MagicMock(pid=876543)
    process.communicate.side_effect = [subprocess.TimeoutExpired("owned probe", 10), ("", "")]
    with patch.object(matrix.os, "killpg") as kill:
        matrix.stop_probe(process)
    assert [call.args for call in kill.call_args_list] == [
        (process.pid, matrix.signal.SIGTERM), (process.pid, matrix.signal.SIGKILL),
    ]


def test_matrix_runs_all_cases_and_preserves_a_failed_case(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    stack = MagicMock()

    def case(_binary, scenario):
        return {"scenario": scenario, "ok": scenario != matrix.SCENARIOS[0]}

    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "preflight", return_value={}), patch.object(matrix, "OwnedStack", return_value=stack), patch.object(matrix, "probe_case", side_effect=case):
        assert matrix.run(binary) == 1
    report, = tmp_path.glob("native_scenario_matrix_*.json")
    saved = json.loads(report.read_text())
    assert [row["scenario"] for row in saved["scenarios"]] == list(matrix.SCENARIOS)
    assert saved["scenarios"][0]["ok"] is False and saved["ok"] is False
    assert all(row["cleanup_complete"] for row in saved["scenarios"])
    assert stack.close.call_count == 8


def test_interrupted_case_is_saved_and_owned_cleanup_runs(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    stack = MagicMock()
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "preflight", return_value={}), patch.object(matrix, "OwnedStack", return_value=stack), patch.object(matrix, "probe_case", side_effect=KeyboardInterrupt):
        assert matrix.run(binary) == 1
    report, = tmp_path.glob("native_scenario_matrix_*.json")
    saved = json.loads(report.read_text())
    assert len(saved["scenarios"]) == 1 and saved["ok"] is False
    assert saved["scenarios"][0]["error_type"] == "ProbeInterrupted"
    assert saved["scenarios"][0]["cleanup_complete"] is True
    stack.close.assert_called_once()


def test_durable_matrix_rejects_invalid_database_before_docker(tmp_path):
    matrix = load_matrix()
    with patch.object(matrix, "validate_database", side_effect=ValueError("invalid test database")), patch.object(matrix, "preflight") as preflight, patch.object(matrix, "OwnedStack") as stack:
        with pytest.raises(ValueError):
            matrix.run(tmp_path / "gateway", checkpoint_postgres=True)
    preflight.assert_not_called()
    stack.assert_not_called()


@pytest.mark.parametrize("missing", [None, "memory", "recovery_failed", *sorted(load_matrix().RECOVERY_CHECKS)])
def test_durable_case_requires_every_recovery_check_and_postgres(tmp_path, missing):
    matrix = load_matrix()
    scenario = matrix.SCENARIOS[0]
    checks = dict.fromkeys(matrix.REQUIRED_CHECKS | matrix.RECOVERY_CHECKS, True)
    if missing in checks:
        checks.pop(missing)
    report = tmp_path / f"native_scenario_{scenario}_test.json"
    report.write_text(json.dumps({
        "ok": True, "scenario": scenario, "checks": checks,
        "model_calls": 0, "production_actions_executed": False,
        "checkpoint_backend": "memory" if missing == "memory" else "postgresql",
        "fresh_process_recovery": {"ok": missing != "recovery_failed"},
    }))
    process = MagicMock()
    process.communicate.return_value = (json.dumps({"report": str(report)}), "")
    process.returncode = 0
    with patch.object(matrix, "ROOT", tmp_path), patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix.subprocess, "Popen", return_value=process) as launch:
        result = matrix.probe_case(Path("/test-gateway"), scenario, checkpoint_postgres=True)
    assert result["ok"] is (missing is None)
    assert "--checkpoint-postgres" in launch.call_args.args[0]
    assert "--verify-log-correlation" in launch.call_args.args[0]


def test_durable_matrix_records_mode_and_reader_hash_without_database_url(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "validate_database") as validate, patch.object(matrix, "preflight", return_value={}), patch.object(matrix, "OwnedStack"), patch.object(matrix, "probe_case", side_effect=lambda binary, scenario, **kwargs: {"scenario": scenario, "ok": True}) as probe:
        assert matrix.run(binary, checkpoint_postgres=True) == 0
    validate.assert_called_once()
    assert len(probe.call_args_list) == 8
    assert all(call.kwargs == {"checkpoint_postgres": True} for call in probe.call_args_list)
    report, = tmp_path.glob("native_scenario_matrix_*.json")
    saved = json.loads(report.read_text())
    assert saved["checkpoint_backend"] == "postgresql"
    assert set(saved["required_checks"]) == matrix.REQUIRED_CHECKS | matrix.RECOVERY_CHECKS
    assert "fault-lab/restore_native_checkpoint.py" in saved["source_sha256"]
    assert "DATABASE_URL" not in report.read_text()


@pytest.mark.parametrize("scenario,checkpoint", [(None, True), ("cpu-spike", True), ("db-pool-exhaustion", False)])
def test_real_pool_flags_are_rejected_before_any_docker_work(tmp_path, scenario, checkpoint):
    matrix = load_matrix()
    with patch.object(matrix, "preflight") as preflight, patch.object(matrix, "OwnedStack") as stack, \
            patch.object(matrix, "validate_database") as database:
        with pytest.raises(ValueError, match="single pool scenario"):
            matrix.run(tmp_path / "gateway", scenario=scenario, checkpoint_postgres=checkpoint, real_postgres_pool=True)
    preflight.assert_not_called()
    stack.assert_not_called()
    database.assert_not_called()


def test_unknown_selector_is_rejected_before_work(tmp_path):
    matrix = load_matrix()
    with patch.object(matrix, "preflight") as preflight:
        with pytest.raises(ValueError, match="Unknown fault"):
            matrix.run(tmp_path / "gateway", scenario="unknown")
    preflight.assert_not_called()


def test_selected_real_pool_run_launches_one_case_and_cannot_claim_full_matrix(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    stack = MagicMock()
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "validate_database"), \
            patch.object(matrix, "preflight", return_value={}), patch.object(matrix, "OwnedStack", return_value=stack), \
            patch.object(matrix, "probe_case", return_value={"scenario": "db-pool-exhaustion", "ok": True}) as probe:
        assert matrix.run(binary, scenario="db-pool-exhaustion", checkpoint_postgres=True, real_postgres_pool=True) == 0
    probe.assert_called_once_with(binary, "db-pool-exhaustion", checkpoint_postgres=True, real_postgres_pool=True)
    stack.close.assert_called_once()
    target, = tmp_path.glob("native_scenario_single_*.json")
    report = json.loads(target.read_text())
    assert report["ok"] and report["selection_mode"] == "single_case"
    assert report["selected_scenarios"] == ["db-pool-exhaustion"]
    assert report["full_matrix_completed"] is False
    assert report["db_pool_backend"] == "postgres"
    assert set(report["required_checks"]) == matrix.REQUIRED_CHECKS | matrix.RECOVERY_CHECKS | matrix.REAL_POOL_CHECKS
    assert "fault-lab/app/incident_lab/postgres_pool.py" in report["source_sha256"]
    assert "DATABASE_URL" not in target.read_text()


@pytest.mark.parametrize("missing", [None, "backend", "boolean_calls", *sorted(load_matrix().REAL_POOL_CHECKS)])
def test_real_pool_probe_requires_backend_and_every_resource_check(tmp_path, missing):
    matrix = load_matrix()
    scenario = "db-pool-exhaustion"
    checks = dict.fromkeys(matrix.REQUIRED_CHECKS | matrix.RECOVERY_CHECKS | matrix.REAL_POOL_CHECKS, True)
    if missing in checks:
        checks.pop(missing)
    target = tmp_path / f"native_scenario_{scenario}_test.json"
    target.write_text(json.dumps({"ok": True, "scenario": scenario, "checks": checks,
        "model_calls": False if missing == "boolean_calls" else 0, "production_actions_executed": False,
        "checkpoint_backend": "postgresql", "fresh_process_recovery": {"ok": True},
        "db_pool_backend": "simulation" if missing == "backend" else "postgres"}))
    process = MagicMock()
    process.communicate.return_value = (json.dumps({"report": str(target)}), "")
    process.returncode = 0
    with patch.object(matrix, "ROOT", tmp_path), patch.object(matrix, "RESULTS", tmp_path), \
            patch.object(matrix.subprocess, "Popen", return_value=process) as launch:
        result = matrix.probe_case(Path("/test-gateway"), scenario, checkpoint_postgres=True, real_postgres_pool=True)
    assert result["ok"] is (missing is None)
    assert "--real-postgres-pool" in launch.call_args.args[0]
    assert "--checkpoint-postgres" in launch.call_args.args[0]


def test_single_simulated_case_preserves_default_probe_mode(tmp_path):
    matrix = load_matrix()
    binary = tmp_path / "gateway"
    binary.write_bytes(b"test binary")
    with patch.object(matrix, "RESULTS", tmp_path), patch.object(matrix, "preflight", return_value={}), \
            patch.object(matrix, "OwnedStack"), patch.object(matrix, "probe_case", return_value={"ok": True}) as probe:
        assert matrix.run(binary, scenario="cpu-spike") == 0
    probe.assert_called_once_with(binary, "cpu-spike")
