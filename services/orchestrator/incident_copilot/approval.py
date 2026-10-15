from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time


class ApprovalReceiptSigner:
    """Creates receipts that the MCP execution boundary can verify."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Approval signing secret must be at least 16 characters.")
        self._secret = secret.encode()

    def issue(
        self,
        *,
        incident_id: str,
        actor: str,
        tool: str,
        service: str,
        target: str,
        namespace: str,
        target_context_sha256: str | None = None,
        ttl_seconds: int = 300,
    ) -> str:
        if not isinstance(namespace, str) or not re.fullmatch(r"[a-z0-9-]{2,63}", namespace):
            raise ValueError("Approval namespace must match the tool namespace schema.")
        if target_context_sha256 is not None and (
            not isinstance(target_context_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", target_context_sha256)
        ):
            raise ValueError("Approval target context digest must be lowercase SHA-256 or null.")
        payload = {
            "version": 3,
            "incident_id": incident_id,
            "actor": actor,
            "tool": tool,
            "service": service,
            "target": target,
            "namespace": namespace,
            "target_context_sha256": target_context_sha256,
            "expires_at": int(time.time()) + ttl_seconds,
        }
        encoded = _base64url(json.dumps(payload, separators=(",", ":")).encode())
        signature = _base64url(
            hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        )
        return f"apr_{encoded}.{signature}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
