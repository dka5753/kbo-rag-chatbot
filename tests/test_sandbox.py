from kbo_bot.sandbox import load_frames, run_code, validate


def test_derived_columns(settings):
    df = load_frames(settings.stats_dir)["batting_sample"]
    assert {"AVG", "OBP", "SLG", "OPS", "ISO", "BABIP"} <= set(df.columns)
    row = df.iloc[0]
    tb = row.H + row["2B"] + 2 * row["3B"] + 3 * row.HR
    assert abs(row.SLG - round(tb / row.AB, 3)) < 1e-9
    assert abs(row.OPS - round(row.OBP + row.SLG, 3)) < 1e-9


def test_run_returns_result(settings):
    r = run_code("result = int(batting_sample['HR'].sum())", settings.stats_dir)
    assert r.ok and r.output.isdigit()


def test_missing_result_variable(settings):
    r = run_code("x = 1", settings.stats_dir)
    assert not r.ok and "result" in r.error


def test_runtime_error_is_reported(settings):
    r = run_code("result = batting_sample['없는컬럼']", settings.stats_dir)
    assert not r.ok and "KeyError" in r.error


def test_timeout(settings):
    r = run_code("while True:\n    pass", settings.stats_dir, timeout_s=1)
    assert not r.ok and "시간 초과" in r.error


def test_validate_blocks_dangerous_code():
    assert validate("import os") is not None
    assert validate("from subprocess import run") is not None
    assert validate("open('/etc/passwd')") is not None
    assert validate("().__class__.__bases__") is not None
    assert validate("batting_sample.to_csv('x.csv')") is not None
    assert validate("import pandas as pd\nresult = 1") is None
