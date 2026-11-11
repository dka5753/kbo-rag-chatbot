"""작업 워커: DB에서 queued 작업을 꺼내 LangGraph 워크플로를 실행하고 결과를 저장한다.

실행: python -m kbo_bot.worker      (여러 개 띄워도 같은 작업을 중복 처리하지 않음)
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from .config import Settings, get_settings
from .db import JobRepo, make_session_factory
from .graph import build_graph
from .llm import get_llm

log = logging.getLogger("kbo_bot.worker")


class Worker:
    def __init__(self, repo: JobRepo, graph, poll_interval: float = 0.5, *,
                 heartbeat_interval: float = 10.0, stale_after_s: float = 60.0,
                 max_attempts: int = 3, reap_interval: float = 30.0):
        self.repo = repo
        self.graph = graph
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.stale_after_s = stale_after_s
        self.max_attempts = max_attempts
        self.reap_interval = reap_interval
        self._stop = threading.Event()

    def _beat(self, job_id: str, attempt: int, done: threading.Event) -> None:
        """작업을 처리하는 동안 '아직 살아 있음'을 DB에 남긴다."""
        while not done.wait(self.heartbeat_interval):
            try:
                if not self.repo.heartbeat(job_id, attempt):
                    log.warning("job %s 의 소유권을 잃었습니다 (다른 워커가 재시도 중)", job_id)
                    return
            except Exception:
                log.exception("job %s heartbeat 실패", job_id)

    def process_one(self) -> bool:
        job = self.repo.claim_next()
        if job is None:
            return False
        log.info("job %s 시작 (시도 %d): %s", job.id, job.attempts, job.question[:60])
        done = threading.Event()
        beat = threading.Thread(target=self._beat, args=(job.id, job.attempts, done), daemon=True)
        beat.start()
        try:
            state = self.graph.invoke({"question": job.question})
            saved = self.repo.finish(
                job.id,
                attempt=job.attempts,
                answer=state.get("answer", ""),
                route=state.get("route"),
                result={
                    "code": state.get("code"),
                    "exec_output": state.get("exec_output"),
                    "contexts": state.get("contexts", []),
                    "trace": state.get("trace", []),
                },
            )
            if saved:
                log.info("job %s 완료 (%s)", job.id, state.get("route"))
            else:
                log.warning("job %s 결과를 버립니다: 이미 다른 시도로 넘어간 작업", job.id)
        except Exception as e:  # 한 작업의 실패가 워커 전체를 죽이지 않게
            log.exception("job %s 실패", job.id)
            self.repo.fail(job.id, f"{type(e).__name__}: {e}", attempt=job.attempts)
        finally:
            done.set()
            beat.join()
        return True

    def reap(self) -> int:
        """heartbeat 가 끊긴 작업을 다시 큐에 넣는다 (상한을 넘긴 작업은 failed 처리)."""
        n = self.repo.requeue_stale(self.stale_after_s, self.max_attempts)
        if n:
            log.warning("멈춰 있던 작업 %d개를 다시 큐에 넣었습니다", n)
        return n

    def run_forever(self) -> None:
        next_reap = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() >= next_reap:  # 시작할 때 한 번, 이후 주기적으로
                    self.reap()
                    next_reap = time.monotonic() + self.reap_interval
                if self.process_one():
                    continue
            except Exception:  # DB 일시 장애 등으로 워커가 죽지 않게
                log.exception("워커 루프 오류, 잠시 후 재시도")
            self._stop.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop.set()


def make_worker(s: Settings | None = None, repo: JobRepo | None = None) -> Worker:
    s = s or get_settings()
    return Worker(
        repo or JobRepo(make_session_factory()), build_graph(get_llm(s), s),
        heartbeat_interval=s.job_heartbeat_s, stale_after_s=s.job_stale_after_s,
        max_attempts=s.job_max_attempts,
    )


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
