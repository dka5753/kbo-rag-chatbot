"""개발·테스트용 가상 타격 데이터 생성기.

선수 이름과 기록은 모두 가짜입니다. 실제 데이터는 같은 컬럼 구조의 CSV를
data/stats/ 에 넣으면 됩니다 (컬럼 설명은 data/stats/SCHEMA.md).
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

TEAMS = ["LG", "KT", "SSG", "NC", "두산", "KIA", "롯데", "삼성", "한화", "키움"]
SURNAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
GIVEN = ["민준", "서준", "도윤", "예준", "시우", "하준", "지호", "준서", "건우", "현우",
         "우진", "선우", "연우", "유준", "정우", "승우", "승현", "시윤", "준혁", "은우"]


def main(out: Path, seed: int = 42) -> None:
    rng = random.Random(seed)
    names = set()
    while len(names) < 60:
        names.add(rng.choice(SURNAMES) + rng.choice(GIVEN))
    rows = []
    for season in (2024, 2025):
        for i, name in enumerate(sorted(names)):
            pa = rng.randint(250, 620)
            bb = int(pa * rng.uniform(0.05, 0.14))
            hbp = rng.randint(0, 12)
            sf = rng.randint(0, 8)
            ab = pa - bb - hbp - sf
            h = int(ab * rng.uniform(0.22, 0.34))
            hr = int(h * rng.uniform(0.02, 0.22))
            d3 = rng.randint(0, 5)
            d2 = int((h - hr - d3) * rng.uniform(0.15, 0.30))
            rows.append({
                "season": season, "player": name, "team": TEAMS[i % len(TEAMS)],
                "G": min(144, pa // 4 + rng.randint(0, 10)), "PA": pa, "AB": ab, "H": h,
                "2B": d2, "3B": d3, "HR": hr, "BB": bb, "HBP": hbp, "SF": sf,
                "SO": int(pa * rng.uniform(0.10, 0.28)), "SB": rng.randint(0, 35),
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    print(f"{len(rows)}행 → {out}")


if __name__ == "__main__":
    main(Path(__file__).resolve().parents[1] / "data" / "stats" / "batting_sample.csv")
