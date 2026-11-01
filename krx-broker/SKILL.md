---
name: krx-broker
description: >-
  Paper Order Executor for Korean stocks (모의투자). PAPER ONLY: never connects to a broker. Shows each
  order as a plain ticket, takes the user's explicit yes ("yes" / "네") for one order at a time,
  fills it in a practice account with Korean commission and sell tax, watches the stop, target and
  flat-by time, and keeps a trade log with net P&L. Use when the user says "place it", "buy it",
  "paper trade", "모의투자", "매수해", "close my position", "청산", "how am I doing", or after /krx-risk
  passes.
argument-hint: "[show | approve | check | flatten | portfolio | log]"
---

# krx-broker (Paper Order Executor)

A practice account in won. It cannot send a real order; that is deliberate. Paper fills are kinder than real ones, and the tool says so.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                       # the tickets, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "<the user's words>"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" check                      # stop / target / flat-by vs krx/prices.json
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten
python3 "${CLAUDE_SKILL_DIR}/ticket.py" portfolio
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
python3 "${CLAUDE_SKILL_DIR}/ticket.py" reset --cash 5000000
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| `krx/orders.json`, `krx/state/risk.json` | the order and today's PASS for its fingerprint |
| `krx/portfolio.json` | paper cash and positions |
| `krx/state/broker.json` | every yes, decline, fill and closed trade with fees, tax and net P&L |
| `krx/prices.json` | latest prices for `check` (krx-alerts: alerts.py quote --holdings --write) |

## Steps

1. Run `show` and present the ticket in plain words with the max loss.
2. Ask: "Reply yes to place this PAPER order, or no." Pass the user's reply word for word to `approve --reply`. Never type the yes yourself, never treat "ok", "sure" or "좋아" as a yes, never approve two orders with one reply.
3. After a fill, set alerts (/krx-alerts add --from-plan) and run `check` after each price update.
4. At the flat-by time run `flatten`. Report net P&L after costs from `log`, and that paper results flatter real fills.

## 한국어 요약

모의투자 전용입니다. 실제 증권사에 연결하지 않습니다. 주문 하나마다 사용자의 명확한 "네"/"yes"를 받아야 모의 체결하며, 수수료와 거래세를 반영해 손익을 기록합니다. 손절·목표·청산 시각을 점검하고 매매일지를 남깁니다.

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
