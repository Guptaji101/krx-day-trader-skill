#!/usr/bin/env python3
"""Entry Timer for krx-entry (Korean market): watches a setup bar by bar and says
the moment to get in. Standard library only. Buys only. It never places an order.

    python3 entry.py check 005930                         # opening-range breakout, 5-minute bars
    python3 entry.py check 삼성전자 --now "2026-10-30 09:45" --offline 005930.json
    python3 entry.py check 005930 --setup level --level 99500   # first close above a price
    python3 entry.py check 005930 --interval 1m --min-volume-x 1.5 --write
    python3 entry.py watch 005930 --every 30 --until 15:00      # one line per closed bar
    python3 entry.py watchlist                                  # every hit in krx/watchlist.json

Setups:
  orb    opening-range breakout. The range is the high and low of the first
         --or-minutes (default 15: the 09:00, 09:05 and 09:10 bars). It triggers on
         the first COMPLETED bar after the range that CLOSES above the range high.
         The 09:00 bar carries the opening auction (08:30-09:00), so it is often big.
  level  the first completed bar that closes above --level.
  --min-volume-x X  the trigger bar needs X times the opening range's average volume.

A trigger's time is the bar's CLOSE time (the 09:40 bar closes at 09:45) and its
price is that close. A bar counts only once it has closed.

Statuses: triggered; waiting; expired (no trigger by no_entries_after, default
15:00, well before the 15:20 closing auction); no-data (closed day or no bars).
krx/config.json: "no_entries_before" (09:15) and "no_entries_after" (15:00), Seoul time.

Korean specifics it watches for: a trigger near the +30 % upper limit (상한가),
where a buy may not fill and the stock can freeze, and moves of 10 % or more from
yesterday's close, where KRX's volatility interruption (VI) can pause trading for
two minutes.

Data: Yahoo's free chart JSON for 005930.KS / 247540.KQ (unofficial, can lag).
--offline PATH reads a saved chart JSON instead. Times are Seoul (KST).
Exit codes: check 0 = triggered, 1 = anything else. watch 0 on a trigger.
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

INTERVALS = {"1m": 1, "5m": 5}
SETTLE = 60
GRACE = 2


class EntryError(Exception):
    pass


def load_chart(info, interval, offline=None):
    try:
        if offline:
            text = krx.read_offline(offline, info, suffix="")
        else:
            text = krx.fetch_chart(info, "5d", interval)
        return krx.chart_result(text, info["code"])
    except krx.KRXError as e:
        raise EntryError(str(e))


def evaluate(result, info, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None):
    """The setup's status at now_utc from the day's bars (a pure function)."""
    cfg = cfg or dict(krx.DEFAULTS)
    if interval not in INTERVALS:
        raise EntryError("--interval is 5m or 1m")
    step = INTERVALS[interval]
    if or_minutes <= 0 or or_minutes % step:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE")
    local = krx.to_kst(now_utc)
    day = local.date().isoformat()
    mins_now = local.hour * 60 + local.minute + local.second / 60.0
    res = {"symbol": info["code"], "code": info["code"], "name": info.get("name", ""), "yahoo": info.get("yahoo"),
           "market": info.get("market"), "setup": setup, "side": "buy", "date": day, "interval": interval,
           "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"), "status": "waiting",
           "phase": "", "reason": "", "opening_range": None, "level": None, "trigger": None, "last": None,
           "min_volume_x": min_volume_x, "calendar_known": krx.calendar_known(local.date()), "warnings": [],
           "_bars": []}
    s = krx.session(day)
    if s is None:
        res.update(status="no-data", phase="closed", reason="KRX is closed today (%s)" % krx.holiday(day),
                   next_open=krx.next_trading_day(day).isoformat())
        return res
    try:
        before = krx.hhmm_min(cfg["no_entries_before"], "no_entries_before") + (s["open"] - krx.OPEN_MIN)
        cutoff = min(krx.hhmm_min(cfg["no_entries_after"], "no_entries_after") + (s["open"] - krx.OPEN_MIN),
                     s["cont_end"])
    except krx.KRXError as e:
        raise EntryError(str(e))
    or_end = s["open"] + or_minutes
    res.update(opens=krx.hm(s["open"]), closes=krx.hm(s["close"]), session_note=s["note"],
               no_entries_before=krx.hm(before), no_entries_after=krx.hm(cutoff))
    days, _ = krx.session_bars(result, now_utc + datetime.timedelta(days=1), step, use_data_time=False)
    meta = result.get("meta") or {}
    dt = meta.get("regularMarketTime")
    today = days.get(day, {"bars": {}})
    prior = [d for d in sorted(days) if d < day]
    base = None
    if prior:
        pd_ = days[prior[-1]]
        base = pd_["auction"] or (pd_["bars"][max(pd_["bars"])]["c"] if pd_["bars"] else None)
    if not prior:
        base = meta.get("chartPreviousClose")
    res["prev_close"] = base
    lim = krx.price_limits(base) if base else None
    if lim:
        res["limits"] = {"lower": lim[0], "upper": lim[1]}
    bars = []
    now_ts = krx.ts_of(now_utc)
    by_slot = today["bars"]
    top = max(by_slot) if by_slot else -1
    for k in sorted(by_slot):
        b = dict(by_slot[k])
        b["start_min"] = s["open"] + k * step
        b["end_min"] = b["start_min"] + step
        end_ts = krx.ts_of(krx.kst_to_utc(datetime.datetime.combine(
            datetime.date.fromisoformat(day), datetime.time(*divmod(b["end_min"], 60)))))
        if end_ts <= now_ts:
            if dt is None or dt >= end_ts or k < top or now_ts >= end_ts + SETTLE:
                bars.append(b)
            else:
                res["forming"] = {"bar_start": b["start"], "price": b["c"]}
        elif b["start_min"] <= mins_now:
            res["forming"] = {"bar_start": b["start"], "price": b["c"]}
    res["_bars"] = bars
    res["bars_closed"] = len(bars)
    if mins_now < s["open"]:
        res.update(phase="pre-open", reason="KRX opens at %s%s" % (krx.hm(s["open"]), (
            " and the %d-minute range is set at %s" % (or_minutes, krx.hm(or_end))) if setup == "orb" else ""))
        return res
    n_or = or_minutes // step
    or_bars = [b for b in bars if b["slot"] < n_or]
    avg_vol = (sum(b["v"] for b in or_bars) / float(len(or_bars))) if or_bars else None
    if or_bars:
        res["opening_range"] = {"high": max(b["h"] for b in or_bars), "low": min(b["l"] for b in or_bars),
                                "minutes": or_minutes, "bars": len(or_bars), "of": n_or,
                                "complete": mins_now >= or_end, "avg_volume": int(round(avg_vol))}
    if setup == "orb":
        if mins_now < or_end:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s"
                       % (len(or_bars), n_or, krx.hm(or_end)))
            return res
        if not or_bars:
            res.update(status="no-data", phase="open", reason="Yahoo sent no bars for the opening range")
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)
    res["level"] = lvl
    first = n_or if setup == "orb" else 0
    trig, thin = None, []
    for b in bars:
        if b["slot"] < first or b["end_min"] < before or b["end_min"] > cutoff:
            continue
        if b["c"] > lvl:
            vx = (b["v"] / avg_vol) if avg_vol else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": last["start"], "close": last["c"], "vs_level_pct": round(100.0 * (last["c"] / lvl - 1), 2)}
    if trig:
        vx = (trig["v"] / avg_vol) if avg_vol else None
        res.update(status="triggered", phase="open" if mins_now < s["cont_end"] else "after-close",
                   reason="the %s bar closed above %s" % (trig["start"], "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": krx.hm(trig["end_min"]), "close_time": krx.hm(trig["end_min"]),
                          "bar_start": trig["start"], "bar_index": trig["slot"], "price": trig["c"],
                          "volume": trig["v"], "volume_x": round(vx, 2) if vx else None,
                          "pct_over": round(100.0 * (trig["c"] / lvl - 1), 2)}
        res["minutes_since"] = int(mins_now - trig["end_min"])
        if base:
            move = trig["c"] / base - 1
            res["trigger"]["vs_prev_close_pct"] = round(100 * move, 2)
            if lim and trig["c"] >= lim[1] * 0.97:
                res["warnings"].append("the price is within 3 %% of the upper limit %s (상한가): a buy may not fill "
                                       "and the stock can lock at the limit" % krx.px(lim[1]))
            elif abs(move) >= 0.10:
                res["warnings"].append("the stock is %+.1f %% from yesterday's close: KRX's volatility interruption "
                                       "(VI) can pause it for two minutes around here" % (100 * move))
        return res
    if not bars:
        res.update(status="no-data", phase="open", reason="Yahoo sent no bars for %s yet" % day)
        return res
    what = ("close above the range high %s" if setup == "orb" else "close above %s") % krx.px(lvl)
    if mins_now >= cutoff:
        res.update(status="expired", phase="after-close" if mins_now >= s["cont_end"] else "after-cutoff",
                   reason="no %s by %s, the last time for a new entry" % (what, krx.hm(cutoff)))
        return res
    res["phase"] = "open"
    if last["c"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at %s but on %.2fx the range's volume, under your %.2fx" % (
            last["start"], krx.px(last["c"]), last["v"] / avg_vol if avg_vol else 0, min_volume_x)
    elif last["c"] > lvl and last["end_min"] < before:
        res["reason"] = "the %s bar closed above at %s, but no entries before %s (config)" % (
            last["start"], krx.px(last["c"]), krx.hm(before))
    else:
        gap = lvl - last["c"]
        res["reason"] = "last close %s (%s bar), %s won %s %s (%+.2f%%)" % (
            krx.px(last["c"]), last["start"], krx.px(abs(gap)), "under" if gap >= 0 else "over",
            "the range high %s" % krx.px(lvl) if setup == "orb" else "the level %s" % krx.px(lvl),
            100.0 * (last["c"] / lvl - 1))
    return res


def public(res):
    return {k: v for k, v in res.items() if not k.startswith("_")}


def entry_doc(res, now_utc):
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    return {"created_by": "krx-entry", "symbol": res["code"], "code": res["code"], "name": res.get("name"),
            "yahoo": res.get("yahoo"), "market": res.get("market"), "setup": res["setup"], "side": "buy",
            "date": res["date"], "status": res["status"], "time": t.get("time"), "bar_start": t.get("bar_start"),
            "bar_index": t.get("bar_index"), "price": t.get("price"), "level": res.get("level"),
            "interval": res["interval"], "prev_close": res.get("prev_close"),
            "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"]} if orr else None,
            "source": "yahoo", "checked_at": krx.iso_utc(now_utc), "reason": res.get("reason"),
            "warnings": res.get("warnings", [])}


LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}
LABEL_KO = {"triggered": "진입 신호", "waiting": "대기", "expired": "만료", "no-data": "데이터 없음"}
PHASE = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED"}


def describe(res, lang="en"):
    ko = lang == "ko"
    who = ("%s %s" % (res["name"], res["code"])) if res.get("name") else res["code"]
    setup = ("opening-range breakout (%d-minute range)" % res["or_minutes"] if res["setup"] == "orb"
             else "close above %s" % krx.px(res["level"] or 0))
    if ko:
        setup = ("시가 범위 돌파 (%d분)" % res["or_minutes"] if res["setup"] == "orb"
                 else "%s 위 마감" % krx.px(res["level"] or 0))
    out = ["%s  %s %s KST  %s, %s" % (who, res["now"][:10], res["now"][11:16], setup,
                                      "5분봉" if ko and res["interval"] == "5m" else
                                      "1분봉" if ko else res["interval"] + " bars (Yahoo, free, can lag)")]
    orr = res.get("opening_range")
    if orr:
        out.append(("시가 범위 %s-%s: 고가 %s, 저가 %s (%d/%d봉%s)." if ko else
                    "Opening range %s-%s: high %s, low %s (%d of %d bars%s).") % (
            res["opens"], krx.hm(krx.hhmm_min(res["opens"]) + orr["minutes"]), krx.px(orr["high"]),
            krx.px(orr["low"]), orr["bars"], orr["of"], "" if orr["complete"] else (", 형성 중" if ko else ", still forming")))
    t = res.get("trigger")
    if t:
        out.append(("진입 신호 %s, %s원: %s 봉(봉 %d)이 %s %s 위에서 마감 (%+.2f%%)." if ko else
                    "TRIGGERED %s at %s: the %s bar (bar %d) closed above %s %s (%+.2f%%).") % (
            t["time"], krx.px(t["price"]), t["bar_start"], t["bar_index"],
            ("시가 범위 고가" if ko else "the range high") if res["setup"] == "orb" else ("기준가" if ko else "the level"),
            krx.px(res["level"]), t["pct_over"]))
        if res.get("minutes_since", 0) > INTERVALS[res["interval"]] and res.get("last"):
            out.append(("%d분 전 신호입니다. 마지막 종가는 %s (%s 봉). 늦은 진입은 다른 거래입니다." if ko else
                        "That was %d minutes ago; the last close is %s (%s bar). A late entry is a different trade.")
                       % (res["minutes_since"], krx.px(res["last"]["close"]), res["last"]["bar_start"]))
        out.append("주문은 하지 않았습니다. 다음: 매수 전에 /krx-exit 로 손절가와 목표가를 정하세요." if ko else
                   "Nothing was placed. Next: set the stop and target BEFORE you buy: /krx-exit.")
    else:
        label = (LABEL_KO.get(res["status"]) if ko else PHASE.get(res["phase"]) or LABEL.get(res["status"]))
        out.append("%s  %s." % (label, res["reason"]))
        if res.get("forming") and res["status"] == "waiting":
            out.append("The %s bar is still forming (%s so far); it counts once it closes." % (
                res["forming"]["bar_start"], krx.px(res["forming"]["price"])))
        if res.get("next_open"):
            out.append("Next open: %s." % res["next_open"])
    for w in res.get("warnings") or []:
        out.append(("주의: " if ko else "Careful: ") + w + ".")
    if res.get("session_note"):
        out.append("Special hours today: %s." % res["session_note"])
    if not res.get("calendar_known"):
        out.append("The holiday list covers %s only: check krx.co.kr for this date." %
                   ", ".join(map(str, krx.CALENDAR_YEARS)))
    return out


def bar_line(b, res):
    orr = res.get("opening_range") or {}
    lvl = res.get("level")
    t = res.get("trigger")
    n_or = orr.get("of") or 0
    if res["setup"] == "orb" and b["slot"] < n_or:
        note = "opening range so far"
        if b["slot"] == n_or - 1 and orr:
            note = "opening range set: high %s, low %s" % (krx.px(orr["high"]), krx.px(orr["low"]))
    elif t and t["bar_index"] == b["slot"]:
        note = "TRIGGERED: closed above %s. Get in now if you take it: %s, %s" % (
            krx.px(lvl), t["time"], krx.px(t["price"]))
    elif lvl is None:
        note = ""
    elif b["c"] > lvl:
        note = "above %s" % krx.px(lvl)
    else:
        note = "%s under %s" % (krx.px(lvl - b["c"]), krx.px(lvl))
    return "%5s bar  closed %s  %s" % (b["start"], krx.px(b["c"]), note)


class SimClock(object):
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


def watch_loop(provider, clock, sleep, emit, opts, every=30, until=None, max_errors=5, on_trigger=None):
    """Poll bar by bar until a trigger (0), expiry, the close or --until (1)."""
    step = INTERVALS[opts.get("interval", "5m")] * 60
    seen, errors = set(), 0
    while True:
        now = clock()
        local = krx.to_kst(now)
        try:
            res = evaluate(provider(now), opts["info"], now, opts.get("setup", "orb"), opts.get("or_minutes", 15),
                           opts.get("interval", "5m"), opts.get("level"), opts.get("min_volume_x"), opts.get("cfg"))
            errors = 0
        except (EntryError, krx.KRXError) as e:
            errors += 1
            emit("error: %s" % e)
            if errors >= max_errors:
                return 1
            sleep(every)
            continue
        for b in res["_bars"]:
            if b["slot"] not in seen:
                seen.add(b["slot"])
                emit(bar_line(b, res))
        if res["status"] == "triggered":
            if on_trigger:
                on_trigger(res, now)
            emit("\n".join(describe(res, opts.get("lang", "en"))))
            return 0
        if res["status"] == "expired" or res["phase"] in ("closed", "after-close"):
            emit("\n".join(describe(res, opts.get("lang", "en"))))
            return 1
        mins = local.hour * 60 + local.minute
        if until is not None and mins >= until:
            emit("Stopped at %s (--until). No trigger." % krx.hm(until))
            return 1
        secs = local.minute * 60 + local.second
        wait = step - (secs % step) + GRACE
        sleep(min(every, wait) if res["phase"] == "open" else min(max(every, 60), wait))


def _cfg(a):
    try:
        return krx.load_config(a.config)
    except krx.KRXError as e:
        raise EntryError(str(e))


def _info(text):
    try:
        return krx.resolve(text)
    except krx.KRXError as e:
        raise EntryError(str(e))


def cmd_check(a):
    cfg = _cfg(a)
    now = krx.parse_now(a.now)
    info = _info(a.symbol)
    result = load_chart(info, a.interval, a.offline)
    info["name"] = info.get("name") or (result.get("meta") or {}).get("longName") or ""
    res = evaluate(result, info, now, a.setup, a.or_minutes, a.interval, a.level, a.min_volume_x, cfg)
    if a.write:
        krx.write_json(a.write, entry_doc(res, now))
    if a.json:
        print(json.dumps(public(res), indent=2, ensure_ascii=False))
    else:
        print("\n".join(describe(res, krx.lang_of(a.lang, cfg))))
        if a.write:
            print("Wrote %s." % a.write)
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = _cfg(a)
    info = _info(a.symbol)
    sim = a.now or a.offline
    clock = SimClock(krx.parse_now(a.now) if a.now else krx.utc_now()) if sim else None
    cache = {}

    def provider(now):
        if a.offline and "r" in cache:
            return cache["r"]
        cache["r"] = load_chart(info, a.interval, a.offline)
        return cache["r"]

    def on_trigger(res, now):
        if a.write:
            krx.write_json(a.write, entry_doc(res, now))
    opts = {"info": info, "setup": a.setup, "or_minutes": a.or_minutes, "interval": a.interval, "level": a.level,
            "min_volume_x": a.min_volume_x, "cfg": cfg, "lang": krx.lang_of(a.lang, cfg)}
    until = krx.hhmm_min(a.until, "--until") if a.until else None
    return watch_loop(provider, clock.now if clock else krx.utc_now, clock.sleep if clock else time.sleep,
                      print, opts, a.every, until, on_trigger=on_trigger if a.write else None)


def cmd_watchlist(a):
    cfg = _cfg(a)
    now = krx.parse_now(a.now)
    doc = krx.read_json(a.watchlist, None)
    if not doc:
        raise EntryError("no watchlist at %s: run /krx-scanner first, or list codes in it" % a.watchlist)
    rows = doc.get("symbols") or doc.get("watchlist") or doc if isinstance(doc, (dict, list)) else []
    syms = [r.get("code") or r.get("symbol") if isinstance(r, dict) else r for r in rows]
    hits = 0
    for i, sym in enumerate(syms):
        if i and not a.offline:
            krx.SLEEP(krx.PAUSE)
        try:
            info = _info(sym)
            res = evaluate(load_chart(info, a.interval, a.offline), info, now, a.setup, a.or_minutes, a.interval,
                           None, a.min_volume_x, cfg)
        except EntryError as e:
            print("%-8s error: %s" % (sym, e))
            continue
        t = res.get("trigger")
        if t:
            hits += 1
            print("%-8s TRIGGERED %s at %s (%+.2f%% over %s)" % (res["code"], t["time"], krx.px(t["price"]),
                                                                t["pct_over"], krx.px(res["level"])))
        else:
            print("%-8s %-10s %s" % (res["code"], LABEL.get(res["status"]), res["reason"]))
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry Timer (krx-entry): the moment to get in.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, sym=True):
        if sym:
            p.add_argument("symbol", help="6-digit code (005930), 005930.KS, or a name once the universe is built")
        p.add_argument("--setup", default="orb", choices=["orb", "level"])
        p.add_argument("--level", type=float)
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--interval", default="5m", choices=["5m", "1m"])
        p.add_argument("--min-volume-x", type=float)
        p.add_argument("--offline", metavar="PATH")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 09:45" Seoul)')
        p.add_argument("--config", default=krx.CONFIG_PATH)
        p.add_argument("--lang", choices=["en", "ko"])

    p = sub.add_parser("check")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="krx/entry.json", metavar="PATH")
    p = sub.add_parser("watch")
    common(p)
    p.add_argument("--every", type=int, default=30)
    p.add_argument("--until", help="stop watching at this Seoul time")
    p.add_argument("--write", nargs="?", const="krx/entry.json", metavar="PATH")
    p = sub.add_parser("watchlist")
    common(p, sym=False)
    p.add_argument("--watchlist", default="krx/watchlist.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except (EntryError, krx.KRXError) as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
