import time
from datetime import UTC, datetime, timedelta

from conftest import fake_llm
from fastapi.testclient import TestClient
from sqlalchemy import update

from kbo_bot.api import create_app
from kbo_bot.db import Job
from kbo_bot.graph import build_graph
from kbo_bot.worker import Worker


def test_ask_then_worker_completes_job(repo, settings):
    client = TestClient(create_app(repo=repo, embedded_worker=False))

    r = client.post("/v1/questions", json={"question": "홈런 합계 알려줘"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert client.get(f"/v1/jobs/{job_id}").json()["status"] == "queued"

    llm = fake_llm("stats", "```python\nresult = int(batting_sample.HR.sum())\n```", "합계는 N개입니다.")
    worker = Worker(repo, build_graph(llm, settings))
    assert worker.process_one() is True
    assert worker.process_one() is False  # 큐가 비었음

    body = client.get(f"/v1/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["route"] == "stats"
    assert body["result"]["exec_output"].isdigit()
    assert body["latency_ms"] is not None


def test_failed_graph_marks_job_failed(repo):
    class Boom:
        def invoke(self, _):
            raise RuntimeError("LLM 서버 연결 실패")

    job = repo.create("질문")
    Worker(repo, Boom()).process_one()
    got = repo.get(job.id)
    assert got.status == "failed" and "연결 실패" in got.error


def test_job_is_claimed_only_once(repo):
    repo.create("한 번만 처리돼야 함")
    first, second = repo.claim_next(), repo.claim_next()
    assert first is not None and second is None


def test_concurrent_workers_never_double_claim(repo):
    from concurrent.futures import ThreadPoolExecutor

    ids = {repo.create(f"q{i}").id for i in range(30)}

    def drain():
        got = []
        while (job := repo.claim_next()) is not None:
            got.append(job.id)
        return got

    with ThreadPoolExecutor(4) as pool:
        results = [f.result() for f in [pool.submit(drain) for _ in range(4)]]
    claimed = [i for r in results for i in r]
    assert sorted(claimed) == sorted(ids)  # 빠짐없이, 중복 없이


def _make_stale(repo, job_id, seconds=3600):
    """heartbeat 가 오래전에 끊긴 상태(=워커가 죽은 상태)를 만든다."""
    old = datetime.now(UTC) - timedelta(seconds=seconds)
    with repo.sf.begin() as s:
        s.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=old))


def test_dead_workers_job_is_requeued_and_retried(repo):
    job = repo.create("워커가 죽은 작업")
    assert repo.claim_next().attempts == 1
    _make_stale(repo, job.id)

    assert repo.requeue_stale(older_than_s=60, max_attempts=3) == 1
    retried = repo.claim_next()
    assert retried.id == job.id and retried.attempts == 2


def test_slow_job_with_live_heartbeat_is_not_requeued(repo):
    job = repo.create("오래 걸리지만 살아 있는 작업")
    claimed = repo.claim_next()
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    with repo.sf.begin() as s:  # 시작은 1시간 전, heartbeat 는 방금
        s.execute(update(Job).where(Job.id == job.id).values(started_at=long_ago, heartbeat_at=long_ago))
    assert repo.heartbeat(job.id, claimed.attempts) is True

    assert repo.requeue_stale(older_than_s=60) == 0
    assert repo.get(job.id).status == "running"


def test_job_that_keeps_killing_workers_fails_after_max_attempts(repo):
    job = repo.create("워커를 죽이는 작업")
    for _ in range(3):
        assert repo.claim_next() is not None
        _make_stale(repo, job.id)
        repo.requeue_stale(older_than_s=60, max_attempts=3)

    got = repo.get(job.id)
    assert got.status == "failed" and got.attempts == 3 and "3회" in got.error
    assert repo.claim_next() is None  # 더 이상 재시도하지 않음


def test_zombie_worker_cannot_overwrite_retried_job(repo):
    job = repo.create("두 워커가 차례로 잡은 작업")
    zombie = repo.claim_next()
    _make_stale(repo, job.id)
    repo.requeue_stale(older_than_s=60)
    current = repo.claim_next()

    # 죽은 줄 알았던 첫 워커가 뒤늦게 결과를 쓰려 해도 반영되지 않는다
    assert repo.finish(job.id, attempt=zombie.attempts, answer="옛 답", route="rag", result={}) is False
    assert repo.fail(job.id, "옛 에러", attempt=zombie.attempts) is False
    assert repo.heartbeat(job.id, zombie.attempts) is False
    assert repo.get(job.id).status == "running"

    assert repo.finish(job.id, attempt=current.attempts, answer="새 답", route="rag", result={}) is True
    assert repo.get(job.id).answer == "새 답"


def test_worker_sends_heartbeats_while_processing(repo):
    seen = {}

    class Slow:
        def invoke(self, _):
            first = repo.get(job.id).heartbeat_at
            time.sleep(0.3)
            seen["advanced"] = repo.get(job.id).heartbeat_at > first
            return {"answer": "ok", "route": "rag"}

    job = repo.create("느린 작업")
    Worker(repo, Slow(), heartbeat_interval=0.05).process_one()
    assert seen["advanced"] is True
    assert repo.get(job.id).status == "succeeded"


def test_validation_and_404(repo):
    client = TestClient(create_app(repo=repo, embedded_worker=False))
    assert client.post("/v1/questions", json={"question": ""}).status_code == 422
    assert client.get("/v1/jobs/nope").status_code == 404
    assert client.get("/healthz").json() == {"status": "ok"}
