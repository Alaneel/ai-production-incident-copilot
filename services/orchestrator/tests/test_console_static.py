"""The bundled console: served from memory, verified at startup, invisible to the API."""
import gzip
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from incident_copilot.api import app
from incident_copilot.console_static import (
    MEDIA_TYPES,
    ConsoleBundle,
    ConsoleBundleError,
    document_policy,
    load_console,
)

HASHES = ["A" * 43 + "=", "b" * 43 + "="]
PAGE = b'<!doctype html><main data-backend="same-origin">OpsPilot</main><script>boot()</script>'
SCRIPT = b"export const console = 'bundled';" * 40
FILES = {
    "index.html": PAGE,
    "_next/static/chunks/app-1a2b.js": SCRIPT,
    "_next/static/_vinext_fonts/geist-0a/geist-1b.woff2": b"wOF2" + bytes(64),
    "favicon.svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    "third-party-licenses.txt": b"react 19.2.9 (MIT)\n",
}


def write_bundle(root: Path, **overrides) -> Path:
    entries = {}
    for name, body in FILES.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        entries[name] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                         "type": MEDIA_TYPES[Path(name).suffix]}
    manifest = {"schema": 1, "backend": "same-origin", "inline_scripts_sha256": HASHES, "files": entries} | overrides
    (root / "console-manifest.json").write_text(json.dumps(manifest))
    return root


def serving(directory: Path | str):
    return patch.dict(os.environ, {"CONSOLE_STATIC_DIR": str(directory)})


def test_document_is_served_at_the_root_with_a_hash_based_script_policy(tmp_path):
    with serving(write_bundle(tmp_path)), TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.content == PAGE
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-cache"
        policy = response.headers["Content-Security-Policy"]
        assert policy == document_policy(tuple(HASHES))
        assert f"script-src 'self' 'sha256-{HASHES[0]}' 'sha256-{HASHES[1]}';" in policy
        assert "default-src 'none'" in policy and "connect-src 'self'" in policy and "frame-ancestors 'none'" in policy
        script_policy = policy.split("script-src ")[1].split(";")[0]
        assert "unsafe" not in script_policy and "http" not in script_policy and "*" not in script_policy
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert client.get("/health").json()["console"] == "bundled"


def test_assets_are_cached_by_kind_and_never_carry_the_document_policy(tmp_path):
    with serving(write_bundle(tmp_path)), TestClient(app) as client:
        script = client.get("/_next/static/chunks/app-1a2b.js")
        assert script.content == SCRIPT
        assert script.headers["Content-Type"] == "text/javascript; charset=utf-8"
        assert script.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert script.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        font = client.get("/_next/static/_vinext_fonts/geist-0a/geist-1b.woff2", headers={"Accept-Encoding": "gzip"})
        assert font.headers["Content-Type"] == "font/woff2"
        assert "Content-Encoding" not in font.headers and "Vary" not in font.headers
        icon = client.get("/favicon.svg")
        assert icon.headers["Content-Type"] == "image/svg+xml"
        assert icon.headers["Cache-Control"] == "public, max-age=3600"
        assert client.get("/third-party-licenses.txt").text == "react 19.2.9 (MIT)\n"


def test_compression_validators_and_head_follow_http_semantics(tmp_path):
    with serving(write_bundle(tmp_path)), TestClient(app) as client:
        path = "/_next/static/chunks/app-1a2b.js"
        plain = client.get(path, headers={"Accept-Encoding": "identity"})
        assert plain.content == SCRIPT and "Content-Encoding" not in plain.headers
        assert plain.headers["Vary"] == "Accept-Encoding"
        with client.stream("GET", path, headers={"Accept-Encoding": "br, gzip"}) as compressed:
            raw = b"".join(compressed.iter_raw())
            assert compressed.headers["Content-Encoding"] == "gzip"
            assert compressed.headers["Content-Length"] == str(len(raw)) and len(raw) < len(SCRIPT)
            assert gzip.decompress(raw) == SCRIPT
            assert compressed.headers["ETag"] == plain.headers["ETag"][:-1] + '-gzip"'
        refused = client.get(path, headers={"Accept-Encoding": "gzip;q=0, identity"})
        assert "Content-Encoding" not in refused.headers
        unchanged = client.get(path, headers={"Accept-Encoding": "identity", "If-None-Match": plain.headers["ETag"]})
        assert unchanged.status_code == 304 and unchanged.content == b""
        assert unchanged.headers["ETag"] == plain.headers["ETag"]
        assert client.get(path, headers={"Accept-Encoding": "identity", "If-None-Match": '"other"'}).status_code == 200
        head = client.head("/", headers={"Accept-Encoding": "identity"})
        assert head.status_code == 200 and head.content == b""
        assert head.headers["Content-Length"] == str(len(PAGE))


