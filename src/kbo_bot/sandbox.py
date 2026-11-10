"""통계 질문용 코드 실행기 (Code Interpreter).

LLM이 만든 pandas 코드를
1) AST 검사로 위험한 구문을 먼저 걸러내고
2) 별도 파이썬 프로세스(-I 격리 모드)에서 타임아웃을 걸고 실행한다.

주의: 이것은 실수 방지용 가드이지 완전한 보안 샌드박스가 아니다.
외부 사용자에게 공개 배포할 때는 컨테이너(Docker, 네트워크 차단) 안에서 돌릴 것.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics"}
BLOCKED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint", "exit", "quit",
}
BLOCKED_ATTRS = {"to_csv", "to_excel", "to_pickle", "to_parquet", "to_sql", "read_pickle", "system"}

# 파생 지표 계산. 프롬프트용 스키마와 서브프로세스 실행에서 같은 정의를 쓰도록 문자열로 공유한다.
DERIVE_SRC = '''
def derive(df):
    cols = set(df.columns)
    if {"H", "AB"} <= cols:
        ab = df["AB"].where(df["AB"] > 0)
        df["AVG"] = (df["H"] / ab).round(3)
        if {"2B", "3B", "HR"} <= cols:
            tb = df["H"] + df["2B"] + 2 * df["3B"] + 3 * df["HR"]
            df["SLG"] = (tb / ab).round(3)
            df["ISO"] = (df["SLG"] - df["AVG"]).round(3)
        if {"BB", "HBP", "SF"} <= cols:
            denom = (df["AB"] + df["BB"] + df["HBP"] + df["SF"]).where(lambda x: x > 0)
            df["OBP"] = ((df["H"] + df["BB"] + df["HBP"]) / denom).round(3)
        if {"OBP", "SLG"} <= set(df.columns):
            df["OPS"] = (df["OBP"] + df["SLG"]).round(3)
        if {"HR", "SO", "SF"} <= cols:
            bip = (df["AB"] - df["SO"] - df["HR"] + df["SF"]).where(lambda x: x > 0)
            df["BABIP"] = ((df["H"] - df["HR"]) / bip).round(3)
    return df
'''
_ns: dict = {}
exec(DERIVE_SRC, _ns)  # noqa: S102 - 신뢰된 내부 코드
derive = _ns["derive"]

_RUNNER = DERIVE_SRC + r'''
import json, sys, pathlib
import pandas as pd
import numpy as np
pd.set_option("display.width", 200)
stats_dir = pathlib.Path(sys.argv[1])
frames = {p.stem: derive(pd.read_csv(p)) for p in sorted(stats_dir.glob("*.csv"))}
user_code = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
env = {"pd": pd, "np": np, **frames}
try:
    exec(user_code, env)
    if "result" not in env:
        raise NameError("코드가 `result` 변수에 최종 답을 저장해야 합니다.")
    r = env["result"]
    if isinstance(r, (pd.DataFrame, pd.Series)):
        out = r.head(30).to_string()
    else:
        out = repr(r)
    print(json.dumps({"ok": True, "output": out}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
'''


@dataclass
class ExecResult:
    ok: bool
    output: str = ""
    error: str = ""


def load_frames(stats_dir: Path) -> dict[str, pd.DataFrame]:
    return {p.stem: derive(pd.read_csv(p)) for p in sorted(stats_dir.glob("*.csv"))}


def describe_frames(frames: dict[str, pd.DataFrame], sample_rows: int = 3) -> str:
    """LLM 프롬프트에 넣을 데이터 설명."""
    parts = []
    for name, df in frames.items():
        cols = ", ".join(f"{c}({df[c].dtype})" for c in df.columns)
        parts.append(
            f"DataFrame `{name}`: {len(df)}행\n컬럼: {cols}\n예시:\n{df.head(sample_rows).to_string()}"
        )
    return "\n\n".join(parts)


def validate(code: str) -> str | None:
    """문제가 있으면 에러 메시지, 없으면 None."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            bad = [m for m in mods if m.split(".")[0] not in ALLOWED_IMPORTS]
            if bad:
                return f"허용되지 않은 import: {bad}. 사용 가능: {sorted(ALLOWED_IMPORTS)}"
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return f"사용할 수 없는 이름: {node.id}"
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr in BLOCKED_ATTRS):
            return f"사용할 수 없는 속성: {node.attr}"
    return None


def run_code(code: str, stats_dir: Path, timeout_s: int = 10) -> ExecResult:
    if err := validate(code):
        return ExecResult(ok=False, error=err)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        runner = tmp_path / "runner.py"
        user = tmp_path / "user_code.py"
        runner.write_text(_RUNNER, encoding="utf-8")
        user.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(runner), str(stats_dir.resolve()), str(user)],
                capture_output=True, text=True, timeout=timeout_s, cwd=tmp, check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(ok=False, error=f"시간 초과 ({timeout_s}초)")
    line = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return ExecResult(ok=False, error=(proc.stderr or proc.stdout)[-1500:])
    return ExecResult(ok=data["ok"], output=data.get("output", ""), error=data.get("error", ""))
