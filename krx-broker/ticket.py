#!/usr/bin/env python3
"""Paper Order Executor for krx-broker (Korean market). Standard library only.

PAPER ONLY. This tool never connects to a broker and cannot send a real order.
It keeps a practice account in krx/portfolio.json (cash in won, positions) and a
log in krx/state/broker.json, with Korean costs: commission both ways and the
0.20 % sell tax (2026) on every sale.

    python3 ticket.py show                               # every ticket, READY or not
    python3 ticket.py approve --index 1 --reply "yes"     # your yes -> a paper fill
    python3 ticket.py decline --index 1
    python3 ticket.py check                              # stop / target / flat-by against krx/prices.json
    python3 ticket.py close --code 005930 --price 99500  # sell a paper position at a price
    python3 ticket.py flatten                            # close everything at the latest prices
    python3 ticket.py portfolio                          # cash, positions, today's P&L
    python3 ticket.py log
    python3 ticket.py reset --cash 5000000               # start the practice account again

An order reaches the paper account only after two gates, every time:
  1. krx-risk passed this exact order today (matched by fingerprint), and
  2. you typed an explicit yes for this one order. "yes" or "yes, buy 30 005930"
     counts; "ok", "sure", "yes all", a question or a yes with a doubt does not.
The paper fill is at the limit price (or the plan's entry for a market order). A real
limit order fills only if the price trades there, and a real market order can fill
worse: paper results flatter you. Positions are checked against their stop and target
with `check` (prices from krx-alerts: alerts.py quote --write).
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

YES_WORDS = ("yes", "y", "네", "예", "응")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each", "전부", "모두", "다"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later", "if", "unless",
               "아니", "잠깐", "취소", "나중", "근데", "만약"}
STATE = "krx/state/broker.json"
PORTFOLIO = "krx/portfolio.json"


class TicketError(Exception):
    pass


def fingerprint(o):
    keys = ("code", "side", "qty", "type", "limit_price", "take_profit_price", "stop_loss_price")
    raw = json.dumps({k: o.get(k) for k in keys}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def plain(o):
    how = ("limit %s" % krx.px(o["limit_price"])) if o.get("type") == "limit" else "market"
    s = "%s %s %s shares %s" % (o.get("side"), o.get("code"), "{:,}".format(int(o.get("qty") or 0)), how)
    if o.get("stop_loss_price"):
        s += ", stop-loss %s, take-profit %s" % (krx.px(o["stop_loss_price"]), krx.px(o.get("take_profit_price")))
    return s


def parse_yes(reply, order, batch_codes=()):
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    low = raw.lower()
    words = re.findall(r"[a-z']+|[가-힣]+", low)
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" (or 네 / 예) counts (not ok, sure, go, 좋아)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if any(w.startswith(d) for w in words[1:] for d in DOUBT_WORDS):
        return False, "the reply has a condition or a doubt in it; ask again"
    code = str(order.get("code"))
    codes = set(re.findall(r"\b[0-9][0-9A-Z]{5}\b", raw.upper()))
    others = (codes | set(batch_codes)) - {code}
    named = codes & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), code)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)
            if n.replace(",", "") != code]
    if nums:
        want = [float(order.get(k)) for k in ("qty", "limit_price", "stop_loss_price", "take_profit_price")
                if order.get(k) is not None]
        if not any(abs(n - w) < 0.5 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (", ".join("%g" % n for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


def load(path, default):
    v = krx.read_json(path, None)
    return v if v is not None else default


def portfolio(path, cfg):
    pf = load(path, None)
    if not isinstance(pf, dict):
        pf = {"created_by": "krx-broker", "paper": True, "cash": float(cfg.get("budget") or 5000000),
              "start_cash": float(cfg.get("budget") or 5000000), "positions": {}}
    pf.setdefault("positions", {})
    return pf


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "krx-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv["verdict"] != "PASS":
        return False, "BLOCKED by krx-risk: " + "; ".join(rv["reasons"]), rv
    for rec in reversed(state.get("orders", [])):
        if rec.get("fingerprint") == fp and str(rec.get("at", ""))[:10] == today and \
                rec.get("status") in ("filled", "declined"):
            return False, "DONE: %s at %s" % (rec["status"], rec["at"][11:16]), rv
    return True, "READY: krx-risk PASS, waiting for your yes", rv


def fill_buy(pf, order, price, now_kst, cfg, max_loss):
    qty = int(order["qty"])
    comm = float(cfg.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    fee = round(qty * price * comm / 100.0)
    cost = qty * price + fee
    if cost > pf["cash"] + 0.5:
        raise TicketError("paper cash %s is short of %s: no margin" % (krx.won(pf["cash"]), krx.won(cost)))
    pf["cash"] = round(pf["cash"] - cost)
    code = order["code"]
    p = pf["positions"].get(code)
    plan = order.get("plan") or {}
    if p:
        tot = p["qty"] + qty
        p["avg"] = (p["avg"] * p["qty"] + price * qty) / tot
        p["qty"] = tot
    else:
        pf["positions"][code] = {"qty": qty, "avg": price, "name": order.get("name", ""),
                                 "stop": order.get("stop_loss_price"), "target": order.get("take_profit_price"),
                                 "flat_by": plan.get("flat_by"), "opened": now_kst, "buy_fees": fee,
                                 "max_loss": max_loss}
    return fee


def fill_sell(pf, code, qty, price, now_utc, cfg, reason):
    p = pf["positions"].get(code)
    if not p or qty > p["qty"]:
        raise TicketError("no paper position of %s shares in %s to sell (short sales are off)" % (qty, code))
    year = int(krx.to_kst(now_utc).date().isoformat()[:4])
    comm = float(cfg.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    gross = qty * price
    fee = round(gross * comm / 100.0)
    tax = round(gross * krx.sell_tax_rate(year))
    pf["cash"] = round(pf["cash"] + gross - fee - tax)
    buy_fee_share = round(p.get("buy_fees", 0) * qty / p["qty"])
    pnl = round((price - p["avg"]) * qty - fee - tax - buy_fee_share)
    p["qty"] -= qty
    p["buy_fees"] = p.get("buy_fees", 0) - buy_fee_share
    if p["qty"] <= 0:
        del pf["positions"][code]
    return {"code": code, "qty": qty, "price": price, "avg": round(p["avg"], 1), "fee": fee, "tax": tax,
            "pnl": pnl, "reason": reason, "at": krx.iso_kst(now_utc)}


def latest_prices(path):
    doc = krx.read_json(path, {}) or {}
    return doc.get("prices", doc) if isinstance(doc, dict) else {}


# ---------------------------------------------------------------- commands

def ctx(a):
    cfg = krx.load_config(a.config)
    if not cfg.get("paper", True):
        raise TicketError("this pack is paper only; set \"paper\": true in krx/config.json")
    now = krx.parse_now(a.now)
    today = krx.to_kst(now).date().isoformat()
    doc = load(a.orders, {}) or {}
    orders = doc.get("orders", []) if isinstance(doc, dict) else doc
    return cfg, now, today, orders, load(a.risk, None), load(a.state, {"orders": [], "closed": []}), \
        portfolio(a.portfolio, cfg)


def cmd_show(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    if not orders:
        print("No orders at %s: run /krx-budget --write first." % a.orders)
        return 1
    ready = 0
    for i, o in enumerate(orders, 1):
        ok, label, rv = status_of(o, risk, state, today)
        ready += ok
        print("ORDER %d of %d, PAPER account (모의)" % (i, len(orders)))
        print("  %s%s" % (plain(o), (" (%s)" % o["name"]) if o.get("name") else ""))
        if rv and rv.get("max_loss"):
            print("  Max loss at the stop: %s, costs included" % krx.won(rv["max_loss"]))
        if o.get("why"):
            print("  Why: %s" % o["why"])
        print("  %s" % label)
    print("Paper cash: %s. Reply \"yes\" for one order at a time (approve --index N --reply ...)." % krx.won(pf["cash"]))
    return 0 if ready else 1


def cmd_approve(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    if not 1 <= a.index <= len(orders):
        raise TicketError("no order %d (there are %d)" % (a.index, len(orders)))
    o = orders[a.index - 1]
    ok, label, rv = status_of(o, risk, state, today)
    if not ok:
        raise TicketError(label)
    yes, why = parse_yes(a.reply, o, [x.get("code") for x in orders])
    fp = fingerprint(o)
    if not yes:
        state.setdefault("orders", []).append({"fingerprint": fp, "status": "not-a-yes", "reply": a.reply,
                                               "at": krx.iso_kst(now), "why": why})
        krx.write_json(a.state, state)
        print("Not a yes: %s. Nothing happened." % why)
        return 1
    price = float(o.get("limit_price") or (o.get("plan") or {}).get("entry") or 0)
    if o["side"] == "buy":
        fee = fill_buy(pf, o, price, krx.iso_kst(now), cfg, rv.get("max_loss"))
        rec = {"fingerprint": fp, "status": "filled", "kind": "entry", "code": o["code"], "side": "buy",
               "qty": int(o["qty"]), "price": price, "fee": fee, "max_loss": rv.get("max_loss"),
               "reply": a.reply, "at": krx.iso_kst(now), "paper": True}
    else:
        t = fill_sell(pf, o["code"], int(o["qty"]), price, now, cfg, "order")
        state.setdefault("closed", []).append(t)
        rec = {"fingerprint": fp, "status": "filled", "kind": "exit", "code": o["code"], "side": "sell",
               "qty": int(o["qty"]), "price": price, "reply": a.reply, "at": krx.iso_kst(now), "paper": True}
    state.setdefault("orders", []).append(rec)
    krx.write_json(a.state, state)
    krx.write_json(a.portfolio, pf)
    print("PAPER FILL (모의 체결): %s at %s. Your reply recorded word for word: %r." % (plain(o), krx.px(price), a.reply))
    print("Paper cash now %s. Stop %s and target %s are watched by `ticket.py check`; flat by %s." % (
        krx.won(pf["cash"]), krx.px(o.get("stop_loss_price")), krx.px(o.get("take_profit_price")),
        (o.get("plan") or {}).get("flat_by") or cfg.get("flat_by")))
    print("A real limit order fills only if the price trades at %s: paper fills flatter you." % krx.px(price))
    return 0


def cmd_decline(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    o = orders[a.index - 1]
    state.setdefault("orders", []).append({"fingerprint": fingerprint(o), "status": "declined", "code": o.get("code"),
                                           "at": krx.iso_kst(now)})
    krx.write_json(a.state, state)
    print("Declined order %d: %s. Nothing happened." % (a.index, plain(o)))
    return 0


def close_position(a, cfg, now, state, pf, code, price, reason):
    p = pf["positions"].get(code)
    t = fill_sell(pf, code, int(p["qty"]), price, now, cfg, reason)
    state.setdefault("closed", []).append(t)
    for r in state.get("orders", []):
        if r.get("code") == code and r.get("kind") == "entry" and not r.get("closed"):
            r["closed"] = t["at"]
    print("PAPER EXIT %s: sold %s %s at %s (%s). Net %s after %s commission and %s sell tax." % (
        reason, "{:,}".format(t["qty"]), code, krx.px(price), reason, krx.won(t["pnl"], True), krx.won(t["fee"]),
        krx.won(t["tax"])))


def cmd_close(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    code = krx.resolve(a.code)["code"]
    if code not in pf["positions"]:
        raise TicketError("no paper position in %s" % code)
    price = krx.num(a.price, "--price")
    if not krx.on_grid(price):
        raise TicketError("%s is not on the KRX tick grid" % krx.px(price))
    close_position(a, cfg, now, state, pf, code, price, a.reason)
    krx.write_json(a.state, state)
    krx.write_json(a.portfolio, pf)
    return 0


def cmd_check(a, flatten=False):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    prices = latest_prices(a.prices)
    local = krx.to_kst(now)
    mins = local.hour * 60 + local.minute
    if not pf["positions"]:
        print("No open paper positions.")
        return 0
    acted = 0
    for code in list(pf["positions"]):
        p = pf["positions"][code]
        q = prices.get(code) or {}
        last = q.get("price")
        if last is None:
            print("%s: no price in %s (run alerts.py quote %s --write)" % (code, a.prices, code))
            continue
        last = float(last)
        reason = None
        if p.get("stop") and last <= p["stop"]:
            # a stop becomes a sell at the market: at the stop, or worse after a gap through it
            reason, px = "stop", krx.round_tick(min(float(p["stop"]), last), "down")
        elif p.get("target") and last >= p["target"]:
            reason, px = "target", float(p["target"])   # a resting limit sell fills at its price
        elif flatten or (p.get("flat_by") and mins >= krx.hhmm_min(p["flat_by"])):
            reason, px = "flat-by" if not flatten else "flatten", krx.round_tick(last, "down")
        if reason:
            close_position(a, cfg, now, state, pf, code, px, reason)
            acted += 1
        else:
            p["last"] = float(last)
            print("%s: holding %s at avg %s, last %s (stop %s, target %s), unrealized %s before costs." % (
                code, "{:,}".format(p["qty"]), krx.px(p["avg"]), krx.px(last), krx.px(p.get("stop")),
                krx.px(p.get("target")), krx.won((float(last) - p["avg"]) * p["qty"], True)))
    krx.write_json(a.state, state)
    krx.write_json(a.portfolio, pf)
    return 0 if acted or pf["positions"] else 1


def cmd_portfolio(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    pos_val = sum(p["qty"] * float(p.get("last") or p["avg"]) for p in pf["positions"].values())
    closed = [t for t in state.get("closed", []) if t["at"][:10] == today]
    print("PAPER account (모의투자): cash %s, positions %s, total %s (started with %s)." % (
        krx.won(pf["cash"]), krx.won(pos_val), krx.won(pf["cash"] + pos_val), krx.won(pf.get("start_cash", 0))))
    for c, p in pf["positions"].items():
        print("  %s %s: %s shares at avg %s, stop %s, target %s" % (c, p.get("name", ""), "{:,}".format(p["qty"]),
                                                                   krx.px(p["avg"]), krx.px(p.get("stop")),
                                                                   krx.px(p.get("target"))))
    print("Today: %d closed trade(s), net %s after costs." % (len(closed), krx.won(sum(t["pnl"] for t in closed), True)))
    return 0


def cmd_log(a):
    cfg, now, today, orders, risk, state, pf = ctx(a)
    rows = state.get("closed", [])
    if not rows:
        print("No closed paper trades yet.")
        return 0
    wins = [t for t in rows if t["pnl"] > 0]
    for t in rows[-a.last:]:
        print("%s  %s %5s sh  avg %s -> %s  %-8s net %s (fee %s, tax %s)" % (
            t["at"][:16].replace("T", " "), t["code"], "{:,}".format(t["qty"]), krx.px(t["avg"]), krx.px(t["price"]),
            t["reason"], krx.won(t["pnl"], True), krx.won(t["fee"]), krx.won(t["tax"])))
    print("%d trades, %d winners (%.0f %%), net %s after costs. Paper results flatter real fills." % (
        len(rows), len(wins), 100.0 * len(wins) / len(rows), krx.won(sum(t["pnl"] for t in rows), True)))
    return 0


def cmd_reset(a):
    cfg = krx.load_config(a.config)
    cash = a.cash or float(cfg.get("budget") or 5000000)
    krx.write_json(a.portfolio, {"created_by": "krx-broker", "paper": True, "cash": cash, "start_cash": cash,
                                 "positions": {}})
    krx.write_json(a.state, {"orders": [], "closed": []})
    print("Paper account reset: %s cash, no positions, empty log." % krx.won(cash))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper Order Executor (krx-broker): practice trades, never real ones.")
    ap.add_argument("--orders", default="krx/orders.json")
    ap.add_argument("--risk", default="krx/state/risk.json")
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--portfolio", default=PORTFOLIO)
    ap.add_argument("--prices", default="krx/prices.json")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--now")
    sub = ap.add_subparsers(dest="cmd")
    shared = argparse.ArgumentParser(add_help=False)
    for flag in ("--orders", "--risk", "--state", "--portfolio", "--prices", "--config", "--now"):
        shared.add_argument(flag, default=argparse.SUPPRESS)
    _add = sub.add_parser
    sub.add_parser = lambda name, **kw: _add(name, parents=[shared], **kw)
    sub.add_parser("show")
    p = sub.add_parser("approve")
    p.add_argument("--index", type=int, default=1)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline")
    p.add_argument("--index", type=int, default=1)
    sub.add_parser("check")
    sub.add_parser("flatten")
    p = sub.add_parser("close")
    p.add_argument("--code", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--reason", default="manual")
    sub.add_parser("portfolio")
    p = sub.add_parser("log")
    p.add_argument("--last", type=int, default=30)
    p = sub.add_parser("reset")
    p.add_argument("--cash", type=float)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        if a.cmd == "flatten":
            return cmd_check(a, flatten=True)
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "check": cmd_check,
                "close": cmd_close, "portfolio": cmd_portfolio, "log": cmd_log, "reset": cmd_reset}[a.cmd](a)
    except (TicketError, krx.KRXError) as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
