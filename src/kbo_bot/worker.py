"""작업 워커: DB에서 queued 작업을 꺼내 LangGraph 워크플로를 실행하고 결과를 저장한다.

실행: python -m kbo_bot.worker      (여러 개 띄워도 같은 작업을 중복 처리하지 않음)
"""

from __future__ import annotations

import logging
import signal
import threading

from .config import Settings, get_settings
from .db import JobRepo, make_session_factory
from .graph import build_graph
from .llm import get_llm

log = logging.getLogger("kbo_bot.worker")


class Worker:
    def __init__(self, repo: JobRepo, graph, poll_interval: float = 0.5):
        self.repo = repo
        self.graph = graph
        self.poll_interval = poll_interval
        self._stop = threading.Event()

    def process_one(self) -> bool:
        job = self.repo.claim_next()
        if job is None:
            return False
        log.info("job %s 시작: %s", job.id, job.question[:60])
        try:
            state = self.graph.invoke({"question": job.question})
            self.repo.finish(
                job.id,
                answer=state.get("answer", ""),
                route=state.get("route"),
                result={
                    "code": state.get("code"),
                    "exec_output": state.get("exec_output"),
                    "contexts": state.get("contexts", []),
                    "trace": state.get("trace", []),
                },
            )
            log.info("job %s 완료 (%s)", job.id, state.get("route"))
        except Exception as e:  # 한 작업의 실패가 워커 전체를 죽이지 않게
            log.exception("job %s 실패", job.id)
            self.repo.fail(job.id, f"{type(e).__name__}: {e}")
        return True

    def run_forever(self) -> None:
        self.repo.requeue_stale()
        while not self._stop.is_set():
            if not self.process_one():
                self._stop.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop.set()


def make_worker(s: Settings | None = None, repo: JobRepo | None = None) -> Worker:
    s = s or get_settings()
    return Worker(repo or JobRepo(make_session_factory()), build_graph(get_llm(s), s))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    w = make_worker()
    signal.signal(signal.SIGTERM, lambda *_: w.stop())
    signal.signal(signal.SIGINT, lambda *_: w.stop())
    log.info("워커 시작")
    w.run_forever()
    log.info("워커 종료")


if __name__ == "__main__":
    main()
