"""Хранилище находок агента и постов блога (PostgreSQL via SQLAlchemy async)."""

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean, Index, select, desc
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool

from metrics import (
    db_query_duration_seconds,
    db_pool_size,
    db_pool_checked_out,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://auto_sre:auto_sre@postgres:5432/auto_sre")

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Update pool metrics periodically
def update_pool_metrics() -> None:
    pool: Pool = engine.pool
    db_pool_size.set(pool.size())
    db_pool_checked_out.set(pool.checkedout())


class Base(DeclarativeBase):
    pass


class Finding(Base):
    __tablename__ = "findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    severity = Column(String(20), nullable=False, default="low")
    service = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False, default="Аномалия")
    summary = Column(Text, nullable=True)
    possible_cause = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    raw_data = Column(Text, nullable=True)
    acknowledged = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_findings_created", "created_at"),
        Index("idx_findings_service", "service"),
    )


class BlogPost(Base):
    __tablename__ = "blog_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_blog_created", "created_at"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    topic = Column(String(255), nullable=False)
    key = Column(String(255), nullable=True)
    payload = Column(Text, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_outbox_unprocessed", "processed_at", "created_at"),
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()


def _record_db_query(operation: str, start_time: float, success: bool) -> None:
    result = "success" if success else "error"
    db_query_duration_seconds.labels(operation=operation, result=result).observe(time.time() - start_time)


class Store:
    def __init__(self) -> None:
        self._session_maker = async_session_maker

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_dict(row) -> dict:
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    async def add_finding(self, finding: dict) -> int:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                obj = Finding(
                    created_at=self._now(),
                    severity=finding.get("severity", "low"),
                    service=finding.get("service", ""),
                    title=finding.get("title", "Аномалия"),
                    summary=finding.get("summary", ""),
                    possible_cause=finding.get("possible_cause", ""),
                    recommended_action=finding.get("recommended_action", ""),
                    confidence=finding.get("confidence"),
                    raw_data=json.dumps(finding.get("raw_data", []), ensure_ascii=False),
                )
                session.add(obj)
                await session.commit()
                await session.refresh(obj)
                _record_db_query("add_finding", start_time, True)
                return obj.id
        except Exception:
            _record_db_query("add_finding", start_time, False)
            raise

    async def add_finding_with_outbox(self, finding: dict, topic: str, payload: dict, key: str = None) -> int:
        """Add finding and outbox event in same transaction. Payload is updated with finding_id."""
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                obj = Finding(
                    created_at=self._now(),
                    severity=finding.get("severity", "low"),
                    service=finding.get("service", ""),
                    title=finding.get("title", "Аномалия"),
                    summary=finding.get("summary", ""),
                    possible_cause=finding.get("possible_cause", ""),
                    recommended_action=finding.get("recommended_action", ""),
                    confidence=finding.get("confidence"),
                    raw_data=json.dumps(finding.get("raw_data", []), ensure_ascii=False),
                )
                session.add(obj)
                await session.flush()

                payload["finding_id"] = obj.id
                outbox_event = OutboxEvent(
                    topic=topic,
                    key=key or finding.get("service", "unknown"),
                    payload=json.dumps(payload, ensure_ascii=False),
                )
                session.add(outbox_event)
                await session.commit()
                await session.refresh(obj)
                _record_db_query("add_finding_with_outbox", start_time, True)
                return obj.id
        except Exception:
            _record_db_query("add_finding_with_outbox", start_time, False)
            raise

    async def add_outbox_event(self, topic: str, payload: dict, key: str = None) -> None:
        """Add event to outbox within existing session (caller manages transaction)."""
        start_time = time.time()
        update_pool_metrics()
        try:
            outbox_event = OutboxEvent(
                topic=topic,
                key=key,
                payload=json.dumps(payload, ensure_ascii=False),
            )
            # This is meant to be called within an existing session
            # For simplicity, we create a new session here - caller should use add_finding_with_outbox
            async with self._session_maker() as session:
                session.add(outbox_event)
                await session.commit()
            _record_db_query("add_outbox_event", start_time, True)
        except Exception:
            _record_db_query("add_outbox_event", start_time, False)
            raise

    async def list_findings(self, limit: int = 50) -> list[dict]:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(Finding).order_by(desc(Finding.created_at)).limit(limit)
                )
                _record_db_query("list_findings", start_time, True)
                return [self._to_dict(row) for row in result.scalars().all()]
        except Exception:
            _record_db_query("list_findings", start_time, False)
            raise

    async def get_finding(self, finding_id: int) -> Optional[dict]:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                result = await session.execute(select(Finding).where(Finding.id == finding_id))
                row = result.scalar_one_or_none()
                _record_db_query("get_finding", start_time, True)
                return self._to_dict(row) if row else None
        except Exception:
            _record_db_query("get_finding", start_time, False)
            raise

    async def acknowledge_finding(self, finding_id: int) -> bool:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(Finding).where(Finding.id == finding_id)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.acknowledged = True
                    await session.commit()
                    _record_db_query("acknowledge_finding", start_time, True)
                    return True
                _record_db_query("acknowledge_finding", start_time, True)
                return False
        except Exception:
            _record_db_query("acknowledge_finding", start_time, False)
            raise

    async def findings_since(self, since_iso: str) -> list[dict]:
        start_time = time.time()
        update_pool_metrics()
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            async with self._session_maker() as session:
                result = await session.execute(
                    select(Finding).where(Finding.created_at >= since_dt).order_by(desc(Finding.created_at))
                )
                _record_db_query("findings_since", start_time, True)
                return [self._to_dict(row) for row in result.scalars().all()]
        except Exception:
            _record_db_query("findings_since", start_time, False)
            raise

    async def add_blog_post(self, title: str, content: str) -> int:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                obj = BlogPost(created_at=self._now(), title=title, content=content)
                session.add(obj)
                await session.commit()
                await session.refresh(obj)
                _record_db_query("add_blog_post", start_time, True)
                return obj.id
        except Exception:
            _record_db_query("add_blog_post", start_time, False)
            raise

    async def list_blog_posts(self, limit: int = 30) -> list[dict]:
        start_time = time.time()
        update_pool_metrics()
        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(BlogPost).order_by(desc(BlogPost.created_at)).limit(limit)
                )
                _record_db_query("list_blog_posts", start_time, True)
                return [self._to_dict(row) for row in result.scalars().all()]
        except Exception:
            _record_db_query("list_blog_posts", start_time, False)
            raise