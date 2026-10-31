#!/usr/bin/env python3
"""Risk Manager for krx-risk (Korean market): checks every proposed order against
your limits and blocks anything over them. Standard library only. It never places
an order. PASS or BLOCK per order, with the reason.

    python3 check.py                          # krx/orders.json against krx/config.json
    python3 check.py --orders FILE --json
    python3 check.py --lang ko

What it checks (an entry is a buy; an exit is a sell of shares you hold):
  format      a 6-character KRX code, buy or sell, whole shares, limit or market,
              every price on the KRX tick grid, a stop-loss on every entry
              (require_stop), a bracket's take-profit above and stop below the entry
  paper       this pack is paper only: "paper": false is blocked
  short       selling shares you do not hold is blocked (no short sales)
  blocked     codes on your blocked list, and stocks KRX has flagged as 관리종목
              (administrative issue) or halted, when krx/kind_flags.json lists them
  time        today is a KRX trading day; a new entry only between no_entries_before
              (09:15) and no_entries_after (15:00), and never in the 15:20-15:30
              closing auction or after the close
  plan        the plan is for today (Seoul)
  max loss    qty x (entry - stop + the round-trip costs at the stop) <= max_loss_per_trade_krw
  daily       losses already booked today + the max loss of today's open entries + this
              one <= max_daily_loss_krw
  count       entries today < max_trades_per_day, orders today < max_orders_per_day
  reward      (target - entry) / (entry - stop) >= min_reward_risk
  size        min_order_krw <= value <= max_order_krw; the position stays under
              max_position_pct of the account
  cash        enough cash after the buffer (cash_buffer_pct); no margin (미수)
  limits      the entry, stop and target inside today's +-30 % price limits when the
              previous close is known (krx/prices.json)
Results go to krx/state/risk.json; krx-broker reads them.
"""

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

TYPES = {"limit", "market"}
STATE = "krx/state/risk.json"
BROKER_STATE = "krx/state/broker.json"


class RiskError(Exception):
    pass


