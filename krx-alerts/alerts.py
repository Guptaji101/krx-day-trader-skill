#!/usr/bin/env python3
"""Price alerts and the KRX clock for krx-alerts (Korean market). Standard library only.

    python3 alerts.py clock                          # is KRX open? next open, the closing auction
    python3 alerts.py quote 005930 --write           # quotes -> krx/prices.json
    python3 alerts.py quote --holdings --write       # every code in your paper portfolio and plan
    python3 alerts.py add --from-plan                # stop, target and flat-by from krx/plan.json
    python3 alerts.py add --symbol 005930 --stop 97900
    python3 alerts.py add --symbol 005930 --target 99500
    python3 alerts.py add --symbol 247540 --pct 5
    python3 alerts.py add --symbol 196170 --near-limit 3     # within 3 % of 상한가 or 하한가
    python3 alerts.py add --at 15:15 --label "flat by" --symbol 005930
    python3 alerts.py add --opening-bell
    python3 alerts.py check                          # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py list
    python3 alerts.py reset 005930-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        the price moved "pct" percent or more from yesterday's close
  near_limit      the price is within "pct" percent of today's upper (상한가) or lower
                  (하한가) price limit, +-30 % of yesterday's close. Near the upper limit a
                  buy may not fill; a locked limit-down day can trap a seller.
  opening_bell    KRX opened today (09:00, or 10:00 on a late-open day)
  time            the Seoul clock passed "at" (HH:MM) on a trading day; on a late-open
                  day (수능) the time moves later with the session
A rule with "date" is watched on that day only (rules from a plan are). A price
alert rings ONCE until you reset it, unless "rearm": true.

Quotes: Yahoo's free chart JSON for 005930.KS / 247540.KQ (unofficial, can lag
by minutes; for alerts only, never for timing an order). One request per code,
a pause between. krx/prices.json also feeds krx-risk (price limits) and
krx-broker (paper stops and targets). --offline DIR reads DIR/<CODE>.json.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

TYPES = {"above", "below", "stop", "target", "pct_move", "near_limit", "opening_bell", "time"}
NO_QUOTE = {"opening_bell", "time"}
RULES = "krx/alerts.json"
STATE = "krx/state/alerts.json"
PRICES = "krx/prices.json"


class AlertError(Exception):
    pass


def quote_from(result):
    """A Yahoo chart result -> {price, prev_close, open, high, low, time} for today."""
    meta = result.get("meta") or {}
    bars = krx.raw_bars(result)
    if not bars and meta.get("regularMarketPrice") is None:
        raise AlertError("no price in the data")
    last_day = krx.to_kst(krx.utc_of(bars[-1][0])).date() if bars else None
    today = [b for b in bars if krx.to_kst(krx.utc_of(b[0])).date() == last_day]
    before = [b for b in bars if last_day and krx.to_kst(krx.utc_of(b[0])).date() < last_day]
    price = meta.get("regularMarketPrice") or (today[-1][4] if today else None)
    prev = before[-1][4] if before else meta.get("chartPreviousClose") or meta.get("previousClose")
    t = meta.get("regularMarketTime") or (bars[-1][0] if bars else None)
    q = {"price": float(price), "prev_close": float(prev) if prev else None,
         "open": today[0][1] if today else None, "high": max(b[2] for b in today) if today else None,
         "low": min(b[3] for b in today) if today else None,
         "time": krx.iso_kst(krx.utc_of(t)) if t else None, "currency": meta.get("currency", "KRW")}
    if q["prev_close"]:
        lo, hi = krx.price_limits(q["prev_close"])
        q["limits"] = {"lower": lo, "upper": hi}
    return q


def get_quotes(codes, offline=None):
    quotes, errors = {}, {}
    for i, raw in enumerate(codes):
        try:
            info = krx.resolve(raw)
            if offline:
                text = krx.read_offline(offline, info, suffix="")
            else:
                if i:
                    krx.SLEEP(krx.PAUSE)
                text = krx.fetch_chart(info, "5d", "5m")
            q = quote_from(krx.chart_result(text, info["code"]))
            q["name"] = info.get("name", "")
            quotes[info["code"]] = q
        except (krx.KRXError, AlertError) as e:
            errors[str(raw)] = str(e)
    return quotes, errors


def validate(r):
    t = r.get("type")
    if t not in TYPES:
        raise AlertError("rule %s: unknown type %r" % (r.get("id"), t))
    if t not in NO_QUOTE and not r.get("symbol"):
        raise AlertError("rule %s needs a symbol" % r.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(r.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % r.get("id"))
    if t in ("pct_move", "near_limit") and not isinstance(r.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % r.get("id"))
    if t == "time":
        krx.hhmm_min(r.get("at"), "rule %s at" % r.get("id"))


def rang_today(stamp, today):
    return bool(stamp) and stamp[:10] == today


def evaluate(rules, quotes, status, state, now_utc):
    """Pure. Returns (rings, new_state)."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    stamp = krx.iso_kst(now_utc)
    mins = status["minutes"]
    s = krx.session(today)
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (r.get("symbol", "market"), r["type"])
        if r.get("date") and str(r["date"]) != today:
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            if status["phase"] in ("open", "closing-auction") and not rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": "", "message": "Opening bell: KRX is open (%s KST)%s." % (
                    status["now_kst"][11:], (", " + s["note"]) if s and s["note"] else "")})
                rung[rid] = stamp
            continue
        if typ == "time":
            if s is None:
                continue
            at = krx.hhmm_min(r["at"]) + (s["open"] - krx.OPEN_MIN)
            if mins >= at and not rang_today(rung.get(rid), today):
                code = str(r.get("symbol") or "")
                msg = "%s: it is %s KST (set for %s)." % ((r.get("label") or "time alert").capitalize(),
                                                        status["now_kst"][11:], krx.hm(at))
                if code:
                    msg += " If %s is still open, close it now, before the 15:20 closing auction (paper: " \
                           "ticket.py flatten)." % code
                rings.append({"id": rid, "symbol": code, "message": msg})
                rung[rid] = stamp
            continue
        code = krx.resolve(r["symbol"])["code"]
        q = quotes.get(code)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]   # the latest price only
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                rings.append({"id": rid, "symbol": code, "message": "%s %s %s: %s your %s %s%s." % (
                    code, "is" if status["phase"] == "open" else "last", krx.px(p), word, label, krx.px(r["price"]),
                    (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            if abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0)) \
                    and not rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": code, "message": "%s moved %+.2f%% to %s (from %s)." % (
                    code, move, krx.px(p), krx.px(ref))})
                rung[rid] = stamp
        elif typ == "near_limit":
            lim = q.get("limits")
            if not lim:
                continue
            up = 100.0 * (lim["upper"] - p) / lim["upper"]
            dn = 100.0 * (p - lim["lower"]) / lim["lower"]
            side = "upper limit (상한가) %s" % krx.px(lim["upper"]) if up <= r["pct"] else \
                "lower limit (하한가) %s" % krx.px(lim["lower"]) if dn <= r["pct"] else None
            if side and not rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": code, "message": "%s at %s is within %g%% of its %s." % (
                    code, krx.px(p), r["pct"], side)})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def rules_from_plan(plan, cfg_flat_by=None):
    if not isinstance(plan, dict) or not (plan.get("code") or plan.get("symbol")):
        raise AlertError("the plan has no code (run krx-exit first)")
    code = krx.resolve(plan.get("code") or plan.get("symbol"))["code"]
    out = []
    for key in ("stop", "target"):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s (run krx-exit)" % key)
        out.append({"id": "%s-plan-%s" % (code, key), "symbol": code, "type": key, "price": float(v),
                    "note": "plan " + key})
    out.append({"id": "%s-flat-by" % code, "symbol": code, "type": "time",
                "at": plan.get("flat_by") or cfg_flat_by or "15:15", "label": "flat by"})
    out.append({"id": "%s-near-limit" % code, "symbol": code, "type": "near_limit", "pct": 3})
    if plan.get("date"):
        for r in out:
            r["date"] = str(plan["date"])
    return out


