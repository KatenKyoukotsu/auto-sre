"""PostgreSQL storage for alert analysis results."""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Float, Integer, String, Text, DateTime, Index, select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from metrics import db_query_duration_seconds

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://auto_sre:auto_sre@postgres:5432/auto_sre")

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class AlertAnalysis(Base):
    __tablename__ = "alert_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    alert_fingerprint = Column(String(64), nullable=False, index=True)
    alertname = Column(String(255), nullable=False, index=True)
    severity = Column(String(50), nullable=False)
    cluster = Column(String(255), nullable=True, index=True)
    namespace = Column(String(255), nullable=True, index=True)
    service = Column(String(255), nullable=True, index=True)
    status = Column(String(20), nullable=False)  # firing, resolved
    correlated_group = Column(Text, nullable=True)  # JSON array of grouped alert fingerprints
    root_cause = Column(Text, nullable=True)
    suggested_actions = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)  # 0..1
    raw_alerts = Column(Text, nullable=False)  # JSON of original alerts
    llm_model = Column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_alert_analysis_created", "created_at"),
        Index("idx_alert_analysis_fingerprint", "alert_fingerprint"),
        Index("idx_alert_analysis_alertname", "alertname"),
        Index("idx_alert_analysis_status", "status"),
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()


def _record_db_query(operation: str, start_time: float, success: bool) -> None:
    result = "success" if success else "error"
    db_query_duration_seconds.labels(operation=operation, result=result).observe(time.time() - start_time)


import time


class AlertStore:
    def __init__(self) -> None:
        self._session_maker = async_session_maker

    async def add_analysis(self, analysis: dict) -> int:
        start_time = time.time()
        try:
            async with self._session_maker() as session:
                obj = AlertAnalysis(
                    alert_fingerprint=analysis["alert_fingerprint"],
                    alertname=analysis["alertname"],
                    severity=analysis["severity"],
                    cluster=analysis.get("cluster"),
                    namespace=analysis.get("namespace"),
                    service=analysis.get("service"),
                    status=analysis["status"],
                    correlated_group=json.dumps(analysis.get("correlated_group", [])),
                    root_cause=analysis.get("root_cause"),
                    suggested_actions=json.dumps(analysis.get("suggested_actions", [])),
                    confidence=analysis.get("confidence"),
                    raw_alerts=json.dumps(analysis.get("raw_alerts", [])),
                    llm_model=analysis.get("llm_model"),
                )
                session.add(obj)
                await session.commit()
                await session.refresh(obj)
                _record_db_query("add_analysis", start_time, True)
                return obj.id
        except Exception:
            _record_db_query("add_analysis", start_time, False)
            raise

    async def get_analysis(self, analysis_id: int) -> Optional[dict]:
        start_time = time.time()
        try:
            async with self._session_maker() as session:
                result = await session.execute(select(AlertAnalysis).where(AlertAnalysis.id == analysis_id))
                row = result.scalar_one_or_none()
                _record_db_query("get_analysis", start_time, True)
                return self._to_dict(row) if row else None
        except Exception:
            _record_db_query("get_analysis", start_time, False)
            raise

    async def list_analyses(
        self,
        limit: int = 50,
        alertname: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        start_time = time.time()
        try:
            async with self._session_maker() as session:
                query = select(AlertAnalysis).order_by(desc(AlertAnalysis.created_at))
                if alertname:
                    query = query.where(AlertAnalysis.alertname == alertname)
                if severity:
                    query = query.where(AlertAnalysis.severity == severity)
                if status:
                    query = query.where(AlertAnalysis.status == status)
                if since:
                    query = query.where(AlertAnalysis.created_at >= since)
                query = query.limit(limit)
                result = await session.execute(query)
                _record_db_query("list_analyses", start_time, True)
                return [self._to_dict(row) for row in result.scalars().all()]
        except Exception:
            _record_db_query("list_analyses", start_time, False)
            raise

    async def count_unresolved_critical(self) -> int:
        start_time = time.time()
        try:
            async with self._session_maker() as session:
                result = await session.execute(
                    select(func.count(AlertAnalysis.id)).where(
                        AlertAnalysis.severity == "critical",
                        AlertAnalysis.status == "firing",
                    )
                )
                _record_db_query("count_unresolved_critical", start_time, True)
                return result.scalar() or 0
        except Exception:
            _record_db_query("count_unresolved_critical", start_time, False)
            raise

    @staticmethod
    def _to_dict(row) -> dict:
        data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        # JSON-колонки хранятся как TEXT — наружу отдаём структуры, а не строки
        for key in ("correlated_group", "suggested_actions", "raw_alerts"):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return data