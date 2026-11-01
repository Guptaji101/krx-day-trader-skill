---
name: krx-alerts
description: >-
  Price Alerts and the KRX clock: is the market open, opening/closing auction times, holidays and
  the exam-day (수능) late open; quotes with today's price limits; alerts for a stop, a target, a %
  move, nearing the upper or lower limit (상한가/하한가), the opening bell and the flat-by time. Use when
  the user says "is the market open", "장 열렸어?", "alert me", "알림", "quote 005930", "현재가", or after a
  paper fill.
argument-hint: "[clock | quote | add | check | list | reset]"
---

# krx-alerts (Price Alerts)

Rings once when a level hits. Quotes are free and delayed: for alerts, never for timing an order.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote 005930 000660 --write
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan             # stop, target, flat-by, near-limit
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol 247540 --pct 5
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check --write
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| Yahoo chart | the latest price, today's range and yesterday's close |
| `krx/alerts.json` | the rules |
| `krx/state/alerts.json` | what rang, so each rings once |
| `krx/prices.json` | with --write: quotes for krx-risk and krx-broker |

## Steps

1. For a time question, run `clock` and answer from it (Seoul time).
2. After a paper fill, `add --from-plan`. While a position is open, `check --write`, then krx-broker `check`.
3. Report each RING line as printed. A flat-by ring means: close it now (paper: ticket.py flatten).
4. To run it on a schedule, offer a scheduled task every 5 minutes during 09:00-15:20 on trading days.

## 한국어 요약

장 운영 시간(동시호가 포함), 휴장일, 수능일 지연 개장을 알려주고, 현재가와 오늘의 상·하한가를 보여줍니다. 손절가, 목표가, 등락률, 상·하한가 근접, 개장, 청산 시각 알림을 설정할 수 있습니다. 시세는 무료·지연 데이터입니다.

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
