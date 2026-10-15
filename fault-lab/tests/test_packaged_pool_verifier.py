import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify_packaged_pool.py"
spec = importlib.util.spec_from_file_location("packaged_pool_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def result(code=0, text=""):
    return SimpleNamespace(returncode=code, stdout=text, stderr="")


def test_preflight_image_failure_cannot_allocate_or_clean_resources():
    with patch.object(verifier, "docker", side_effect=RuntimeError("test")) as docker:
        with pytest.raises(RuntimeError):
            verifier.run("fixture-image")
    assert docker.call_count == 1
    assert docker.call_args.args[:2] == ("image", "inspect")


@pytest.mark.parametrize("listing", [result(), result(1), result(0, "copilot-packaged-pool-fixture\n")])
def test_failure_cleanup_targets_only_new_resources_and_requires_successful_absence_listing(listing):
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        if args[:2] == ("image", "inspect"):
            return result(text="sha256:fixture\n")
        if args[0] == "run":
            raise RuntimeError("Synthetic startup failure")
        if args[:2] in (("network", "ls"), ("volume", "ls")) or args[0] == "ps":
            return listing
        return result()

    with patch.object(verifier.uuid, "uuid4", return_value=SimpleNamespace(hex="fixture")), patch.object(verifier, "docker", side_effect=docker):
        report = verifier.run("fixture-image")
    assert report["ok"] is False
    assert report["error_type"] == "RuntimeError"
    assert report["model_calls"] == report["production_actions"] == 0
    assert report["full_platform_acceptance"] is report["kubernetes_acceptance"] is False
    assert report["cleanup_complete"] == (listing.returncode == 0 and not listing.stdout)
    removals = [command for command in commands if "rm" in command]
    assert removals == [
        ("rm", "--force", "copilot-packaged-pool-fixture-db"),
        ("volume", "rm", "copilot-packaged-pool-fixture-socket"),
        ("network", "rm", "copilot-packaged-pool-fixture"),
    ]
    assert not any("prune" in command for command in commands)
    assert "FAULT_INJECTION_TOKEN" not in str(report)


def test_docker_errors_redact_provider_output_and_use_bounded_commands():
    with patch.object(verifier.subprocess, "run", return_value=result(1, "private provider details")) as run:
        with pytest.raises(RuntimeError) as error:
            verifier.docker("image", "inspect", "fixture")
    assert str(error.value) == "Docker operation failed: image"
    assert run.call_args.kwargs["timeout"] == 45
    assert run.call_args.args[0] == ["docker", "image", "inspect", "fixture"]
