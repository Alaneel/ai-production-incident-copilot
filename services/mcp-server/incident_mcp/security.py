from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise AuthorizationError(f"Missing required scope: {scope}")


class TokenAuthenticator:
    """Development token mapper. Production replaces this with OAuth resource validation."""

    def __init__(self, read_token: str, operator_token: str) -> None:
        self._principals = {
            read_token: Principal(
                subject="incident-agent",
                scopes=frozenset({"telemetry:read", "runbook:read"}),
            ),
            operator_token: Principal(
                subject="incident-operator",
                scopes=frozenset(
                    {"telemetry:read", "runbook:read", "production:execute"}
                ),
            ),
        }

    def authenticate(self, authorization: str | None) -> Principal:
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise AuthorizationError("Bearer token required.")
        candidate = authorization[len(prefix) :]
        for token, principal in self._principals.items():
            if hmac.compare_digest(candidate, token):
                return principal
        raise AuthorizationError("Invalid bearer token.")


class ApprovalReceiptVerifier:
    """Verifies v3 receipts bound to namespaced operations and approved targets."""

    def __init__(self, secret: str) -> None:
        self.secret = secret.encode()

    def issue(
        self,
        *,
        incident_id: str,
        actor: str,
        tool: str,
        service: str,
        namespace: str,
        target: str,
        target_context_sha256: str | None = None,
        ttl_seconds: int = 300,
    ) -> str:
        if not isinstance(namespace, str) or re.fullmatch(r"[a-z0-9-]{2,63}", namespace) is None:
            raise ValueError("Approval namespace is invalid.")
        if not _valid_context_digest(target_context_sha256):
            raise ValueError("Approval target context digest is invalid.")
        payload = {
            "version": 3,
            "incident_id": incident_id,
            "actor": actor,
            "tool": tool,
            "service": service,
            "namespace": namespace,
            "target": target,
            "target_context_sha256": target_context_sha256,
            "expires_at": int(time.time()) + ttl_seconds,
        }
        encoded = _base64url(json.dumps(payload, separators=(",", ":")).encode())
        signature = _base64url(
            hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest()
        )
        return f"apr_{encoded}.{signature}"

    def verify(
        self, receipt: str | None, *, tool: str, service: str, namespace: str, target: str,
        target_context_sha256: str | None = None,
    ) -> dict[str, str | int | None]:
        if not _valid_context_digest(target_context_sha256):
            raise AuthorizationError("Requested approval target context digest is invalid.")
        if not isinstance(receipt, str) or not receipt.startswith("apr_"):
            raise AuthorizationError("A signed human approval receipt is required.")
        try:
            encoded, signature = receipt[4:].split(".", maxsplit=1)
            expected = _base64url(
                hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                raise AuthorizationError("Approval receipt signature is invalid.")
            payload = json.loads(_base64url_decode(encoded))
        except (ValueError, TypeError):
            raise AuthorizationError("Approval receipt is malformed.") from None

        if not isinstance(payload, dict):
            raise AuthorizationError("Approval receipt payload must be an object.")
        version = payload.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 3:
            raise AuthorizationError("Approval receipt version is unsupported.")
        if any(not isinstance(payload.get(field), str) or not payload[field].strip()
               for field in ("incident_id", "actor", "tool", "service", "namespace", "target")):
            raise AuthorizationError("Approval receipt scope is malformed.")
        if re.fullmatch(r"[a-z0-9-]{2,63}", payload["namespace"]) is None:
            raise AuthorizationError("Approval receipt namespace is malformed.")
        if "target_context_sha256" not in payload or not _valid_context_digest(payload["target_context_sha256"]):
            raise AuthorizationError("Approval receipt target context digest is malformed.")
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            raise AuthorizationError("Approval receipt expiration is malformed.")
        if expires_at < int(time.time()):
            raise AuthorizationError("Approval receipt has expired.")
        expected_fields = {"tool": tool, "service": service, "namespace": namespace, "target": target,
                           "target_context_sha256": target_context_sha256}
        for key, value in expected_fields.items():
            if payload.get(key) != value:
                raise AuthorizationError(
                    f"Approval receipt is not valid for requested {key}."
                )
        return payload


def _valid_context_digest(value: object) -> bool:
    return value is None or isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
