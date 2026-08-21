"""Kafka consumer worker for Auto SRE - processes LLM analysis asynchronously."""

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent import Agent
from kafka_producer import get_kafka_producer, process_outbox
from llm import LlmClient
from store import Store, async_session_maker
from vl import build_vl_client
from metrics import (
    kafka_consumer_process_duration_seconds,
    kafka_consumer_process_total,
    kafka_consumer_lag,
)

logger = logging.getLogger("sre.kafka.consumer")

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC_FINDINGS = os.environ.get("KAFKA_TOPIC_FINDINGS", "auto-sre.findings")
KAFKA_TOPIC_BLOG = os.environ.get("KAFKA_TOPIC_BLOG", "auto-sre.blog")
KAFKA_TOPIC_SCAN_EVENTS = os.environ.get("KAFKA_TOPIC_SCAN_EVENTS", "auto-sre.scan-events")
KAFKA_CONSUMER_GROUP = os.environ.get("KAFKA_CONSUMER_GROUP", "auto-sre-worker")

OUTBOX_POLL_INTERVAL = int(os.environ.get("OUTBOX_POLL_INTERVAL", "5"))
CONSUMER_MAX_POLL_RECORDS = int(os.environ.get("CONSUMER_MAX_POLL_RECORDS", "10"))
CONSUMER_MAX_POLL_INTERVAL_MS = int(os.environ.get("CONSUMER_MAX_POLL_INTERVAL_MS", "300000"))


@dataclass
class FindingPayload:
    finding_id: int
    stream: str
    query: str
    window_start: str
    window_end: str
    samples: list
    context: dict


