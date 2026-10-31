---
name: krx-timemachine
description: >-
  Time Machine for Korean stocks: replays a planned day trade 10,000 times from the stock's own
  5-minute bars over the last 60 days (KRX hours, +-30 % limits) and re-runs the same rule on every
  past session, then shows the odds and the average R before AND after Korean costs. Use when the
  user says "run the time machine", "replay this trade", "what are the odds", "백테스트", "시뮬레이션",
  "확률", or after /krx-exit writes a plan.
argument-hint: "[path to plan.json, or code]"
---

# krx-timemachine (Time Machine)

Asks one question before any money: if today plays out like the stock's last 60 days, how often does this plan hit the target first, the stop first, or neither by 15:20? It is a simulation, not a prediction.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write          # krx/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:15     # end at your flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --lang ko
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol 005930 --date 2026-10-30 \
    --entry 98700 --stop 97900 --target 99500 --entry-time 09:45
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from          # ASCII fan chart
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| `krx/plan.json` | the plan from krx-exit |
| Yahoo chart | one request: 60 days of 5-minute bars |
| `krx/timemachine.json` | with --write: odds, percentiles, bands, history, verdict |

## Steps

1. Run it with `--until` set to the plan's flat-by time.
2. Report, in order: the four odds, the price range at the end, average R before and after costs, the history line, then the verdict and the lines under it exactly as printed. Say the seed.
3. If costs flip the average R negative, lead with that. If it is a coin flip, say so.
4. Say once: built from the last 60 days, not a prediction; filings, VI halts and the limits can do what those days never did.
5. Only if the user still wants the trade: /krx-budget.

## 한국어 요약

계획한 단타를 최근 60거래일의 같은 시간대 5분봉으로 10,000번 재생하고, 같은 규칙을 과거 모든 거래일에 다시 적용합니다. 목표가 먼저/손절가 먼저/둘 다 아님 확률과, 거래세·수수료 전후의 평균 R을 보여줍니다. 예측이 아닌 시뮬레이션입니다.

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
