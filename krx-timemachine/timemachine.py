#!/usr/bin/env python3
"""Time Machine for krx-timemachine (Korean market). Standard library only.

Replays a planned day trade before you risk a won: 10,000 simulated runs of the
rest of the session from your entry, plus the same rule on every past session in
the data, with Korean costs (sell tax + commission) taken out.

    python3 timemachine.py run                          # krx/plan.json, 10,000 replays, seed 19
    python3 timemachine.py run --write                  # also save krx/timemachine.json
    python3 timemachine.py run --until 15:15            # stop each replay at your flat-by time
    python3 timemachine.py run --symbol 005930 --date 2026-10-30 --entry 98700 \\
        --stop 97900 --target 99500 --entry-time 09:45
    python3 timemachine.py run --lang ko                # 한국어 보고서
    python3 timemachine.py chart --from                 # ASCII fan chart of the saved run

How the replays work (the same algorithm as the US pack's timemachine.py):
  1. Fetch 60 days of 5-minute bars from Yahoo (005930.KS / 247540.KQ). Keep only
     FULL regular sessions before the plan's date: the 76 continuous-trading bars
     09:00 to 15:20 Seoul time. KRX stocks that skip a five-minute window get that
     bar filled flat (up to 10 % of a day); late-open days (the first trading day,
     the exam day 수능) and gaps are left out.
  2. The pool: for every bar slot 1..75, each past session's bar as ratios to the
     previous bar's close.
  3. One replay starts at the entry and walks the rest of the session. Bar i draws
     a slot j = i + randint(-window, window), kept in 1..75, then one past bar from
     pool[j], scaled to the current price and clamped to the day's +-30 % price
     limits. The stop is checked before the target in every bar (a bar that
     touches both counts as the stop: the conservative call).
  4. Outcomes: target, stop, or neither by --until (default 15:20, when continuous
     trading ends and the closing auction starts; an exit is planned before it).
  5. Costs: every replay pays the round trip: commission on both legs (config
     commission_pct, default 0.015 %) plus the sell tax (0.20 % in 2026). "Net R"
     is R after those costs. On a tight stop the costs are a large share of R.
  6. History: the plan's own opening-range-breakout rule re-run on every past full
     session, same stop rule and target R, stops and targets on the tick grid.

Data: https://query2.finance.yahoo.com/v8/finance/chart/<CODE>.KS?range=60d&interval=5m
(unofficial and free; can lag). --offline FILE reads a saved chart JSON instead.
Times are Seoul (KST, UTC+9); --now "2026-10-30 09:45" pins the clock.
A simulation built from the stock's own recent bars is not a prediction.
"""

import argparse
import datetime
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
MIN_SESSIONS = 20
COIN_FLIP = 0.05
STRONG = 0.15
MANY_NEITHER = 0.40
N = krx.SESSION_BARS          # 76 bars, 09:00 to 15:20
OPEN = krx.OPEN_MIN


class TMError(Exception):
    pass


# ---------------------------------------------------------------- sessions

def standard(day):
    return day["session"]["open"] == krx.OPEN_MIN and day["session"]["cont_end"] == krx.CONT_END_MIN


def full_sessions(days, before):
    """[(date, [76 bars])] of full standard sessions strictly before `before`, oldest first,
    and the ones left out with the reason."""
    full, short = [], []
    for d in sorted(days):
        if d >= before:
            continue
        day = days[d]
        if not standard(day):
            short.append({"date": d, "why": day["session"]["note"] or "special hours"})
            continue
        bars = krx.complete_day(day)
        if bars is None:
            short.append({"date": d, "why": "%d of %d bars" % (len(day["bars"]), N)})
        else:
            full.append((d, bars))
    return full, short


def day_close(day):
    """The day's closing price: the closing auction if the data has it, else the last bar."""
    if day.get("auction"):
        return day["auction"]
    if day["bars"]:
        return day["bars"][max(day["bars"])]["c"]
    return None


