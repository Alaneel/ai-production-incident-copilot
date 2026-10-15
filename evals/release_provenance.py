"""Exact release-runtime provenance, without host paths or configuration values."""
from __future__ import annotations

import hashlib
import importlib.metadata
import platform
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def release_recipe_fingerprints(*, root: Path = ROOT) -> dict[str, str]:
    """Bind the declared local release recipes; this does not run those images."""
    inputs = [root / "container-images.lock.json"]
    inputs.extend(path for path in sorted((root / "deploy/images").rglob("*"))
                  if path.is_file() and path.name not in {"README.md", "provenance.json", "dependency-lock-provenance.json"})
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs}


def locked_runtime_versions(environment: dict[str, str], *, root: Path = ROOT) -> dict[str, str]:
    """Derive canonical, platform-applicable exact pins from the recorded lock."""
    expected = {}
    for line in (root / "requirements-runtime.lock").read_text().splitlines():
        if not line or line.startswith(("#", " ", "--")):
            continue
        requirement = Requirement(line.removesuffix("\\").strip())
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        pins = list(requirement.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise ValueError("Release runtime requirements must use one exact pin")
        name = canonicalize_name(requirement.name)
        if name in expected and expected[name] != pins[0].version:
            raise ValueError("Conflicting platform-applicable runtime pins")
        expected[name] = pins[0].version
    expected.update({"incident-copilot-orchestrator": "0.2.0", "incident-copilot-mcp-server": "0.2.0"})
    return dict(sorted(expected.items()))


def runtime_provenance() -> dict:
    """Fail closed if this replay differs from its platform-applicable runtime lock."""
    environment = default_environment()
    versions = locked_runtime_versions(environment)
    for name, expected in versions.items():
        if importlib.metadata.version(name) != expected:
            raise ValueError(f"Runtime differs from release lock: {name}")
    return {
        "python": platform.python_version(),
        "marker_environment": environment,
        "runtime_lock_sha256": hashlib.sha256((ROOT / "requirements-runtime.lock").read_bytes()).hexdigest(),
        "build_lock_sha256": hashlib.sha256((ROOT / "requirements-build.lock").read_bytes()).hexdigest(),
        "provenance_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "installed_runtime_versions": versions,
        "release_recipe_sha256": release_recipe_fingerprints(),
    }
