---
name: krx-news
description: >-
  News Monitor for Korean stocks: Korean headlines (Google News), DART filings (전자공시) tagged by
  what moves prices (잠정실적 earnings, 유상증자 / CB dilution, 공급계약 contracts, 자사주 buybacks, 최대주주 변경,
  횡령·배임, 거래정지), and each stock's move today. Use when the user says "news on 삼성전자", "any filings",
  "공시 확인", "뉴스", "why is it moving", "왜 올라", or before an entry.
argument-hint: "[codes or names]"
---

# krx-news (News Monitor)

Reads what could be behind the price action. In Korea the filing (공시) moves the stock more often than the headline, so DART filings come first.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current project. Python 3.8+, standard library only.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                               # portfolio, plan and watchlist
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers 005930,000660 --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers 삼성전자 --days 7 --all
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-dart                     # no API key needed
```

Every tool takes `--now "2026-10-30 09:45"` (Seoul time) to pin the clock and `--offline` to read saved files instead of the network. The script's own `--help` and docstring hold the full details.

## What it reads and writes

| source / file | what |
| --- | --- |
| Google News RSS (ko) | headlines searched by company name; titles and links only |
| OpenDART `list.json` | the company's filings from the last --days days (needs "dart_api_key") |
| Yahoo chart | today's change from yesterday's close |
| `krx/state/news.json` | what was already shown, so the next run shows only what is new |

## Steps

1. Run it for the stock(s). Without a DART key it still shows headlines and moves; say a key adds the filings and how to get one (free, opendart.fss.or.kr).
2. Lead with filings marked `!` (danger, dilution, earnings, contract, control). Give the filing title and its link.
3. Report each headline with its source and time (KST). Headlines are leads, not facts: point to the filing.
4. If a filing is `dilution` or `danger`, say plainly that it usually weighs on the price; do not soften it.
5. Never paste article text. Summarize in a line and give the link.

## 한국어 요약

관심 종목의 뉴스 헤드라인(구글 뉴스), DART 공시, 오늘 등락률을 한 번에 봅니다. 공시는 잠정실적, 유상증자·CB, 공급계약, 자사주, 최대주주 변경, 횡령·배임·거래정지 등으로 분류됩니다. DART 공시를 보려면 무료 OpenDART 인증키가 필요합니다 (opendart.fss.or.kr).

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
