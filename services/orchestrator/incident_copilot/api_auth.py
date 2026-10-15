"""Explicit API credential scopes; token possession is not human SSO identity."""
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Mapping

from fastapi import HTTPException


@dataclass(frozen=True)
class ApiAuthorization:
    mode: str
    read_token: str = field(default="", repr=False)
    operator_token: str = field(default="", repr=False)
    operator_actor: str = ""

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]):
        mode = environment.get("ORCHESTRATOR_AUTH_MODE", "disabled")
        if mode not in {"disabled", "required"}:
            raise ValueError("ORCHESTRATOR_AUTH_MODE must be disabled or required.")
        if mode == "disabled":
            return cls(mode)
        read = environment.get("ORCHESTRATOR_READ_TOKEN", "")
        operator = environment.get("ORCHESTRATOR_OPERATOR_TOKEN", "")
        actor = environment.get("ORCHESTRATOR_OPERATOR_ACTOR", "").strip()
        valid_tokens = all(32 <= len(token) <= 4096 and all(33 <= ord(char) <= 126 for char in token)
                           for token in (read, operator))
        if not valid_tokens or compare_digest(read.encode(), operator.encode()):
            raise ValueError("Required API authentication needs distinct read/operator tokens of at least 32 characters.")
        if not 2 <= len(actor) <= 120:
            raise ValueError("Required API authentication needs a configured operator actor.")
        return cls(mode, read, operator, actor)

    def authorize(self, authorization: str | None, *, approval: bool) -> str | None:
        if self.mode == "disabled":
            return None
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied or len(supplied) > 4096:
            raise HTTPException(401, "API authentication required.", headers={"WWW-Authenticate": "Bearer"})
        # Bytes comparison avoids exceptions from non-ASCII input.
        candidate = supplied.encode()
        operator = compare_digest(candidate, self.operator_token.encode())
        reader = compare_digest(candidate, self.read_token.encode())
        if not operator and not reader:
            raise HTTPException(401, "Invalid API credential.", headers={"WWW-Authenticate": "Bearer"})
        if approval and not operator:
            raise HTTPException(403, "Operator permission required.")
        return self.operator_actor if operator else None
