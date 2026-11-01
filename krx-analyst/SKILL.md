---
name: krx-analyst
description: >-
  Earnings Analyst for Korean companies: reads quarterly results from DART (revenue 매출액, operating
  income 영업이익, net income 순이익, EPS) for the last quarters, with year-on-year growth and margins,
  and lists recent preliminary results filings (잠정실적). Use when the user says "earnings", "실적",
  "how did 삼성전자 do", "분기 실적", "영업이익", or before trading around results.
argument-hint: "[code or name]"
---

# krx-analyst (Earnings Analyst)

Sums up what changed in the numbers, from the filings themselves. In Korea the preliminary results (잠정실적 공정공시) come weeks before the quarterly report and move the price on results day.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" 005930                    # 6 quarters, consolidated (연결)
python3 "${CLAUDE_SKILL_DIR}/facts.py" 삼성전자 --quarters 8 --lang ko
python3 "${CLAUDE_SKILL_DIR}/facts.py" 000660 --separate         # 별도 (parent only)
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| OpenDART `fnlttSinglAcntAll.json` | full statements per report (Q1, half, Q3, annual); needs "dart_api_key" |
| OpenDART `list.json` | preliminary results filings from the last 120 days |
| `krx/metrics.json` | with --write: the latest quarter per company |

## Steps

1. Run it. Show the table (amounts in 억원) and the change lines.
2. Point out a turn to profit or loss (흑자/적자 전환) and margin changes. Link the preliminary filing if any.
3. Banks, insurers and holding companies use other layouts: a '-' means the line is not there, not zero.
4. No price targets, no valuation calls. Numbers as filed; point to the filing.

## 한국어 요약

DART 재무제표로 최근 분기의 매출액, 영업이익, 순이익, EPS와 전년 동기 대비 증감, 이익률을 보여줍니다. 4분기는 연간에서 1~3분기를 빼서 계산합니다. 최근 120일의 잠정실적 공시도 함께 보여줍니다. 무료 OpenDART 인증키가 필요합니다.

## Language / 언어

Answer in the user's language. If the user writes in Korean, or `krx/config.json`
has `"lang": "ko"`, reply in Korean and pass `--lang ko` to the tool where it has
one (the tools print English otherwise; translate the rest faithfully, numbers
unchanged). Company names stay as the tool prints them (삼성전자 005930).

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- PAPER ONLY. This pack never connects to a broker and cannot place a real order. `krx-broker`
  keeps a practice account in `krx/portfolio.json`. Never offer to place a real order, never ask
  for brokerage logins, keys or account numbers, and never suggest a workaround to trade live.
- A paper order needs both: krx-risk passed that exact order today, and the user typed an explicit
  yes ("yes" / "네" / "예") for that one order in the chat.
- No short selling, no margin (미수), no options or leverage.
- Korean market rules the tools enforce: the KRX tick grid, the +-30 % daily price limits,
  continuous trading 09:00-15:20 (closing auction 15:20-15:30), the 0.20 % sell tax (2026) plus
  commission, and the KRX holiday calendar.
- Free data (Yahoo Finance, Google News, DART) can be delayed or wrong. Check anything that matters.
- Most day traders lose money, and costs in Korea are high relative to a tight stop. Say so when
  the numbers show it.
