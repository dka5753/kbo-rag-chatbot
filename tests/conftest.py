from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy import delete

from kbo_bot.config import Settings
from kbo_bot.db import Job, JobRepo, make_session_factory
from kbo_bot.rag import build_index

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        llm_provider="fake", embed_provider="fake",
        docs_dir=ROOT / "data" / "docs", stats_dir=ROOT / "data" / "stats",
        vector_dir=tmp_path / "chroma", code_timeout_s=5, max_code_retries=2,
    )


@pytest.fixture
def indexed_settings(settings) -> Settings:
    build_index(settings)
    return settings


@pytest.fixture
def repo(tmp_path) -> JobRepo:
    # CI에서는 TEST_DATABASE_URL로 실제 Postgres를 붙여 같은 테스트를 돌린다.
    url = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'jobs.db'}"
    sf = make_session_factory(url)
    with sf.begin() as s:
        s.execute(delete(Job))
    return JobRepo(sf)


def fake_llm(*responses: str) -> FakeListChatModel:
    """정해진 순서대로 응답하는 가짜 LLM. 그래프 흐름을 결정적으로 테스트하기 위함."""
    return FakeListChatModel(responses=list(responses))
