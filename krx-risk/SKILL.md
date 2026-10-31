---
name: krx-risk
description: >-
  Risk Manager for Korean stocks: checks every proposed paper order against the user's limits and
  KRX rules (tick grid, +-30 % limits, trading hours and the closing auction, holidays, max loss
  with costs, daily loss, trades per day, reward:risk, cash and no margin, no short sales, blocked
  and 관리종목 codes) and prints PASS or BLOCK with the reason. Use when the user says "check the
  order", "is this within my limits", "리스크 점검", or after /krx-budget.
argument-hint: ""
---

# krx-risk (Risk Manager)

The rules check between sizing and the paper broker. It blocks; it never loosens a rule.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # krx/orders.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --lang ko
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| `krx/orders.json` | the proposals from krx-budget |
| `krx/portfolio.json`, `krx/state/broker.json` | cash, positions, today's entries and losses (paper) |
| `krx/prices.json` | yesterday's close for the price limits (krx-alerts quote --write) |
| `krx/kind_flags.json` | optional {"flags": {code: reason}} for 관리종목 / 거래정지 stocks |
| `krx/state/risk.json` | PASS / BLOCK per order with a fingerprint; krx-broker reads it |

## Steps

1. Run it. Report PASS or BLOCK per order with every reason, as printed.
2. A BLOCK stands. Never edit the config or the order to get a PASS without the user asking, and say what changing it would mean.
3. Next for a PASS: /krx-broker shows the ticket and asks for an explicit yes.

## 한국어 요약

모의 주문마다 호가 단위, 가격제한폭, 거래 시간(15:20 동시호가 제외), 휴장일, 비용 포함 최대 손실, 일일 손실 한도, 하루 거래 횟수, 손익비, 현금(미수 금지), 공매도 금지, 차단·관리종목을 점검해 통과/차단을 알려줍니다.

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
