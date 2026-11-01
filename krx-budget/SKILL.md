---
name: krx-budget
description: >-
  Budget Manager for Korean stocks: sizes a planned trade in won and whole shares to the user's
  budget, cash buffer, position cap and max loss per trade, counting the sell tax and commission in
  the loss at the stop. Writes krx/orders.json. Use when the user says "how many shares", "size
  it", "몇 주", "수량", "예산", or after /krx-timemachine.
argument-hint: "[--budget WON] [--max-loss WON]"
---

# krx-budget (Budget Manager)

Turns a plan into one proposed paper order. The smallest of four limits wins.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                    # krx/plan.json -> krx/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 3000000 --max-loss 30000
python3 "${CLAUDE_SKILL_DIR}/size.py" --market --lang ko         # 시장가 instead of 지정가
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| `krx/plan.json` | entry, stop, target from krx-exit |
| `krx/config.json` | budget, cash_buffer_pct, max_position_pct, max_order_krw, max_loss_per_trade_krw |
| `krx/orders.json` | with --write: one bracket order (limit buy, stop-loss, take-profit) |

## Steps

1. Run it. Show the limits table and which one binds.
2. Report the shares, the cost, the max loss at the stop WITH costs, and the net gain at the target.
3. If it refuses, give the reason as printed; never raise the user's limits for them.
4. Next: /krx-risk, then /krx-broker (paper).

## 한국어 요약

예산, 현금 비중, 종목 비중 한도, 1회 최대 손실에 맞춰 정수 주로 수량을 정합니다. 손절 시 손실에 거래세와 수수료를 포함해 계산합니다. 결과는 모의 주문 제안(krx/orders.json)입니다.

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
