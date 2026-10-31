#!/usr/bin/env python3
"""Chart Scanner for krx-scanner (Korean market): scans KOSPI and KOSDAQ charts for
setups. Standard library only.

    python3 scan.py universe                          # KRX KIND lists -> krx/universe.json
    python3 scan.py daily                             # before 09:00: daily setups -> krx/watchlist.json
    python3 scan.py daily --symbols 005930,000660,035720 # or --watchlist FILE, or --limit 200
    python3 scan.py daily --market KOSDAQ             # one market only
    python3 scan.py orb                               # after 09:20: opening-range breakouts so far
    python3 scan.py orb --at 09:45 --or-minutes 15    # cut the clock at 09:45 KST
    python3 scan.py orb --offline charts/ --now "2026-10-30 09:45"

universe  Downloads the listed-company lists from KRX's KIND site, once for KOSPI
          (유가증권시장) and once for KOSDAQ (코스닥시장), and writes
          krx/universe.json: {code: {name, market, sector}}. SPACs (스팩) are left
          out, and so is anything without a six-character KRX code. KONEX is out.
          With the list in place every tool accepts a company name as well as a code.

daily     Daily closes from Yahoo's spark endpoint (20 symbols a request, 3 months
          of 1-day bars). Liquidity: last price >= --min-price (1,000 won) and
          trading value (price x volume) >= --min-value (1,000,000,000 won = 10억).
          Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the last 5,
                             then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 under 30 yesterday and today closed up
            limit_up         the last close was up 29.5 % or more: at or near the
                             upper limit (상한가). Watched, rarely calm the next day.
          Today's daily bar is dropped until 15:30.

orb       Opening-range breakouts on 5-minute bars, 09:00 KST. Stage 1: spark closes
          for every symbol; a chart goes on only if a bar after the range closed at
          or above the range's highest close. Stage 2: the candidate's v8 chart (real
          highs and lows): the first completed bar to CLOSE above the range high is
          the hit. At most --max-confirm (200) charts, most traded first. KRX stocks
          that skip a five-minute window are allowed up to 10 % missing bars.

Times are Seoul (KST). --now pins the clock. Yahoo is free and unofficial; KIND is
public. --offline reads files instead of the network.
A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import datetime
import gzip
import html.parser
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13&marketType=%s"
KIND_MARKETS = (("KOSPI", "stockMkt"), ("KOSDAQ", "kosdaqMkt"))
SPARK_MAX = 20
MAX_CONFIRM = 200
MIN_DAILY_BARS = 30
BAR_MIN = 5
WATCHLIST = "krx/watchlist.json"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
SETUPS = [("breakout_20d", "20-day closing high"), ("bull_flag", "bull flag"),
          ("reclaim_ma20", "reclaimed the 20-day average"), ("oversold", "oversold (RSI < 30)"),
          ("oversold_bounce", "oversold bounce"), ("limit_up", "closed at the upper limit (상한가)")]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- universe (KIND)

class _Table(html.parser.HTMLParser):
    def __init__(self):
        html.parser.HTMLParser.__init__(self)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def decode(data):
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp949", "replace")


def parse_kind(text, market):
    """KIND's corpList download (an HTML table) -> [{code, name, market, sector}], dropped counts."""
    t = _Table()
    t.feed(decode(text))
    if not t.rows:
        raise ScanError("KIND's list is not the table this tool knows")
    hdr = t.rows[0]
    try:
        ci, ni = hdr.index("종목코드"), hdr.index("회사명")
    except ValueError:
        raise ScanError("KIND's list has no 종목코드 / 회사명 columns (header: %s)" % ", ".join(hdr[:6]))
    si = hdr.index("업종") if "업종" in hdr else None
    mi = hdr.index("시장구분") if "시장구분" in hdr else None
    out, dropped = [], {}
    for r in t.rows[1:]:
        if len(r) <= max(ci, ni):
            continue
        code = r[ci].strip().upper().zfill(6)
        name = r[ni].strip()
        mk = market
        if mi is not None and len(r) > mi and r[mi]:
            mk = {"유가": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}.get(r[mi][:3].strip(), r[mi]) or market
        why = None
        if not krx.CODE_RE.match(code):
            why = "not a KRX code"
        elif "스팩" in name or "SPAC" in name.upper():
            why = "SPAC (스팩)"
        elif mk == "KONEX":
            why = "KONEX"
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        out.append({"code": code, "name": name, "market": mk, "sector": r[si] if si is not None and len(r) > si else ""})
    return out, dropped


