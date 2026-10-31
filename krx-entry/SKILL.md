---
name: krx-entry
description: >-
  Entry Timer for Korean stocks: watches an opening-range breakout (or a close above a level) bar
  by bar in Seoul time and says the moment to get in: waiting, triggered, or expired. Warns near
  the upper limit (상한가) and around KRX volatility interruptions (VI). Writes krx/entry.json. Use
  when the user says "when do I get in", "is it triggered", "watch 005930", "진입 타이밍", "돌파했어?", or
  after /krx-scanner.
argument-hint: "[code or name]"
---

# krx-entry (Entry Timer)

Watches one setup on 5-minute bars. The opening range is 09:00-09:15 by default; a trigger is the first COMPLETED bar after it that CLOSES above the range high. A wick is not a trigger.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check 005930 --write          # waiting / triggered / expired
python3 "${CLAUDE_SKILL_DIR}/entry.py" check 삼성전자 --setup level --level 99500
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch 005930 --every 30 --write   # one line per closed bar
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                     # every hit in krx/watchlist.json
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| Yahoo chart | 5 days of 5-minute (or 1-minute) bars for the code |
| `krx/config.json` | no_entries_before (09:15) and no_entries_after (15:00) |
| `krx/entry.json` | with --write: the trigger time, price, bar and opening range, for krx-exit |

## Steps

1. Run `check` (or `watch` while the user waits). Report the status line exactly as printed.
2. On TRIGGERED: give the time and price, and if it was more than one bar ago, say a late entry is a different trade.
3. Repeat any 'Careful' line (upper limit, VI). Never encourage chasing a stock near 상한가.
4. Next: /krx-exit sets the stop and target BEFORE any buy.

## 한국어 요약

시가 범위(기본 09:00-09:15) 돌파를 5분봉으로 지켜보고 진입 시점을 알려줍니다. 봉이 마감된 뒤 범위 고가 위에서 종가가 형성되어야 신호입니다. 상한가 근처나 VI(변동성 완화장치) 발동 가능 구간이면 경고합니다.

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
