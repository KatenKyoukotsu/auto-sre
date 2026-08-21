"""SRE-агент: детекция аномалий в логах Victoria Logs через LLM.

Периодически снимает срезы ошибок, строит базовую линию (rolling baseline),
выявляет всплески, отдаёт выжимку в LiteLLM (Gemma) и сохраняет находки.
Также генерирует ежедневный пост Auto SRE-блога.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from llm import LlmClient, LlmError
from store import Store
from vl import VlError, build_vl_client
from metrics import (
    scan_duration_seconds,
    scan_total,
    scan_findings_created,
    scan_streams_checked,
    scan_candidates_found,
    scan_deduped,
    scan_windows_queried,
    last_scan_timestamp,
    last_scan_error,
    findings_created_total,
    findings_acknowledged_total,
    finding_age_seconds,
    blog_generation_duration_seconds,
    blog_generation_total,
    blog_findings_included,
)

KAFKA_TOPIC_FINDINGS = os.environ.get("KAFKA_TOPIC_FINDINGS", "auto-sre.findings")

logger = logging.getLogger("sre.agent")

ERROR_PATTERN = os.environ.get(
    "ERROR_PATTERN",
    "i(error*) OR i(exception*) OR i(panic*) OR i(fatal*) OR i(traceback*)",
)
HISTORY_HOURS = int(os.environ.get("HISTORY_HOURS", "6"))
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "15"))
MIN_ABS_SPIKE = int(os.environ.get("MIN_ABS_SPIKE", "20"))
SPIKE_STD_MULTIPLIER = float(os.environ.get("SPIKE_STD_MULTIPLIER", "3.0"))
SPIKE_MEAN_MULTIPLIER = float(os.environ.get("SPIKE_MEAN_MULTIPLIER", "2.0"))
SAMPLE_LIMIT = int(os.environ.get("SAMPLE_LIMIT", "40"))
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "8"))
DEDUP_MINUTES = int(os.environ.get("DEDUP_MINUTES", "60"))
FULL_SCAN_HOURS = int(os.environ.get("FULL_SCAN_HOURS", "24"))
FULL_SCAN_MAX_WINDOWS = int(os.environ.get("FULL_SCAN_MAX_WINDOWS", "96"))


class Agent:
    def __init__(self, store: Store, vl=None, llm: LlmClient | None = None):
        self.store = store
        self.vl = vl or build_vl_client()
        self.llm = llm or LlmClient()
        self.last_scan = None
        self.last_error = None
        self.blog_status = "idle"  # idle | generating
        self.blog_error = None

    def _windows(self) -> tuple[list[dict], dict]:
        """Возвращает историю окон и текущее окно (start/end) в относительных величинах."""
        now = datetime.now(timezone.utc)
        history = timedelta(hours=HISTORY_HOURS)
        window = timedelta(minutes=WINDOW_MINUTES)
        windows = []
        cursor = now - history
        while cursor < now - window:
            start, end = cursor, cursor + window
            windows.append({
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            cursor += window
        current = {
            "start": (now - window).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return windows, current

    async def _series(self, query: str) -> list[int]:
        windows, _current = self._windows()
        series = []
        failures = 0
        for win in windows:
            try:
                count = await self.vl.count_logs(query, win["start"], win["end"])
            except (VlError, Exception) as exc:
                logger.warning("count_logs сбой (%s): %s", win["start"], exc)
                count = 0
                failures += 1
            series.append(count)
        if failures == len(windows):
            raise VlError("все окна базовой линии недоступны")
        if failures:
            logger.warning("%d из %d окон базовой линии обработаны как 0", failures, len(windows))
        return series

    @staticmethod
    def _error_query(stream: str | None) -> str:
        """Строит LogsQL-фильтр ошибок, ограниченный стримом при его наличии.

        ERROR_PATTERN — OR-цепочка, поэтому без скобок приоритет AND (выше OR)
        приклеил бы стрим-фильтр только к последнему терму. `_stream:{...}`
        — собственный синтаксис VictoriaLogs для выборки по значению `_stream`.
        """
        base = f"({ERROR_PATTERN})"
        if not stream or stream == "{}":
            return base
        return f"{base} AND _stream:{stream}"

    @staticmethod
    def _is_spike(series: list[int], current: int) -> tuple[bool, float, float]:
        if not series:
            return False, 0.0, 0.0
        mean = sum(series) / len(series)
        variance = sum((x - mean) ** 2 for x in series) / len(series)
        std = variance ** 0.5
        threshold = max(mean + SPIKE_STD_MULTIPLIER * std, SPIKE_MEAN_MULTIPLIER * mean)
        return current > threshold and current >= MIN_ABS_SPIKE, mean, current

    async def scan(self) -> list[int]:
        """Один проход детекции. Возвращает id созданных находок."""
        start_time = time.time()
        scan_streams_checked.set(0)
        scan_candidates_found.set(0)
        logger.info("Сканирование прод-логов за последние %sh", HISTORY_HOURS)
        _, current = self._windows()
        created = []

        try:
            streams = await self.vl.get_streams(current["start"], current["end"])
        except Exception as exc:
            logger.warning("get_streams сбой, работаем без разбивки по потокам: %s", exc)
            streams = [{"stream": None}]

        scan_streams_checked.set(len(streams[:MAX_STREAMS]))
        candidates = []
        windows_count = 0
        for entry in streams[:MAX_STREAMS]:
            stream = entry.get("stream")
            query = self._error_query(stream)
            try:
                series = await self._series(query)
                windows_count += len(series)
                current_count = await self.vl.count_logs(query, current["start"], current["end"])
            except Exception as exc:
                logger.warning("серия для потока %s не получена: %s", stream, exc)
                continue
            is_spike, mean, latest = self._is_spike(series, current_count)
            if is_spike:
                candidates.append({"stream": stream, "mean": mean, "latest": latest})

        scan_candidates_found.set(len(candidates))
        scan_windows_queried.inc(windows_count)

        if not candidates:
            self.last_scan = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.last_error = None
            last_scan_timestamp.set(time.time())
            last_scan_error.set(0)
            scan_duration_seconds.labels(result="success").observe(time.time() - start_time)
            scan_total.labels(result="success").inc()
            logger.info("Аномалий не найдено")
            return created

        for cand in candidates:
            try:
                finding_id = await self._analyze_and_store(cand, current)
                if finding_id:
                    created.append(finding_id)
            except Exception as exc:
                logger.exception("Анализ кандидата %s не удался: %s", cand["stream"], exc)
                self.last_error = str(exc)

        self.last_scan = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.last_error = None
        last_scan_timestamp.set(time.time())
        last_scan_error.set(0)
        scan_duration_seconds.labels(result="success").observe(time.time() - start_time)
        scan_total.labels(result="success").inc()

        for finding_id in created:
            finding = await self.store.get_finding(finding_id)
            if finding:
                severity = finding.get("severity", "low")
                service = finding.get("service", "unknown")
                scan_findings_created.labels(severity=severity).inc()
                findings_created_total.labels(severity=severity, service=service).inc()

        return created

    @staticmethod
    def _windows_range(start_dt: datetime, end_dt: datetime, step: timedelta) -> list[dict]:
        """Разбивает [start_dt, end_dt] на окна шага `step`."""
        windows = []
        cursor = start_dt
        while cursor < end_dt:
            w_end = min(cursor + step, end_dt)
            windows.append({
                "start": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": w_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            cursor = w_end
        return windows

    async def full_scan(self, start: str | None = None, end: str | None = None) -> list[int]:
        """Полное сканирование диапазона: перечисляет ВСЕ потоки (без лимита)
        и ищет окна-всплески ошибок относительно базовой линии каждого потока."""
        now = datetime.now(timezone.utc)
        if start and end:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        else:
            start_dt, end_dt = now - timedelta(hours=FULL_SCAN_HOURS), now
        if end_dt <= start_dt:
            raise ValueError("конец диапазона должен быть позже начала")
        end_dt = min(end_dt, now)

        window = timedelta(minutes=WINDOW_MINUTES)
        raw_windows = int((end_dt - start_dt).total_seconds() / window.total_seconds())
        if raw_windows > FULL_SCAN_MAX_WINDOWS:
            step = (end_dt - start_dt) / FULL_SCAN_MAX_WINDOWS
            logger.info(
                "full_scan: %d окон > %d, шаг увеличен до %s",
                raw_windows, FULL_SCAN_MAX_WINDOWS, step,
            )
        else:
            step = window
        windows = self._windows_range(start_dt, end_dt, step)
        logger.info(
            "Полное сканирование [%s, %s]: %d окон",
            windows[0]["start"], windows[-1]["end"], len(windows),
        )

        totals: dict[str, int] = {}
        series: dict[str, list[int]] = {}
        failures = 0
        for win_index, win in enumerate(windows):
            try:
                rows = await self.vl.count_by_stream(ERROR_PATTERN, win["start"], win["end"])
            except (VlError, Exception) as exc:
                logger.warning("count_by_stream сбой (%s): %s", win["start"], exc)
                failures += 1
                continue
            for row in rows:
                stream = row["stream"]
                series.setdefault(stream, [0] * len(windows))
                series[stream][win_index] = row["count"]
                totals[stream] = totals.get(stream, 0) + row["count"]

        if failures > len(windows) // 2:
            raise VlError(f"больше половины окон недоступны ({failures}/{len(windows)})")

        self.last_scan = now.isoformat(timespec="seconds")
        if not totals:
            self.last_error = None
            logger.info("full_scan: ошибок в диапазоне не найдено")
            return []

        candidates = []
        for stream, total in totals.items():
            if total < MIN_ABS_SPIKE:
                continue
            s = series[stream]
            for i, value in enumerate(s):
                if value < MIN_ABS_SPIKE:
                    continue
                baseline = s[:i] + s[i + 1:]
                if baseline:
                    is_spike, mean, _ = self._is_spike(baseline, value)
                else:
                    is_spike, mean = value >= MIN_ABS_SPIKE, 0.0
                if is_spike:
                    candidates.append({"stream": stream, "mean": mean, "latest": value, "window": windows[i]})

        self.last_error = None
        if not candidates:
            logger.info("full_scan: всплесков не найдено")
            return []

        logger.info("full_scan: %d кандидатов на анализ", len(candidates))
        created = []
        for cand in candidates:
            try:
                created.append(await self._analyze_and_store(cand, cand["window"]))
            except Exception as exc:
                logger.exception("Анализ кандидата %s не удался: %s", cand["stream"], exc)
                self.last_error = str(exc)
        return created

    async def _analyze_and_store(self, cand: dict, current: dict) -> int | None:
        query = self._error_query(cand["stream"])
        samples = await self.vl.search_logs(query, current["start"], current["end"], limit=SAMPLE_LIMIT)
        logger.info("Поток %s: выборка для LLM = %d строк", cand["stream"] or "все потоки", len(samples))

        recent = await self.store.list_findings(limit=50)
        dedup_age = timedelta(minutes=DEDUP_MINUTES)
        for existing in recent:
            created_at = existing.get("created_at", "")
            try:
                if isinstance(created_at, str):
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(created_at)
                else:
                    age = datetime.now(timezone.utc) - created_at
            except ValueError:
                age = timedelta(seconds=1 << 30)
            if (existing.get("service") == (cand["stream"] or "global")
                    and age <= dedup_age):
                logger.info("Пропускаю повторный фиксинг для %s (дедупликация)", cand["stream"])
                return None

        context = {
            "stream": cand["stream"] or "все потоки",
            "baseline_mean_per_window": round(cand["mean"], 1),
            "current_count_in_last_window": cand["latest"],
            "window_minutes": WINDOW_MINUTES,
            "sample_logs": samples,
        }
        logger.info("Отправляю в LLM анализ всплеска потока %s", cand["stream"])
        try:
            finding = await self.llm.analyze_logs(json.dumps(context, ensure_ascii=False, indent=2))
        except LlmError as exc:
            logger.error("LLM-анализ не удался: %s", exc)
            finding = {}

        finding.setdefault("service", cand["stream"] or "global")
        finding.setdefault("title", f"Всплеск ошибок: {cand['stream'] or 'все потоки'}")
        finding.setdefault(
            "summary",
            f"Число ошибок в текущем окне ({cand['latest']}) значительно выше "
            f"базовой линии ({round(cand['mean'], 1)} в среднем за окно).",
        )
        finding.setdefault("severity", "medium")
        finding.setdefault("confidence", None)
        finding.setdefault("raw_data", {"count": cand["latest"], "baseline": cand["mean"], "samples": samples})

        outbox_payload = {
            "finding_id": 0,
            "stream": cand["stream"] or "global",
            "query": query,
            "window_start": current["start"],
            "window_end": current["end"],
            "samples": samples,
            "baseline_mean": round(cand["mean"], 1),
            "current_count": cand["latest"],
            "window_minutes": WINDOW_MINUTES,
            "context": context,
        }
        finding_id = await self.store.add_finding_with_outbox(
            finding=finding,
            topic=KAFKA_TOPIC_FINDINGS,
            payload=outbox_payload,
            key=cand["stream"] or "global",
        )
        return finding_id

    async def generate_daily_blog(self) -> int | None:
        start_time = time.time()
        self.blog_status = "generating"
        self.blog_error = None
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
            findings = await self.store.findings_since(since)
            blog_findings_included.set(len(findings))
            digest = {
                "period": "последние 24 часа",
                "findings_count": len(findings),
                "findings": [
                    {k: v for k, v in f.items() if k not in ("raw_data",)}
                    for f in findings
                ],
            }
            try:
                post = await self.llm.write_blog_post(json.dumps(digest, ensure_ascii=False, indent=2, default=str))
            except LlmError as exc:
                logger.error("Блог-пост не сгенерирован: %s", exc)
                self.blog_error = str(exc)
                blog_generation_duration_seconds.labels(result="error").observe(time.time() - start_time)
                blog_generation_total.labels(result="error").inc()
                return None
            if not post.get("content"):
                self.blog_error = "пустой ответ модели"
                blog_generation_duration_seconds.labels(result="error").observe(time.time() - start_time)
                blog_generation_total.labels(result="error").inc()
                return None
            post_id = await self.store.add_blog_post(post.get("title", "SRE-дайджест"), post["content"])
            blog_generation_duration_seconds.labels(result="success").observe(time.time() - start_time)
            blog_generation_total.labels(result="success").inc()
            return post_id
        finally:
            self.blog_status = "idle"


async def scan_job(agent: Agent) -> None:
    try:
        created = await agent.scan()
        logger.info("Скан завершён, создано находок: %d", len(created))
    except Exception:
        scan_duration_seconds.labels(result="error").observe(0)
        scan_total.labels(result="error").inc()
        last_scan_error.set(1)
        logger.exception("Скан завершился с ошибкой")


async def full_scan_job(agent: Agent, start: str | None = None, end: str | None = None) -> None:
    try:
        created = await agent.full_scan(start, end)
        logger.info("Полный скан завершён, создано находок: %d", len(created))
    except Exception:
        logger.exception("Полный скан завершился с ошибкой")

