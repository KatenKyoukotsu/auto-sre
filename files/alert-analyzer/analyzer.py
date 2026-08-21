"""Alert batching and LLM analysis logic."""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from common.llm_client import LlmClient, LlmError
from metrics import (
    alert_batch_size,
    alert_batch_duration_seconds,
    alert_analysis_duration_seconds,
    alert_analysis_total,
    alert_deduped_total,
    alert_webhook_received_total,
)

from models import Alert, AlertGroupKey, AlertmanagerPayload
from store import AlertStore

logger = logging.getLogger("alert.analyzer")

ALERT_BATCH_WINDOW_SEC = int(os.environ.get("ALERT_BATCH_WINDOW_SEC", "300"))
ALERT_BATCH_MAX = int(os.environ.get("ALERT_BATCH_MAX", "20"))
ALERT_DEDUP_WINDOW = int(os.environ.get("ALERT_DEDUP_WINDOW", "3600"))

# LLM prompt for alert analysis
ALERT_ANALYSIS_SYSTEM_PROMPT = """
Ты — Auto SRE, анализируешь алерты Alertmanager. Твоя задача:
1. Найти коррелированные алерты (одинаковая корневая причина)
2. Оценить эскалацию серьёзности
3. Предложить вероятную причину
4. Рекомендовать немедленные действия
5. Дать уверенность (0-1)

Правила:
- Группируй алерты по fingerprint, alertname, severity, cluster, namespace, service
- Игнорируй resolved алерты для анализа причины (но учитывай для контекста)
- Не выдумывай факты, которых нет в данных
- Пиши на русском языке, кратко и по делу

Ответ строго JSON:
{
  "correlated_groups": [
    {
      "fingerprints": ["fp1", "fp2"],
      "root_cause": "описание причины",
      "severity": "critical|high|medium|low",
      "actions": ["действие 1", "действие 2"],
      "confidence": 0.85
    }
  ],
  "unmatched_alerts": ["fp3"],
  "summary": "краткое резюме"
}
"""

ALERT_ANALYSIS_USER_TEMPLATE = """
Алерты для анализа (JSON):
{alerts_json}

Контекст:
- Временное окно: {window_sec} сек
- Всего алертов: {total_count}
- Уникальных групп (по fingerprint): {unique_groups}
"""


