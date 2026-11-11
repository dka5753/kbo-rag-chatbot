FROM python:3.12-slim

# uv 버전을 고정해 빌드를 재현 가능하게 한다
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 UV_LINK_MODE=copy

# .chroma 를 미리 만들어 두어야 named volume 이 app 소유로 초기화된다
RUN useradd -m app && mkdir -p /app/.chroma && chown -R app /app
USER app
WORKDIR /app

# 의존성만 먼저 설치 → 소스만 바뀌면 이 레이어는 캐시 재사용
COPY --chown=app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY --chown=app data ./data
COPY --chown=app scripts ./scripts

# 패키지가 site-packages 에 설치되므로 데이터 위치는 소스 기준 상대경로가 아니라 명시적으로 지정한다
ENV PATH="/app/.venv/bin:$PATH" \
    DOCS_DIR=/app/data/docs \
    STATS_DIR=/app/data/stats \
    VECTOR_DIR=/app/.chroma

EXPOSE 8000
# 같은 이미지를 api / worker 두 역할로 쓴다 (docker-compose.yml 참고)
CMD ["uvicorn", "kbo_bot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
