# 통계 데이터 스키마

`data/stats/*.csv` 파일은 코드 실행 툴이 DataFrame으로 불러옵니다. 파일 이름이 변수 이름이 됩니다 (`batting_sample.csv` → `batting_sample`).

> `batting_sample.csv`는 `scripts/make_sample_data.py`로 만든 **가상 데이터**입니다. 선수와 기록 모두 실제와 무관합니다.

## 타격 (batting*.csv)

| 컬럼 | 의미 |
|---|---|
| season | 시즌 연도 |
| player | 선수 이름 |
| team | 소속 구단 |
| G | 경기 수 |
| PA | 타석 |
| AB | 타수 |
| H | 안타 |
| 2B, 3B, HR | 2루타, 3루타, 홈런 |
| BB, HBP | 볼넷, 몸에 맞는 공 |
| SF | 희생플라이 |
| SO | 삼진 |
| SB | 도루 |

AVG, OBP, SLG, OPS, ISO, BABIP는 불러올 때 자동으로 계산해서 컬럼으로 붙습니다 (`kbo_bot/sandbox.py`).
