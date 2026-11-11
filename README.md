# ⚾ KBO RAG Chatbot

KBO 야구 질문에 답하는 **LLM 에이전트 서비스**입니다.
"wOBA가 뭐야?" 같은 개념 질문은 **문서 검색(RAG)** 으로, "2025 시즌 OPS 상위 5명" 같은 기록 질문은 **LLM이 pandas 코드를 직접 작성하고 실행(Code Interpreter)** 해서 답합니다.
에이전트 워크플로는 **LangGraph**, 서비스는 **FastAPI + 비동기 워커 + Postgres** 구조로 만들었습니다.

![CI](https://github.com/dka5753/kbo-rag-chatbot/actions/workflows/ci.yml/badge.svg)

## 핵심 기능

- **질문 라우팅**: LLM이 질문을 `rag` / `stats` 로 분류해 다른 경로로 처리
- **RAG**: 마크다운 문서를 헤더 단위로 청킹하고, 섹션 경로(`타자 지표 > OPS`)를 청크에 붙여 검색 맥락 보존. Chroma 벡터 DB 사용
- **Code Interpreter**: LLM이 만든 코드를 AST로 검사한 뒤 격리된 서브프로세스에서 타임아웃을 걸고 실행. **에러가 나면 에러 메시지를 LLM에 돌려줘 스스로 고치게 함** (최대 N회)
- **비동기 작업 API**: 질문을 받으면 즉시 `202 + job_id` 반환 → 워커가 처리 → 클라이언트가 조회
- **수평 확장 워커**: 여러 워커가 같은 DB를 바라봐도 조건부 UPDATE로 작업을 한 번만 선점
- **워커 장애 복구**: 워커가 처리 중 heartbeat를 남기고, heartbeat가 끊긴 작업만 다시 큐에 넣음. 작업당 시도 횟수에 상한을 두고, 뒤늦게 돌아온 워커가 재시도 중인 작업의 결과를 덮어쓰지 못하게 함
- **모델 교체 자유**: `.env` 한 줄로 **로컬 Llama(Ollama, 온프레미스)** ↔ OpenAI ↔ Anthropic 전환
- **관측성**: 노드별 실행 시간·라우팅 결과·코드 실행 성공 여부를 `trace`로 저장

## 아키텍처

```mermaid
flowchart LR
    C[Client] -- POST /v1/questions --> API[FastAPI]
    API -- INSERT queued --> DB[(Postgres / SQLite)]
    C -- GET /v1/jobs/id --> API
    W1[Worker 1..N] -- claim & update --> DB
    W1 --> G

    subgraph G[LangGraph 워크플로]
      R{route} -- rag --> RET[retrieve<br/>Chroma] --> AR[answer_rag]
      R -- stats --> WC[write_code] --> EX[execute<br/>sandbox]
      EX -- error & retry --> WC
      EX -- ok --> AS[answer_stats]
    end

    G -.-> LLM[(Ollama Llama 3 /<br/>OpenAI / Anthropic)]
```

## 기술 선택 이유

| 결정 | 이유 |
|---|---|
| 기록 질문은 RAG 대신 **코드 실행** | 숫자 데이터를 텍스트로 임베딩하면 정렬·집계·조건 필터를 정확히 못 합니다. 표 데이터는 계산하고, 개념은 검색하는 게 맞다고 판단했습니다. |
| **에러 피드백 재시도 루프** | 소형 로컬 모델은 컬럼 이름 오타 같은 실수가 잦습니다. 실행 에러를 그대로 돌려주면 모델이 원인을 보고 스스로 고칠 기회를 얻습니다 (`tests/test_graph.py`에 이 흐름을 테스트로 고정). |
| **API와 워커 분리** (job 큐) | LLM 호출은 수 초~수십 초가 걸려서 HTTP 요청 안에서 처리하면 타임아웃과 커넥션 고갈이 생깁니다. 워커를 늘리는 것만으로 처리량을 키울 수 있습니다. |
| 큐로 Redis 대신 **DB 테이블** | 작업 상태·결과를 어차피 DB에 저장해야 하므로 인프라 구성요소를 하나 줄였습니다. 조건부 `UPDATE ... WHERE status='queued'` 로 중복 처리를 막고, 동시 워커 테스트를 SQLite와 Postgres 양쪽 CI에서 돌립니다. 처리량이 커지면 Celery/Redis나 `SKIP LOCKED`로 교체할 수 있게 `JobRepo`로 분리했습니다. |
| **heartbeat 기반 재큐잉** + 시도 횟수 상한 | "시작한 지 N분 지났으면 죽은 것"으로 판단하면 오래 걸리는 정상 작업까지 중복 실행됩니다. 살아 있는 워커는 heartbeat를 갱신하므로 실제로 멈춘 작업만 되살립니다. 워커를 매번 죽이는 작업이 큐를 영원히 도는 것을 막으려고 `JOB_MAX_ATTEMPTS`회 이후에는 `failed`로 끝냅니다. |
| 결과 저장 시 **시도 번호 확인** | 재큐잉된 뒤 원래 워커가 살아 돌아오면 두 워커가 같은 작업의 결과를 쓰게 됩니다. `finish`/`fail`은 선점 당시의 `attempts` 값이 일치할 때만 반영해서 늦게 온 결과를 버립니다. |
| 의존성 관리 **uv + `uv.lock`** | 로컬·CI·Docker가 같은 버전을 설치하도록 lock 파일을 커밋하고, CI는 `uv sync --locked`로 lock과 `pyproject.toml`이 어긋나면 실패하게 했습니다. |
| **LLM 프로바이더 추상화** | 온프레미스 환경(데이터 외부 반출 불가)에서는 로컬 Llama를, 품질이 중요한 데모에서는 API 모델을 쓸 수 있도록 했습니다. |
| **가짜 LLM으로 테스트** | 응답 순서를 정한 가짜 LLM을 넣으면 라우팅·재시도·실패 처리 같은 워크플로 분기를 외부 서비스 없이 결정적으로 검증할 수 있습니다. CI에서 모델 없이 돌아갑니다. |

## 실행 방법

### 1) 로컬 (Ollama, [uv](https://docs.astral.sh/uv/) 필요)

```bash
# Ollama 설치 후
ollama pull llama3.1:8b && ollama pull bge-m3

uv sync                                       # .venv 생성 + uv.lock 기준으로 설치
cp .env.example .env

uv run python -m kbo_bot.rag                  # 문서 인덱스 구축
EMBEDDED_WORKER=1 uv run uvicorn kbo_bot.api:create_app --factory --reload
```

### 2) Docker Compose (API + 워커 + Postgres + Ollama)

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec ollama ollama pull bge-m3
docker compose run --rm indexer
docker compose up -d --scale worker=3
```

### 3) 질문하기

```bash
curl -X POST localhost:8000/v1/questions \
  -H 'content-type: application/json' \
  -d '{"question": "2025 시즌 OPS 상위 5명 알려줘"}'
# → {"job_id": "…", "status": "queued"}

curl localhost:8000/v1/jobs/<job_id>
# → status, route, answer, result.code, result.exec_output, result.trace, latency_ms
```

API 문서: http://localhost:8000/docs

## 테스트

```bash
uv run pytest -q                            # 전체 (가짜 LLM/임베딩, 외부 의존 없음)
TEST_DATABASE_URL=postgresql+psycopg://kbo:kbo@localhost:5432/kbo uv run pytest tests/test_api_worker.py
```

| 테스트 | 검증 내용 |
|---|---|
| `test_sandbox.py` | 파생 지표(OPS 등) 계산 정확성, 위험 코드 차단, 타임아웃, 에러 전달 |
| `test_graph.py` | RAG 경로, 코드 에러 후 자기 수정, 최대 재시도 후 포기, 청크 섹션 경로 |
| `test_api_worker.py` | API→워커→결과 조회 end-to-end, 실패 처리, 동시 워커 4개 중복 선점 없음, 죽은 워커의 작업 재큐잉, 살아 있는 느린 작업 보호, 시도 횟수 상한, 늦게 온 결과 차단 |

## 데이터

- `data/docs/` : RAG 문서 (세이버메트릭스 지표 설명, 직접 작성)
- `data/stats/batting_sample.csv` : **개발용 가상 데이터** (선수·기록 모두 가짜). 실제 데이터는 같은 스키마로 넣으면 됩니다 → [`data/stats/SCHEMA.md`](data/stats/SCHEMA.md)

## 한계와 다음 단계

- [ ] 실제 KBO 시즌 기록 수집 파이프라인 (수집 → 정제 → 적재 자동화)
- [ ] 투수 기록 테이블과 팀 기록 추가
- [ ] 코드 실행을 컨테이너 단위 샌드박스로 격리 (현재는 AST 검사 + 프로세스 격리 수준)
- [ ] 질문-정답 평가셋으로 라우팅 정확도·답변 정확도 측정 (`eval/`)
- [ ] 스트리밍 응답(SSE), 대화 맥락 유지
- [ ] 클라우드 배포 (Kubernetes 매니페스트)
- [ ] DB 스키마 마이그레이션 도구(Alembic) 도입. 현재는 `create_all`이라 컬럼이 추가되면 기존 DB를 새로 만들어야 함

## 프로젝트 구조

```
src/kbo_bot/
  config.py    # 환경변수 설정
  llm.py       # LLM·임베딩 프로바이더 팩토리
  rag.py       # 청킹, 인덱싱, 검색
  sandbox.py   # 코드 검사·격리 실행
  graph.py     # LangGraph 워크플로
  db.py        # 작업 테이블, 선점 로직
  worker.py    # 작업 워커
  api.py       # FastAPI
tests/         # pytest (가짜 LLM)
.github/workflows/ci.yml   # lint + test(SQLite, Postgres) + docker build
```
