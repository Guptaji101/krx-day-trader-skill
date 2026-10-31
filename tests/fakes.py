"""Deterministic, synthetic Yahoo-format data for the tests.

The sandbox these tools were built in could not reach Yahoo or DART, so the tests
run on generated data shaped like the real thing: Yahoo v8 chart JSON for a KRX
code, five-minute bars 09:00-15:20 Seoul time (UTC 00:00-06:20), a closing-auction
bar at 15:30, prices on the KRX tick grid, a few missing bars for thin stocks.
Real responses can differ in details; the parsers are written to be tolerant.
"""

import datetime
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "common"))
import krx  # noqa: E402


def trading_days(end, n):
    """n KRX trading days ending on `end` (inclusive), oldest first."""
    d = datetime.date.fromisoformat(end) if isinstance(end, str) else end
    out = []
    while len(out) < n:
        if krx.trading_day(d):
            out.append(d)
        d -= datetime.timedelta(days=1)
    return out[::-1]


def chart(code="005930", market="KOSPI", end="2026-10-30", days=60, start_price=98000.0, seed=1,
          interval=5, until=None, missing=0.0, auction=True, drift=0.0, vol=0.0025, name="Samsung Electronics"):
    """Yahoo v8 chart JSON (dict). until='HH:MM' cuts the last day at that bar start."""
    rng = random.Random(seed)
    sym = code + krx.SUFFIX[market]
    ts, o_, h_, l_, c_, v_ = [], [], [], [], [], []
    p = start_price
    prev_close = start_price
    for d in trading_days(end, days):
        s = krx.session(d)
        gap = rng.gauss(0, 0.006)
        p = krx.round_tick(prev_close * (1 + gap))
        n = (s["cont_end"] - s["open"]) // interval
        stop_at = krx.hhmm_min(until) if (until and d.isoformat() == end) else None
        for k in range(n):
            start_min = s["open"] + k * interval
            if stop_at is not None and start_min > stop_at:
                break
            u = 1.6 if k < 6 else (1.3 if k > n - 6 else 1.0)
            if missing and k > 0 and rng.random() < missing:
                rng.gauss(0, 1)
                continue
            o = p
            ret = rng.gauss(drift, vol * u)
            c = krx.round_tick(max(10, o * (1 + ret)))
            wick = abs(rng.gauss(0, vol * u * 0.6))
            h = krx.round_tick(max(o, c) * (1 + wick), "up")
            l = krx.round_tick(min(o, c) * (1 - abs(rng.gauss(0, vol * u * 0.6))), "down")
            local = datetime.datetime.combine(d, datetime.time(*divmod(start_min, 60)))
            ts.append(krx.ts_of(krx.kst_to_utc(local)))
            o_.append(float(o)); h_.append(float(h)); l_.append(float(l)); c_.append(float(c))
            v_.append(int(abs(rng.gauss(50000, 20000)) * u))
            p = c
        if auction and stop_at is None:
            ac = krx.round_tick(p * (1 + rng.gauss(0, vol * 0.3)))
            local = datetime.datetime.combine(d, datetime.time(*divmod(s["close"], 60)))
            ts.append(krx.ts_of(krx.kst_to_utc(local)))
            o_.append(float(ac)); h_.append(float(ac)); l_.append(float(ac)); c_.append(float(ac))
            v_.append(300000)
            p = ac
        prev_close = p
    last = ts[-1] + interval * 60 - 2      # the last trade, just before the last bar closes
    meta = {"currency": "KRW", "symbol": sym, "exchangeName": "KSC" if market == "KOSPI" else "KOE",
            "instrumentType": "EQUITY", "regularMarketTime": last, "gmtoffset": 32400,
            "timezone": "KST", "exchangeTimezoneName": "Asia/Seoul", "regularMarketPrice": c_[-1],
            "chartPreviousClose": start_price, "dataGranularity": "%dm" % interval,
            "longName": name, "shortName": name}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o_, "high": h_, "low": l_, "close": c_,
                                                            "volume": v_}]}}], "error": None}}


def daily(code="005930", market="KOSPI", end="2026-10-30", days=70, start_price=90000.0, seed=3,
          trend=0.0, last_moves=None):
    """Daily-bar Yahoo chart JSON. last_moves = list of returns forced on the final days."""
    rng = random.Random(seed)
    ts, o_, h_, l_, c_, v_ = [], [], [], [], [], []
    p = start_price
    dlist = trading_days(end, days)
    forced = list(last_moves or [])
    for i, d in enumerate(dlist):
        j = i - (len(dlist) - len(forced))
        r = forced[j] if j >= 0 else rng.gauss(trend, 0.015)
        o = p
        c = krx.round_tick(o * (1 + r))
        h = krx.round_tick(max(o, c) * 1.005, "up")
        l = krx.round_tick(min(o, c) * 0.995, "down")
        local = datetime.datetime.combine(d, datetime.time(9, 0))
        ts.append(krx.ts_of(krx.kst_to_utc(local)))
        o_.append(float(o)); h_.append(float(h)); l_.append(float(l)); c_.append(float(c))
        v_.append(int(abs(rng.gauss(2e6, 5e5))))
        p = c
    meta = {"currency": "KRW", "symbol": code + krx.SUFFIX[market], "dataGranularity": "1d",
            "regularMarketTime": ts[-1], "regularMarketPrice": c_[-1], "chartPreviousClose": start_price}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o_, "high": h_, "low": l_, "close": c_,
                                                            "volume": v_}]}}], "error": None}}


def dump(doc, path):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    return path
