"""작업(job) 저장소. DATABASE_URL 하나로 SQLite(개발) ↔ Postgres(운영) 전환.

작업 상태: queued → running → succeeded | failed
여러 워커가 같은 작업을 잡지 않도록 '조건부 UPDATE'로 선점한다 (DB 종류와 무관하게 동작).

워커 장애 복구:
- 워커는 작업을 처리하는 동안 heartbeat_at 을 주기적으로 갱신한다.
- heartbeat 가 끊긴 running 작업만 다시 큐에 넣는다 (오래 걸리는 정상 작업은 건드리지 않음).
- attempts 가 상한에 도달한 작업은 재시도하지 않고 failed 로 끝낸다 (워커를 죽이는 작업의 무한 반복 방지).
- finish/fail/heartbeat 는 선점 당시의 attempts 값이 일치할 때만 반영한다.
  → 죽은 줄 알았던 워커가 뒤늦게 돌아와도 재시도 중인 작업의 결과를 덮어쓰지 못한다.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, func, select, update
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
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
                    .values(status="running", started_at=_now(), heartbeat_at=_now(),
                            attempts=Job.attempts + 1)
                )
                s.commit()
                if res.rowcount == 1:
                    return s.get(Job, job_id)
        return None

    @staticmethod
    def _owned(job_id: str, attempt: int | None) -> list:
        """attempt 를 주면 '내가 선점한 그 시도'가 아직 유효할 때만 매칭된다."""
        cond = [Job.id == job_id]
        if attempt is not None:
            cond += [Job.status == "running", Job.attempts == attempt]
        return cond

    def heartbeat(self, job_id: str, attempt: int) -> bool:
        with self.sf.begin() as s:
            res = s.execute(update(Job).where(*self._owned(job_id, attempt)).values(heartbeat_at=_now()))
            return res.rowcount == 1

    def finish(self, job_id: str, *, answer: str, route: str | None, result: dict,
               attempt: int | None = None) -> bool:
        with self.sf.begin() as s:
            res = s.execute(update(Job).where(*self._owned(job_id, attempt)).values(
                status="succeeded", answer=answer, route=route, result=result, finished_at=_now()))
            return res.rowcount == 1

    def fail(self, job_id: str, error: str, attempt: int | None = None) -> bool:
        with self.sf.begin() as s:
            res = s.execute(update(Job).where(*self._owned(job_id, attempt)).values(
                status="failed", error=error[:4000], finished_at=_now()))
            return res.rowcount == 1

    def requeue_stale(self, older_than_s: float = 60, max_attempts: int = 3) -> int:
        """heartbeat 가 끊긴 running 작업을 되살린다. 반환값은 다시 큐에 넣은 개수.

        이미 max_attempts 번 시도한 작업은 큐에 넣지 않고 failed 로 끝낸다.
        """
        cutoff = _now() - timedelta(seconds=older_than_s)
        stale = (Job.status == "running", func.coalesce(Job.heartbeat_at, Job.started_at) < cutoff)
        with self.sf.begin() as s:
            s.execute(update(Job).where(*stale, Job.attempts >= max_attempts).values(
                status="failed", finished_at=_now(),
                error=f"워커가 응답하지 않아 {max_attempts}회 시도 후 중단했습니다."))
            res = s.execute(update(Job).where(*stale).values(status="queued"))
            return res.rowcount