def build_universe(texts):
    """{market: html} -> ({code: info}, counts)."""
    stocks, counts = {}, {"dropped": {}}
    lists = {}
    for market, _ in KIND_MARKETS:
        if market not in texts:
            raise ScanError("missing the %s list" % market)
        rows, dropped = parse_kind(texts[market], market)
        lists[market] = rows
        for k, v in dropped.items():
            counts["dropped"][k] = counts["dropped"].get(k, 0) + v
    a, b = {r["code"] for r in lists["KOSPI"]}, {r["code"] for r in lists["KOSDAQ"]}
    same = a == b and a
    if same:
        counts["warning"] = ("KIND sent the same list for both markets, so the market is unknown; tools try "
                             ".KS then .KQ on Yahoo")
    for market, _ in KIND_MARKETS:
        for r in lists[market]:
            stocks[r["code"]] = {"name": r["name"], "market": "" if same else r["market"], "sector": r["sector"]}
    counts["KOSPI"] = sum(1 for v in stocks.values() if v["market"] == "KOSPI")
    counts["KOSDAQ"] = sum(1 for v in stocks.values() if v["market"] == "KOSDAQ")
    return stocks, counts


# ---------------------------------------------------------------- Yahoo data

def yahoo_get(path):
    try:
        return krx.yahoo_text(lambda host: "https://%s.finance.yahoo.com%s" % (host, path))
    except krx.KRXError as e:
        raise ScanError(str(e))


def spark_path(ysyms, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        urllib.parse.quote(",".join(ysyms), safe=","), rng, interval)


def series_from_result(res):
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def code_of(ysym):
    return ysym.upper().split(".")[0]


def parse_spark(doc):
    doc = json.loads(doc) if isinstance(doc, str) else doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        out[code_of(ysym)] = series_from_result(resp[0])
    if sp.get("error") and not out:
        raise ScanError("Yahoo spark: %s" % (sp["error"].get("description") or sp["error"]))
    return out


def parse_chart(doc, code=""):
    try:
        res = krx.chart_result(doc, code)
    except krx.KRXError as e:
        raise ScanError(str(e))
    return series_from_result(res)


