#!/usr/bin/env python3
"""Writes the eleven SKILL.md files from one place, so the shared safety block,
the language rule and the Korean summaries stay in step. Edit here, then run:

    python3 tools/skill_docs.py
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LANG = """## Language / 언어

Answer in the user's language. If the user writes in Korean, or `krx/config.json`
has `"lang": "ko"`, reply in Korean and pass `--lang ko` to the tool where it has
one (the tools print English otherwise; translate the rest faithfully, numbers
unchanged). Company names stay as the tool prints them (삼성전자 005930)."""

SAFETY = """## Safety

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
  the numbers show it."""

SKILLS = [
    {
        "dir": "krx-scanner", "script": "scan.py", "title": "Chart Scanner",
        "desc": ("Chart Scanner for the Korean market (KOSPI, KOSDAQ). Builds the list of listed stocks from "
                 "KRX KIND, scans daily closes for setups (20-day highs, bull flags, 20-day average reclaims, "
                 "oversold bounces, stocks that closed at the upper limit 상한가) and scans live opening-range "
                 "breakouts after 09:15 Seoul time. Writes krx/watchlist.json. Use when the user says \"scan the "
                 "market\", \"find setups\", \"what's breaking out\", \"종목 검색\", \"스캔\", \"돌파 종목\", "
                 "\"상한가 종목\", or asks for a Korean stock watchlist."),
        "hint": "[universe | daily | orb]",
        "intro": ("Finds charts that match a rule, across about 2,500 KOSPI and KOSDAQ stocks. A scan is a list "
                  "of charts that match a rule, not a list of stocks to buy."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                  # KIND lists -> krx/universe.json (do this first)',
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                     # before 09:00: daily setups',
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --market KOSDAQ --limit 300',
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols 005930,000660,247540',
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                       # after 09:20: opening-range breakouts',
            'python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 09:45 --or-minutes 15',
        ],
        "files": [("KRX KIND", "the listed-company lists, once for KOSPI and once for KOSDAQ (SPACs left out)"),
                  ("Yahoo spark / chart", "daily closes and 5-minute bars for 005930.KS / 247540.KQ, 20 codes a request"),
                  ("`krx/universe.json`", "code -> name, market, sector; lets every tool take 삼성전자 as well as 005930"),
                  ("`krx/watchlist.json`", "the hits, most traded (won value) first")],
        "steps": [
            "If `krx/universe.json` does not exist, run `universe` first (two downloads).",
            "Before 09:00 run `daily`; after 09:20 (the range is 09:00-09:15) run `orb`.",
            "Show the hits as printed: code, name, price, the setups. Say how many were scanned and filtered.",
            "A `limit_up` stock closed near +30 %: say plainly that these swing hard the next day.",
            "Suggest the next step for ONE chart: /krx-news CODE, then /krx-entry CODE.",
        ],
        "ko": ("코스피·코스닥 약 2,500개 종목에서 조건에 맞는 차트를 찾습니다. 먼저 `universe`로 종목 목록을 만들고, "
               "장 시작 전에는 `daily`(20일 신고가, 깃발형, 20일선 회복, 과매도 반등, 상한가 마감), 09:20 이후에는 "
               "`orb`(시가 범위 돌파)를 실행합니다. 스캔 결과는 조건에 맞는 차트 목록일 뿐, 매수 추천이 아닙니다."),
    },
    {
        "dir": "krx-news", "script": "news.py", "title": "News Monitor",
        "desc": ("News Monitor for Korean stocks: Korean headlines (Google News), DART filings (전자공시) tagged by "
                 "what moves prices (잠정실적 earnings, 유상증자 / CB dilution, 공급계약 contracts, 자사주 buybacks, "
                 "최대주주 변경, 횡령·배임, 거래정지), and each stock's move today. Use when the user says \"news on "
                 "삼성전자\", \"any filings\", \"공시 확인\", \"뉴스\", \"why is it moving\", \"왜 올라\", or before an entry."),
        "hint": "[codes or names]",
        "intro": ("Reads what could be behind the price action. In Korea the filing (공시) moves the stock more often "
                  "than the headline, so DART filings come first."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/news.py"                               # portfolio, plan and watchlist',
            'python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers 005930,000660 --hours 12',
            'python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers 삼성전자 --days 7 --all',
            'python3 "${CLAUDE_SKILL_DIR}/news.py" --no-dart                     # no API key needed',
        ],
        "files": [("Google News RSS (ko)", "headlines searched by company name; titles and links only"),
                  ("OpenDART `list.json`", "the company's filings from the last --days days (needs \"dart_api_key\")"),
                  ("Yahoo chart", "today's change from yesterday's close"),
                  ("`krx/state/news.json`", "what was already shown, so the next run shows only what is new")],
        "steps": [
            "Run it for the stock(s). Without a DART key it still shows headlines and moves; say a key adds the filings "
            "and how to get one (free, opendart.fss.or.kr).",
            "Lead with filings marked `!` (danger, dilution, earnings, contract, control). Give the filing title and its link.",
            "Report each headline with its source and time (KST). Headlines are leads, not facts: point to the filing.",
            "If a filing is `dilution` or `danger`, say plainly that it usually weighs on the price; do not soften it.",
            "Never paste article text. Summarize in a line and give the link.",
        ],
        "ko": ("관심 종목의 뉴스 헤드라인(구글 뉴스), DART 공시, 오늘 등락률을 한 번에 봅니다. 공시는 잠정실적, 유상증자·CB, "
               "공급계약, 자사주, 최대주주 변경, 횡령·배임·거래정지 등으로 분류됩니다. DART 공시를 보려면 무료 "
               "OpenDART 인증키가 필요합니다 (opendart.fss.or.kr)."),
    },
    {
        "dir": "krx-entry", "script": "entry.py", "title": "Entry Timer",
        "desc": ("Entry Timer for Korean stocks: watches an opening-range breakout (or a close above a level) bar by "
                 "bar in Seoul time and says the moment to get in: waiting, triggered, or expired. Warns near the "
                 "upper limit (상한가) and around KRX volatility interruptions (VI). Writes krx/entry.json. Use when "
                 "the user says \"when do I get in\", \"is it triggered\", \"watch 005930\", \"진입 타이밍\", "
                 "\"돌파했어?\", or after /krx-scanner."),
        "hint": "[code or name]",
        "intro": ("Watches one setup on 5-minute bars. The opening range is 09:00-09:15 by default; a trigger is the "
                  "first COMPLETED bar after it that CLOSES above the range high. A wick is not a trigger."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/entry.py" check 005930 --write          # waiting / triggered / expired',
            'python3 "${CLAUDE_SKILL_DIR}/entry.py" check 삼성전자 --setup level --level 99500',
            'python3 "${CLAUDE_SKILL_DIR}/entry.py" watch 005930 --every 30 --write   # one line per closed bar',
            'python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                     # every hit in krx/watchlist.json',
        ],
        "files": [("Yahoo chart", "5 days of 5-minute (or 1-minute) bars for the code"),
                  ("`krx/config.json`", "no_entries_before (09:15) and no_entries_after (15:00)"),
                  ("`krx/entry.json`", "with --write: the trigger time, price, bar and opening range, for krx-exit")],
        "steps": [
            "Run `check` (or `watch` while the user waits). Report the status line exactly as printed.",
            "On TRIGGERED: give the time and price, and if it was more than one bar ago, say a late entry is a "
            "different trade.",
            "Repeat any 'Careful' line (upper limit, VI). Never encourage chasing a stock near 상한가.",
            "Next: /krx-exit sets the stop and target BEFORE any buy.",
        ],
        "ko": ("시가 범위(기본 09:00-09:15) 돌파를 5분봉으로 지켜보고 진입 시점을 알려줍니다. 봉이 마감된 뒤 범위 고가 위에서 "
               "종가가 형성되어야 신호입니다. 상한가 근처나 VI(변동성 완화장치) 발동 가능 구간이면 경고합니다."),
    },
    {
        "dir": "krx-exit", "script": "exit.py", "title": "Exit Planner",
        "desc": ("Exit Planner for Korean stocks: sets the stop and the target BEFORE the entry, on the KRX tick "
                 "grid, inside the +-30 % price limits, and shows what the 0.20 % sell tax and commission do to the "
                 "reward:risk. Writes krx/plan.json. Use when the user says \"where's my stop\", \"set a target\", "
                 "\"손절가\", \"목표가\", \"청산 계획\", or after /krx-entry triggers."),
        "hint": "[--stop or-mid|or-low|pct:X|atr:K|price:X] [--target 2R|price:X]",
        "intro": ("Sets the exit before the entry, so the user knows when to get out, win or lose, and out by 15:15 "
                  "before the closing auction."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --write                  # from krx/entry.json: or-mid stop, 2R',
            'python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write',
            'python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol 005930 --entry 98700 --stop pct:1 --target 2R',
            'python3 "${CLAUDE_SKILL_DIR}/exit.py" show --lang ko',
        ],
        "files": [("`krx/entry.json`", "the trigger from krx-entry (or give --symbol and --entry)"),
                  ("`krx/plan.json`", "with --write: entry, stop, target, risk, costs, price limits, flat_by")],
        "steps": [
            "Make the plan. Report entry, stop, target, risk a share, reward:risk before AND after costs, and flat-by.",
            "If the tool notes costs are over 20 % of the risk, say it plainly: a tight stop on a Korean stock can "
            "lose money even when it wins half the time. Suggest a wider stop and target, not a smaller one.",
            "Never move a stop further away once set. Never plan a short.",
            "Next: /krx-timemachine to replay it, then /krx-budget.",
        ],
        "ko": ("진입 전에 손절가와 목표가를 정합니다. 모든 가격은 호가 단위에 맞추고(손절은 내림, 목표는 진입가 쪽으로 내림), "
               "±30% 가격제한폭 안에 있어야 합니다. 거래세 0.20%와 수수료를 반영한 손익비도 보여주며, 비용이 위험의 20%를 "
               "넘으면 경고합니다. 15:15까지 청산(15:20 동시호가 전)."),
    },
    {
        "dir": "krx-timemachine", "script": "timemachine.py", "title": "Time Machine",
        "desc": ("Time Machine for Korean stocks: replays a planned day trade 10,000 times from the stock's own "
                 "5-minute bars over the last 60 days (KRX hours, +-30 % limits) and re-runs the same rule on every "
                 "past session, then shows the odds and the average R before AND after Korean costs. Use when the "
                 "user says \"run the time machine\", \"replay this trade\", \"what are the odds\", \"백테스트\", "
                 "\"시뮬레이션\", \"확률\", or after /krx-exit writes a plan."),
        "hint": "[path to plan.json, or code]",
        "intro": ("Asks one question before any money: if today plays out like the stock's last 60 days, how often "
                  "does this plan hit the target first, the stop first, or neither by 15:20? It is a simulation, not "
                  "a prediction."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write          # krx/plan.json, 10,000 replays, seed 19',
            'python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:15     # end at your flat-by time',
            'python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --lang ko',
            'python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol 005930 --date 2026-10-30 \\',
            '    --entry 98700 --stop 97900 --target 99500 --entry-time 09:45',
            'python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from          # ASCII fan chart',
        ],
        "files": [("`krx/plan.json`", "the plan from krx-exit"),
                  ("Yahoo chart", "one request: 60 days of 5-minute bars"),
                  ("`krx/timemachine.json`", "with --write: odds, percentiles, bands, history, verdict")],
        "steps": [
            "Run it with `--until` set to the plan's flat-by time.",
            "Report, in order: the four odds, the price range at the end, average R before and after costs, "
            "the history line, then the verdict and the lines under it exactly as printed. Say the seed.",
            "If costs flip the average R negative, lead with that. If it is a coin flip, say so.",
            "Say once: built from the last 60 days, not a prediction; filings, VI halts and the limits can do "
            "what those days never did.",
            "Only if the user still wants the trade: /krx-budget.",
        ],
        "ko": ("계획한 단타를 최근 60거래일의 같은 시간대 5분봉으로 10,000번 재생하고, 같은 규칙을 과거 모든 거래일에 다시 "
               "적용합니다. 목표가 먼저/손절가 먼저/둘 다 아님 확률과, 거래세·수수료 전후의 평균 R을 보여줍니다. "
               "예측이 아닌 시뮬레이션입니다."),
    },
    {
        "dir": "krx-budget", "script": "size.py", "title": "Budget Manager",
        "desc": ("Budget Manager for Korean stocks: sizes a planned trade in won and whole shares to the user's "
                 "budget, cash buffer, position cap and max loss per trade, counting the sell tax and commission in "
                 "the loss at the stop. Writes krx/orders.json. Use when the user says \"how many shares\", "
                 "\"size it\", \"몇 주\", \"수량\", \"예산\", or after /krx-timemachine."),
        "hint": "[--budget WON] [--max-loss WON]",
        "intro": "Turns a plan into one proposed paper order. The smallest of four limits wins.",
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/size.py" --write                    # krx/plan.json -> krx/orders.json',
            'python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 3000000 --max-loss 30000',
            'python3 "${CLAUDE_SKILL_DIR}/size.py" --market --lang ko         # 시장가 instead of 지정가',
        ],
        "files": [("`krx/plan.json`", "entry, stop, target from krx-exit"),
                  ("`krx/config.json`", "budget, cash_buffer_pct, max_position_pct, max_order_krw, max_loss_per_trade_krw"),
                  ("`krx/orders.json`", "with --write: one bracket order (limit buy, stop-loss, take-profit)")],
        "steps": [
            "Run it. Show the limits table and which one binds.",
            "Report the shares, the cost, the max loss at the stop WITH costs, and the net gain at the target.",
            "If it refuses, give the reason as printed; never raise the user's limits for them.",
            "Next: /krx-risk, then /krx-broker (paper).",
        ],
        "ko": ("예산, 현금 비중, 종목 비중 한도, 1회 최대 손실에 맞춰 정수 주로 수량을 정합니다. 손절 시 손실에 거래세와 "
               "수수료를 포함해 계산합니다. 결과는 모의 주문 제안(krx/orders.json)입니다."),
    },
    {
        "dir": "krx-risk", "script": "check.py", "title": "Risk Manager",
        "desc": ("Risk Manager for Korean stocks: checks every proposed paper order against the user's limits and "
                 "KRX rules (tick grid, +-30 % limits, trading hours and the closing auction, holidays, max loss "
                 "with costs, daily loss, trades per day, reward:risk, cash and no margin, no short sales, blocked "
                 "and 관리종목 codes) and prints PASS or BLOCK with the reason. Use when the user says \"check the "
                 "order\", \"is this within my limits\", \"리스크 점검\", or after /krx-budget."),
        "hint": "",
        "intro": "The rules check between sizing and the paper broker. It blocks; it never loosens a rule.",
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/check.py"                           # krx/orders.json',
            'python3 "${CLAUDE_SKILL_DIR}/check.py" --lang ko',
            'python3 "${CLAUDE_SKILL_DIR}/check.py" --json',
        ],
        "files": [("`krx/orders.json`", "the proposals from krx-budget"),
                  ("`krx/portfolio.json`, `krx/state/broker.json`", "cash, positions, today's entries and losses (paper)"),
                  ("`krx/prices.json`", "yesterday's close for the price limits (krx-alerts quote --write)"),
                  ("`krx/kind_flags.json`", "optional {\"flags\": {code: reason}} for 관리종목 / 거래정지 stocks"),
                  ("`krx/state/risk.json`", "PASS / BLOCK per order with a fingerprint; krx-broker reads it")],
        "steps": [
            "Run it. Report PASS or BLOCK per order with every reason, as printed.",
            "A BLOCK stands. Never edit the config or the order to get a PASS without the user asking, and say what "
            "changing it would mean.",
            "Next for a PASS: /krx-broker shows the ticket and asks for an explicit yes.",
        ],
        "ko": ("모의 주문마다 호가 단위, 가격제한폭, 거래 시간(15:20 동시호가 제외), 휴장일, 비용 포함 최대 손실, 일일 손실 한도, "
               "하루 거래 횟수, 손익비, 현금(미수 금지), 공매도 금지, 차단·관리종목을 점검해 통과/차단을 알려줍니다."),
    },
    {
        "dir": "krx-broker", "script": "ticket.py", "title": "Paper Order Executor",
        "desc": ("Paper Order Executor for Korean stocks (모의투자). PAPER ONLY: never connects to a broker. Shows each "
                 "order as a plain ticket, takes the user's explicit yes (\"yes\" / \"네\") for one order at a time, "
                 "fills it in a practice account with Korean commission and sell tax, watches the stop, target and "
                 "flat-by time, and keeps a trade log with net P&L. Use when the user says \"place it\", \"buy it\", "
                 "\"paper trade\", \"모의투자\", \"매수해\", \"close my position\", \"청산\", \"how am I doing\", or "
                 "after /krx-risk passes."),
        "hint": "[show | approve | check | flatten | portfolio | log]",
        "intro": ("A practice account in won. It cannot send a real order; that is deliberate. Paper fills are kinder "
                  "than real ones, and the tool says so."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                       # the tickets, READY or not',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "<the user\'s words>"',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" check                      # stop / target / flat-by vs krx/prices.json',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" portfolio',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" log',
            'python3 "${CLAUDE_SKILL_DIR}/ticket.py" reset --cash 5000000',
        ],
        "files": [("`krx/orders.json`, `krx/state/risk.json`", "the order and today's PASS for its fingerprint"),
                  ("`krx/portfolio.json`", "paper cash and positions"),
                  ("`krx/state/broker.json`", "every yes, decline, fill and closed trade with fees, tax and net P&L"),
                  ("`krx/prices.json`", "latest prices for `check` (krx-alerts: alerts.py quote --holdings --write)")],
        "steps": [
            "Run `show` and present the ticket in plain words with the max loss.",
            "Ask: \"Reply yes to place this PAPER order, or no.\" Pass the user's reply word for word to "
            "`approve --reply`. Never type the yes yourself, never treat \"ok\", \"sure\" or \"좋아\" as a yes, never "
            "approve two orders with one reply.",
            "After a fill, set alerts (/krx-alerts add --from-plan) and run `check` after each price update.",
            "At the flat-by time run `flatten`. Report net P&L after costs from `log`, and that paper results flatter real fills.",
        ],
        "ko": ("모의투자 전용입니다. 실제 증권사에 연결하지 않습니다. 주문 하나마다 사용자의 명확한 \"네\"/\"yes\"를 받아야 "
               "모의 체결하며, 수수료와 거래세를 반영해 손익을 기록합니다. 손절·목표·청산 시각을 점검하고 매매일지를 남깁니다."),
    },
    {
        "dir": "krx-alerts", "script": "alerts.py", "title": "Price Alerts",
        "desc": ("Price Alerts and the KRX clock: is the market open, opening/closing auction times, holidays and "
                 "the exam-day (수능) late open; quotes with today's price limits; alerts for a stop, a target, a % "
                 "move, nearing the upper or lower limit (상한가/하한가), the opening bell and the flat-by time. Use "
                 "when the user says \"is the market open\", \"장 열렸어?\", \"alert me\", \"알림\", \"quote 005930\", "
                 "\"현재가\", or after a paper fill."),
        "hint": "[clock | quote | add | check | list | reset]",
        "intro": "Rings once when a level hits. Quotes are free and delayed: for alerts, never for timing an order.",
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock',
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote 005930 000660 --write',
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write',
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan             # stop, target, flat-by, near-limit',
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol 247540 --pct 5',
            'python3 "${CLAUDE_SKILL_DIR}/alerts.py" check --write',
        ],
        "files": [("Yahoo chart", "the latest price, today's range and yesterday's close"),
                  ("`krx/alerts.json`", "the rules"), ("`krx/state/alerts.json`", "what rang, so each rings once"),
                  ("`krx/prices.json`", "with --write: quotes for krx-risk and krx-broker")],
        "steps": [
            "For a time question, run `clock` and answer from it (Seoul time).",
            "After a paper fill, `add --from-plan`. While a position is open, `check --write`, then krx-broker `check`.",
            "Report each RING line as printed. A flat-by ring means: close it now (paper: ticket.py flatten).",
            "To run it on a schedule, offer a scheduled task every 5 minutes during 09:00-15:20 on trading days.",
        ],
        "ko": ("장 운영 시간(동시호가 포함), 휴장일, 수능일 지연 개장을 알려주고, 현재가와 오늘의 상·하한가를 보여줍니다. 손절가, "
               "목표가, 등락률, 상·하한가 근접, 개장, 청산 시각 알림을 설정할 수 있습니다. 시세는 무료·지연 데이터입니다."),
    },
    {
        "dir": "krx-analyst", "script": "facts.py", "title": "Earnings Analyst",
        "desc": ("Earnings Analyst for Korean companies: reads quarterly results from DART (revenue 매출액, operating "
                 "income 영업이익, net income 순이익, EPS) for the last quarters, with year-on-year growth and margins, "
                 "and lists recent preliminary results filings (잠정실적). Use when the user says \"earnings\", "
                 "\"실적\", \"how did 삼성전자 do\", \"분기 실적\", \"영업이익\", or before trading around results."),
        "hint": "[code or name]",
        "intro": ("Sums up what changed in the numbers, from the filings themselves. In Korea the preliminary results "
                  "(잠정실적 공정공시) come weeks before the quarterly report and move the price on results day."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/facts.py" 005930                    # 6 quarters, consolidated (연결)',
            'python3 "${CLAUDE_SKILL_DIR}/facts.py" 삼성전자 --quarters 8 --lang ko',
            'python3 "${CLAUDE_SKILL_DIR}/facts.py" 000660 --separate         # 별도 (parent only)',
        ],
        "files": [("OpenDART `fnlttSinglAcntAll.json`", "full statements per report (Q1, half, Q3, annual); needs \"dart_api_key\""),
                  ("OpenDART `list.json`", "preliminary results filings from the last 120 days"),
                  ("`krx/metrics.json`", "with --write: the latest quarter per company")],
        "steps": [
            "Run it. Show the table (amounts in 억원) and the change lines.",
            "Point out a turn to profit or loss (흑자/적자 전환) and margin changes. Link the preliminary filing if any.",
            "Banks, insurers and holding companies use other layouts: a '-' means the line is not there, not zero.",
            "No price targets, no valuation calls. Numbers as filed; point to the filing.",
        ],
        "ko": ("DART 재무제표로 최근 분기의 매출액, 영업이익, 순이익, EPS와 전년 동기 대비 증감, 이익률을 보여줍니다. 4분기는 "
               "연간에서 1~3분기를 빼서 계산합니다. 최근 120일의 잠정실적 공시도 함께 보여줍니다. 무료 OpenDART 인증키가 필요합니다."),
    },
    {
        "dir": "krx-insider", "script": "insider.py", "title": "Insider Tracker",
        "desc": ("Insider Tracker for Korean companies (replaces the US pack's Pelosi tracker): reads DART reports of "
                 "executives' and major shareholders' trades (임원·주요주주 소유상황보고서) and 5 %+ stake reports "
                 "(대량보유 보고, 5% 룰), per stock or market-wide. Use when the user says \"insider buying\", "
                 "\"who is buying\", \"임원 매수\", \"내부자 거래\", \"대주주 지분\", \"5% 공시\", \"국민연금 지분\"."),
        "hint": "[codes or names | --watch | --market]",
        "intro": ("Korea's lawmakers report assets once a year, not trade by trade, so the timely public trail in Korea "
                  "is the insiders': executives and 10 %+ holders report changes within 5 business days, and 5 %+ "
                  "holders report every 1 %-point change."),
        "cmds": [
            'python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930                  # executives and major holders',
            'python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930 --major          # 5 %+ stakes (대량보유)',
            'python3 "${CLAUDE_SKILL_DIR}/insider.py" --watch --new           # portfolio, plan, watchlist; new only',
            'python3 "${CLAUDE_SKILL_DIR}/insider.py" --market --days 3       # every such filing on DART',
            'python3 "${CLAUDE_SKILL_DIR}/insider.py" 005930 --buys --days 180',
        ],
        "files": [("OpenDART `elestock.json`", "임원ㆍ주요주주 소유보고: who, role, change in shares, holding after"),
                  ("OpenDART `majorstock.json`", "대량보유: holder, shares, %, change, purpose"),
                  ("OpenDART `list.json`", "market-wide mode (지분공시, last N days)"),
                  ("`krx/state/insider.json`", "filings already shown")],
        "steps": [
            "Run it. Report each filing as printed with its link.",
            "Say how to read it: an on-market buy (장내매수) by an executive is the strongest signal; sales, grants, "
            "gifts and conversions often mean little. Open the filing for the reason.",
            "These are public filings after the fact, not inside information. Never suggest copying a trade blindly.",
        ],
        "ko": ("미국판의 펠로시(의회) 추적기를 대신합니다. 한국 국회의원 재산은 1년에 한 번 공개되므로, 시의성 있는 공개 기록은 "
               "임원·주요주주의 소유상황보고(5영업일 내)와 5% 이상 대량보유 보고입니다. 장내매수인지, 증여·전환인지 공시 "
               "사유를 꼭 확인하세요."),
    },
]


def render(s):
    out = ["---", "name: %s" % s["dir"], "description: >-"]
    words, line = s["desc"].split(), "  "
    for w in words:
        if len(line) + len(w) + 1 > 100:
            out.append(line.rstrip())
            line = "  "
        line += w + " "
    out.append(line.rstrip())
    out.append('argument-hint: "%s"' % s["hint"])
    out.append("---")
    out.append("")
    out.append("# %s (%s)" % (s["dir"], s["title"]))
    out.append("")
    out.append(s["intro"])
    out.append("")
    out.append("`${CLAUDE_SKILL_DIR}` is this skill's folder. Everything is written under `krx/` in the current "
               "project. Python 3.8+, standard library only. On Windows, `python3` is often missing or opens the "
               "Microsoft Store: use `python` instead (run `python --version` once to check), and set "
               "`PYTHONUTF8=1` so Korean text prints correctly.")
    out.append("")
    out.append("```bash")
    out += s["cmds"]
    out.append("```")
    out.append("")
    out.append("Every tool takes `--now \"2026-10-30 09:45\"` (Seoul time) to pin the clock and `--offline` to read "
               "saved files instead of the network. The script's own `--help` and docstring hold the full details.")
    out.append("")
    out.append("## What it reads and writes")
    out.append("")
    out.append("| source / file | what |")
    out.append("| --- | --- |")
    for a, b in s["files"]:
        out.append("| %s | %s |" % (a, b))
    out.append("")
    out.append("## Steps")
    out.append("")
    for i, st in enumerate(s["steps"], 1):
        out.append("%d. %s" % (i, st))
    out.append("")
    out.append("## 한국어 요약")
    out.append("")
    out.append(s["ko"])
    out.append("")
    out.append(LANG)
    out.append("")
    out.append(SAFETY)
    out.append("")
    return "\n".join(out)


def main():
    for s in SKILLS:
        path = os.path.join(ROOT, s["dir"], "SKILL.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(s))
        print("wrote %s" % os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
