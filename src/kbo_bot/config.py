"""환경변수 기반 설정. `.env` 하나로 로컬(Ollama) ↔ API 모델을 전환한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    # LLM: ollama | openai | anthropic
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "ollama"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "llama3.1:8b"))
    # 임베딩: ollama | openai | fake(테스트용)
    embed_provider: str = field(default_factory=lambda: os.getenv("EMBED_PROVIDER", "ollama"))
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "bge-m3"))
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )

    docs_dir: Path = field(default_factory=lambda: Path(os.getenv("DOCS_DIR", ROOT / "data" / "docs")))
    stats_dir: Path = field(default_factory=lambda: Path(os.getenv("STATS_DIR", ROOT / "data" / "stats")))
    vector_dir: Path = field(
        default_factory=lambda: Path(os.getenv("VECTOR_DIR", ROOT / ".chroma"))
    )
    collection: str = "kbo_docs"

    top_k: int = field(default_factory=lambda: int(os.getenv("TOP_K", "4")))
    code_timeout_s: int = field(default_factory=lambda: int(os.getenv("CODE_TIMEOUT_S", "10")))
    max_code_retries: int = field(default_factory=lambda: int(os.getenv("MAX_CODE_RETRIES", "2")))


def get_settings() -> Settings:
    return Settings()
