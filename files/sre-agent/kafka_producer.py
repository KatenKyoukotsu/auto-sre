"""Kafka producer for Auto SRE with transactional outbox pattern."""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaProducer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from store import Base, async_session_maker, OutboxEvent
from metrics import (
    kafka_producer_send_duration_seconds,
    kafka_producer_send_total,
    kafka_outbox_pending,
    kafka_outbox_processed_total,
)

logger = logging.getLogger("sre.kafka")

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC_FINDINGS = os.environ.get("KAFKA_TOPIC_FINDINGS", "auto-sre.findings")
KAFKA_TOPIC_BLOG = os.environ.get("KAFKA_TOPIC_BLOG", "auto-sre.blog")
KAFKA_TOPIC_SCAN_EVENTS = os.environ.get("KAFKA_TOPIC_SCAN_EVENTS", "auto-sre.scan-events")


@dataclass
class KafkaEvent:
    topic: str
    key: Optional[str]
    payload: dict


class KafkaProducer:
    """Async Kafka producer with outbox pattern for guaranteed delivery."""

    def __init__(self) -> None:
        self._producer: Optional[AIOKafkaProducer] = None
        self._running = False

    async def start(self) -> None:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                enable_idempotence=True,
                max_in_flight_requests_per_connection=5,
                compression_type="snappy",
            )
            await self._producer.start()
            logger.info("Kafka producer started: %s", KAFKA_BOOTSTRAP_SERVERS)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None
            logger.info("Kafka producer stopped")

    async def send(self, topic: str, payload: dict, key: Optional[str] = None) -> None:
        if not self._producer:
            raise RuntimeError("Producer not started")
        start_time = time.time()
        try:
            await self._producer.send_and_wait(topic, value=payload, key=key)
            kafka_producer_send_duration_seconds.labels(topic=topic, result="success").observe(time.time() - start_time)
            kafka_producer_send_total.labels(topic=topic, result="success").inc()
            logger.debug("Sent event to %s: key=%s", topic, key)
        except Exception as exc:
            kafka_producer_send_duration_seconds.labels(topic=topic, result="error").observe(time.time() - start_time)
            kafka_producer_send_total.labels(topic=topic, result="error").inc()
            raise

    async def send_finding(self, finding: dict) -> None:
        await self.send(KAFKA_TOPIC_FINDINGS, finding, key=finding.get("service", "unknown"))

    async def send_blog_post(self, post: dict) -> None:
        await self.send(KAFKA_TOPIC_BLOG, post, key=str(post.get("id", "")))

    async def send_scan_event(self, event_type: str, data: dict) -> None:
        payload = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
        await self.send(KAFKA_TOPIC_SCAN_EVENTS, payload, key=event_type)


async def add_to_outbox(session: AsyncSession, topic: str, payload: dict, key: Optional[str] = None) -> None:
    """Add event to outbox table within the same transaction as business logic."""
    event = OutboxEvent(
        topic=topic,
        key=key,
        payload=json.dumps(payload, ensure_ascii=False),
    )
    session.add(event)
    logger.debug("Added to outbox: topic=%s key=%s", topic, key)


async def process_outbox(batch_size: int = 100) -> int:
    """Process pending outbox events. Returns number of processed events."""
    processed = 0
    async with async_session_maker() as session:
        result = await session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = result.scalars().all()

        if not events:
            kafka_outbox_pending.set(0)
            return 0

        kafka_outbox_pending.set(len(events))

        producer = KafkaProducer()
        await producer.start()
        try:
            for event in events:
                try:
                    await producer.send(event.topic, json.loads(event.payload), event.key)
                    event.processed_at = datetime.now(timezone.utc)
                    processed += 1
                    kafka_outbox_processed_total.labels(result="success").inc()
                except Exception as exc:
                    event.retry_count += 1
                    event.last_error = str(exc)
                    kafka_outbox_processed_total.labels(result="error").inc()
                    logger.warning("Failed to send outbox event %d (retry %d): %s", event.id, event.retry_count, exc)
            await session.commit()
        finally:
            await producer.stop()
    return processed


_kafka_producer: Optional[KafkaProducer] = None


def get_kafka_producer() -> KafkaProducer:
    global _kafka_producer
    if _kafka_producer is None:
        _kafka_producer = KafkaProducer()
    return _kafka_producer


async def init_kafka() -> None:
    producer = get_kafka_producer()
    await producer.start()


async def close_kafka() -> None:
    global _kafka_producer
    if _kafka_producer:
        await _kafka_producer.stop()
        _kafka_producer = None