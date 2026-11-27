"""Serve the exported console from the API's own origin.

The bundle is a directory written by ``build/export-console.mjs``: static files
plus ``console-manifest.json`` naming each file's size, SHA-256 and media type.
Every file is read and verified once at startup and served from memory, so a
request never touches the filesystem and a modified bundle stops startup.

The console holds no credential of its own. It is a page that calls the same
``/v1/incidents`` API as any other client, with tokens the user types in.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

MANIFEST_NAME = "console-manifest.json"
DOCUMENT = "index.html"
IMMUTABLE_PREFIX = "_next/static/"
MAX_FILES = 400
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
# Extension -> the only media type a file with it may declare.
MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}
COMPRESSIBLE = {".html", ".js", ".css", ".svg", ".txt"}
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_SHA256_BASE64 = re.compile(r"[A-Za-z0-9+/]{43}=")
ASSET_POLICY = "default-src 'none'; frame-ancestors 'none'"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class ConsoleBundleError(RuntimeError):
    """The configured console bundle is missing, malformed or modified."""


def document_policy(inline_script_hashes: tuple[str, ...]) -> str:
    """Scripts run only from this origin or when they hash to an exported inline script."""
    scripts = " ".join(["'self'", *(f"'sha256-{value}'" for value in inline_script_hashes)])
    return "; ".join((
        "default-src 'none'",
        f"script-src {scripts}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ))


@dataclass(frozen=True)
class ConsoleFile:
    body: bytes
    compressed: bytes | None
    headers: tuple[tuple[str, str], ...]
    etag: str


def _valid_path(path: object) -> bool:
    if not isinstance(path, str) or path == MANIFEST_NAME:
        return False
    segments = path.split("/")
    if not all(_SEGMENT.fullmatch(segment) for segment in segments):
        return False
    suffix = Path(segments[-1]).suffix
    if suffix not in MEDIA_TYPES or (suffix == ".html") != (path == DOCUMENT):
        return False
    # Only the document, hashed build assets and top-level files with a known
    # extension are served, so no bundle path can shadow an API route.
    return len(segments) == 1 or path.startswith(IMMUTABLE_PREFIX)


class ConsoleBundle:
    def __init__(self, files: dict[str, ConsoleFile]):
        self._files = files

    def __len__(self) -> int:
        return len(self._files)

    @classmethod
    def load(cls, directory: str | Path) -> "ConsoleBundle":
        root = Path(directory)
        try:
            manifest = json.loads((root / MANIFEST_NAME).read_bytes())
        except (OSError, ValueError) as error:
            raise ConsoleBundleError(f"Console bundle manifest is unreadable: {type(error).__name__}.") from None
        if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("backend") != "same-origin":
            raise ConsoleBundleError("Console bundle manifest is not a same-origin schema 1 export.")
        entries, hashes = manifest.get("files"), manifest.get("inline_scripts_sha256")
        if not isinstance(entries, dict) or not 0 < len(entries) <= MAX_FILES or DOCUMENT not in entries:
            raise ConsoleBundleError("Console bundle manifest lists no document or too many files.")
        if not isinstance(hashes, list) or not all(isinstance(item, str) and _SHA256_BASE64.fullmatch(item) for item in hashes):
            raise ConsoleBundleError("Console bundle manifest has invalid inline script hashes.")
        policy = document_policy(tuple(hashes))
        resolved_root, total, files = root.resolve(), 0, {}
        for path, entry in entries.items():
            if not _valid_path(path) or not isinstance(entry, dict):
                raise ConsoleBundleError("Console bundle manifest lists a path that cannot be served.")
            suffix = Path(path).suffix
            size, digest = entry.get("bytes"), entry.get("sha256")
            if (entry.get("type") != MEDIA_TYPES[suffix] or type(size) is not int or not 0 < size <= MAX_FILE_BYTES
                    or not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest)):
                raise ConsoleBundleError(f"Console bundle manifest entry is invalid: {path}")
            total += size
            if total > MAX_BUNDLE_BYTES:
                raise ConsoleBundleError("Console bundle is larger than the serving limit.")
            target = (root / path).resolve()
            if not target.is_relative_to(resolved_root) or not target.is_file():
                raise ConsoleBundleError(f"Console bundle file is missing: {path}")
            body = target.read_bytes()
            if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                raise ConsoleBundleError(f"Console bundle file does not match its manifest: {path}")
            document = path == DOCUMENT
            cache = ("no-cache" if document else "public, max-age=31536000, immutable"
                     if path.startswith(IMMUTABLE_PREFIX) else "public, max-age=3600")
            files["/" if document else f"/{path}"] = ConsoleFile(
                body=body,
                compressed=gzip.compress(body, mtime=0) if suffix in COMPRESSIBLE else None,
                headers=(("Content-Type", MEDIA_TYPES[suffix]), ("Cache-Control", cache),
                         ("Content-Security-Policy", policy if document else ASSET_POLICY),
                         *SECURITY_HEADERS.items()),
                etag=digest[:32],
            )
        return cls(files)

    def response(self, method: str, path: str, headers: Headers) -> Response | None:
        """The response for an exact bundle path, or None so the API handles the request."""
        file = self._files.get(path)
        if file is None or method not in {"GET", "HEAD"}:
            return None
        compressed = file.compressed is not None and _accepts_gzip(headers.get("accept-encoding", ""))
        body = file.compressed if compressed else file.body
        etag = f'"{file.etag}-gzip"' if compressed else f'"{file.etag}"'
        response_headers = dict(file.headers) | {"ETag": etag}
        if file.compressed is not None:
            response_headers["Vary"] = "Accept-Encoding"
        if etag in {item.strip() for item in headers.get("if-none-match", "").split(",")}:
            return Response(status_code=304, headers=response_headers)
        if compressed:
            response_headers["Content-Encoding"] = "gzip"
        response_headers["Content-Length"] = str(len(body))
        return Response(content=b"" if method == "HEAD" else body, headers=response_headers)


def _accepts_gzip(header: str) -> bool:
    for item in header.split(","):
        coding, _, parameters = item.partition(";")
        if coding.strip().lower() == "gzip":
            return parameters.strip().replace(" ", "") not in {"q=0", "q=0.0", "q=0.00", "q=0.000"}
    return False


def load_console(directory: str | None) -> ConsoleBundle | None:
    """An unset or empty CONSOLE_STATIC_DIR means the API serves no console."""
    return ConsoleBundle.load(directory) if directory else None


class ConsoleStaticMiddleware:
    """Answers exact bundle paths; every other request passes through untouched."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            bundle = getattr(scope["app"].state, "console", None)
            if bundle is not None:
                response = bundle.response(scope["method"], scope["path"], Headers(scope=scope))
                if response is not None:
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
