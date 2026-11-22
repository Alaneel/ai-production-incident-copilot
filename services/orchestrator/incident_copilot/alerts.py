"""Alertmanager webhook adapter.

Turns firing alerts into ordinary read-only diagnosis requests. Nothing here can
authorize a production change: diagnosis still stops at the human approval
interrupt, exactly as a question typed in the console would.

Alert labels and annotations are untrusted input. They are length-bounded,
stripped of control characters and screened by the same input policy before a
diagnosis starts.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .models import IncidentQuestion


DEFAULT_SERVICE_LABELS = ("service", "service_name", "app_kubernetes_io_name", "app", "job")
ENVIRONMENT_LABELS = ("environment", "env")
_SERVICE = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$")
_ENVIRONMENT = re.compile(r"^[a-z0-9-]{2,40}$")


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    annotations: dict[str, str] = Field(default_factory=dict, max_length=64)
    startsAt: datetime | None = None
    fingerprint: str | None = Field(default=None, max_length=64)


class AlertmanagerWebhook(BaseModel):
    """Alertmanager webhook payload, version 4. Unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore")
    status: Literal["firing", "resolved"] | None = None
    receiver: str | None = Field(default=None, max_length=200)
    alerts: list[AlertmanagerAlert] = Field(max_length=100)


@dataclass(frozen=True)
class AlertDiagnosisRequest:
    key: str
    alertname: str
    service: str
    request: IncidentQuestion


@dataclass
class AlertTriage:
    requests: list[AlertDiagnosisRequest] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def _clean(value: str, limit: int) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(character if unicodedata.category(character)[0] != "C" else " " for character in value)
    return " ".join(value.split())[:limit]


def normalize_service(value: str) -> str | None:
    """Map label values such as ``Payments_API`` or ``payments.api`` onto the
    service-name form the telemetry gateway accepts."""
    candidate = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")
    candidate = re.sub(r"-{2,}", "-", candidate)
    return candidate if _SERVICE.fullmatch(candidate) else None


def parse_service_labels(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_SERVICE_LABELS
    labels = tuple(item.strip() for item in value.split(",") if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item.strip()))
    return labels or DEFAULT_SERVICE_LABELS


def triage_alerts(
    payload: AlertmanagerWebhook,
    *,
    service_labels: Sequence[str] = DEFAULT_SERVICE_LABELS,
    default_environment: str = "prod-sg",
) -> AlertTriage:
    triage = AlertTriage()
    for alert in payload.alerts:
        alertname = _clean(alert.labels.get("alertname", "unnamed alert"), 120) or "unnamed alert"
        if alert.status != "firing":
            triage.skipped.append({"alertname": alertname, "reason": "resolved"})
            continue
        service = None
        for label in service_labels:
            raw = alert.labels.get(label)
            if raw:
                service = normalize_service(raw)
                if service:
                    break
        if service is None:
            triage.skipped.append({"alertname": alertname,
                                   "reason": f"no usable service label (looked for {', '.join(service_labels)})"})
            continue
        environment = default_environment
        for label in ENVIRONMENT_LABELS:
            candidate = alert.labels.get(label, "").strip().lower()
            if _ENVIRONMENT.fullmatch(candidate):
                environment = candidate
                break
        summary = _clean(alert.annotations.get("summary") or alert.annotations.get("description") or "", 600)
        severity = _clean(alert.labels.get("severity", ""), 40)
        question = f"Alert {alertname} is firing for {service}" + (f" (severity {severity})" if severity else "")
        question += f": {summary}" if summary else ". Why?"
        key = alert.fingerprint or hashlib.sha256(
            f"{alertname}|{service}|{alert.startsAt.isoformat() if alert.startsAt else ''}".encode()).hexdigest()[:32]
        triage.requests.append(AlertDiagnosisRequest(
            key=key, alertname=alertname, service=service,
            request=IncidentQuestion(question=question[:2000], environment=environment, service=service),
        ))
    return triage


class AlertDeduplicator:
    """Remembers recently accepted alerts so Alertmanager's repeat notifications
    (repeat_interval, group re-sends, retries) do not start duplicate runs."""

    def __init__(self, ttl_seconds: float = 3600, capacity: int = 2048, clock=time.monotonic):
        self._ttl, self._capacity, self._clock = ttl_seconds, capacity, clock
        self._seen: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def existing(self, key: str) -> str | None:
        self._expire()
        entry = self._seen.get(key)
        return entry[1] if entry else None

    def remember(self, key: str, incident_id: str) -> None:
        self._seen[key] = (self._clock(), incident_id)
        self._seen.move_to_end(key)
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)

    def _expire(self) -> None:
        cutoff = self._clock() - self._ttl
        while self._seen and next(iter(self._seen.values()))[0] < cutoff:
            self._seen.popitem(last=False)


def console_link(base: str | None, incident_id: str) -> str | None:
    if not base:
        return None
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        return None
    return f"{base.rstrip('/')}/?incident={quote(incident_id)}"


def alert_settings(environment: Mapping[str, str]) -> dict[str, object]:
    def bounded(name: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(environment.get(name, default))))
        except ValueError:
            return default
    default_environment = environment.get("ALERT_DEFAULT_ENVIRONMENT", "prod-sg").strip().lower()
    return {
        "service_labels": parse_service_labels(environment.get("ALERT_SERVICE_LABELS")),
        "default_environment": default_environment if _ENVIRONMENT.fullmatch(default_environment) else "prod-sg",
        "max_per_webhook": bounded("ALERT_MAX_DIAGNOSES_PER_WEBHOOK", 5, 1, 20),
        "max_concurrent": bounded("ALERT_MAX_CONCURRENT_DIAGNOSES", 2, 1, 8),
        "dedupe_seconds": bounded("ALERT_DEDUPE_SECONDS", 3600, 60, 86_400),
        "console_url": environment.get("CONSOLE_URL") or None,
    }
