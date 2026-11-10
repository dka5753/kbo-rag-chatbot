from conftest import fake_llm
from fastapi.testclient import TestClient

from kbo_bot.api import create_app
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


def test_validation_and_404(repo):
    client = TestClient(create_app(repo=repo, embedded_worker=False))
    assert client.post("/v1/questions", json={"question": ""}).status_code == 422
    assert client.get("/v1/jobs/nope").status_code == 404
    assert client.get("/healthz").json() == {"status": "ok"}