@dataclass
class PendingAlert:
    alert: Alert
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertBatcher:
    """Batches alerts by time window and group key."""

    def __init__(self, window_sec: int = ALERT_BATCH_WINDOW_SEC, max_size: int = ALERT_BATCH_MAX):
        self.window_sec = window_sec
        self.max_size = max_size
        self._buffer: dict[AlertGroupKey, list[PendingAlert]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add(self, alert: Alert) -> Optional[list[Alert]]:
        """Add alert, return batch if window exceeded or max size reached."""
        group_key = AlertGroupKey.from_alert(alert)
        async with self._lock:
            self._buffer[group_key].append(PendingAlert(alert=alert))

            # Check if this group should be flushed
            group_alerts = self._buffer[group_key]
            if len(group_alerts) >= self.max_size:
                return self._flush_group(group_key)

            # Check time window for oldest alert in this group
            oldest = group_alerts[0].received_at
            if (datetime.now(timezone.utc) - oldest).total_seconds() >= self.window_sec:
                return self._flush_group(group_key)

        return None

    def _flush_group(self, group_key: AlertGroupKey) -> list[Alert]:
        alerts = [pa.alert for pa in self._buffer.pop(group_key, [])]
        return alerts

    async def flush_all(self) -> list[Alert]:
        """Flush all buffered alerts."""
        async with self._lock:
            all_alerts = []
            for group_key in list(self._buffer.keys()):
                all_alerts.extend(self._flush_group(group_key))
            return all_alerts

    async def get_buffer_stats(self) -> dict:
        async with self._lock:
            return {
                "groups": len(self._buffer),
                "total_alerts": sum(len(v) for v in self._buffer.values()),
            }


class AlertAnalyzer:
    """Analyzes batched alerts using LLM."""

    def __init__(self, store: AlertStore, llm: LlmClient):
        self.store = store
        self.llm = llm
        self.batcher = AlertBatcher()

    async def process_webhook(self, payload: AlertmanagerPayload) -> dict:
        """Process incoming webhook payload, batch alerts, trigger analysis."""
        results = {"received": 0, "batched": 0, "analyzed": 0, "deduped": 0}

        for alert in payload.alerts:
            results["received"] += 1
            alert_webhook_received_total.labels(status=alert.status).inc()

            # Deduplicate by fingerprint within dedup window
            if await self._is_duplicate(alert.fingerprint):
                alert_deduped_total.inc()
                results["deduped"] += 1
                continue

            batch = await self.batcher.add(alert)
            if batch:
                results["batched"] += len(batch)
                await self._analyze_batch(batch)
                results["analyzed"] += 1

        return results

    async def _is_duplicate(self, fingerprint: str) -> bool:
        """Check if we've seen this fingerprint recently."""
        # Simple approach: check if analysis exists for this fingerprint recently
        since = datetime.now(timezone.utc) - timedelta(seconds=ALERT_DEDUP_WINDOW)
        analyses = await self.store.list_analyses(limit=100, since=since)
        # store._to_dict уже распарсил JSON-колонки в структуры
        return any(
            isinstance(a.get("correlated_group"), list) and fingerprint in a["correlated_group"]
            for a in analyses
        )

    async def _analyze_batch(self, alerts: list[Alert]) -> None:
        if not alerts:
            return

        start_time = time.time()
        firing_alerts = [a for a in alerts if a.status == "firing"]
        if not firing_alerts:
            logger.info("All alerts resolved, skipping LLM analysis")
            return

        # Prepare payload for LLM
        alerts_data = []
        for a in firing_alerts:
            alerts_data.append({
                "fingerprint": a.fingerprint,
                "alertname": a.labels.alertname,
                "severity": a.labels.severity,
                "instance": a.labels.instance,
                "job": a.labels.job,
                "namespace": a.labels.namespace,
                "cluster": a.labels.cluster,
                "service": a.labels.service,
                "summary": a.annotations.summary,
                "description": a.annotations.description,
                "startsAt": a.startsAt.isoformat(),
                "status": a.status,
            })

        user_prompt = ALERT_ANALYSIS_USER_TEMPLATE.format(
            alerts_json=json.dumps(alerts_data, ensure_ascii=False, indent=2),
            window_sec=ALERT_BATCH_WINDOW_SEC,
            total_count=len(alerts),
            unique_groups=len(set(a.fingerprint for a in firing_alerts)),
        )

        try:
            result = await self.llm.complete_json(ALERT_ANALYSIS_SYSTEM_PROMPT, user_prompt)
        except LlmError as exc:
            logger.error("LLM analysis failed: %s", exc)
            alert_analysis_duration_seconds.labels(result="error").observe(time.time() - start_time)
            alert_analysis_total.labels(result="error").inc()
            return

        # Store results
        for group in result.get("correlated_groups", []):
            fingerprints = group.get("fingerprints", [])
            for fp in fingerprints:
                alert = next((a for a in firing_alerts if a.fingerprint == fp), None)
                if not alert:
                    continue

                analysis = {
                    "alert_fingerprint": fp,
                    "alertname": alert.labels.alertname,
                    "severity": group.get("severity", alert.labels.severity),
                    "cluster": alert.labels.cluster,
                    "namespace": alert.labels.namespace,
                    "service": alert.labels.service,
                    "status": alert.status,
                    "correlated_group": fingerprints,
                    "root_cause": group.get("root_cause"),
                    "suggested_actions": group.get("actions"),
                    "confidence": group.get("confidence", 0),
                    "raw_alerts": [a for a in alerts_data if a["fingerprint"] in fingerprints],
                    "llm_model": self.llm.model,
                }
                await self.store.add_analysis(analysis)

        # Handle unmatched
        for fp in result.get("unmatched_alerts", []):
            alert = next((a for a in firing_alerts if a.fingerprint == fp), None)
            if not alert:
                continue
            analysis = {
                "alert_fingerprint": fp,
                "alertname": alert.labels.alertname,
                "severity": alert.labels.severity,
                "cluster": alert.labels.cluster,
                "namespace": alert.labels.namespace,
                "service": alert.labels.service,
                "status": alert.status,
                "correlated_group": [fp],
                "root_cause": "Не удалось коррелировать с другими алертами",
                "suggested_actions": ["Проверить алерт вручную"],
                "confidence": 0.3,
                "raw_alerts": [a for a in alerts_data if a["fingerprint"] == fp],
                "llm_model": self.llm.model,
            }
            await self.store.add_analysis(analysis)

        alert_batch_size.observe(len(firing_alerts))
        alert_batch_duration_seconds.observe(time.time() - start_time)
        alert_analysis_duration_seconds.labels(result="success").observe(time.time() - start_time)
        alert_analysis_total.labels(result="success").inc()

    async def flush_pending(self) -> int:
        """Flush all pending alerts in buffer."""
        alerts = await self.batcher.flush_all()
        if alerts:
            await self._analyze_batch(alerts)
        return len(alerts)

    async def periodic_flush(self, interval: int = 60) -> None:
        """Periodically flush pending alerts."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.flush_pending()
            except Exception:
                logger.exception("Periodic flush failed")