"""Outbound notification when an unattended diagnosis needs a human.

An alert-started diagnosis has nobody watching it. When it pauses for approval
or finishes, one bounded message is posted to an operator-configured webhook
(Slack-compatible or generic JSON) with a link back to the console.

The message is a pointer, not a control surface: it carries no evidence,
commands, credentials or approval capability. Delivery is best effort and can
never change incident state.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .models import IncidentState, IncidentStatus

NOTIFY_TIMEOUT_SECONDS = 5.0
_HEADLINES = {
    IncidentStatus.WAITING_APPROVAL: "needs a human decision",
    IncidentStatus.DIAGNOSED: "diagnosed, no production change proposed",
    IncidentStatus.INSUFFICIENT_EVIDENCE: "could not be diagnosed from available telemetry",
    IncidentStatus.REMEDIATION_FAILED: "proposal blocked before approval",
}


@dataclass(frozen=True)
class NotificationSettings:
    url: str | None
    format: str  # "json" or "slack"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "NotificationSettings":
        url = (environment.get("NOTIFY_WEBHOOK_URL") or "").strip() or None
        if url is not None:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
                raise ValueError("NOTIFY_WEBHOOK_URL must be an http(s) URL without embedded credentials.")
        style = environment.get("NOTIFY_WEBHOOK_FORMAT", "json").strip().lower()
        if style not in {"json", "slack"}:
            raise ValueError("NOTIFY_WEBHOOK_FORMAT must be json or slack.")
        return cls(url, style)


def _plain(value: str | None, limit: int) -> str:
    """Single-line text that cannot form chat markup, mentions or links."""
    value = unicodedata.normalize("NFKC", value or "")
    value = "".join(character if unicodedata.category(character)[0] != "C" else " " for character in value)
    value = " ".join(value.split())[:limit]
    for raw, safe in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("@", "@​")):
        value = value.replace(raw, safe)
    return value


def build_notification(state: IncidentState, *, alertname: str | None, console_url: str | None) -> dict | None:
    """Return the structured notification, or None when nothing needs saying."""
    headline = _HEADLINES.get(state.status)
    if headline is None:
        return None
    service = state.services[0] if state.services else state.requested_service or "unknown service"
    leading = state.hypotheses[0] if state.hypotheses else None
    lines = [f"OpsPilot {state.incident_id} for {_plain(service, 80)} {headline}."]
    if alertname:
        lines.append(f"Alert: {_plain(alertname, 120)}")
    if leading and state.status in {IncidentStatus.WAITING_APPROVAL, IncidentStatus.DIAGNOSED}:
        lines.append(f"Leading hypothesis ({round(leading.confidence * 100)}%): {_plain(leading.cause, 240)}")
    if state.remediation and state.status is IncidentStatus.WAITING_APPROVAL:
        lines.append(f"Proposed (not executed): {_plain(state.remediation.title, 160)}")
    lines.append(f"Review: {console_url}" if console_url else "Review it in the OpsPilot console.")
    return {
        "event": "incident.attention",
        "incident_id": state.incident_id,
        "status": state.status.value,
        "service": service,
        "environment": state.environment,
        "alertname": alertname,
        "requires_approval": state.status is IncidentStatus.WAITING_APPROVAL,
        "console_url": console_url,
        "text": "\n".join(lines),
    }


async def send_notification(settings: NotificationSettings, notification: dict,
                            *, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Best-effort POST. Returns whether the receiver acknowledged with 2xx."""
    if settings.url is None:
        return False
    body = {"text": notification["text"]} if settings.format == "slack" else notification
    try:
        # Redirects are not followed: the operator names the exact receiver.
        async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT_SECONDS, follow_redirects=False,
                                     transport=transport, trust_env=False) as client:
            response = await client.post(settings.url, json=body)
    except httpx.HTTPError:
        return False
    return 200 <= response.status_code < 300