def load_rules(path):
    doc = krx.read_json(path, {"rules": []}) or {"rules": []}
    return doc.get("rules", []) if isinstance(doc, dict) else doc


def save_rules(path, rules):
    krx.write_json(path, {"rules": rules})


def holdings(a):
    codes = []
    pf = krx.read_json(a.portfolio, {}) or {}
    codes += list((pf.get("positions") or {}).keys())
    plan = krx.read_json(a.plan, {}) or {}
    if plan.get("code") or plan.get("symbol"):
        codes.append(plan.get("code") or plan.get("symbol"))
    for r in load_rules(a.rules):
        if r.get("symbol"):
            codes.append(r["symbol"])
    out = []
    for c in codes:
        c = str(c)
        if c not in out:
            out.append(c)
    return out


def write_prices(path, quotes, now_utc):
    doc = krx.read_json(path, {}) or {}
    prices = doc.get("prices", {}) if isinstance(doc, dict) else {}
    prices.update(quotes)
    krx.write_json(path, {"updated_at": krx.iso_utc(now_utc), "source": "yahoo", "prices": prices,
                          "note": "Free, delayed, unofficial quotes. For alerts and paper trading only."})


def cmd_clock(a):
    st = krx.market_status(krx.parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    words = {"pre-open": "not open yet (opens %s)" % st.get("opens"),
             "opening-auction": "in the opening auction (08:30-09:00), opens %s" % st.get("opens"),
             "open": "OPEN, continuous trading until %s, closing auction to %s" % (st.get("auction_at"), st.get("closes")),
             "closing-auction": "in the closing auction (single price at %s)" % st.get("closes"),
             "closed": "CLOSED (%s), next open %s" % (st.get("reason"), st.get("next_open"))}
    print("KRX %s KST: %s." % (st["now_kst"], words[st["phase"]]))
    if st.get("session", {}).get("note"):
        print("Special hours today: %s." % st["session"]["note"])
    if not st["calendar_known"]:
        print("The holiday list covers %s only: add this year's to \"extra_holidays\" in krx/config.json." %
              ", ".join(map(str, krx.CALENDAR_YEARS)))
    return 0 if st["phase"] == "open" else 1


def cmd_quote(a):
    now = krx.parse_now(a.now)
    codes = list(a.symbols or [])
    if a.holdings:
        codes += holdings(a)
    if not codes:
        raise AlertError("give codes (quote 005930 000660) or --holdings")
    quotes, errors = get_quotes(codes, a.offline)
    for c, q in quotes.items():
        lim = q.get("limits") or {}
        chg = (100.0 * (q["price"] / q["prev_close"] - 1)) if q.get("prev_close") else None
        print("%s %-16s %10s  %s  day %s-%s  limits %s-%s  (%s)" % (
            c, (q.get("name") or "")[:16], krx.px(q["price"]), ("%+.2f%%" % chg) if chg is not None else "",
            krx.px(q.get("low")), krx.px(q.get("high")), krx.px(lim.get("lower")), krx.px(lim.get("upper")),
            (q.get("time") or "")[11:16]))
    for c, e in errors.items():
        print("%s: %s" % (c, e))
    if a.write and quotes:
        write_prices(a.prices, quotes, now)
        print("Wrote %s." % a.prices)
    return 0 if quotes else 1


def cmd_add(a):
    rules = load_rules(a.rules)
    new = []
    cfg = krx.load_config(a.config)
    if a.from_plan:
        plan = krx.read_json(a.plan)
        new += rules_from_plan(plan, cfg.get("flat_by"))
    if a.opening_bell:
        new.append({"id": "opening-bell", "type": "opening_bell"})
    if a.at:
        new.append({"id": "%s-time-%s" % (a.symbol or "market", a.at.replace(":", "")), "type": "time", "at": a.at,
                    "label": a.label or "time alert", "symbol": krx.resolve(a.symbol)["code"] if a.symbol else ""})
    if a.symbol and not a.at:
        code = krx.resolve(a.symbol)["code"]
        for key, val in (("stop", a.stop), ("target", a.target), ("above", a.above), ("below", a.below)):
            if val is not None:
                new.append({"id": "%s-%s-%s" % (code, key, int(val)), "symbol": code, "type": key, "price": float(val)})
        if a.pct is not None:
            new.append({"id": "%s-pct-%g" % (code, a.pct), "symbol": code, "type": "pct_move", "pct": a.pct})
        if a.near_limit is not None:
            new.append({"id": "%s-near-limit" % code, "symbol": code, "type": "near_limit", "pct": a.near_limit})
    if not new:
        raise AlertError("nothing to add: --from-plan, --opening-bell, --at, or --symbol with a level")
    for r in new:
        validate(r)
    ids = {r["id"] for r in new}
    rules = [r for r in rules if r.get("id") not in ids] + new
    save_rules(a.rules, rules)
    for r in new:
        print("Added %s" % r["id"])
    return 0


def cmd_check(a):
    now = krx.parse_now(a.now)
    rules = load_rules(a.rules)
    if not rules:
        print("No alerts set: alerts.py add --from-plan")
        return 1
    status = krx.market_status(now)
    codes = sorted({krx.resolve(r["symbol"])["code"] for r in rules
                    if r.get("type") not in NO_QUOTE and r.get("symbol")
                    and (not r.get("date") or r["date"] == status["date"])})
    quotes, errors = get_quotes(codes, a.offline) if codes else ({}, {})
    rings, state = evaluate(rules, quotes, status, krx.read_json(a.state, {}), now)
    krx.write_json(a.state, state)
    if quotes and a.write:
        write_prices(a.prices, quotes, now)
    for r in rings:
        print("RING  " + r["message"])
    for c, e in errors.items():
        print("%s: no quote (%s)" % (c, e))
    if not rings:
        print("Nothing rang (%s KST, %d rule%s)." % (status["now_kst"][11:], len(rules), "" if len(rules) == 1 else "s"))
    return 0 if rings else 1


def cmd_list(a):
    rules = load_rules(a.rules)
    rung = (krx.read_json(a.state, {}) or {}).get("rung", {})
    for r in rules:
        lvl = krx.px(r["price"]) if "price" in r else ("%g%%" % r["pct"]) if "pct" in r else r.get("at", "")
        print("%-28s %-12s %-8s %-10s %s%s" % (r.get("id"), r["type"], r.get("symbol", ""), lvl, r.get("date", ""),
                                             "  (rang %s)" % rung[r["id"]][:16] if r.get("id") in rung else ""))
    if not rules:
        print("No alerts set.")
    return 0


def cmd_reset(a):
    state = krx.read_json(a.state, {}) or {}
    rung = state.setdefault("rung", {})
    if a.id == "all":
        rung.clear()
    elif a.id in rung:
        del rung[a.id]
    else:
        raise AlertError("no rung alert %r" % a.id)
    krx.write_json(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price alerts and the KRX clock (krx-alerts).")
    ap.add_argument("--rules", default=RULES)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--prices", default=PRICES)
    ap.add_argument("--plan", default="krx/plan.json")
    ap.add_argument("--portfolio", default="krx/portfolio.json")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--now")
    ap.add_argument("--offline", metavar="DIR")
    sub = ap.add_subparsers(dest="cmd")
    shared = argparse.ArgumentParser(add_help=False)
    for flag in ("--rules", "--state", "--prices", "--plan", "--portfolio", "--config", "--now", "--offline"):
        shared.add_argument(flag, default=argparse.SUPPRESS)
    _add = sub.add_parser
    sub.add_parser = lambda name, **kw: _add(name, parents=[shared], **kw)
    p = sub.add_parser("clock")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("--from-plan", action="store_true")
    p.add_argument("--symbol")
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--above", type=float)
    p.add_argument("--below", type=float)
    p.add_argument("--pct", type=float)
    p.add_argument("--near-limit", type=float)
    p.add_argument("--at")
    p.add_argument("--label")
    p.add_argument("--opening-bell", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--write", action="store_true", help="also save the quotes to krx/prices.json")
    sub.add_parser("list")
    p = sub.add_parser("reset")
    p.add_argument("id")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "add": cmd_add, "check": cmd_check, "list": cmd_list,
                "reset": cmd_reset}[a.cmd](a)
    except (AlertError, krx.KRXError) as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
