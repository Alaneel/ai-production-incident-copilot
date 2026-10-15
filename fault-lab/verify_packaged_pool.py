"""Opt-in real-pool image acceptance using only new disposable Docker resources."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError("Docker operation failed: " + args[0])
    return result


def run(image):
    # Resolve images before allocating resources; never implicitly pull or build.
    images = {role: docker("image", "inspect", "--format", "{{.Id}}", ref).stdout.strip()
              for role, ref in {"payment": image, "postgres": "postgres:17-alpine"}.items()}
    name = "copilot-packaged-pool-" + uuid.uuid4().hex
    volume, database, payment = name + "-socket", name + "-db", name + "-payment"
    owned = []
    token = secrets.token_urlsafe(24)
    report = {"contract": "packaged-real-pool-acceptance-v1", "started_at": datetime.now(UTC).isoformat(),
              "images": images, "checks": {}, "model_calls": 0, "production_actions": 0,
              "full_platform_acceptance": False, "kubernetes_acceptance": False,
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in [Path(__file__), ROOT / "fault-lab/app/Dockerfile",
                                             ROOT / "fault-lab/app/pyproject.toml",
                                             *sorted((ROOT / "fault-lab/app/incident_lab").glob("*.py"))]}}
    checks = report["checks"]
    started = time.monotonic()
    try:
        for kind, target, command in [
            ("network", name, ("network", "create", "--internal", name)),
            ("volume", volume, ("volume", "create", volume)),
        ]:
            owned.append((kind, target))
            docker(*command)
        owned.append(("container", database))
        docker("run", "--detach", "--pull=never", "--name", database, "--network", name,
               "--tmpfs", "/var/lib/postgresql/data:uid=70,gid=70,mode=0700",
               "--mount", f"type=volume,source={volume},target=/var/run/postgresql",
               "--env", "POSTGRES_DB=incident_pool_lab", "--env", "POSTGRES_USER=incident_lab",
               "--env", "POSTGRES_PASSWORD=" + secrets.token_urlsafe(24), images["postgres"],
               "postgres", "-c", "listen_addresses=", "-c", "unix_socket_directories=/var/run/postgresql",
               "-c", "unix_socket_permissions=0770")
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if docker("exec", database, "pg_isready", "-h", "/var/run/postgresql",
                      "-U", "incident_lab", "-d", "incident_pool_lab", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("Dedicated container database readiness")
        # Internal-network loopback publishing varies between Docker versions;
        # HTTP probes run inside the owned payment container, never on a host port.
        owned.append(("container", payment))
        docker("run", "--detach", "--pull=never", "--name", payment, "--network", name,
               "--user", "10001:70", "--read-only", "--tmpfs", "/tmp:uid=10001,gid=70,mode=1770",
               "--mount", f"type=volume,source={volume},target=/tmp/incident-lab-postgres,readonly",
               "--env", "SERVICE_NAME=payment-service", "--env", "FAULT_INJECTION_TOKEN=" + token,
               "--env", "INCIDENT_LAB_DB_POOL_BACKEND=postgres", "--env",
               "INCIDENT_LAB_POSTGRES_URL=postgresql:///incident_pool_lab?host=/tmp/incident-lab-postgres&user=incident_lab",
               images["payment"], "python", "-m", "incident_lab.start")

        def request(path, method="GET", body=None, *, authenticated=False):
            code = ("import httpx,json; r=httpx.request(" + repr(method)
                    + "," + repr("http://127.0.0.1:8090" + path)
                    + ",json=" + repr(body) + ",headers="
                    + repr({"x-fault-token": token} if authenticated else {})
                    + ",timeout=3); print(json.dumps({'status':r.status_code,'body':r.json()}))")
            return json.loads(docker("exec", payment, "python", "-c", code).stdout)

        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                health = request("/health")
                if health["status"] == 200:
                    break
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(0.5)
        else:
            raise TimeoutError("Packaged payment readiness")
        checks["postgres_backend"] = health["body"]["db_pool_backend"] == "postgres"
        checks["non_root_identity"] = docker("exec", payment, "id", "-u").stdout.strip() == "10001"
        settings = json.loads(docker("inspect", payment).stdout)[0]
        checks["read_only_filesystem"] = settings["HostConfig"]["ReadonlyRootfs"] is True
        checks["socket_read_only"] = any(m["Destination"] == "/tmp/incident-lab-postgres" and not m["RW"] for m in settings["Mounts"])
        checks["no_host_ports"] = not settings["HostConfig"]["PortBindings"]
        code = "import hashlib,json,pathlib; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app/incident_lab').glob('*.py')}))"
        installed = json.loads(docker("exec", payment, "python", "-c", code).stdout)
        expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (ROOT / "fault-lab/app/incident_lab").glob("*.py")}
        checks["image_source_matches_worktree"] = installed == expected
        db_settings = json.loads(docker("inspect", database).stdout)[0]
        checks["database_data_ephemeral"] = "/var/lib/postgresql/data" in db_settings["HostConfig"]["Tmpfs"]
        checks["database_no_host_ports"] = not db_settings["HostConfig"]["PortBindings"]
        listen = docker("exec", database, "psql", "-h", "/var/run/postgresql", "-U", "incident_lab",
                        "-d", "incident_pool_lab", "-At", "-c", "SHOW listen_addresses").stdout
        checks["database_has_no_tcp_listener"] = not listen.strip()

        def sessions():
            query = "SELECT pid FROM pg_stat_activity WHERE application_name='incident-fault-lab-pool' ORDER BY pid"
            text = docker("exec", database, "psql", "-h", "/var/run/postgresql", "-U", "incident_lab",
                          "-d", "incident_pool_lab", "-At", "-c", query).stdout
            return [int(pid) for pid in text.split()]

        baseline = sessions()
        checks["pool_initially_unleased"] = request("/admin/pool", authenticated=True)["body"]["active"] == 0
        checks["four_actual_sessions"] = len(baseline) == 4 and len(set(baseline)) == 4
        checks["healthy_request"] = request("/v1/work")["status"] == 200
        checks["unauthorized_injection_rejected"] = request("/admin/fault", "POST", {"mode": "db_pool_exhaustion", "duration_seconds": 30})["status"] in {401, 403}
        injected = request("/admin/fault", "POST", {"mode": "db_pool_exhaustion", "duration_seconds": 30}, authenticated=True)
        checks["fault_accepted"] = injected["status"] == 200 and injected["body"]["accepted"] is True
        checks["four_leases_reserved"] = request("/admin/pool", authenticated=True)["body"]["active"] == 4
        exhausted = request("/v1/work")
        checks["actual_acquisition_timeout"] = exhausted == {"status": 503, "body": {"error": "database connection acquisition timed out"}}
        checks["sessions_retained_during_fault"] = sessions() == baseline
        checks["fault_cleared"] = request("/admin/fault", "DELETE", authenticated=True)["status"] == 200
        checks["recovered_request"] = request("/v1/work")["status"] == 200
        checks["all_leases_released"] = request("/admin/pool", authenticated=True)["body"]["active"] == 0
        checks["same_sessions_after_recovery"] = sessions() == baseline
        report["postgres_backend_pids"] = baseline
        docker("stop", "--time", "10", payment)
        checks["shutdown_closes_all_pool_sessions"] = sessions() == []
    except Exception as error:
        # Never save Docker output, environments, HTTP tokens or provider text.
        report["error_type"] = type(error).__name__
    finally:
        cleanup = []
        for kind, target in reversed(owned):
            args = ("rm", "--force", target) if kind == "container" else (kind, "rm", target)
            try:
                docker(*args, check=False)
                # A daemon/permission error is not evidence of absence.
                listing = docker("ps", "--all", "--format", "{{.Names}}", check=False) if kind == "container" else docker(kind, "ls", "--format", "{{.Name}}", check=False)
                absent = listing.returncode == 0 and target not in listing.stdout.splitlines()
            except (RuntimeError, subprocess.TimeoutExpired):
                absent = False
            cleanup.append({"kind": kind, "name": target, "absent": absent})
        report["cleanup"] = cleanup
        report["cleanup_complete"] = bool(cleanup) and all(item["absent"] for item in cleanup)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = bool(checks) and all(checks.values()) and "error_type" not in report and report["cleanup_complete"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-integration", action="store_true", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    report = run(args.image)
    output = ROOT / "fault-lab/results" / ("packaged_pool_runtime_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8] + ".json")
    with output.open("x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(output), "ok": report["ok"], "checks": report["checks"],
                      "cleanup_complete": report["cleanup_complete"]}))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