def test_only_exact_bundle_paths_are_answered_and_the_api_is_untouched(tmp_path):
    with serving(write_bundle(tmp_path)), TestClient(app) as client:
        for path in ("/console-manifest.json", "/index.html", "/_next/static/chunks/", "/_next/static/chunks/missing.js",
                     "/favicon.svg/", "/FAVICON.SVG", "/_next/static/../../console-manifest.json"):
            assert client.get(path).status_code == 404, path
        # Raw request paths a client library would normalise away.
        bundle = app.state.console
        for path in ("//favicon.svg", "/./favicon.svg", "/_next/static/chunks/../chunks/app-1a2b.js", "favicon.svg", "", "/%2e/favicon.svg"):
            assert bundle.response("GET", path, Headers()) is None, path
        assert bundle.response("POST", "/", Headers()) is None and bundle.response("DELETE", "/favicon.svg", Headers()) is None
        assert client.post("/").status_code in {404, 405}
        assert client.get("/favicon.svg?incident=INC-1").status_code == 200
        assert client.get("/?incident=INC-1&approval_pending=1").content == PAGE
        assert client.get("/health").json()["status"] == "ok"
        created = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
        assert created.status_code == 201
        assert "Content-Security-Policy" not in created.headers
        assert client.get("/openapi.json").json()["info"]["title"] == "AI Production Incident Copilot"


@pytest.mark.parametrize("configured", [None, ""])
def test_no_console_is_served_unless_a_bundle_is_configured(configured):
    with patch.dict(os.environ):
        os.environ.pop("CONSOLE_STATIC_DIR", None)
        if configured is not None:
            os.environ["CONSOLE_STATIC_DIR"] = configured
        with TestClient(app) as client:
            assert client.get("/").status_code == 404
            assert client.get("/health").json()["console"] == "not_bundled"
    assert load_console(configured) is None


def test_a_modified_bundle_stops_startup_instead_of_serving(tmp_path):
    write_bundle(tmp_path)
    (tmp_path / "_next/static/chunks/app-1a2b.js").write_bytes(SCRIPT.replace(b"bundled", b"tampered"))
    with serving(tmp_path), pytest.raises(ConsoleBundleError, match="does not match its manifest"):
        with TestClient(app):
            pass
    with serving(tmp_path / "absent"), pytest.raises(ConsoleBundleError, match="manifest is unreadable"):
        with TestClient(app):
            pass


@pytest.mark.parametrize("name", [
    "../outside.txt", "/etc/passwd.txt", "v1/incidents.txt", "nested/index.html", "about.html", "health",
    "openapi.json", "mcp", ".env.txt", "_next/static/../../escape.js", "_next/other/app.js", "notes.md",
    "a//b.js", "console-manifest.json", "_next/static/chunks/app.js.map", "",
])
def test_paths_that_could_escape_or_shadow_the_api_are_refused(tmp_path, name):
    write_bundle(tmp_path)
    manifest = json.loads((tmp_path / "console-manifest.json").read_text())
    manifest["files"][name] = {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest(), "type": "text/plain; charset=utf-8"}
    (tmp_path / "console-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ConsoleBundleError, match="cannot be served"):
        ConsoleBundle.load(tmp_path)


@pytest.mark.parametrize("overrides, message", [
    ({"schema": 2}, "same-origin schema 1"),
    ({"backend": "https://api.example.com"}, "same-origin schema 1"),
    ({"files": {}}, "lists no document"),
    ({"files": {"favicon.svg": {}}}, "lists no document"),
    ({"inline_scripts_sha256": ["'unsafe-inline'"]}, "invalid inline script hashes"),
    ({"inline_scripts_sha256": "A" * 43 + "="}, "invalid inline script hashes"),
])
def test_manifests_that_are_not_a_same_origin_export_are_refused(tmp_path, overrides, message):
    with pytest.raises(ConsoleBundleError, match=message):
        ConsoleBundle.load(write_bundle(tmp_path, **overrides))


def test_entries_must_match_their_extension_size_and_location(tmp_path):
    def reload(change):
        write_bundle(tmp_path)
        manifest = json.loads((tmp_path / "console-manifest.json").read_text())
        change(manifest["files"])
        (tmp_path / "console-manifest.json").write_text(json.dumps(manifest))
        return ConsoleBundle.load(tmp_path)

    with pytest.raises(ConsoleBundleError, match="entry is invalid: favicon.svg"):
        reload(lambda files: files["favicon.svg"].update(type="text/html; charset=utf-8"))
    with pytest.raises(ConsoleBundleError, match="entry is invalid: favicon.svg"):
        reload(lambda files: files["favicon.svg"].update(bytes=True))
    with pytest.raises(ConsoleBundleError, match="does not match its manifest: favicon.svg"):
        reload(lambda files: files["favicon.svg"].update(bytes=files["favicon.svg"]["bytes"] + 1))
    with pytest.raises(ConsoleBundleError, match="file is missing: favicon.svg"):
        reload(lambda files: (tmp_path / "favicon.svg").unlink())
    assert len(reload(lambda files: None)) == len(FILES)
    # A link that leaves the bundle directory is not followed, even to identical content.
    outside = tmp_path.parent / f"{tmp_path.name}-outside.svg"
    outside.write_bytes(FILES["favicon.svg"])
    try:
        with pytest.raises(ConsoleBundleError, match="file is missing: favicon.svg"):
            reload(lambda files: ((tmp_path / "favicon.svg").unlink(), (tmp_path / "favicon.svg").symlink_to(outside)))
    finally:
        (tmp_path / "favicon.svg").unlink()
        outside.unlink()
