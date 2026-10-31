---
name: krx-scanner
description: >-
  Chart Scanner for the Korean market (KOSPI, KOSDAQ). Builds the list of listed stocks from KRX
  KIND, scans daily closes for setups (20-day highs, bull flags, 20-day average reclaims, oversold
  bounces, stocks that closed at the upper limit 상한가) and scans live opening-range breakouts after
  09:15 Seoul time. Writes krx/watchlist.json. Use when the user says "scan the market", "find
  setups", "what's breaking out", "종목 검색", "스캔", "돌파 종목", "상한가 종목", or asks for a Korean stock
  watchlist.
argument-hint: "[universe | daily | orb]"
---

# krx-scanner (Chart Scanner)

Finds charts that match a rule, across about 2,500 KOSPI and KOSDAQ stocks. A scan is a list of charts that match a rule, not a list of stocks to buy.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                  # KIND lists -> krx/universe.json (do this first)
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                     # before 09:00: daily setups
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --market KOSDAQ --limit 300
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols 005930,000660,247540
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                       # after 09:20: opening-range breakouts
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 09:45 --or-minutes 15
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| KRX KIND | the listed-company lists, once for KOSPI and once for KOSDAQ (SPACs left out) |
| Yahoo spark / chart | daily closes and 5-minute bars for 005930.KS / 247540.KQ, 20 codes a request |
| `krx/universe.json` | code -> name, market, sector; lets every tool take 삼성전자 as well as 005930 |
| `krx/watchlist.json` | the hits, most traded (won value) first |

## Steps

1. If `krx/universe.json` does not exist, run `universe` first (two downloads).
2. Before 09:00 run `daily`; after 09:20 (the range is 09:00-09:15) run `orb`.
3. Show the hits as printed: code, name, price, the setups. Say how many were scanned and filtered.
4. A `limit_up` stock closed near +30 %: say plainly that these swing hard the next day.
5. Suggest the next step for ONE chart: /krx-news CODE, then /krx-entry CODE.

## 한국어 요약

코스피·코스닥 약 2,500개 종목에서 조건에 맞는 차트를 찾습니다. 먼저 `universe`로 종목 목록을 만들고, 장 시작 전에는 `daily`(20일 신고가, 깃발형, 20일선 회복, 과매도 반등, 상한가 마감), 09:20 이후에는 `orb`(시가 범위 돌파)를 실행합니다. 스캔 결과는 조건에 맞는 차트 목록일 뿐, 매수 추천이 아닙니다.

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
