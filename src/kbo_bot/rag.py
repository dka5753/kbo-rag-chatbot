"""문서 RAG: data/docs/*.md → 헤더 기준 청킹 → Chroma 적재 → 검색.

실행: python -m kbo_bot.rag            (인덱스 재구축)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from .config import Settings, get_settings
from .llm import get_embeddings

_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def load_and_split(docs_dir: Path, chunk_size: int = 600, overlap: int = 80) -> list[Document]:
    """마크다운 문서를 섹션 단위로 자르고, 긴 섹션은 다시 글자 수 기준으로 자른다.

    각 청크 앞에 섹션 경로(예: "타자 지표 > OPS")를 붙여 검색 시 맥락이 살아 있게 한다.
    """
    header_splitter = MarkdownHeaderTextSplitter(_HEADERS, strip_headers=False)
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=overlap, separators=["\n\n", "\n", ". ", " "]
    )
    chunks: list[Document] = []
    for path in sorted(docs_dir.glob("**/*.md")):
        if path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8")
        for section in header_splitter.split_text(text):
            crumb = " > ".join(section.metadata[k] for k in ("h1", "h2", "h3") if k in section.metadata)
            for i, piece in enumerate(char_splitter.split_text(section.page_content)):
                chunks.append(
                    Document(
                        page_content=f"[{crumb}]\n{piece}" if crumb else piece,
                        metadata={"source": path.name, "section": crumb, "part": i},
                    )
                )
    return chunks


def build_index(s: Settings | None = None, reset: bool = True) -> int:
    s = s or get_settings()
    if reset and s.vector_dir.exists():
        shutil.rmtree(s.vector_dir)
    chunks = load_and_split(s.docs_dir)
    if not chunks:
        raise FileNotFoundError(f"{s.docs_dir} 에 .md 문서가 없습니다.")
    Chroma.from_documents(
        chunks,
        embedding=get_embeddings(s),
        collection_name=s.collection,
        persist_directory=str(s.vector_dir),
    )
    return len(chunks)


class Retriever:
    def __init__(self, s: Settings | None = None):
        self.s = s or get_settings()
        self.store = Chroma(
            collection_name=self.s.collection,
            embedding_function=get_embeddings(self.s),
            persist_directory=str(self.s.vector_dir),
        )

    def search(self, query: str, k: int | None = None) -> list[Document]:
        return self.store.similarity_search(query, k=k or self.s.top_k)


def format_context(docs: list[Document]) -> str:
    return "\n\n".join(f"<doc id={i} source={d.metadata.get('source')}>\n{d.page_content}\n</doc>"
                       for i, d in enumerate(docs, 1))


if __name__ == "__main__":
    n = build_index()
    print(f"인덱스 구축 완료: {n}개 청크")
