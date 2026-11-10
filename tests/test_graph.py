from conftest import fake_llm

from kbo_bot.graph import build_graph, extract_code
from kbo_bot.rag import load_and_split


def test_chunks_keep_section_breadcrumb(settings):
    chunks = load_and_split(settings.docs_dir)
    assert chunks
    assert all(c.metadata["source"] != "README.md" for c in chunks)
    ops = [c for c in chunks if c.metadata["section"].endswith("OPS")]
    assert ops and ops[0].page_content.startswith("[세이버메트릭스 지표 가이드 > 타자 지표 > OPS]")


def test_extract_code():
    assert extract_code("설명\n```python\nresult = 1\n```\n끝") == "result = 1"
    assert extract_code("result = 2") == "result = 2"


def test_rag_path(indexed_settings):
    llm = fake_llm("rag", "OPS는 출루율과 장타율의 합입니다 [1]")
    out = build_graph(llm, indexed_settings).invoke({"question": "OPS가 뭐야?"})
    assert out["route"] == "rag"
    assert len(out["contexts"]) == indexed_settings.top_k
    assert out["answer"].startswith("OPS는")
    assert [t["node"] for t in out["trace"]] == ["route", "retrieve", "answer_rag"]


def test_stats_path_self_corrects_after_error(settings):
    llm = fake_llm(
        "stats",
        "```python\nresult = batting_sample['ops'].max()\n```",  # 1차: 컬럼명 오류
        "```python\nresult = batting_sample.nlargest(3, 'OPS')[['player', 'OPS']]\n```",
        "OPS 상위 3명은 ... 입니다.",
    )
    out = build_graph(llm, settings).invoke({"question": "OPS 상위 3명"})
    assert out["route"] == "stats"
    assert out["attempts"] == 2
    assert out["exec_ok"] and "OPS" in out["exec_output"]
    nodes = [t["node"] for t in out["trace"]]
    assert nodes == ["route", "write_code", "execute", "write_code", "execute", "answer_stats"]


def test_stats_path_gives_up_after_max_retries(settings):
    bad = "```python\nimport os\nresult = 1\n```"
    llm = fake_llm("stats", bad, bad, bad)
    out = build_graph(llm, settings).invoke({"question": "아무거나"})
    assert not out["exec_ok"]
    assert out["attempts"] == settings.max_code_retries + 1
    assert "계산하지 못했습니다" in out["answer"]
