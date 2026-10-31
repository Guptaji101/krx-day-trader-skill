# KRX Day Trader skill · 한국 주식 단타 스킬

11 Claude skills for **paper** day trading on the Korean market (KOSPI, KOSDAQ): scan
charts, read DART filings and news, time the entry, plan the exit, replay the trade
10,000 times, size it in won, check it against your limits, and practise it in a
paper account. Every number counts Korea's 0.20 % sell tax, commission, tick sizes
and ±30 % price limits.

코스피·코스닥 **모의** 단타를 위한 Claude 스킬 11개: 차트 스캔, DART 공시와 뉴스, 진입 타이밍,
청산 계획, 10,000번 재생 시뮬레이션, 원화 수량 계산, 리스크 점검, 모의투자. 모든 계산에
거래세(0.20%), 수수료, 호가 단위, ±30% 가격제한폭이 반영됩니다.

A Korean-market rebuild of [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT). Same method, same Time Machine algorithm; new market rules, data sources and
a paper-only broker.

> **Paper only. 모의투자 전용.** Nothing here can place a real order. Not financial advice.
> Most people who day trade lose money, and in Korea the costs are large next to a tight stop.

## Install · 설치

In Claude Code:

```
/plugin marketplace add Guptaji101/krx-day-trader-skill
/plugin install krx-day-trader-skill@krx-day-trader-skill
```

Or copy the folders for plain `/krx-scanner` commands:

```bash
git clone https://github.com/Guptaji101/krx-day-trader-skill.git
cp -r krx-day-trader-skill/krx-* ~/.claude/skills/
```

Python 3.8 or newer. Nothing to pip install. Windows: use `python` instead of `python3`.

### Settings · 설정

Copy `config.example.json` to `krx/config.json` in your project and edit it:

- `budget`, `max_loss_per_trade_krw`, `max_daily_loss_krw` and the other limits, in won.
- `commission_pct`: your broker's online commission, each way (default 0.015 %).
- `dart_api_key`: a free OpenDART key for filings, earnings and insider reports. Get one at
  [opendart.fss.or.kr](https://opendart.fss.or.kr) (회원가입 → 인증키 신청 → 이메일로 발급).
  Or set the `DART_API_KEY` environment variable. Never paste it into a chat.
- `lang`: `"ko"` for Korean reports, `"en"` for English.

## The eleven · 11개 스킬

| command | job | 역할 |
| --- | --- | --- |
| `/krx-scanner` | Scans KOSPI/KOSDAQ for daily setups and opening-range breakouts | 종목 스캔 (신고가, 상한가, 시가 범위 돌파) |
| `/krx-news` | Korean headlines, DART filings tagged by impact, today's move | 뉴스·공시·등락 |
| `/krx-entry` | Watches the breakout bar by bar; warns near 상한가 and VI | 진입 타이밍 |
| `/krx-exit` | Stop and target on the tick grid, cost-adjusted reward:risk | 손절가·목표가 |
| `/krx-timemachine` | 10,000 replays + the rule's real history, before and after costs | 10,000회 재생 시뮬레이션 |
| `/krx-budget` | Shares in won to your limits, loss at the stop incl. tax | 수량 계산 |
| `/krx-risk` | PASS or BLOCK against your limits and KRX rules | 리스크 점검 |
| `/krx-broker` | Paper account: your explicit yes, fills, stops, P&L log | 모의투자 |
| `/krx-alerts` | KRX clock, quotes with price limits, alerts | 장 시간·시세·알림 |
| `/krx-analyst` | Quarterly results from DART, 잠정실적 filings | 분기 실적 |
| `/krx-insider` | Executives' and 5 %+ holders' trades from DART | 임원·대주주 지분 변동 |

The US pack's Pelosi (Congress) tracker has no Korean equivalent: National Assembly
members disclose assets once a year. `/krx-insider` follows the trail that is timely in
Korea: executive and major-holder trades, reported within days.

## How it fits together · 흐름

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker (paper)
alerts: stop, target, near-limit, flat by 15:15                     (while a paper trade is open)
news / analyst / insider                                            (any time)
```

Everything lands in a `krx/` folder in your project; each skill reads what the last one wrote.

## What changed from the US pack · 미국판과 다른 점

| | US pack | this pack |
| --- | --- | --- |
| Session | 9:30-16:00 ET, 78 bars | 09:00-15:20 KST continuous, 76 bars; closing auction 15:20-15:30 |
| Calendar | NYSE holidays, early closes | KRX 2026 holidays, 1/2 and 수능 late opens |
| Prices | cents | the KRX tick table (1 to 1,000 won by price band) |
| Limits | none | ±30 % daily price limits, clamped in the simulation |
| Costs | none counted | 0.20 % sell tax + commission in every R, size and P&L |
| Symbols | Nasdaq Trader lists | KRX KIND lists; codes or Korean names |
| Filings | SEC 8-K, XBRL | DART 공시, DART financial statements |
| Politicians | House PTRs | DART insider and 5 % reports |
| Broker | Alpaca, paper by default | paper only, no broker connection |

## Tests · 테스트

```bash
python3 -m unittest discover -s tests -v
```

70 tests, offline. The build sandbox could not reach Yahoo, KIND or DART, so the tests use
generated data shaped like their responses (`tests/fakes.py`). The live endpoints should be
checked on a first real run: if a source changed shape, the tool says so plainly.

## Data sources · 데이터

- Yahoo Finance chart API (005930.KS / 247540.KQ): free, unofficial, can lag.
- KRX KIND (kind.krx.co.kr): listed-company lists.
- OpenDART (opendart.fss.or.kr): filings, financial statements, insider reports. Free key.
- Google News RSS (Korean): headlines and links.

## Fine print · 유의사항

**Not financial advice. 투자 조언이 아닙니다.** Software that reads public data and does arithmetic.

**Paper only.** There is no broker connection. Paper fills are kinder than real fills.

**Market rules are encoded as of 2026-10-31**: tick table (2023 reform), sell tax 0.20 % (2026),
the 2026 holiday list, the 2026-11-19 exam day. Check them each year; add holidays and special
sessions in `krx/config.json` (`extra_holidays`, `special_sessions`).

**The Time Machine is a simulation, not a prediction.** Filings, VI halts and limit days can do
what the last 60 days never did.

**A stop is a plan, not a guarantee.** A gap, a VI halt or a locked limit-down day can fill far past it.

**Short selling** is off. **Margin (미수)** is off.

## License

MIT. Original work © 2026 Jake Schincariol. Korean-market rebuild © 2026 its contributors.
