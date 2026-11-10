"""FastAPI 서버. 질문을 받으면 바로 202 + job_id를 돌려주고, 처리는 워커가 비동기로 한다.

실행: uvicorn kbo_bot.api:create_app --factory --reload
개발 편의: EMBEDDED_WORKER=1 이면 API 프로세스 안에서 워커 스레드도 함께 띄운다.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import JobRepo, make_session_factory


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500, examples=["2025 시즌 OPS 상위 5명 알려줘"])


class JobCreated(BaseModel):
    job_id: str
    status: Literal["queued"]


class JobView(BaseModel):
    job_id: str
    status: str
    question: str
    route: str | None = None
    answer: str | None = None
    error: str | None = None
    result: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: float | None = None


def create_app(repo: JobRepo | None = None, embedded_worker: bool | None = None) -> FastAPI:
    repo = repo or JobRepo(make_session_factory())
    if embedded_worker is None:
        embedded_worker = os.getenv("EMBEDDED_WORKER", "0") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if embedded_worker:
            from .worker import make_worker

            worker = make_worker(repo=repo)
            threading.Thread(target=worker.run_forever, daemon=True).start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(title="KBO RAG Chatbot API", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/v1/questions", response_model=JobCreated, status_code=202)
    def ask(req: AskRequest):
        job = repo.create(req.question.strip())
        return JobCreated(job_id=job.id, status="queued")

    @app.get("/v1/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str):
        job = repo.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        latency = None
        if job.finished_at and job.created_at:
            latency = round((job.finished_at - job.created_at).total_seconds() * 1000, 1)
        return JobView(
            job_id=job.id, status=job.status, question=job.question, route=job.route,
            answer=job.answer, error=job.error, result=job.result, created_at=job.created_at,
            started_at=job.started_at, finished_at=job.finished_at, latency_ms=latency,
        )

    return app