def base_price(days, date, meta):
    """Yesterday's close, the base for today's +-30 % limits."""
    prior = [d for d in sorted(days) if d < date]
    if prior:
        return day_close(days[prior[-1]])
    v = meta.get("chartPreviousClose") or meta.get("previousClose")
    return float(v) if v else None


# ---------------------------------------------------------------- the simulation

def build_pool(sessions):
    pool = [[] for _ in range(N)]
    for _, D in sessions:
        for i in range(1, N):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pctl(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, side="buy",
             limits=None, cost_fn=None):
    """Monte Carlo replays of bars k+1..last. limits = (lower, upper) clamps every price.
    cost_fn(exit_price) -> won per share for the round trip; gives net R."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    lo_lim, hi_lim = limits or (0.0, float("inf"))

    def slot(rng, i):
        return i if window == 0 else min(N - 1, max(1, i + rng.randint(-window, window)))

    def clamp(x):
        return min(hi_lim, max(lo_lim, x))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths, limit_days = [], [], [], 0
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        hit_limit = False
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = clamp(p * h), clamp(p * l), clamp(p * c)
            if hi >= hi_lim or lo <= lo_lim:
                hit_limit = True
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 1))
        limit_days += hit_limit
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    exits = [target if o == "target" else stop if o == "stop" else p for o, p in zip(outcomes, closes)]
    Rs = [((x - entry) if long else (entry - x)) / risk for x in exits]
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else r for o, r in zip(outcomes, Rs)]
    if cost_fn:
        net = [r - cost_fn(x) / risk for r, x in zip(Rs, exits)]
    else:
        net = Rs
    srt = sorted(closes)
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = clamp(p * c)
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pctl(col, q), 1) for q in BAND_Q])
    lo_, hi_ = pctl(srt, 0.01), pctl(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "p_limit": round(limit_days / replays, 4),
        "median_close": round(pctl(srt, 0.5), 1), "p5_close": round(pctl(srt, 0.05), 1),
        "p95_close": round(pctl(srt, 0.95), 1), "avg_R": round(statistics.mean(Rs), 3),
        "avg_R_net": round(statistics.mean(net), 3),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 1), "hi": round(hi_, 1), "bins": bins},
    }


# ---------------------------------------------------------------- the rule and history

def opening_range(D, n):
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry, long):
    r = rule["stop_rule"]
    if r == "or_mid":
        return krx.round_away((oh + ol) / 2, long)
    if r in ("or_low", "or_high"):
        return krx.round_away(ol if long else oh, long)
    frac = rule["stop_pct"] / 100.0
    return krx.round_away(entry * (1 - frac) if long else entry * (1 + frac), long)


def history_replay(sessions, rule, last, side="buy", cost_fn=None):
    long = side == "buy"
    n = rule["or_minutes"] // 5
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1) if (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = krx.round_tick(D[kk]["c"])
        s = rule_stop(rule, oh, ol, e, long)
        risk = (e - s) if long else (s - e)
        if risk <= 0:
            continue
        t = krx.round_toward(e + rule["target_r"] * risk if long else e - rule["target_r"] * risk, long)
        if (t <= e) if long else (t >= e):
            continue
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        x = t if oc == "target" else s if oc == "stop" else cl
        R = ((x - e) if long else (e - x)) / risk
        Rn = R - (cost_fn(x, e) / risk if cost_fn else 0)
        trades.append({"date": d, "time": krx.hm(OPEN + 5 * (kk + 1)), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2), "R_net": round(Rn, 2)})
    return {
        "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "avg_R_net": round(statistics.mean(x["R_net"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


# ---------------------------------------------------------------- the plan

def _pos(v, what):
    try:
        x = krx.num(v, what)
    except krx.KRXError as e:
        raise TMError(str(e))
    if x <= 0:
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def load_plan(a, now_utc):
    if a.symbol:
        missing = [f for f in ("entry", "stop", "target", "entry_time") if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {"symbol": a.symbol, "side": a.side, "date": a.date or krx.to_kst(now_utc).date().isoformat(),
               "entry": a.entry, "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
               "setup": a.setup, "opening_range": {"minutes": a.or_minutes}, "stop_rule": a.stop_rule}
        return normalize_plan(raw, "flags")
    if not os.path.exists(a.plan):
        raise TMError("no plan at %s: run /krx-exit first, or pass --symbol --date --entry --stop "
                      "--target --entry-time" % a.plan)
    raw = krx.read_json(a.plan)
    if not isinstance(raw, dict):
        raise TMError("%s is not a plan" % a.plan)
    return normalize_plan(raw, a.plan)


def normalize_plan(raw, src="flags"):
    try:
        info = krx.resolve(raw.get("code") or raw.get("symbol"))
    except krx.KRXError as e:
        raise TMError(str(e))
    side = str(raw.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell, got %r" % raw.get("side"))
    if side == "sell":
        raise TMError("a short sale (sell first) is out of scope for retail day trading in Korea; "
                      "plan a buy")
    try:
        date = datetime.date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-30, got %r" % raw.get("date"))
    entry, stop, target = (_pos(raw.get(k), k) for k in ("entry", "stop", "target"))
    if not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)"
                      % (krx.px(stop), krx.px(entry), krx.px(target)))
    off = [k for k, v in (("entry", entry), ("stop", stop), ("target", target)) if not krx.on_grid(v)]
    if off:
        raise TMError("%s not on the KRX tick grid (e.g. ticks of 100 won between 50,000 and 200,000): "
                      "round them first" % ", ".join("%s %s" % (k, krx.px(dict(entry=entry, stop=stop, target=target)[k]))
                                                     for k in off))
    s = krx.session(date)
    if s is None:
        raise TMError("%s is not a trading day in Korea (%s)" % (date, krx.holiday(date)))
    k = None
    if raw.get("entry_time"):
        try:
            close_min = krx.hhmm_min(raw["entry_time"], "entry_time")
        except krx.KRXError as e:
            raise TMError(str(e))
        if (close_min - s["open"]) % 5:
            raise TMError("entry_time %s is not on the 5-minute grid (use the entry bar's close, e.g. 09:45)"
                          % raw["entry_time"])
        k = (close_min - s["open"]) // 5 - 1
    if raw.get("entry_bar") is not None and k is None:
        k = int(raw["entry_bar"])
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 09:45)")
    n_bars = krx.slots_in(s)
    if not 0 <= k <= n_bars - 2:
        raise TMError("the entry must close between %s and %s to leave bars to replay"
                      % (krx.hm(s["open"] + 5), krx.hm(s["cont_end"] - 5)))
    orng = raw.get("opening_range") or {}
    or_min = int(orng.get("minutes", 15) if isinstance(orng, dict) else 15)
    if or_min < 5 or or_min % 5 or or_min >= (s["cont_end"] - s["open"]):
        raise TMError("opening_range.minutes must be a multiple of 5 (got %s)" % or_min)
    risk = entry - stop
    return {
        "symbol": info["code"], "code": info["code"], "name": raw.get("name") or info["name"],
        "yahoo": raw.get("yahoo") or info["yahoo"], "market": raw.get("market") or info["market"],
        "side": side, "date": date, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": krx.hm(s["open"] + 5 * (k + 1)), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": risk,
        "target_r": round((target - entry) / risk, 2),
        "opening_range": {"minutes": or_min}, "stop_rule": str(raw.get("stop_rule") or "auto"),
        "flat_by": raw.get("flat_by"), "from": src,
    }


def resolve_rule(plan, entry_day_bars):
    if plan["setup"].lower() != "orb":
        return None
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"], "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day_bars for i in range(n)):
            oh, ol = opening_range(entry_day_bars, n)
            if krx.round_away((oh + ol) / 2, True) == plan["stop"]:
                rule["stop_rule"] = "or_mid"
            elif krx.round_away(ol, True) == plan["stop"]:
                rule["stop_rule"] = "or_low"
    elif rule["stop_rule"] not in ("or_mid", "or_low", "pct"):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- the run

def lean(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc, lang="en"):
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    until = mc.get("until", "15:20")
    if lv is None:
        return ("어떤 리플레이도 %s까지 목표가나 손절가에 닿지 않았습니다." % until if lang == "ko" else
                "No replay reached the target or the stop by %s." % until)
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = krx.T("coin_flip" if one_r else "break_even", lang)
    elif abs(d) < STRONG:
        head = krx.T("lean_target" if d > 0 else "lean_stop", lang)
    else:
        head = krx.T("strong_target" if d > 0 else "strong_stop", lang)
    if lang == "ko":
        odds = "목표가 먼저 %.1f %% vs 손절가 먼저 %.1f %%" % (100 * pt, 100 * ps)
        if one_r:
            return "%s: %s." % (head, odds)
        return ("%s: %gR에서는 한쪽에 닿은 리플레이 중 %.0f %%에서 목표가가 먼저 와야 본전인데, "
                "실제로는 %.0f %%였습니다 (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc, lang="en"):
    notes = []
    lv = lean(mc)
    until = mc.get("until", "15:20")
    if mc.get("p_neither", 0) > MANY_NEITHER and lv is not None:
        notes.append("리플레이의 %.1f %%는 %s까지 어느 쪽에도 닿지 않았고, 그 가격이 결과를 정합니다." %
                     (100 * mc["p_neither"], until) if lang == "ko" else
                     "%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * mc["p_neither"], until, until))
    g, n = mc.get("avg_R"), mc.get("avg_R_net")
    if g is not None and n is not None and g > 0 >= n:
        notes.append("비용(거래세+수수료)을 빼면 평균 R이 %+.3f에서 %+.3f로 마이너스가 됩니다." % (g, n)
                     if lang == "ko" else
                     "Costs (sell tax + commission) turn the average R from %+.3f to %+.3f." % (g, n))
    return notes


def time_machine(text, plan, now_utc, replays=10000, seed=19, window=2, until=None, source="yahoo",
                 cfg=None, lang="en"):
    cfg = cfg or dict(krx.DEFAULTS)
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    try:
        result = krx.chart_result(text, plan["code"])
    except krx.KRXError as e:
        raise TMError(str(e))
    meta = result.get("meta") or {}
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise TMError("the data has %s bars; the time machine needs 5-minute bars" % gran)
    msym = str(meta.get("symbol") or "").upper()
    if msym and msym.split(".")[0] != plan["code"]:
        raise TMError("the data is for %s but the plan is for %s" % (msym, plan["code"]))
    days, dropped = krx.session_bars(result, now_utc)
    if not days:
        raise TMError("no complete 5-minute bars for %s in the data" % plan["code"])
    entry_day = days.get(plan["date"])
    k = plan["entry_bar"]
    if not entry_day or k not in entry_day["bars"]:
        reason = "no bars for that day" if not entry_day else \
            "the data stops at the %s bar" % entry_day["bars"][max(entry_day["bars"])]["start"] \
            if entry_day["bars"] else "no bars for that day"
        raise TMError("the entry is not in the data: no complete bar closing %s on %s for %s (%s)"
                      % (plan["entry_time"], plan["date"], plan["code"], reason))
    if not standard(entry_day):
        raise TMError("%s has special hours (%s): the pool's time-of-day slots do not line up. "
                      "Replay a normal day." % (plan["date"], entry_day["session"]["note"]))
    end_min = krx.CONT_END_MIN
    try:
        until_min = end_min if until is None else krx.hhmm_min(until, "--until")
    except krx.KRXError as e:
        raise TMError(str(e))
    if (until_min - OPEN) % 5 or until_min > end_min:
        raise TMError("--until must be on the 5-minute grid and no later than 15:20 "
                      "(continuous trading ends; the closing auction is one price at 15:30)")
    last = (until_min - OPEN) // 5 - 1
    if last <= k:
        raise TMError("--until %s leaves nothing to replay after the %s entry" % (krx.hm(until_min),
                                                                             plan["entry_time"]))
    eb = entry_day["bars"][k]
    warn = []
    if not eb["l"] - krx.tick_size(eb["l"]) <= plan["entry"] <= eb["h"] + krx.tick_size(eb["h"]):
        warn.append("the plan's entry %s is outside that bar's range %s to %s"
                    % (krx.px(plan["entry"]), krx.px(eb["l"]), krx.px(eb["h"])))
    base = base_price(days, plan["date"], meta)
    limits = krx.price_limits(base) if base else None
    if limits and plan["target"] > limits[1]:
        warn.append("the target %s is above today's upper limit %s (상한가): it cannot fill"
                    % (krx.px(plan["target"]), krx.px(limits[1])))
    if limits and plan["stop"] < limits[0]:
        warn.append("the stop %s is below today's lower limit %s (하한가)" % (krx.px(plan["stop"]), krx.px(limits[0])))
    sessions, short = full_sessions(days, plan["date"])
    if len(sessions) < MIN_SESSIONS:
        raise TMError("only %d full session(s) of %s before %s in the data; the time machine needs at least %d"
                      % (len(sessions), plan["code"], plan["date"], MIN_SESSIONS))
    year = int(plan["date"][:4])
    comm = float(cfg.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    entry = plan["entry"]

    def cost_fn(exit_px, e=entry):
        return krx.cost_per_share(e, exit_px, year, comm)

    pool = build_pool(sessions)
    mc = simulate(pool, entry, plan["stop"], plan["target"], k, last, replays, seed, window, plan["side"],
                  limits, cost_fn)
    cost_at_target = cost_fn(plan["target"])
    mc = dict(mc, until=krx.hm(until_min), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0], band_times=[krx.hm(OPEN + 5 * (j + 1)) for j in range(k, last + 1)],
              cost_per_share=round(cost_at_target, 1),
              cost_R=round(cost_at_target / plan["risk_per_share"], 3),
              limits={"base": base, "lower": limits[0], "upper": limits[1]} if limits else None,
              costs={"commission_pct": comm, "sell_tax_pct": round(100 * krx.sell_tax_rate(year), 3)})
    rule = resolve_rule(plan, entry_day["bars"])
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"], lambda x, e: krx.cost_per_share(e, x, year, comm))
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) only"}
    lastd = max(days)
    lastb = days[lastd]["bars"][max(days[lastd]["bars"])] if days[lastd]["bars"] else None
    return {
        "created_by": "krx-timemachine",
        "created_at": krx.iso_utc(now_utc),
        "source": source,
        "data": {"symbol": msym or plan["yahoo"], "interval": "5m", "range": "60d",
                 "last_bar": "%s %s KST" % (lastd, lastb["start"]) if lastb else None,
                 "dropped": dropped, "sessions_left_out": short},
        "plan": plan_out, "mc": mc, "history": history,
        "verdict": verdict(mc, lang), "verdict_notes": verdict_notes(mc, lang),
        "warnings": warn, "lang": lang,
        "note": ("A simulation built from %s's own last %d full sessions. It is not a prediction: earnings, "
                 "disclosures, VI halts and the price limits can do things those sessions never did. "
                 "Same seed, same numbers." % (plan["code"], len(sessions))),
    }


# ---------------------------------------------------------------- output

def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    ko = doc.get("lang") == "ko"
    who = ("%s %s" % (p.get("name"), p["code"])) if p.get("name") else p["code"]
    until = m["until"]
    r = p["target_r"]
    pc = lambda x: "%.1f %%" % (100.0 * x)  # noqa: E731
    if ko:
        lines = [
            "%s 매수, %s: %s에 %s 진입 (봉 %d), 손절 %s, 목표 %s (%gR, 주당 위험 %s원)."
            % (who, krx.day_label(p["date"], "ko"), p["entry_time"], krx.px(p["entry"]), p["entry_bar"],
               krx.px(p["stop"]), krx.px(p["target"]), r, krx.px(p["risk_per_share"])),
            "남은 장을 %s번 재생: %s ~ %s (5분봉 %d개), 시드 %d."
            % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"], m["seed"]),
            "각 봉은 최근 %d거래일(%s ~ %s)의 같은 시간대(±%d봉) 5분봉에서 통째로 뽑았습니다."
            % (m["pool_sessions"], m["pool_from"], m["pool_to"], m["window"]),
            "",
            "  목표가 먼저   %s" % pc(m["p_target"]),
            "  손절가 먼저   %s" % pc(m["p_stop"]),
            "  둘 다 아님    %-8s %s까지 보유 중" % (pc(m["p_neither"]), until),
            "  수익 마감     %-8s 목표가 도달, 또는 %s에 진입가 위" % (pc(m["p_green"]), until),
            "",
            "  %s 가격: 중앙값 %s, 리플레이의 90 %%가 %s ~ %s." % (until, krx.px(m["median_close"]),
                                                          krx.px(m["p5_close"]), krx.px(m["p95_close"])),
            "  평균 R: %+.3f (비용 전), %+.3f (거래세 %.2f %% + 수수료 %.3f %% x2 후)."
            % (m["avg_R"], m["avg_R_net"], m["costs"]["sell_tax_pct"], m["costs"]["commission_pct"]),
            "  왕복 비용은 주당 약 %s원, 위험(1R)의 %.0f %%입니다." % (krx.px(m["cost_per_share"]), 100 * m["cost_R"]),
            "",
        ]
    else:
        lines = [
            "%s buy, %s: entry %s at %s (bar %d), stop %s, target %s (%gR, risk %s won a share)."
            % (who, krx.day_label(p["date"]), krx.px(p["entry"]), p["entry_time"], p["entry_bar"],
               krx.px(p["stop"]), krx.px(p["target"]), r, krx.px(p["risk_per_share"])),
            "%s replays of the rest of the session, %s to %s KST (%d five-minute bars), seed %d."
            % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"], m["seed"]),
            "Each bar is drawn whole from the stock's own 5-min bars at the same time of day (+-%d bars) over its "
            "last %d full sessions (%s to %s)." % (m["window"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
            "",
            "  target first   %s" % pc(m["p_target"]),
            "  stop first     %s" % pc(m["p_stop"]),
            "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
            "  green          %-8s target first, or above the entry at %s" % (pc(m["p_green"]), until),
            "",
            "  Price at %s: median %s, 90 %% of replays between %s and %s."
            % (until, krx.px(m["median_close"]), krx.px(m["p5_close"]), krx.px(m["p95_close"])),
            "  Average R: %+.3f before costs, %+.3f after the %.2f %% sell tax and %.3f %% commission each way."
            % (m["avg_R"], m["avg_R_net"], m["costs"]["sell_tax_pct"], m["costs"]["commission_pct"]),
            "  The round trip costs about %s won a share: %.0f %% of the risk (1R)."
            % (krx.px(m["cost_per_share"]), 100 * m["cost_R"]),
            "",
        ]
    if m.get("p_limit", 0) > 0.001:
        lines.insert(-1, ("  %.1f %%의 리플레이가 상·하한가(±30 %%)에 닿았습니다." if ko else
                          "  %.1f %% of the replays touched a daily price limit (+-30 %%).") % (100 * m["p_limit"]))
    if h.get("rule"):
        ru = h["rule"]
        if ko:
            sw = {"or_mid": "시가 범위 중간값", "or_low": "시가 범위 저점"}.get(
                ru["stop_rule"], "진입가 대비 %.2f %%" % ru.get("stop_pct", 0))
            lines.append("과거: 같은 규칙(%d분 시가 범위, 손절 %s, %gR 목표)이 실제 %d거래일 중 %d번 발동: 목표 %d, 손절 %d, "
                         "둘 다 아님 %d. 평균 R %+.3f, 비용 후 %+.3f."
                         % (ru["or_minutes"], sw, ru["target_r"], h["sessions"], h["triggered"], h["target"],
                            h["stop"], h["close"], h["avg_R"] or 0, h["avg_R_net"] or 0)
                         if h["triggered"] else "과거: 같은 규칙이 %d거래일 동안 한 번도 발동하지 않았습니다." % h["sessions"])
        else:
            sw = {"or_mid": "at the opening-range midpoint", "or_low": "at the opening-range low"}.get(
                ru["stop_rule"], "%.2f %% below the entry" % ru.get("stop_pct", 0))
            lines.append("History: the same rule (%d-min opening range, stop %s, %gR target) on those %d real "
                         "sessions fired %d times: %d target, %d stop, %d neither. Average R %+.3f, %+.3f after costs."
                         % (ru["or_minutes"], sw, ru["target_r"], h["sessions"], h["triggered"], h["target"],
                            h["stop"], h["close"], h["avg_R"] or 0, h["avg_R_net"] or 0)
                         if h["triggered"] else "History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append(("과거: " if ko else "History: ") + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append(("참고: " if ko else "Note: ") + w + ".")
    left = doc["data"]["sessions_left_out"]
    if left:
        lines.append(("제외한 거래일 %d개: %s." if ko else "Left out %d session(s): %s.")
                     % (len(left), ", ".join("%s (%s)" % (x["date"], x["why"]) for x in left[:8])))
    lines.append(("%s의 최근 %d거래일로 만든 시뮬레이션입니다. 예측이 아닙니다: 실적, 공시, VI 발동, 가격제한폭은 과거에 없던 일을 "
                  "만들 수 있습니다. 같은 시드, 같은 결과 (--seed %d)." % (p["code"], m["pool_sessions"], m["seed"]))
                 if ko else doc["note"] + " Repeat this run with --seed %d." % m["seed"])
    return lines


def render_chart(doc, height=17):
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 1
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d)" % (
        p["code"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"])]
    for r in range(height):
        lab = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(lab, hi - r * step)
        left = ("%10s |" % krx.px(price)) if (lab or r in (0, height - 1)) else "           |"
        out.append(left + "".join(grid[r]) + ("  " + lab if lab else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        lab = times[j]
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("           +" + "-" * len(bands))
    out.append("            " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- commands

def compute(a):
    try:
        cfg = krx.load_config(a.config)
        now = krx.parse_now(a.now)
    except krx.KRXError as e:
        raise TMError(str(e))
    lang = krx.lang_of(a.lang, cfg)
    plan = load_plan(a, now)
    info = {"code": plan["code"], "yahoo": plan["yahoo"], "market": plan["market"], "name": plan["name"]}
    try:
        if a.offline:
            text, source = krx.read_offline(a.offline, info), "offline"
        else:
            text, source = krx.fetch_chart(info, "60d", "5m"), "yahoo"
    except krx.KRXError as e:
        raise TMError(str(e))
    plan.update(yahoo=info["yahoo"], market=info["market"], name=plan["name"] or info.get("name", ""))
    return time_machine(text, plan, now, a.replays, a.seed, a.window, a.until, source, cfg, lang)


def cmd_run(a):
    doc = compute(a)
    if a.write:
        krx.write_json(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("다음: /krx-budget 으로 예산과 1회 최대 손실에 맞게 수량을 정하세요." if doc["lang"] == "ko"
              else "Next: /krx-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        doc = krx.read_json(a.from_file)
        if not isinstance(doc, dict) or "bands" not in (doc.get("mc") or {}):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned KRX day trade (krx-timemachine).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="krx/plan.json", help="the plan from krx-exit")
        p.add_argument("--config", default=krx.CONFIG_PATH)
        p.add_argument("--symbol", help="or give the plan as flags: a 6-digit code like 005930")
        p.add_argument("--side", default="buy", choices=["buy"])
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default today, Seoul)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help="the entry bar's close, 24-hour Seoul time, e.g. 09:45")
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "pct"])
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2)
        p.add_argument("--until", help="end each replay at this time (default 15:20)")
        p.add_argument("--offline", metavar="FILE", help="a saved Yahoo chart JSON (.json or .json.gz)")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 09:45" Seoul)')
        p.add_argument("--lang", choices=["en", "ko"], help="report language (default: config lang, else en)")

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated sessions plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="krx/timemachine.json", metavar="PATH")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="krx/timemachine.json", metavar="PATH")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except TMError as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