class KafkaConsumerWorker:
    """Background worker that consumes finding events and runs LLM analysis."""

    def __init__(self) -> None:
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._agent: Optional[Agent] = None

    async def start(self) -> None:
        if self._running:
            return

        self._agent = Agent(
            store=Store(),
            vl=build_vl_client(),
            llm=LlmClient(),
        )

        self._consumer = AIOKafkaConsumer(
            KAFKA_TOPIC_FINDINGS,
            KAFKA_TOPIC_BLOG,
            KAFKA_TOPIC_SCAN_EVENTS,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id=KAFKA_CONSUMER_GROUP,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            max_poll_records=CONSUMER_MAX_POLL_RECORDS,
            max_poll_interval_ms=CONSUMER_MAX_POLL_INTERVAL_MS,
        )
        await self._consumer.start()
        self._running = True
        logger.info("Kafka consumer worker started, group=%s", KAFKA_CONSUMER_GROUP)

        asyncio.create_task(self._consume_loop())
        asyncio.create_task(self._outbox_poller())
        asyncio.create_task(self._lag_updater())

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Kafka consumer worker...")

        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

        if self._tasks:
            logger.info("Waiting for %d running tasks...", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)

        logger.info("Kafka consumer worker stopped")

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_loop(self) -> None:
        if not self._consumer:
            return

        async for msg in self._consumer:
            if not self._running:
                break

            start_time = time.time()
            try:
                await self._process_message(msg.topic, msg.key, msg.value)
                kafka_consumer_process_duration_seconds.labels(topic=msg.topic, result="success").observe(time.time() - start_time)
                kafka_consumer_process_total.labels(topic=msg.topic, result="success").inc()
            except Exception as exc:
                kafka_consumer_process_duration_seconds.labels(topic=msg.topic, result="error").observe(time.time() - start_time)
                kafka_consumer_process_total.labels(topic=msg.topic, result="error").inc()
                logger.exception("Error processing message from %s: %s", msg.topic, exc)

    async def _process_message(self, topic: str, key: Optional[str], value: dict) -> None:
        logger.info("Received message: topic=%s key=%s", topic, key)

        if topic == KAFKA_TOPIC_FINDINGS:
            self._track_task(asyncio.create_task(self._handle_finding(value)))
        elif topic == KAFKA_TOPIC_BLOG:
            self._track_task(asyncio.create_task(self._handle_blog(value)))
        elif topic == KAFKA_TOPIC_SCAN_EVENTS:
            self._track_task(asyncio.create_task(self._handle_scan_event(value)))

    async def _handle_finding(self, payload: dict) -> None:
        """Process finding analysis request."""
        finding_id = payload.get("finding_id")
        if not finding_id:
            logger.warning("Finding payload missing finding_id")
            return

        logger.info("Processing finding analysis for id=%s", finding_id)

        async with async_session_maker() as session:
            from store import Finding
            result = await session.execute(select(Finding).where(Finding.id == finding_id))
            finding = result.scalar_one_or_none()

        if not finding:
            logger.warning("Finding %s not found in DB", finding_id)
            return

        if finding.acknowledged:
            logger.info("Finding %s already acknowledged, skipping", finding_id)
            return

        try:
            query = payload.get("query", "")
            window_start = payload.get("window_start", "")
            window_end = payload.get("window_end", "")
            samples = payload.get("samples", [])

            context = {
                "stream": finding.service or "все потоки",
                "baseline_mean_per_window": payload.get("baseline_mean", 0),
                "current_count_in_last_window": payload.get("current_count", 0),
                "window_minutes": payload.get("window_minutes", 15),
                "sample_logs": samples,
            }

            finding_data = await self._agent.llm.analyze_logs(json.dumps(context, ensure_ascii=False, indent=2))

            finding_data.setdefault("service", finding.service)
            finding_data.setdefault("title", finding.title)
            finding_data.setdefault("summary", finding.summary)
            finding_data.setdefault("severity", finding.severity)
            finding_data.setdefault("confidence", finding.confidence)

            async with async_session_maker() as session:
                from store import Finding
                result = await session.execute(select(Finding).where(Finding.id == finding_id))
                db_finding = result.scalar_one_or_none()
                if db_finding:
                    db_finding.severity = finding_data.get("severity", db_finding.severity)
                    db_finding.title = finding_data.get("title", db_finding.title)
                    db_finding.summary = finding_data.get("summary", db_finding.summary)
                    db_finding.possible_cause = finding_data.get("possible_cause", db_finding.possible_cause)
                    db_finding.recommended_action = finding_data.get("recommended_action", db_finding.recommended_action)
                    db_finding.confidence = finding_data.get("confidence", db_finding.confidence)
                    await session.commit()
                    logger.info("Finding %s updated with LLM analysis", finding_id)

            producer = get_kafka_producer()
            await producer.send_finding({
                "id": finding_id,
                "service": finding.service,
                "title": finding_data.get("title"),
                "severity": finding_data.get("severity"),
                "summary": finding_data.get("summary"),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            })

        except Exception as exc:
            logger.exception("Failed to process finding %s: %s", finding_id, exc)

    async def _handle_blog(self, payload: dict) -> None:
        """Process blog generation request."""
        logger.info("Processing blog generation request")
        try:
            post_id = await self._agent.generate_daily_blog()
            if post_id:
                logger.info("Blog post generated: id=%s", post_id)
        except Exception as exc:
            logger.exception("Blog generation failed: %s", exc)

    async def _handle_scan_event(self, payload: dict) -> None:
        """Process scan event (for monitoring/metrics)."""
        event_type = payload.get("event_type")
        logger.debug("Scan event: %s", event_type)

    async def _outbox_poller(self) -> None:
        """Periodically process outbox events."""
        while self._running:
            try:
                count = await process_outbox()
                if count > 0:
                    logger.debug("Processed %d outbox events", count)
            except Exception as exc:
                logger.warning("Outbox poller error: %s", exc)

            await asyncio.sleep(OUTBOX_POLL_INTERVAL)

    async def _lag_updater(self) -> None:
        """Periodically update consumer lag metrics."""
        while self._running:
            try:
                if self._consumer:
                    partitions = self._consumer.assignment()
                    for tp in partitions:
                        committed = await self._consumer.committed(tp)
                        end_offsets = await self._consumer.end_offsets([tp])
                        lag = end_offsets.get(tp, 0) - (committed or 0)
                        kafka_consumer_lag.labels(topic=tp.topic, partition=tp.partition).set(lag)
            except Exception as exc:
                logger.warning("Lag updater error: %s", exc)
            await asyncio.sleep(30)


_kafka_consumer: Optional[KafkaConsumerWorker] = None


def get_kafka_consumer() -> KafkaConsumerWorker:
    global _kafka_consumer
    if _kafka_consumer is None:
        _kafka_consumer = KafkaConsumerWorker()
    return _kafka_consumer


async def init_kafka_consumer() -> None:
    worker = get_kafka_consumer()
    await worker.start()


async def close_kafka_consumer() -> None:
    global _kafka_consumer
    if _kafka_consumer:
        await _kafka_consumer.stop()
        _kafka_consumer = None