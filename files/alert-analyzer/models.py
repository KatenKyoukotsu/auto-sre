"""Pydantic models for Alertmanager webhook payloads."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class AlertLabels(BaseModel):
    alertname: str
    severity: str = "unknown"
    instance: Optional[str] = None
    job: Optional[str] = None
    namespace: Optional[str] = None
    cluster: Optional[str] = None
    service: Optional[str] = None

    class Config:
        extra = "allow"


class AlertAnnotations(BaseModel):
    summary: Optional[str] = None
    description: Optional[str] = None
    runbook_url: Optional[str] = None

    class Config:
        extra = "allow"


class Alert(BaseModel):
    labels: AlertLabels
    annotations: AlertAnnotations = AlertAnnotations()
    startsAt: datetime
    endsAt: Optional[datetime] = None
    generatorURL: Optional[str] = None
    fingerprint: str
    status: str = "firing"


class AlertmanagerPayload(BaseModel):
    receiver: str
    status: str
    alerts: list[Alert]
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: Optional[str] = None
    version: str = "4"
    groupKey: Optional[str] = None
    truncatedAlerts: int = 0


class AlertGroupKey(BaseModel):
    alertname: str
    severity: str
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    service: Optional[str] = None

    @classmethod
    def from_alert(cls, alert: Alert) -> "AlertGroupKey":
        return cls(
            alertname=alert.labels.alertname,
            severity=alert.labels.severity,
            cluster=alert.labels.cluster,
            namespace=alert.labels.namespace,
            service=alert.labels.service,
        )

    def __hash__(self):
        return hash((self.alertname, self.severity, self.cluster, self.namespace, self.service))

    def __eq__(self, other):
        if not isinstance(other, AlertGroupKey):
            return False
        return (
            self.alertname == other.alertname
            and self.severity == other.severity
            and self.cluster == other.cluster
            and self.namespace == other.namespace
            and self.service == other.service
        )