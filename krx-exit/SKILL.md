---
name: krx-exit
description: >-
  Exit Planner for Korean stocks: sets the stop and the target BEFORE the entry, on the KRX tick
  grid, inside the +-30 % price limits, and shows what the 0.20 % sell tax and commission do to the
  reward:risk. Writes krx/plan.json. Use when the user says "where's my stop", "set a target",
  "손절가", "목표가", "청산 계획", or after /krx-entry triggers.
argument-hint: "[--stop or-mid|or-low|pct:X|atr:K|price:X] [--target 2R|price:X]"
---

# krx-exit (Exit Planner)

Sets the exit before the entry, so the user knows when to get out, win or lose, and out by 15:15 before the closing auction.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --write                  # from krx/entry.json: or-mid stop, 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol 005930 --entry 98700 --stop pct:1 --target 2R
python3 "${CLAUDE_SKILL_DIR}/exit.py" show --lang ko
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| `krx/entry.json` | the trigger from krx-entry (or give --symbol and --entry) |
| `krx/plan.json` | with --write: entry, stop, target, risk, costs, price limits, flat_by |

## Steps

1. Make the plan. Report entry, stop, target, risk a share, reward:risk before AND after costs, and flat-by.
2. If the tool notes costs are over 20 % of the risk, say it plainly: a tight stop on a Korean stock can lose money even when it wins half the time. Suggest a wider stop and target, not a smaller one.
3. Never move a stop further away once set. Never plan a short.
4. Next: /krx-timemachine to replay it, then /krx-budget.

## 한국어 요약

진입 전에 손절가와 목표가를 정합니다. 모든 가격은 호가 단위에 맞추고(손절은 내림, 목표는 진입가 쪽으로 내림), ±30% 가격제한폭 안에 있어야 합니다. 거래세 0.20%와 수수료를 반영한 손익비도 보여주며, 비용이 위험의 20%를 넘으면 경고합니다. 15:15까지 청산(15:20 동시호가 전).

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
