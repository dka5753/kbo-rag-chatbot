FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY data ./data
COPY scripts ./scripts

RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000
# 같은 이미지를 api / worker 두 역할로 쓴다 (docker-compose.yml 참고)
CMD ["uvicorn", "kbo_bot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