def _read(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _absorb(doc, hint, data, errors):
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        code = hint
        if not code:
            try:
                code = code_of(doc["chart"]["result"][0]["meta"]["symbol"])
            except (KeyError, TypeError, IndexError):
                raise ScanError("an offline chart without a symbol: name the file <CODE>.json")
        try:
            data[code_of(code)] = parse_chart(doc, code)
        except ScanError as e:
            errors[code_of(code)] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a chart or a bundle" % hint)


def load_offline(path):
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            stem = re.sub(r"(-\w+)?\.json(\.gz)?$", "", f)
            _absorb(doc, "" if "spark" in doc else stem, data, errors)
    elif os.path.isfile(path):
        _absorb(json.loads(_read(path)), "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


def fetch_spark(stocks, rng, interval, pause, a=None):
    """stocks = [(code, yahoo_symbol)] -> ({code: series}, {code: error})."""
    data, failed = {}, {}
    batches = [stocks[i:i + SPARK_MAX] for i in range(0, len(stocks), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            krx.SLEEP(pause)
        try:
            got = parse_spark(yahoo_get(spark_path([y for _, y in batch], rng, interval)))
        except (ScanError, ValueError) as e:
            for c, _ in batch:
                failed[c] = str(e)
            continue
        for c, _ in batch:
            if c in got:
                data[c] = got[c]
            else:
                failed[c] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 25 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc):
    """[(date, close)] of complete daily bars; today's bar only after 15:30."""
    now = krx.to_kst(now_utc)
    today = now.date()
    s = krx.session(today)
    today_done = s is None or now.hour * 60 + now.minute >= s["close"]
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = krx.to_kst(krx.utc_of(ts)).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)
    return sorted(out.items())


def find_setups(closes):
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = rsi(c), rsi(c[:-1])
    day = last / c[-2] - 1
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    if day >= 0.295:
        tags.append("limit_up")
    return tags, {"rsi": r, "high20": hi20, "ma20": ma20, "run_pct": run * 100, "range5_pct": rng5 * 100,
                  "day_pct": day * 100}


def scan_daily(data, codes, now_utc, min_price, min_value):
    hits, dates = [], {}
    counts = {"requested": len(codes), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for code in codes:
        s = data.get(code)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = bars[-1][1] if bars else meta.get("regularMarketPrice")
        vol = meta.get("regularMarketVolume")
        if vol is None and s.get("v"):
            vs = [v for v in s["v"] if v]
            vol = vs[-1] if vs else None
        value = (price or 0) * (vol or 0)
        if price is None or price < min_price or (min_value > 0 and value < min_value):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": code, "code": code, "setups": tags, "price": closes[-1],
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1) if nums["rsi"] else None,
                         "day_pct": round(nums["day_pct"], 2), "volume": int(vol) if vol else None,
                         "value_krw": int(value)})
    hits.sort(key=lambda h: (-h["value_krw"], h["code"]))
    counts["hits"] = len(hits)
    return hits, counts, (max(dates, key=lambda d: (dates[d], d)) if dates else None)


# ---------------------------------------------------------------- opening-range breakout

def day_bars(series, day, cutoff_min):
    """{slot: bar} of `day`'s continuous-session 5-minute bars complete by cutoff_min."""
    s = krx.session(day)
    out = {}
    hs, ls, vs = series.get("h"), series.get("l"), series.get("v")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None or ts % (BAR_MIN * 60):
            continue
        local = krx.to_kst(krx.utc_of(ts))
        if local.date() != day:
            continue
        m = local.hour * 60 + local.minute
        if m < s["open"] or m + BAR_MIN > s["cont_end"] or m + BAR_MIN > cutoff_min:
            continue
        k = (m - s["open"]) // BAR_MIN
        out[k] = {"slot": k, "start_min": m, "c": float(c),
                  "h": None if not hs or hs[i] is None else float(hs[i]),
                  "l": None if not ls or ls[i] is None else float(ls[i]),
                  "v": (vs[i] or 0) if vs else 0}
    return out


def usable(bars, day, cutoff_min, n_range, max_missing=0.10):
    """A full list of slots 0..want-1 with gaps filled flat, or None."""
    s = krx.session(day)
    want = (min(cutoff_min, s["cont_end"]) - s["open"]) // BAR_MIN
    if want < n_range or any(k not in bars for k in range(n_range)):
        return None
    if len([k for k in bars if k < want]) < want * (1 - max_missing):
        return None
    out = []
    for k in range(want):
        b = bars.get(k)
        if b is None:
            p = out[-1]
            b = {"slot": k, "start_min": s["open"] + k * BAR_MIN, "c": p["c"], "h": p["c"], "l": p["c"], "v": 0,
                 "filled": True}
        out.append(b)
    return out


def orb_candidate(bars, n):
    top = max(b["c"] for b in bars[:n])
    return any(b["c"] >= top for b in bars[n:])


