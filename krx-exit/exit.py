#!/usr/bin/env python3
"""Exit Planner for krx-exit (Korean market): sets the stop and the target BEFORE
you enter, so you know when to get out. Standard library only. Buys only.
It never places an order.

    python3 exit.py plan                                   # from krx/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write
    python3 exit.py plan --symbol 005930 --entry 98700 --or-high 98400 --or-low 97300
    python3 exit.py plan --stop atr:1.5 --offline 005930.json
    python3 exit.py plan --symbol 000660 --entry 201500 --setup level --stop pct:1.2
    python3 exit.py show                                   # the current krx/plan.json

Stops (on the KRX tick grid, rounded DOWN, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, from the session's bars

Targets (on the tick grid, rounded DOWN toward the entry so a resting sell fills):
  2R (default), 1R, 1.5R ...   entry + R x risk
  price:X                      your price

Korean rules it applies:
  - Ticks: prices move in steps that depend on the price (1 won under 2,000 ...
    100 won from 50,000 to 200,000 ... 1,000 won from 500,000). Every price here
    lands on that grid.
  - Price limits: a target above today's +30 % limit (상한가) cannot fill;
    a stop under the -30 % limit (하한가) is refused.
  - Costs: the 0.20 % sell tax (2026) plus commission both ways. The plan shows
    the round trip in won and as a share of the risk, and warns when costs eat
    more than 20 % of it: a tight stop that wins half the time still loses money.
  - flat_by (default 15:15): out before the 15:20 closing auction, win or lose.

Refused, with the reason: a stop at or above the entry, a target at or below
it, a risk under 2 ticks or 0.1 % of the price, an entry.json that has not
triggered (unless you give --entry), a short sale.
A stop is a plan, not a guarantee: a gap, a VI halt or a limit-down day can fill past it.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

ATR_BARS = 14
MIN_RISK_PCT = 0.10
MIN_RISK_TICKS = 2
COST_WARN = 0.20
NOTE = "A stop is a plan, not a guarantee: a gap, a VI halt or a limit-down day can fill past it."
NOTE_KO = "손절가는 계획일 뿐 보장이 아닙니다: 갭, VI 발동, 하한가에서는 더 불리하게 체결될 수 있습니다."
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


class PlanError(Exception):
    pass


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*₩?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), float(m.group(2).replace(",", ""))
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 30:
        raise PlanError("--stop pct:X needs X under 30 (the daily price limit)")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = float(m.group(1))
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*₩?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", float(m.group(1).replace(",", ""))
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_text(r):
    n = int(r) if float(r).is_integer() else None
    return "%gR (%s times the risk)" % (r, WORDS.get(n, "%g" % r))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, limits=None):
    """The stop, the target and the risk, in won on the tick grid. Raises PlanError."""
    e = krx.round_tick(entry)
    if e <= 0:
        raise PlanError("the entry price must be above 0")
    if e != entry and abs(e - entry) > 1e-6:
        raise PlanError("the entry %s is not on the KRX tick grid (a %d-won tick at that price): did you mean %s?"
                        % (krx.px(entry), krx.tick_size(entry), krx.px(e)))
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        if or_high <= or_low:
            raise PlanError("the opening-range high %s must be above its low %s" % (krx.px(or_high), krx.px(or_low)))
        if stop_rule == "or_mid":
            raw, label = (or_high + or_low) / 2.0, "opening-range midpoint"
            detail = "(%s + %s) / 2 = %s" % (krx.px(or_high), krx.px(or_low), "{:,.1f}".format(raw))
        else:
            raw, label = or_low, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100.0)
        label = "%g%% under the entry" % stop_param
        detail = "%s x (1 - %g%%) = %s" % (krx.px(e), stop_param, "{:,.1f}".format(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the session's bars")
        raw = e - stop_param * atr
        label = "%g x ATR(%d) under the entry" % (stop_param, atr_bars)
        detail = "ATR %s over the %d bars to the entry" % ("{:,.1f}".format(atr), atr_bars)
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = krx.round_tick(raw, "down")
    if abs(stop - raw) > 1e-6:
        detail = (detail + ", down to the tick" if detail else "%s down to the tick" % "{:,.1f}".format(raw))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a buy's stop goes under the entry"
                        % (krx.px(stop), label, krx.px(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a higher stop" % krx.px(stop))
    if limits and stop < limits[0]:
        raise PlanError("the stop %s is below today's lower limit %s (하한가, -30 %%): it can never trade there"
                        % (krx.px(stop), krx.px(limits[0])))
    risk = e - stop
    tick = krx.tick_size(stop)
    floor = max(MIN_RISK_TICKS * tick, e * MIN_RISK_PCT / 100.0)
    if risk < floor - 1e-9:
        raise PlanError("the stop is %s won under the entry, less than %d ticks or %.1f%% of the price (%s won): too "
                        "tight, the normal wiggle would stop you out and the costs would eat the trade. Widen it."
                        % (krx.px(risk), MIN_RISK_TICKS, MIN_RISK_PCT, krx.px(floor)))
    if target_rule == "r":
        target = krx.round_tick(e + target_param * risk, "down")
        t_label = r_text(target_param)
    else:
        target = krx.round_tick(target_param, "down")
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a buy's target goes above it"
                        % (krx.px(target), krx.px(e)))
    rr = round((target - e) / float(risk), 2)
    if t_label is None:
        t_label = "your price (%gR)" % rr
    elif abs(rr - target_param) > 0.005:
        t_label += ", %gR after rounding to the tick" % rr
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": rr, "target_label": t_label, "risk": risk, "reward_risk": rr}


def atr_of(bars, n=ATR_BARS):
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


def day_bars(info, day, offline, upto_slot, now_utc):
    try:
        text = krx.read_offline(offline, info, suffix="") if offline else krx.fetch_chart(info, "5d", "5m")
        res = krx.chart_result(text, info["code"])
    except krx.KRXError as e:
        raise PlanError(str(e))
    days, _ = krx.session_bars(res, now_utc or krx.utc_now())
    d = days.get(day)
    if not d:
        return [], None
    bars = [d["bars"][k] for k in sorted(d["bars"]) if upto_slot is None or k <= upto_slot]
    prior = [x for x in sorted(days) if x < day]
    base = None
    if prior:
        p = days[prior[-1]]
        base = p["auction"] or (p["bars"][max(p["bars"])]["c"] if p["bars"] else None)
    return bars, base


def resolve_flat_by(day, explicit, cfg, entry_min):
    s = krx.session(day)
    try:
        t = krx.hhmm_min(explicit or cfg.get("flat_by") or krx.DEFAULTS["flat_by"],
                         "--flat-by" if explicit else "flat_by")
    except krx.KRXError as e:
        raise PlanError(str(e))
    note = ""
    if s:
        shift = s["open"] - krx.OPEN_MIN
        if shift and not explicit:
            t += shift
            note = "moved %d minutes later for today's special hours (%s)" % (shift, s["note"])
        if t >= s["cont_end"]:
            raise PlanError("flat-by %s is at or after %s, when continuous trading ends and the closing auction "
                            "starts: pick an earlier time" % (krx.hm(t), krx.hm(s["cont_end"])))
    if entry_min is not None and entry_min >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (krx.hm(entry_min), krx.hm(t)))
    return krx.hm(t), note


def gather(a):
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        try:
            info = krx.resolve(a.symbol)
        except krx.KRXError as e:
            raise PlanError(str(e))
        return dict(info, entry=a.entry, setup=a.setup or ("orb" if a.or_high is not None else "level"),
                    date=a.date, entry_time=a.time, entry_bar=a.entry_bar, or_high=a.or_high, or_low=a.or_low,
                    or_minutes=a.or_minutes, prev_close=a.prev_close, source="flags")
    path = a.from_path or "krx/entry.json"
    doc = krx.read_json(path)
    if doc is None:
        raise PlanError("no entry at %s (run /krx-entry first, or give --symbol and --entry)" % path)
    if doc.get("status") != "triggered" and a.entry is None:
        raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                        % (path, doc.get("code") or doc.get("symbol"), doc.get("status") or "unknown"))
    orr = doc.get("opening_range") or {}
    try:
        info = krx.resolve(doc.get("code") or doc.get("symbol"))
    except krx.KRXError as e:
        raise PlanError(str(e))
    info.update({k: doc[k] for k in ("name", "yahoo", "market") if doc.get(k)})
    return dict(info, entry=a.entry if a.entry is not None else doc.get("price"),
                setup=a.setup or doc.get("setup") or "orb", date=a.date or doc.get("date"),
                entry_time=a.time or (doc.get("time") if a.entry is None else None),
                entry_bar=a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
                or_high=a.or_high if a.or_high is not None else orr.get("high"),
                or_low=a.or_low if a.or_low is not None else orr.get("low"),
                or_minutes=orr.get("minutes") or a.or_minutes, prev_close=a.prev_close or doc.get("prev_close"),
                source=path)


def build_plan(a, now_utc):
    info = gather(a)
    try:
        cfg = krx.load_config(a.config)
    except krx.KRXError as e:
        raise PlanError(str(e))
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"])).isoformat()
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = krx.to_kst(now_utc).date().isoformat()
    if krx.session(day) is None:
        raise PlanError("%s is not a trading day in Korea (%s)" % (day, krx.holiday(day)))
    try:
        entry_min = krx.hhmm_min(info["entry_time"], "the entry time") if info["entry_time"] else None
    except krx.KRXError as e:
        raise PlanError(str(e))
    atr = atr_n = None
    base = info.get("prev_close")
    if stop_rule == "atr" or (base is None and a.offline):
        bars, b2 = day_bars(info, day, a.offline, info["entry_bar"], krx.parse_now(a.now) if a.now else None)
        base = base or b2
        if stop_rule == "atr":
            if len(bars) < 3:
                raise PlanError("an atr stop needs at least 3 closed bars of %s on %s by the entry; found %d"
                                % (info["code"], day, len(bars)))
            atr, atr_n = atr_of(bars)
    limits = krx.price_limits(float(base)) if base else None
    c = compute(float(info["entry"]), stop_rule, stop_param, target_rule, target_param,
                info["or_high"], info["or_low"], atr, atr_n, limits)
    flat_by, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_min)
    year = int(day[:4])
    comm = float(cfg.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    cost_t = krx.cost_per_share(c["entry"], c["target"], year, comm)
    cost_s = krx.cost_per_share(c["entry"], c["stop"], year, comm)
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(info["or_high"]), "low": float(info["or_low"]), "minutes": int(info["or_minutes"] or 15)}
    plan = {"created_by": "krx-exit", "symbol": info["code"], "code": info["code"], "name": info.get("name", ""),
            "yahoo": info.get("yahoo"), "market": info.get("market"), "side": "buy", "date": day,
            "setup": info["setup"], "entry": c["entry"], "entry_time": krx.hm(entry_min) if entry_min is not None else None,
            "entry_bar": info["entry_bar"], "interval": "5m", "opening_range": orr,
            "stop": c["stop"], "stop_rule": stop_rule, "stop_label": c["stop_label"], "stop_detail": c["stop_detail"],
            "target": c["target"], "target_r": c["target_r"], "target_label": c["target_label"],
            "risk_per_share": c["risk"], "reward_risk": c["reward_risk"], "flat_by": flat_by,
            "costs": {"per_share_at_target": round(cost_t, 1), "per_share_at_stop": round(cost_s, 1),
                      "share_of_risk": round(cost_t / c["risk"], 3), "commission_pct": comm,
                      "sell_tax_pct": round(100 * krx.sell_tax_rate(year), 3),
                      "net_reward_risk": round((c["target"] - c["entry"] - cost_t) / (c["risk"] + cost_s), 2)},
            "prev_close": float(base) if base else None,
            "limits": {"lower": limits[0], "upper": limits[1]} if limits else None,
            "created_at": krx.iso_utc(now_utc), "note": NOTE}
    if atr is not None:
        plan["atr"] = round(atr, 2)
        plan["atr_bars"] = atr_n
    warnings = []
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    if plan["costs"]["share_of_risk"] > COST_WARN:
        warnings.append("Costs are %.0f %% of the risk: a win nets %s won a share, a loss costs %s. After costs the "
                        "reward:risk is %.2f, not %.2f. A wider stop and target cut that share."
                        % (100 * plan["costs"]["share_of_risk"], krx.px(c["target"] - c["entry"] - cost_t),
                           krx.px(c["risk"] + cost_s), plan["costs"]["net_reward_risk"], c["reward_risk"]))
    if limits and c["target"] > limits[1]:
        warnings.append("The target %s is above today's upper limit %s (상한가): it cannot fill today."
                        % (krx.px(c["target"]), krx.px(limits[1])))
    try:
        mrr = float(cfg.get("min_reward_risk") or 0)
    except (TypeError, ValueError):
        mrr = 0
    if mrr and c["reward_risk"] < mrr:
        warnings.append("Reward:risk %.2f is under your min_reward_risk %g: krx-risk checks that." % (c["reward_risk"], mrr))
    today = krx.to_kst(now_utc).date().isoformat()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day, today))
    return plan, warnings


def describe(plan, lang="en"):
    who = ("%s %s" % (plan["name"], plan["code"])) if plan.get("name") else plan.get("code") or plan["symbol"]
    pct = 100.0 * plan["risk_per_share"] / plan["entry"]
    c = plan.get("costs") or {}
    when = (" at %s" % plan["entry_time"]) if plan.get("entry_time") else ""
    if lang == "ko":
        out = ["청산 계획: %s 매수, %s, %s" % (who, krx.day_label(plan["date"], "ko"),
                                         {"orb": "시가 범위 돌파", "level": "기준가 돌파"}.get(plan["setup"], plan["setup"]))]
        out.append("  진입가      %s%s" % (krx.px(plan["entry"]), (" (%s)" % plan["entry_time"]) if plan.get("entry_time") else ""))
        out.append("  손절가      %s  %s" % (krx.px(plan["stop"]), plan.get("stop_label", "")))
        out.append("  목표가      %s  %s" % (krx.px(plan["target"]), plan.get("target_label", "")))
        out.append("  주당 위험   %s원 (진입가의 %.2f%%)" % (krx.px(plan["risk_per_share"]), pct))
        out.append("  손익비      %.2f, 비용 후 %.2f" % (plan["reward_risk"], c.get("net_reward_risk", plan["reward_risk"])))
        if c:
            out.append("  왕복 비용   주당 약 %s원 (거래세 %.2f%% + 수수료 %.3f%% x2), 위험의 %.0f%%" % (
                krx.px(c["per_share_at_target"]), c["sell_tax_pct"], c["commission_pct"], 100 * c["share_of_risk"]))
        out.append("  청산 시각   %s, 수익이든 손실이든: 오버나잇 없음, 15:20 동시호가 전" % plan["flat_by"])
        out.append("진입 전에 정합니다. 손절가에 닿으면 나오고, 손절가를 더 멀리 옮기지 마세요.")
        out.append(NOTE_KO)
        return out
    out = ["Exit plan: %s buy, %s, %s on 5-minute bars" % (
        who, krx.day_label(plan["date"]), {"orb": "opening-range breakout", "level": "close above a level"}.get(
            plan["setup"], plan["setup"]))]
    out.append("  Entry         %s%s" % (krx.px(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (krx.px(plan["stop"]), plan.get("stop_label", ""),
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (krx.px(plan["target"]), plan.get("target_label", "")))
    out.append("  Risk a share  %s won (%.2f%% of the entry)" % (krx.px(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %.2f, %.2f after costs" % (plan["reward_risk"], c.get("net_reward_risk", plan["reward_risk"])))
    if c:
        out.append("  Round trip    about %s won a share (%.2f%% sell tax + %.3f%% commission each way), %.0f%% of the risk"
                   % (krx.px(c["per_share_at_target"]), c["sell_tax_pct"], c["commission_pct"], 100 * c["share_of_risk"]))
    if plan.get("limits"):
        out.append("  Price limits  %s to %s today (+-30 %% of %s)" % (krx.px(plan["limits"]["lower"]),
                                                                    krx.px(plan["limits"]["upper"]),
                                                                    krx.px(plan["prev_close"])))
    out.append("  Flat by       %s KST, win or lose: no overnight hold, out before the 15:20 closing auction"
               % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


def cmd_plan(a):
    now = krx.parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        krx.write_json(a.write, plan)
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2, ensure_ascii=False))
        return 0
    lang = krx.lang_of(a.lang, krx.load_config(a.config))
    print("\n".join(describe(plan, lang)))
    for w in warnings:
        print(("참고: " if lang == "ko" else "Note: ") + w)
    if a.write:
        print("Wrote %s." % a.write)
    print("다음: /krx-timemachine 으로 재생, /krx-budget 으로 수량. 주문은 하지 않았습니다." if lang == "ko" else
          "Next: /krx-timemachine replays it, then /krx-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    plan = krx.read_json(a.plan)
    if not plan:
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    for k in ("date", "entry", "stop", "target", "risk_per_share", "reward_risk", "flat_by"):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    if a.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    print("\n".join(describe(dict({"setup": "orb", "stop_label": "", "target_label": ""}, **plan),
                             krx.lang_of(a.lang, krx.load_config(a.config)))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (krx-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date")
    p.add_argument("--time", help="entry time, 24-hour Seoul (e.g. 09:45)")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--prev-close", type=float, help="yesterday's close, for the +-30 %% limits")
    p.add_argument("--stop")
    p.add_argument("--target")
    p.add_argument("--flat-by")
    p.add_argument("--offline")
    p.add_argument("--now")
    p.add_argument("--config", default=krx.CONFIG_PATH)
    p.add_argument("--lang", choices=["en", "ko"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="krx/plan.json", metavar="PATH")
    p = sub.add_parser("show")
    p.add_argument("--plan", default="krx/plan.json")
    p.add_argument("--config", default=krx.CONFIG_PATH)
    p.add_argument("--lang", choices=["en", "ko"])
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except (PlanError, krx.KRXError) as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
