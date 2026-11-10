"""LangGraph 워크플로.

            ┌─(rag)──► retrieve ─► answer_rag ──────────────┐
question ─► route                                            ├─► END
            └─(stats)► write_code ─► execute ─┬─► answer_stats┘
                            ▲                 │ (에러 & 재시도 남음)
                            └─────────────────┘
"""

from __future__ import annotations

import re
import time
from typing import Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .rag import Retriever, format_context
from .sandbox import describe_frames, load_frames, run_code

Route = Literal["rag", "stats"]


class AgentState(TypedDict, total=False):
    question: str
    route: Route
    contexts: list[dict]          # [{source, section, text}]
    code: str
    exec_ok: bool
    exec_output: str
    exec_error: str
    attempts: int
    answer: str
    trace: list[dict]             # [{node, ms, ...}] 관측용


ROUTER_PROMPT = """너는 KBO 야구 챗봇의 질문 분류기다. 질문을 아래 둘 중 하나로 분류하고 단어 하나만 출력하라.
- stats: 선수/팀 기록 수치를 조회, 정렬, 집계, 비교해야 답할 수 있는 질문 (예: "2025 OPS 1위", "LG 팀 홈런 합계")
- rag: 지표의 정의, 규정, 개념 설명처럼 문서를 찾아 답하는 질문 (예: "wOBA가 뭐야", "FIP는 왜 써")
출력: stats 또는 rag"""

CODE_PROMPT = """너는 pandas 전문가다. 아래 DataFrame들이 이미 메모리에 로드되어 있다 (pd, np 사용 가능).
질문에 답하는 파이썬 코드를 ```python 블록 하나로만 작성하라.

규칙:
- 최종 답은 반드시 `result` 변수에 저장 (DataFrame, Series, 숫자, 문자열 가능)
- 파일 읽기/쓰기, 네트워크, pandas/numpy/math/statistics 외 import 금지
- 규정타석 같은 조건이 질문에 없으면 PA >= 300 처럼 합리적 기준을 쓰고 주석으로 남겨라
- 결과는 필요한 컬럼만, 최대 20행

데이터:
{schema}"""

ANSWER_RAG_PROMPT = """너는 KBO 야구 해설 챗봇이다. 아래 <doc> 문서만 근거로 한국어로 답하라.
문서에 없는 내용은 추측하지 말고 "자료에 없는 내용"이라고 말하라. 근거 문서 번호를 [1]처럼 표시하라.

{context}"""

ANSWER_STATS_PROMPT = """너는 KBO 야구 기록 분석 챗봇이다. 아래 코드 실행 결과만 근거로 질문에 한국어로 답하라.
숫자를 지어내지 말고, 코드에서 쓴 기준(예: 최소 타석)이 있으면 함께 밝혀라.

실행한 코드:
```python
{code}
```
실행 결과:
{output}"""

_CODE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    m = _CODE_RE.search(text)
    return (m.group(1) if m else text).strip()


def _text(msg) -> str:
    c = msg.content
    if isinstance(c, list):  # Anthropic 등 블록형 응답
        c = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c).strip()


def build_graph(llm: BaseChatModel, settings: Settings, retriever: Retriever | None = None):
    frames = load_frames(settings.stats_dir)
    schema = describe_frames(frames) if frames else "(데이터 없음)"
    _retriever = retriever

    def get_retriever() -> Retriever:
        nonlocal _retriever
        if _retriever is None:
            _retriever = Retriever(settings)
        return _retriever

    def timed(name):
        def deco(fn):
            def wrapper(state: AgentState) -> AgentState:
                t0 = time.perf_counter()
                update = fn(state)
                entry = {"node": name, "ms": round((time.perf_counter() - t0) * 1000, 1)}
                entry.update(update.pop("_trace", {}))
                update["trace"] = [*state.get("trace", []), entry]
                return update
            return wrapper
        return deco

    @timed("route")
    def route(state: AgentState) -> AgentState:
        out = _text(llm.invoke([SystemMessage(ROUTER_PROMPT), HumanMessage(state["question"])])).lower()
        r: Route = "stats" if "stats" in out else "rag"
        return {"route": r, "attempts": 0, "_trace": {"route": r}}

    @timed("retrieve")
    def retrieve(state: AgentState) -> AgentState:
        docs = get_retriever().search(state["question"])
        ctx = [{"source": d.metadata.get("source"), "section": d.metadata.get("section"),
                "text": d.page_content} for d in docs]
        return {"contexts": ctx, "_trace": {"n_docs": len(ctx)}}

    @timed("answer_rag")
    def answer_rag(state: AgentState) -> AgentState:
        from langchain_core.documents import Document

        docs = [Document(page_content=c["text"], metadata={"source": c["source"]})
                for c in state.get("contexts", [])]
        msgs = [SystemMessage(ANSWER_RAG_PROMPT.format(context=format_context(docs))),
                HumanMessage(state["question"])]
        return {"answer": _text(llm.invoke(msgs))}

    @timed("write_code")
    def write_code(state: AgentState) -> AgentState:
        msgs = [SystemMessage(CODE_PROMPT.format(schema=schema)), HumanMessage(state["question"])]
        if state.get("exec_error"):
            msgs.append(HumanMessage(
                f"이전 코드:\n```python\n{state['code']}\n```\n에러: {state['exec_error']}\n"
                "에러를 고친 코드를 다시 작성하라."))
        code = extract_code(_text(llm.invoke(msgs)))
        return {"code": code, "attempts": state.get("attempts", 0) + 1}

    @timed("execute")
    def execute(state: AgentState) -> AgentState:
        r = run_code(state["code"], settings.stats_dir, settings.code_timeout_s)
        return {"exec_ok": r.ok, "exec_output": r.output, "exec_error": r.error,
                "_trace": {"ok": r.ok, "error": r.error[:200]}}

    def after_execute(state: AgentState) -> str:
        if state["exec_ok"]:
            return "answer_stats"
        if state["attempts"] <= settings.max_code_retries:
            return "write_code"
        return "answer_stats"

    @timed("answer_stats")
    def answer_stats(state: AgentState) -> AgentState:
        if not state["exec_ok"]:
            return {"answer": f"기록을 계산하지 못했습니다 ({state['attempts']}회 시도). "
                              f"마지막 오류: {state['exec_error']}"}
        msgs = [SystemMessage(ANSWER_STATS_PROMPT.format(code=state["code"], output=state["exec_output"])),
                HumanMessage(state["question"])]
        return {"answer": _text(llm.invoke(msgs))}

    g = StateGraph(AgentState)
    for name, fn in [("route", route), ("retrieve", retrieve), ("answer_rag", answer_rag),
                     ("write_code", write_code), ("execute", execute), ("answer_stats", answer_stats)]:
        g.add_node(name, fn)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", lambda s: s["route"], {"rag": "retrieve", "stats": "write_code"})
    g.add_edge("retrieve", "answer_rag")
    g.add_edge("answer_rag", END)
    g.add_edge("write_code", "execute")
    g.add_conditional_edges("execute", after_execute, {"write_code": "write_code", "answer_stats": "answer_stats"})
    g.add_edge("answer_stats", END)
    return g.compile()
