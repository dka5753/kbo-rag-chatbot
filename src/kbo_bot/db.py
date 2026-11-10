"""작업(job) 저장소. DATABASE_URL 하나로 SQLite(개발) ↔ Postgres(운영) 전환.

작업 상태: queued → running → succeeded | failed
여러 워커가 같은 작업을 잡지 않도록 '조건부 UPDATE'로 선점한다 (DB 종류와 무관하게 동작).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    route: Mapped[str | None] = mapped_column(String(16), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # code, contexts, trace
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def make_session_factory(url: str | None = None) -> sessionmaker[Session]:
    url = url or os.getenv("DATABASE_URL", "sqlite:///./kbo_jobs.db")
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


class JobRepo:
    def __init__(self, sf: sessionmaker[Session]):
        self.sf = sf

    def create(self, question: str) -> Job:
        with self.sf.begin() as s:
            job = Job(question=question)
            s.add(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.sf() as s:
            return s.get(Job, job_id)

    def claim_next(self) -> Job | None:
        """가장 오래된 queued 작업을 running으로 바꾸며 가져온다. 경쟁에서 지면 다음 후보를 시도."""
        with self.sf() as s:
            ids = s.scalars(
                select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(5)
            ).all()
            for job_id in ids:
                res = s.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(status="running", started_at=_now(), attempts=Job.attempts + 1)
                )
                s.commit()
                if res.rowcount == 1:
                    return s.get(Job, job_id)
        return None

    def finish(self, job_id: str, *, answer: str, route: str | None, result: dict) -> None:
        with self.sf.begin() as s:
            s.execute(update(Job).where(Job.id == job_id).values(
                status="succeeded", answer=answer, route=route, result=result, finished_at=_now()))

    def fail(self, job_id: str, error: str) -> None:
        with self.sf.begin() as s:
            s.execute(update(Job).where(Job.id == job_id).values(
                status="failed", error=error[:4000], finished_at=_now()))

    def requeue_stale(self, older_than_s: int = 300) -> int:
        """워커가 죽어서 running에 멈춘 작업을 되살린다."""
        from datetime import timedelta

        cutoff = _now() - timedelta(seconds=older_than_s)
        with self.sf.begin() as s:
            res = s.execute(update(Job).where(Job.status == "running", Job.started_at < cutoff)
                            .values(status="queued"))
            return res.rowcount
