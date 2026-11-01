---
name: krx-insider
description: >-
  Insider Tracker for Korean companies (replaces the US pack's Pelosi tracker): reads DART reports
  of executives' and major shareholders' trades (임원·주요주주 소유상황보고서) and 5 %+ stake reports (대량보유 보고,
  5% 룰), per stock or market-wide. Use when the user says "insider buying", "who is buying", "임원
  매수", "내부자 거래", "대주주 지분", "5% 공시", "국민연금 지분".
argument-hint: "[codes or names | --watch | --market]"
---

# krx-insider (Insider Tracker)

Korea's lawmakers report assets once a year, not trade by trade, so the timely public trail in Korea is the insiders': executives and 10 %+ holders report changes within 5 business days, and 5 %+ holders report every 1 %-point change.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the Microsoft Store: use `python` instead (run `python --version` once to check), and set `PYTHONUTF8=1` so Korean text prints correctly.

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930                  # executives and major holders
python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930 --major          # 5 %+ stakes (대량보유)
python3 "${CLAUDE_SKILL_DIR}/insider.py" --watch --new           # portfolio, plan, watchlist; new only
python3 "${CLAUDE_SKILL_DIR}/insider.py" --market --days 3       # every such filing on DART
python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930 --buys --days 180
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| OpenDART `elestock.json` | 임원ㆍ주요주주 소유보고: who, role, change in shares, holding after |
| OpenDART `majorstock.json` | 대량보유: holder, shares, %, change, purpose |
| OpenDART `list.json` | market-wide mode (지분공시, last N days) |
| `krx/state/insider.json` | filings already shown |

## Steps

1. Run it. Report each filing as printed with its link.
2. Say how to read it: an on-market buy (장내매수) by an executive is the strongest signal; sales, grants, gifts and conversions often mean little. Open the filing for the reason.
3. These are public filings after the fact, not inside information. Never suggest copying a trade blindly.

## 한국어 요약

미국판의 펠로시(의회) 추적기를 대신합니다. 한국 국회의원 재산은 1년에 한 번 공개되므로, 시의성 있는 공개 기록은 임원·주요주주의 소유상황보고(5영업일 내)와 5% 이상 대량보유 보고입니다. 장내매수인지, 증여·전환인지 공시 사유를 꼭 확인하세요.

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