def fingerprint(o):
    keys = ("code", "side", "qty", "type", "limit_price", "take_profit_price", "stop_loss_price")
    raw = json.dumps({k: o.get(k) for k in keys}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def code_of(o):
    return str(o.get("code") or o.get("symbol") or "").strip().upper()


def account(cfg, portfolio):
    pf = portfolio if isinstance(portfolio, dict) else {}
    cash = float(pf.get("cash", cfg.get("budget") or 0))
    shares, values = {}, {}
    for code, p in (pf.get("positions") or {}).items():
        q = float(p.get("qty") or 0)
        shares[code] = q
        values[code] = q * float(p.get("last") or p.get("avg") or 0)
    return {"cash": cash, "shares": shares, "values": values}


def today_records(state, today):
    return [r for r in (state or {}).get("orders", []) if str(r.get("at", ""))[:10] == today
            and r.get("status") in ("placed", "filled")]


def window(now_utc, cfg):
    """(phase, reasons for an entry)."""
    local = krx.to_kst(now_utc)
    d = local.date()
    mins = local.hour * 60 + local.minute
    s = krx.session(d)
    if s is None:
        return "closed", ["time: KRX is closed today (%s)" % krx.holiday(d)]
    shift = s["open"] - krx.OPEN_MIN
    before = krx.hhmm_min(cfg["no_entries_before"], "no_entries_before") + shift
    after = min(krx.hhmm_min(cfg["no_entries_after"], "no_entries_after") + shift, s["cont_end"])
    if mins < s["open"]:
        return "pre-open", ["time: the market opens at %s; no entries before %s" % (krx.hm(s["open"]), krx.hm(before))]
    if mins >= s["close"]:
        return "closed", ["time: the market closed at %s" % krx.hm(s["close"])]
    if mins >= s["cont_end"]:
        return "auction", ["time: %s is the closing auction (single price at %s); no new entries"
                           % (krx.hm(mins), krx.hm(s["close"]))]
    if mins < before:
        return "open", ["time: no entries before %s (no_entries_before), it is %s" % (krx.hm(before), krx.hm(mins))]
    if mins > after:
        return "open", ["time: no entries after %s (no_entries_after), it is %s" % (krx.hm(after), krx.hm(mins))]
    return "open", []


def check(orders, cfg, portfolio=None, prices=None, state=None, flags=None, now=None):
    c = dict(krx.DEFAULTS)
    c.update(cfg or {})
    now = now or krx.utc_now()
    today = krx.to_kst(now).date().isoformat()
    acct = account(c, portfolio)
    blocked = {str(b).strip().upper().zfill(6) for b in c.get("blocked") or []}
    flags = flags or {}
    done = today_records(state, today)
    entries_today = [r for r in done if r.get("kind") == "entry"]
    booked = sum(min(0.0, float(r.get("pnl") or 0)) for r in (state or {}).get("closed", [])
                 if str(r.get("at", ""))[:10] == today)
    open_risk = sum(float(r.get("max_loss") or 0) for r in entries_today if not r.get("closed"))
    phase, time_reasons = window(now, c)
    year = int(today[:4])
    comm = float(c.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    prices = prices or {}
    top_warn = []
    results = []
    batch_entries, batch_loss, batch_orders = 0, 0.0, 0
    cash = acct["cash"]
    total = cash + sum(acct["values"].values())
    for i, o in enumerate(orders, 1):
        reasons, warns = [], []
        code = code_of(o)
        side = str(o.get("side", "")).lower()
        typ = str(o.get("type", "limit")).lower()
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        try:
            qty = int(o.get("qty"))
            if float(o.get("qty")) != qty or qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            qty = None
            reasons.append("format: qty must be a whole number of shares above 0, got %r" % o.get("qty"))
        if not krx.CODE_RE.match(code):
            reasons.append("format: %r is not a 6-character KRX code (like 005930)" % code)
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if typ not in TYPES:
            reasons.append("format: order type is limit (지정가) or market (시장가), got %r" % typ)
        if typ == "limit" and o.get("limit_price") is None:
            reasons.append("format: a limit order needs a limit_price")
        for k in ("limit_price", "take_profit_price", "stop_loss_price"):
            v = o.get(k)
            if v is not None:
                try:
                    x = krx.num(v, k)
                except krx.KRXError as e:
                    reasons.append("format: %s" % e)
                    continue
                if x <= 0 or not krx.on_grid(x):
                    reasons.append("format: %s %s is not on the KRX tick grid (a %d-won tick at that price)"
                                   % (k, krx.px(x), krx.tick_size(x)))
        if not c.get("paper", True):
            reasons.append("paper: this pack is paper only (\"paper\": false is not supported)")
        held = acct["shares"].get(code, 0.0)
        kind = "exit" if side == "sell" and held > 0 and qty is not None and qty <= held else "entry"
        if side == "sell" and kind == "entry":
            reasons.append("short: selling %s shares of %s you do not hold is a short sale; this pack does not "
                           "short" % (qty, code))
        ref = None
        if typ == "limit" and o.get("limit_price") is not None:
            ref = krx.num(o["limit_price"])
        elif plan.get("entry"):
            ref = float(plan["entry"])
        elif code in prices:
            ref = float(prices[code].get("price") or 0) or None
        stop = o.get("stop_loss_price")
        target = o.get("take_profit_price")
        if kind == "entry":
            reasons.extend(time_reasons)
            if code in blocked:
                reasons.append("blocked: %s is on your blocked list" % code)
            if code in flags:
                reasons.append("blocked: KRX lists %s as %s" % (code, flags[code]))
            if plan.get("date") and plan["date"] != today:
                reasons.append("plan: the plan is for %s, today is %s in Seoul" % (plan["date"], today))
            if c.get("require_stop", True) and stop is None:
                reasons.append("stop: every entry needs a stop-loss (require_stop)")
            if ref is None:
                reasons.append("price: no entry price to size the risk")
        if kind == "entry" and ref and stop is not None and qty:
            s = float(stop)
            if s >= ref:
                reasons.append("stop: the stop %s is not below the entry %s" % (krx.px(s), krx.px(ref)))
            else:
                per = ref - s + krx.cost_per_share(ref, s, year, comm)
                ml = qty * per
                o_ml = round(ml)
                lim = float(c.get("max_loss_per_trade_krw") or 0)
                if lim and ml > lim + 0.5:
                    reasons.append("max loss: %s shares x %s (risk + costs) = %s, over your %s per trade"
                                   % ("{:,}".format(qty), krx.px(per), krx.won(ml), krx.won(lim)))
                day_lim = float(c.get("max_daily_loss_krw") or 0)
                day_total = -booked + open_risk + batch_loss + ml
                if day_lim and day_total > day_lim + 0.5:
                    reasons.append("daily loss: %s booked today + %s at risk in open trades + %s here = %s, over "
                                   "your %s daily limit" % (krx.won(-booked), krx.won(open_risk + batch_loss),
                                                           krx.won(ml), krx.won(day_total), krx.won(day_lim)))
                if target is not None:
                    t = float(target)
                    if t <= ref:
                        reasons.append("reward: the target %s is not above the entry %s" % (krx.px(t), krx.px(ref)))
                    else:
                        rr = (t - ref) / (ref - s)
                        rr_net = (t - ref - krx.cost_per_share(ref, t, year, comm)) / per
                        mrr = float(c.get("min_reward_risk") or 0)
                        if mrr and rr < mrr - 1e-9:
                            reasons.append("reward: reward:risk %.2f is under your minimum %g" % (rr, mrr))
                        if rr_net < 1.0 <= rr:
                            warns.append("after costs the reward:risk is %.2f (%.2f before)" % (rr_net, rr))
                o["_max_loss"] = o_ml
        if kind == "entry":
            n_ent = len(entries_today) + batch_entries
            if n_ent >= int(c.get("max_trades_per_day") or 10 ** 6):
                reasons.append("count: %d entries today already, your max is %d" % (n_ent, c["max_trades_per_day"]))
        n_ord = len(done) + batch_orders
        if n_ord >= int(c.get("max_orders_per_day") or 10 ** 6):
            reasons.append("count: %d orders today already, your max is %d" % (n_ord, c["max_orders_per_day"]))
        value = qty * ref if (qty and ref) else None
        if kind == "entry" and value is not None:
            if value < float(c.get("min_order_krw") or 0):
                reasons.append("size: %s is under your %s minimum order" % (krx.won(value), krx.won(c["min_order_krw"])))
            if c.get("max_order_krw") and value > float(c["max_order_krw"]):
                reasons.append("size: %s is over your %s max order" % (krx.won(value), krx.won(c["max_order_krw"])))
            need = value * (1 + comm / 100.0)
            buf = total * float(c.get("cash_buffer_pct") or 0) / 100.0
            if need > cash and not c.get("allow_margin"):
                reasons.append("cash: needs %s but only %s cash; that would be margin (미수)" % (krx.won(need), krx.won(cash)))
            elif cash - need < buf:
                reasons.append("cash: leaves %s, under your %g%% buffer of %s" % (
                    krx.won(cash - need), float(c["cash_buffer_pct"]), krx.won(buf)))
            cap = total * float(c.get("max_position_pct") or 100) / 100.0
            if acct["values"].get(code, 0) + value > cap + 0.5:
                reasons.append("position: %s would be %s, over your %g%% cap of %s" % (
                    code, krx.won(acct["values"].get(code, 0) + value), float(c["max_position_pct"]), krx.won(cap)))
        pc = (prices.get(code) or {}).get("prev_close")
        if pc:
            lo, hi = krx.price_limits(float(pc))
            for k, v in (("entry", ref), ("stop", stop), ("target", target)):
                if v is not None and not lo <= float(v) <= hi:
                    (reasons if k != "target" else warns).append(
                        "limits: the %s %s is outside today's price limits %s to %s (+-30 %% of %s)"
                        % (k, krx.px(v), krx.px(lo), krx.px(hi), krx.px(pc)))
        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            batch_orders += 1
            if kind == "entry":
                batch_entries += 1
                batch_loss += o.get("_max_loss") or 0
                cash -= (value or 0) * (1 + comm / 100.0)
        results.append({"index": i, "code": code, "side": side, "qty": qty, "kind": kind, "verdict": verdict,
                        "reasons": reasons, "warnings": warns, "fingerprint": fingerprint(o),
                        "max_loss": o.pop("_max_loss", None), "value": round(value) if value else None})
    if phase != "open":
        top_warn.append("the market is %s right now" % {"pre-open": "not open yet", "closed": "closed",
                                                        "auction": "in the closing auction"}[phase])
    return {"created_by": "krx-risk", "date": today, "checked_at": krx.iso_utc(now), "paper": True,
            "results": results, "pass": sum(r["verdict"] == "PASS" for r in results),
            "block": sum(r["verdict"] == "BLOCK" for r in results), "warnings": top_warn}


def report(res, orders, lang="en"):
    ko = lang == "ko"
    L = []
    for r, o in zip(res["results"], orders):
        head = "%s %d  %s %s %s주 %s" % (krx.T("pass" if r["verdict"] == "PASS" else "block", "ko"), r["index"],
                                        "매수" if r["side"] == "buy" else "매도", r["code"], "{:,}".format(r["qty"] or 0),
                                        "(%s)" % ("진입" if r["kind"] == "entry" else "청산")) if ko else \
            "%s  order %d: %s %s %s shares (%s)" % (r["verdict"], r["index"], r["side"], r["code"],
                                                   "{:,}".format(r["qty"] or 0), r["kind"])
        L.append(head)
        if r["max_loss"]:
            L.append(("    손절 시 최대 손실 %s (비용 포함)" if ko else "    max loss at the stop %s, costs included")
                     % krx.won(r["max_loss"]))
        for x in r["reasons"]:
            L.append("    - " + x)
        for x in r["warnings"]:
            L.append(("    참고: " if ko else "    note: ") + x)
    for w in res["warnings"]:
        L.append(("참고: " if ko else "Note: ") + w)
    L.append(("%d개 통과, %d개 차단. 모의투자 전용입니다. 다음: /krx-broker 가 통과한 주문마다 예/아니오를 묻습니다." if ko else
              "%d PASS, %d BLOCK. Paper trading only. Next: /krx-broker asks you yes or no for each PASS.")
             % (res["pass"], res["block"]))
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Risk Manager (krx-risk): PASS or BLOCK each proposed order.")
    ap.add_argument("--orders", default="krx/orders.json")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--portfolio", default="krx/portfolio.json")
    ap.add_argument("--prices", default="krx/prices.json")
    ap.add_argument("--state", default=BROKER_STATE)
    ap.add_argument("--flags", default="krx/kind_flags.json")
    ap.add_argument("--out", default=STATE)
    ap.add_argument("--now")
    ap.add_argument("--lang", choices=["en", "ko"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = krx.load_config(a.config)
        doc = krx.read_json(a.orders)
        if not doc:
            raise RiskError("no orders at %s: run /krx-budget --write first" % a.orders)
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("%s has no orders" % a.orders)
        pdoc = krx.read_json(a.prices, {}) or {}
        prices = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
        flags = (krx.read_json(a.flags, {}) or {}).get("flags", {})
        res = check([dict(o) for o in orders], cfg, krx.read_json(a.portfolio), prices,
                    krx.read_json(a.state), flags, krx.parse_now(a.now))
        krx.write_json(a.out, res)
        if a.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(report(res, orders, krx.lang_of(a.lang, cfg)))
        return 0 if res["block"] == 0 else 1
    except (RiskError, krx.KRXError) as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