def orb_hit(bars, n):
    rng = bars[:n]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh, ol = max(b["h"] for b in rng), min(b["l"] for b in rng)
    for b in bars[n:]:
        if b["c"] > oh and not b.get("filled"):
            return {"price": b["c"], "at": krx.hm(b["start_min"] + BAR_MIN), "bar_start": krx.hm(b["start_min"]),
                    "bar_index": b["slot"], "or_high": oh, "or_low": ol,
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": b["start_min"]}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    now = krx.to_kst(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    s = krx.session(day)
    if s is None:
        raise ScanError("No session on %s (%s): KRX is closed. Replay a past day with --offline --now."
                        % (krx.day_label(day), krx.holiday(day)))
    range_end = s["open"] + or_minutes
    first_close = range_end + BAR_MIN
    now_min = now.hour * 60 + now.minute
    if at is not None:
        cutoff = at + (s["open"] - krx.OPEN_MIN)
        if explicit_day is None and cutoff > now_min:
            raise ScanError("--at %s is later than now (%s KST)." % (krx.hm(cutoff), krx.hm(now_min)))
    else:
        cutoff = now_min
    if cutoff >= s["cont_end"]:
        raise ScanError("Continuous trading ended at %s on %s. Replay the session with --at 09:45."
                        % (krx.hm(s["cont_end"]), krx.day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s KST: the %d-minute opening range runs %s to %s and the first bar after it "
                        "closes at %s. Run orb again then." % (krx.hm(cutoff), or_minutes, krx.hm(s["open"]),
                                                              krx.hm(range_end), krx.hm(first_close)))
    if until is not None and cutoff > until + (s["open"] - krx.OPEN_MIN):
        raise ScanError("It is %s KST, after %s, your last time for new entries (no_entries_after)."
                        % (krx.hm(cutoff), krx.hm(until)))
    return day, cutoff


def scan_orb(stage1, codes, day, cutoff, or_minutes, min_price=0.0, min_value=0, confirm=None,
             max_confirm=None):
    n = or_minutes // BAR_MIN
    counts = {"requested": len(codes), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for code in codes:
        s = stage1.get(code)
        if not s:
            continue
        counts["fetched"] += 1
        bars = usable(day_bars(s, day, cutoff), day, cutoff, n)
        if not bars:
            counts["unusable"] += 1
            continue
        value = sum(b["c"] * b["v"] for b in bars)
        if bars[-1]["c"] < min_price or value < min_value:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n):
            cands.append((code, bars, value))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for code, bars, value in cands:
        full = bars
        if confirm is not None:
            try:
                full = usable(day_bars(confirm(code), day, cutoff), day, cutoff, n)
            except ScanError as e:
                failed[code] = str(e)
                continue
            if not full:
                failed[code] = "the chart has too many missing bars"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n)
        if h:
            hits.append(dict({"symbol": code, "code": code, "setups": ["orb"], "value_krw": int(value)}, **h))
    hits.sort(key=lambda x: (x["_t"], x["code"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- symbols

def read_codes(a, universe, offline_codes=None):
    if a.symbols:
        raw = [s for s in re.split(r"[,\s]+", a.symbols) if s]
    elif a.watchlist:
        doc = krx.read_json(a.watchlist)
        if doc is None:
            raise ScanError("no file at %s" % a.watchlist)
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        raw = [str(x.get("code") or x.get("symbol")) if isinstance(x, dict) else str(x) for x in items]
    elif offline_codes is not None and not universe:
        raw = sorted(offline_codes)
    else:
        if not universe:
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % krx.UNIVERSE_PATH)
        raw = sorted(c for c, i in universe.items() if not a.market or i.get("market") == a.market)
        if offline_codes is not None:
            raw = [c for c in raw if c in offline_codes]
    out = []
    for r in raw:
        try:
            info = krx.resolve(r, universe)
        except krx.KRXError as e:
            raise ScanError(str(e))
        if info["code"] not in [c for c, _ in out]:
            out.append((info["code"], info["yahoo"] if "." in info["yahoo"] else info["code"] + ".KS"))
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def names(universe, code):
    return (universe.get(code) or {}).get("name", "")


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    texts = {}
    for k, (market, mtype) in enumerate(KIND_MARKETS):
        if a.offline:
            p = os.path.join(a.offline, "kind-%s.html" % market.lower())
            if not os.path.exists(p):
                raise ScanError("no %s in %s" % (os.path.basename(p), a.offline))
            with open(p, "rb") as fh:
                texts[market] = fh.read()
        else:
            if k and a.pause:
                krx.SLEEP(a.pause)
            try:
                texts[market] = krx.http_get(KIND_URL % mtype, timeout=60, binary=True)
            except Exception as e:
                raise ScanError("could not download the %s list from kind.krx.co.kr: %s" % (market, e))
    stocks, counts = build_universe(texts)
    doc = {"created_by": "krx-scanner", "created_at": krx.iso_utc(krx.utc_now()), "source": "kind.krx.co.kr",
           "count": len(stocks), "stocks": stocks}
    if not a.no_write:
        krx.write_json(a.out, doc)
    if a.json:
        print(json.dumps({k: v for k, v in dict(doc, counts=counts).items() if k != "stocks"}, indent=2,
                         ensure_ascii=False))
    else:
        print("KRX KIND listed companies: %s KOSPI, %s KOSDAQ (%s total)." % (
            "{:,}".format(counts["KOSPI"]), "{:,}".format(counts["KOSDAQ"]), "{:,}".format(len(stocks))))
        if counts["dropped"]:
            print("Left out: " + ", ".join("%s %s" % ("{:,}".format(v), k) for k, v in counts["dropped"].items()) + ".")
        if counts.get("warning"):
            print("Note: " + counts["warning"] + ".")
        if not a.no_write:
            print("Wrote %s. Every tool now accepts names like 삼성전자 as well as codes." % a.out)
    return 0 if stocks else 1


def cmd_daily(a):
    now = krx.parse_now(a.now)
    universe = krx.load_universe(a.universe)
    offline_codes = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_codes = set(data) | set(failed)
    stocks = read_codes(a, universe, offline_codes)
    if not a.offline:
        progress(a, "Fetching daily closes for %s stocks (%d requests)..." % (
            "{:,}".format(len(stocks)), (len(stocks) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(stocks, "3mo", "1d", a.pause, a)
    codes = [c for c, _ in stocks]
    hits, counts, last_date = scan_daily(data, codes, now, a.min_price, a.min_value)
    for h in hits:
        h["name"] = names(universe, h["code"]) or ((data.get(h["code"]) or {}).get("meta") or {}).get("longName", "")
    doc = {"created_by": "krx-scanner", "created_at": krx.iso_utc(now), "mode": "daily",
           "as_of": ("%s close" % last_date.isoformat()) if last_date else None, "universe": len(codes),
           "scanned": counts["scanned"], "failed": len(codes) - counts["fetched"], "source": "yahoo",
           "filters": {"min_price": a.min_price, "min_value_krw": a.min_value}, "note": NOTE, "hits": hits}
    if not a.no_write:
        krx.write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Daily scan, %s KST, closes through %s (Yahoo, daily closes only)." % (
        krx.to_kst(now).strftime("%Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts. %s passed the liquidity filter (%s won+ and %s won traded); %s scanned." % (
        "{:,}".format(counts["fetched"]), "{:,}".format(len(codes)), "{:,}".format(counts["fetched"] - counts["illiquid"]),
        krx.px(a.min_price), "{:,}".format(int(a.min_value)), "{:,}".format(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched: %s. Most traded first:" % (len(hits), "" if len(hits) == 1 else "s",
                                                             ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %s %-14s %10s  RSI %5s  %s" % (h["code"], (h["name"] or "")[:14], krx.px(h["price"]),
                                                "%.1f" % h["rsi"] if h["rsi"] is not None else "-",
                                                ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s." % a.out)
    print(NOTE + " Next: pick one chart, then /krx-news CODE.")
    return 0 if hits else 1


def cmd_orb(a):
    now = krx.parse_now(a.now)
    cfg = krx.load_config(a.config)
    universe = krx.load_universe(a.universe)
    at = krx.hhmm_min(a.at, "--at") if a.at else None
    until = krx.hhmm_min(a.until or cfg.get("no_entries_after") or "15:00", "--until")
    day, cutoff = orb_window(now, at, a.or_minutes, until,
                             explicit_day=krx.to_kst(now).date() if (a.offline or a.now) and at is not None else None)
    offline_codes = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_codes = set(data) | set(failed)
    stocks = read_codes(a, universe, offline_codes)
    codes = [c for c, _ in stocks]
    ymap = dict(stocks)
    confirm = None
    if not a.offline:
        data, failed = fetch_spark(stocks, "1d", "5m", a.pause, a)

        def confirm(code):
            krx.SLEEP(a.pause)
            return parse_chart(yahoo_get("/v8/finance/chart/%s?range=1d&interval=5m" % ymap[code]), code)
    hits, counts, failed2 = scan_orb(data, codes, day, cutoff, a.or_minutes, a.min_price, a.min_value, confirm,
                                     a.max_confirm)
    for h in hits:
        h["name"] = names(universe, h["code"])
    doc = {"created_by": "krx-scanner", "created_at": krx.iso_utc(now), "mode": "orb",
           "as_of": "%s %s KST" % (day.isoformat(), krx.hm(cutoff)), "universe": len(codes),
           "scanned": counts["scanned"], "unconfirmed": counts["unconfirmed"], "source": "yahoo",
           "or_minutes": a.or_minutes, "interval": "5m",
           "filters": {"min_price": a.min_price, "min_value_krw": a.min_value}, "note": NOTE, "hits": hits}
    if not a.no_write:
        krx.write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    s = krx.session(day)
    print("Opening-range breakout scan, %s %s KST: %d-minute range (%s to %s), 5-minute bars, Yahoo."
          % (krx.day_label(day), krx.hm(cutoff), a.or_minutes, krx.hm(s["open"]), krx.hm(s["open"] + a.or_minutes)))
    print("Fetched %s of %s charts; %s usable; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(codes)), "{:,}".format(counts["scanned"]),
             "{:,}".format(counts["candidates"]), "{:,}".format(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (--max-confirm 0 checks all)." % counts["unconfirmed"])
    for h in hits[:a.show]:
        print("  %5s  %s %-14s %10s  range %s to %s  %+.2f%% over" % (
            h["at"], h["code"], (h["name"] or "")[:14], krx.px(h["price"]), krx.px(h["or_low"]),
            krx.px(h["or_high"]), h["pct_over"]))
    if not hits:
        print("No chart closed above its opening-range high by %s." % krx.hm(cutoff))
    if not a.no_write:
        print("Wrote %s." % a.out)
    print(NOTE + " Next: /krx-news CODE and /krx-entry CODE.")
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (krx-scanner): KOSPI and KOSDAQ setups.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("universe", help="download KRX KIND's lists -> krx/universe.json")
    p.add_argument("--out", default=krx.UNIVERSE_PATH)
    p.add_argument("--offline", metavar="DIR", help="a folder with kind-kospi.html and kind-kosdaq.html")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=krx.PAUSE)
    p.add_argument("--json", action="store_true")

    def common(p, min_value):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated codes or names instead of the universe")
        g.add_argument("--watchlist", metavar="FILE")
        p.add_argument("--universe", default=krx.UNIVERSE_PATH)
        p.add_argument("--market", choices=["KOSPI", "KOSDAQ"])
        p.add_argument("--limit", type=int)
        p.add_argument("--min-price", type=float, default=1000)
        p.add_argument("--min-value", type=float, default=min_value, help="minimum won traded")
        p.add_argument("--offline", metavar="PATH")
        p.add_argument("--now")
        p.add_argument("--out", default=WATCHLIST)
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=krx.PAUSE)
        p.add_argument("--show", type=int, default=50)
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily")
    common(p, 1000000000)
    p = sub.add_parser("orb")
    common(p, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this Seoul time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config, 15:00)")
    p.add_argument("--config", default=krx.CONFIG_PATH)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except (ScanError, krx.KRXError) as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
