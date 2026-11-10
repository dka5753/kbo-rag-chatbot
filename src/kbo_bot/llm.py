"""LLM·임베딩 팩토리. 그래프 코드는 프로바이더를 모른 채 BaseChatModel만 쓴다."""

from __future__ import annotations

from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from langchain_core.language_models import BaseChatModel

from .config import Settings


def get_llm(s: Settings, temperature: float = 0.0) -> BaseChatModel:
    p = s.llm_provider.lower()
    if p == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=s.llm_model, base_url=s.ollama_base_url, temperature=temperature)
    if p == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=s.llm_model, temperature=temperature)
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=s.llm_model, temperature=temperature)
    raise ValueError(f"지원하지 않는 LLM_PROVIDER: {s.llm_provider}")


def get_embeddings(s: Settings) -> Embeddings:
    p = s.embed_provider.lower()
    if p == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=s.embed_model, base_url=s.ollama_base_url)
    if p == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=s.embed_model)
    if p == "fake":
        return DeterministicFakeEmbedding(size=256)
    raise ValueError(f"지원하지 않는 EMBED_PROVIDER: {s.embed_provider}")
