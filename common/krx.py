#!/usr/bin/env python3
"""krx.py: the Korean market (KRX: KOSPI and KOSDAQ) for the krx-day-trader skills.
Standard library only. One copy of this file lives in every skill folder so each
skill installs on its own; tools/sync_krx.py copies common/krx.py over them and a
test checks they are identical.

What it knows, and where each number comes from (read 2026-10-31):

  Time        Korea Standard Time is UTC+9 all year. No daylight saving.
  Session     Opening call auction 08:30-09:00, continuous trading 09:00-15:20,
              closing call auction 15:20-15:30 (one price at 15:30).
              The tools trade the continuous session; a stop or a target cannot
              fill bar by bar during the closing auction.
              Extended hours (NXT 08:00-20:00, KRX after-market 16:00-20:00 from
              2026-09-14) are out of scope: thin books, limit orders only.
  Holidays    KRX's 2026 weekday closures (17 days, last trading day 2026-12-30).
              Add more years with "extra_holidays" in krx/config.json.
  Late opens  2026-01-02 opens at 10:00 (first trading day). The college entrance
              exam day (수능) moves the whole session one hour later, 10:00-16:30;
              2026's exam is on 2026-11-19 (verify each year; override with
              "special_sessions" in krx/config.json).
  Ticks       One table for KOSPI and KOSDAQ since 2023-01-25:
              < 2,000: 1 | < 5,000: 5 | < 20,000: 10 | < 50,000: 50 |
              < 200,000: 100 | < 500,000: 500 | otherwise 1,000 won.
  Limits      A stock can move at most 30 % from the previous close (기준가) in a
              day. The tools approximate the limit as base x 1.3 rounded down to a
              tick (상한가) and base x 0.7 rounded up to a tick (하한가).
  Costs       Securities transaction tax on every SELL: 0.20 % in 2026 on KOSPI
              (0.05 % tax + 0.15 % rural special tax) and on KOSDAQ; 0.15 % in 2025.
              Plus your broker's commission on both sides (default 0.015 %).
              No capital-gains tax for ordinary holders of listed Korean shares.
  Symbols     A six-character code (005930, or a newer alphanumeric one). Yahoo
              Finance wants 005930.KS for KOSPI and 247540.KQ for KOSDAQ.
"""

import datetime
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
UA = "Mozilla/5.0 (compatible; krx-day-trader-skill/%s)" % VERSION
DATA_DIR = "krx"
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
UNIVERSE_PATH = os.path.join(DATA_DIR, "universe.json")

KST = datetime.timedelta(hours=9)
EPOCH = datetime.datetime(1970, 1, 1)

OPEN_MIN = 9 * 60          # 09:00
CONT_END_MIN = 15 * 60 + 20  # 15:20, the closing auction starts
CLOSE_MIN = 15 * 60 + 30   # 15:30, the closing price
BAR = 5                    # minutes per bar for the session tools
SESSION_BARS = (CONT_END_MIN - OPEN_MIN) // BAR   # 76 continuous five-minute bars

HOLIDAYS = {  # KRX weekday closures, from the exchange's 2026 calendar
    "2026-01-01": "New Year's Day (신정)",
    "2026-02-16": "Seollal (설날)", "2026-02-17": "Seollal (설날)", "2026-02-18": "Seollal (설날)",
    "2026-03-02": "Independence Movement Day, substitute (삼일절 대체휴일)",
    "2026-05-01": "Labour Day (노동절)",
    "2026-05-05": "Children's Day (어린이날)",
    "2026-05-25": "Buddha's Birthday, substitute (부처님오신날 대체휴일)",
    "2026-06-03": "Local elections (전국동시지방선거)",
    "2026-07-17": "Constitution Day (제헌절)",
    "2026-08-17": "Liberation Day, substitute (광복절 대체휴일)",
    "2026-09-24": "Chuseok (추석)", "2026-09-25": "Chuseok (추석)",
    "2026-10-05": "National Foundation Day, substitute (개천절 대체휴일)",
    "2026-10-09": "Hangeul Day (한글날)",
    "2026-12-25": "Christmas (성탄절)",
    "2026-12-31": "Year-end closing (연말 휴장일)",
}
CALENDAR_YEARS = (2026,)

SPECIAL_SESSIONS = {  # date: (open, close) in minutes; continuous trading ends 10 minutes before close
    "2026-01-02": (10 * 60, 15 * 60 + 30, "first trading day, opens at 10:00"),
    "2026-11-19": (10 * 60, 16 * 60 + 30, "college entrance exam (수능), 10:00-16:30"),
}

TICKS = ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500))
LIMIT_PCT = 0.30
SELL_TAX = {2025: 0.0015, 2026: 0.0020}   # fraction of the sale amount
DEFAULT_COMMISSION_PCT = 0.015            # percent, each side

CODE_RE = re.compile(r"^[0-9][0-9A-Z]{4}[0-9A-Z]$")
SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
MARKET_OF_SUFFIX = {".KS": "KOSPI", ".KQ": "KOSDAQ"}

DEFAULTS = {
    "lang": "en",
    "budget": 5000000,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_krw": 4000000,
    "min_order_krw": 10000,
    "max_loss_per_trade_krw": 50000,
    "max_daily_loss_krw": 150000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:15",
    "no_entries_after": "15:00",
    "flat_by": "15:15",
    "commission_pct": DEFAULT_COMMISSION_PCT,
    "include_costs": True,
    "paper": True,
    "blocked": [],
    "allow_margin": False,
    "allow_short": False,
    "dart_api_key": "",
    "extra_holidays": {},
    "special_sessions": {},
}


class KRXError(Exception):
    pass


# ---------------------------------------------------------------- config and files

def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise KRXError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    raw = read_json(path, {})
    if not isinstance(raw, dict):
        raise KRXError("%s is not a JSON object" % path)
    cfg.update(raw)
    for d, why in (cfg.get("extra_holidays") or {}).items():
        HOLIDAYS[str(d)] = str(why)
        CALENDAR_YEARS_EXTRA.add(int(str(d)[:4]))
    for d, v in (cfg.get("special_sessions") or {}).items():
        SPECIAL_SESSIONS[str(d)] = (hhmm_min(v[0], "special_sessions"), hhmm_min(v[1], "special_sessions"),
                                    v[2] if len(v) > 2 else "special session")
    return cfg


CALENDAR_YEARS_EXTRA = set()


def dart_key(cfg=None, cli=None):
    key = cli or os.environ.get("DART_API_KEY") or (cfg or {}).get("dart_api_key") or ""
    return key.strip()


# ---------------------------------------------------------------- time (KST = UTC+9, no DST)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc - EPOCH).total_seconds())


def to_kst(utc):
    return utc + KST


def kst_to_utc(local):
    return local - KST


def parse_now(text):
    """'2026-10-30 10:15' (Seoul time) or ISO with Z / an offset -> naive UTC. None -> now."""
    if not text:
        return utc_now()
    t = str(text).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise KRXError('--now wants a time like "2026-10-30 10:15" (Seoul) or ...Z (UTC), not %r' % text)
    if dt.tzinfo is None:
        return kst_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def iso_utc(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_kst(utc):
    return to_kst(utc).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def hhmm_min(text, what="time"):
    """'09:15', '9:15', '15:20' -> minutes after midnight (24-hour clock; Korea uses it)."""
    if isinstance(text, int):
        return text
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise KRXError("%s wants a 24-hour time like 09:15 or 15:15, not %r" % (what, text))
    return int(m.group(1)) * 60 + int(m.group(2))


def hm(minutes):
    return "%02d:%02d" % divmod(int(minutes), 60)


WEEKDAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_KO = ("월", "화", "수", "목", "금", "토", "일")


def day_label(d, lang="en"):
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d)
    wd = (WEEKDAYS_KO if lang == "ko" else WEEKDAYS_EN)[d.weekday()]
    return "%s (%s)" % (d.isoformat(), wd)


# ---------------------------------------------------------------- the calendar

def holiday(d):
    """The reason the exchange is shut on date d, or None."""
    key = d.isoformat() if hasattr(d, "isoformat") else str(d)
    if datetime.date.fromisoformat(key).weekday() >= 5:
        return "weekend"
    return HOLIDAYS.get(key)


def calendar_known(d):
    y = (d if hasattr(d, "year") else datetime.date.fromisoformat(str(d))).year
    return y in CALENDAR_YEARS or y in CALENDAR_YEARS_EXTRA


def trading_day(d):
    return holiday(d) is None


def session(d):
    """{open, cont_end, close, note} in minutes for a trading day, None on a closed day."""
    key = d.isoformat() if hasattr(d, "isoformat") else str(d)
    if not trading_day(key):
        return None
    if key in SPECIAL_SESSIONS:
        o, c, note = SPECIAL_SESSIONS[key]
        return {"open": o, "cont_end": c - 10, "close": c, "note": note}
    return {"open": OPEN_MIN, "cont_end": CONT_END_MIN, "close": CLOSE_MIN, "note": ""}


def next_trading_day(d):
    d = d if hasattr(d, "isoformat") else datetime.date.fromisoformat(str(d))
    for _ in range(15):
        d += datetime.timedelta(days=1)
        if trading_day(d):
            return d
    return d


def prev_trading_day(d):
    d = d if hasattr(d, "isoformat") else datetime.date.fromisoformat(str(d))
    for _ in range(15):
        d -= datetime.timedelta(days=1)
        if trading_day(d):
            return d
    return d


def market_status(now_utc):
    """Where the market is right now, Seoul time."""
    local = to_kst(now_utc)
    today = local.date()
    mins = local.hour * 60 + local.minute
    s = session(today)
    out = {"now_kst": local.strftime("%Y-%m-%d %H:%M"), "date": today.isoformat(), "minutes": mins,
           "calendar_known": calendar_known(today)}
    if s is None:
        out.update(phase="closed", reason=holiday(today), next_open=next_trading_day(today).isoformat())
    elif mins < s["open"] - 30:
        out.update(phase="pre-open", opens=hm(s["open"]))
    elif mins < s["open"]:
        out.update(phase="opening-auction", opens=hm(s["open"]))
    elif mins < s["cont_end"]:
        out.update(phase="open", closes=hm(s["close"]), auction_at=hm(s["cont_end"]))
    elif mins < s["close"]:
        out.update(phase="closing-auction", closes=hm(s["close"]))
    else:
        out.update(phase="closed", reason="after the close", next_open=next_trading_day(today).isoformat())
    if s:
        out["session"] = {"open": hm(s["open"]), "cont_end": hm(s["cont_end"]), "close": hm(s["close"]),
                          "note": s["note"]}
    return out


# ---------------------------------------------------------------- prices: ticks, limits, costs

def tick_size(price):
    for top, tick in TICKS:
        if price < top:
            return tick
    return 1000


def round_tick(price, mode="nearest"):
    """Round a won price onto the KRX tick grid: 'down', 'up' or 'nearest'."""
    p = float(price)
    if p <= 0:
        return 0
    t = tick_size(p)
    q = p / t
    if mode == "down":
        v = math.floor(q + 1e-9) * t
    elif mode == "up":
        v = math.ceil(q - 1e-9) * t
    else:
        v = math.floor(q + 0.5) * t
        if tick_size(v) != t and v > 0:          # crossed into the next band
            v = math.floor(v / tick_size(v) + 0.5) * tick_size(v)
    return int(v)


def round_away(price, long):
    """A stop: rounded away from the entry. Down for a buy, up for a sell."""
    return round_tick(price, "down" if long else "up")


def round_toward(price, long):
    """A target: rounded toward the entry, so the order rests where the price must trade."""
    return round_tick(price, "down" if long else "up")


def on_grid(price):
    p = float(price)
    return p > 0 and abs(p - round_tick(p)) < 1e-6


def price_limits(base):
    """(lower, upper) daily price limits from the base price (usually yesterday's close)."""
    if not base or base <= 0:
        return None
    return round_tick(base * (1 - LIMIT_PCT), "up"), round_tick(base * (1 + LIMIT_PCT), "down")


def sell_tax_rate(year):
    if year in SELL_TAX:
        return SELL_TAX[year]
    return SELL_TAX[max(k for k in SELL_TAX if k <= year)] if year > min(SELL_TAX) else SELL_TAX[min(SELL_TAX)]


def trade_costs(entry, exit_, qty, year, commission_pct=DEFAULT_COMMISSION_PCT, side="buy"):
    """Won paid in commission (both legs) and sell tax (the selling leg) for one round trip."""
    buy_px, sell_px = (entry, exit_) if side == "buy" else (exit_, entry)
    comm = (buy_px + sell_px) * qty * commission_pct / 100.0
    tax = sell_px * qty * sell_tax_rate(year)
    return {"commission": round(comm), "tax": round(tax), "total": round(comm + tax)}


def cost_per_share(entry, exit_, year, commission_pct=DEFAULT_COMMISSION_PCT, side="buy"):
    """Unrounded won of commission plus sell tax for one share round trip."""
    buy_px, sell_px = (entry, exit_) if side == "buy" else (exit_, entry)
    return (buy_px + sell_px) * commission_pct / 100.0 + sell_px * sell_tax_rate(year)


# ---------------------------------------------------------------- formatting

def won(x, sign=False):
    if x is None:
        return "-"
    v = int(round(float(x)))
    s = "{:,}".format(abs(v))
    if v < 0:
        return "-₩" + s
    return ("+₩" if sign and v > 0 else "₩") + s


def px(x):
    """A share price, no symbol: 73,400."""
    if x is None:
        return "-"
    return "{:,}".format(int(round(float(x))))   # every KRX price is a whole number of won


def pct(x, digits=1):
    return ("%." + str(digits) + "f %%") % (100.0 * x)


def num(v, what="a number"):
    try:
        x = float(str(v).replace(",", "").replace("₩", "").strip())
    except (TypeError, ValueError):
        raise KRXError("%s must be a number, got %r" % (what, v))
    if math.isnan(x) or math.isinf(x):
        raise KRXError("%s must be a number, got %r" % (what, v))
    return x


# ---------------------------------------------------------------- symbols and the universe

def load_universe(path=UNIVERSE_PATH):
    """{code: {"name": ..., "market": "KOSPI" | "KOSDAQ" | ""}} from scan.py universe, or {}."""
    doc = read_json(path, {}) or {}
    return doc.get("stocks", doc) if isinstance(doc, dict) else {}


def resolve(text, universe=None):
    """'005930', 'A005930', '005930.KS', '삼성전자' or 'Samsung Electronics' -> a dict:
    {code, yahoo, name, market}. market is '' when only Yahoo can tell (try .KS, then .KQ)."""
    if text is None:
        raise KRXError("no stock given")
    raw = str(text).strip()
    universe = universe if universe is not None else load_universe()
    up = raw.upper()
    suffix = ""
    if up.endswith(".KS") or up.endswith(".KQ"):
        up, suffix = up[:-3], up[-3:]
    if len(up) == 7 and up[0] == "A" and CODE_RE.match(up[1:]):
        up = up[1:]                      # 'A005930', the way HTS shows codes
    if up.isdigit() and len(up) < 6:
        up = up.zfill(6)
    if CODE_RE.match(up):
        info = universe.get(up) or {}
        market = MARKET_OF_SUFFIX.get(suffix) or info.get("market") or ""
        return {"code": up, "yahoo": up + (suffix or SUFFIX.get(market, "")), "name": info.get("name") or "",
                "market": market}
    key = raw.replace(" ", "").lower()
    hits = [(c, i) for c, i in universe.items() if str(i.get("name", "")).replace(" ", "").lower() == key]
    if not hits:
        hits = [(c, i) for c, i in universe.items()
                if key and key in str(i.get("name", "")).replace(" ", "").lower()]
    if len(hits) == 1:
        c, i = hits[0]
        return {"code": c, "yahoo": c + SUFFIX.get(i.get("market"), ""), "name": i.get("name", ""),
                "market": i.get("market", "")}
    if len(hits) > 1:
        names = ", ".join("%s %s" % (i.get("name"), c) for c, i in sorted(hits)[:6])
        raise KRXError("%r matches %d stocks (%s): give the 6-digit code" % (raw, len(hits), names))
    if not universe:
        raise KRXError("%r is not a 6-digit KRX code. To use names, build the list first: "
                       "python3 krx-scanner/scan.py universe" % raw)
    raise KRXError("no listed stock called %r (codes look like 005930)" % raw)


def label(info, lang="en"):
    """'삼성전자 005930' or just the code."""
    return ("%s %s" % (info["name"], info["code"])) if info.get("name") else info["code"]


# ---------------------------------------------------------------- Yahoo Finance charts

YAHOO_HOSTS = ("query2", "query1")
PAUSE = 0.4
BACKOFF = 5.0
SLEEP = time.sleep          # tests swap this out
OPENER = None               # tests swap this out: callable(url) -> text


def http_get(url, headers=None, timeout=20, binary=False):
    if OPENER is not None:
        return OPENER(url)
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def chart_url(yahoo_sym, rng, interval, host="query2"):
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=false" % (
        host, urllib.parse.quote(yahoo_sym), rng, interval)


def yahoo_text(path_fn):
    """GET a Yahoo URL built by path_fn(host): query2 first, one retry after a 429, then query1."""
    last = None
    for n, host in enumerate(YAHOO_HOSTS):
        if n:
            SLEEP(PAUSE)
        for attempt in (0, 1):
            try:
                return http_get(path_fn(host))
            except urllib.error.HTTPError as e:
                last = e.code
                if e.code == 429 and attempt == 0:
                    SLEEP(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                if e.code == 404:
                    raise KRXError("Yahoo has no data for this symbol (HTTP 404)")
                raise KRXError("Yahoo answered HTTP %d" % e.code)
            except urllib.error.URLError as e:
                raise KRXError("could not reach Yahoo Finance: %s" % e.reason)
            except OSError as e:
                raise KRXError("could not reach Yahoo Finance: %s" % e)
    if last == 429:
        raise KRXError("Yahoo is rate limiting (HTTP 429). Wait a minute and try again.")
    raise KRXError("Yahoo answered HTTP %s on both hosts. Try again later." % last)


def chart_result(text, what=""):
    try:
        data = json.loads(text) if isinstance(text, (str, bytes)) else text
    except ValueError:
        raise KRXError("Yahoo returned something that is not JSON%s" % (" for " + what if what else ""))
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise KRXError("Yahoo returned something unexpected%s" % (" for " + what if what else ""))
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise KRXError("Yahoo has no data%s: %s" % (" for " + what if what else "",
                                                     err.get("description") or "unknown symbol"))
    return res[0]


def fetch_chart(info, rng="60d", interval="5m"):
    """Chart JSON text for a resolved stock. Without a known market it tries .KS, then .KQ,
    and records which one answered in info['yahoo'] / info['market']."""
    tries = [info["yahoo"]] if info.get("market") or "." in info.get("yahoo", "") else \
        [info["code"] + ".KS", info["code"] + ".KQ"]
    err = None
    for i, sym in enumerate(tries):
        if i:
            SLEEP(PAUSE)
        try:
            text = yahoo_text(lambda host: chart_url(sym, rng, interval, host))
            res = chart_result(text, sym)
            if not (res.get("timestamp") or []):
                raise KRXError("no bars")
            info["yahoo"] = sym
            info["market"] = info.get("market") or MARKET_OF_SUFFIX.get(sym[-3:], "")
            nm = (res.get("meta") or {}).get("longName") or (res.get("meta") or {}).get("shortName")
            if nm and not info.get("name"):
                info["name"] = nm
            return text
        except KRXError as e:
            err = e
    raise KRXError("no chart for %s on Yahoo (%s)" % (info["code"], err))


def read_offline(path, info=None, suffix="-60d5m"):
    """A saved Yahoo chart file (.json or .json.gz), or a folder holding <CODE><suffix>.json(.gz)."""
    import gzip
    if os.path.isdir(path):
        code = (info or {}).get("code", "")
        for name in ("%s%s.json" % (code, suffix), "%s%s.json.gz" % (code, suffix)):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise KRXError("no %s%s.json(.gz) in %s" % (code, suffix, path))
    if not os.path.exists(path):
        raise KRXError("no offline file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        text = fh.read()
    if info is not None:
        try:
            meta = chart_result(text).get("meta") or {}
            sym = str(meta.get("symbol") or "")
            if sym[-3:] in MARKET_OF_SUFFIX:
                info["yahoo"], info["market"] = sym, info.get("market") or MARKET_OF_SUFFIX[sym[-3:]]
        except KRXError:
            pass
    return text


def raw_bars(result):
    """[(utc_ts, o, h, l, c, v)] with missing prices dropped, oldest first."""
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    out = []
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            continue
        out.append((int(ts), float(o), float(h), float(l), float(c), float(v or 0)))
    return out


def session_bars(result, now_utc, interval_min=BAR, use_data_time=True):
    """Yahoo intraday chart -> {date: {"bars": {slot: bar}, "auction": close or None, "session": s}}.
    Slot 0 is the bar that starts at the open. Only bars fully inside continuous trading
    (open to cont_end) get a slot; a bar at or after cont_end is the closing auction.
    A bar counts once it is complete: its start + the interval is at or before the
    earlier of now_utc and the data's own last trade (meta.regularMarketTime)."""
    meta = result.get("meta") or {}
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if use_data_time and isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    days = {}
    dropped = {"off_grid": 0, "outside_hours": 0, "not_complete": 0, "closed_day": 0}
    step = interval_min * 60
    for ts, o, h, l, c, v in raw_bars(result):
        if ts % step:
            dropped["off_grid"] += 1
            continue
        start = utc_of(ts)
        local = to_kst(start)
        d = local.date().isoformat()
        s = session(d)
        if s is None:
            dropped["closed_day"] += 1
            continue
        mins = local.hour * 60 + local.minute
        auction = s["cont_end"] <= mins <= s["close"]
        if (start > as_of) if auction else (start + datetime.timedelta(seconds=step) > as_of):
            dropped["not_complete"] += 1    # the closing auction is one print: it counts once it is there
            continue
        day = days.setdefault(d, {"bars": {}, "auction": None, "session": s})
        if s["open"] <= mins and mins + interval_min <= s["cont_end"]:
            k = (mins - s["open"]) // interval_min
            day["bars"][k] = {"date": d, "slot": k, "start": local.strftime("%H:%M"),
                              "o": o, "h": h, "l": l, "c": c, "v": v}
        elif s["cont_end"] <= mins <= s["close"]:
            day["auction"] = c
        else:
            dropped["outside_hours"] += 1
    return days, dropped


def slots_in(s, interval_min=BAR):
    return (s["cont_end"] - s["open"]) // interval_min


def complete_day(day, interval_min=BAR, max_missing=0.10):
    """The day's bars as a full list 0..n-1, or None. A KRX stock that does not trade in a
    five-minute window has no bar; up to max_missing of them are filled flat at the last
    close with zero volume (that is what the tape did). The first bar must exist."""
    n = slots_in(day["session"], interval_min)
    bars = day["bars"]
    if 0 not in bars or len(bars) < n * (1 - max_missing):
        return None
    out, prev = [], None
    for k in range(n):
        b = bars.get(k)
        if b is None:
            c = prev["c"]
            b = {"date": prev["date"], "slot": k, "start": hm(day["session"]["open"] + k * interval_min),
                 "o": c, "h": c, "l": c, "c": c, "v": 0, "filled": True}
        out.append(b)
        prev = b
    return out


# ---------------------------------------------------------------- two languages

TEXT = {
    "target_first": ("target first", "목표가 먼저"),
    "stop_first": ("stop first", "손절가 먼저"),
    "neither": ("neither", "둘 다 아님"),
    "green": ("green", "수익 마감"),
    "coin_flip": ("Close to a coin flip", "거의 동전 던지기"),
    "break_even": ("Close to break-even", "거의 손익분기"),
    "lean_target": ("Leans toward the target", "목표가 쪽으로 기움"),
    "lean_stop": ("Leans toward the stop", "손절가 쪽으로 기움"),
    "strong_target": ("Strongly toward the target", "목표가 쪽으로 강하게 기움"),
    "strong_stop": ("Strongly toward the stop", "손절가 쪽으로 강하게 기움"),
    "not_advice": ("Not financial advice. A simulation, not a prediction.",
                   "투자 조언이 아닙니다. 예측이 아닌 시뮬레이션입니다."),
    "paper": ("PAPER", "모의"),
    "pass": ("PASS", "통과"),
    "block": ("BLOCK", "차단"),
}


def T(key, lang="en"):
    en, ko = TEXT[key]
    return ko if lang == "ko" else en


def lang_of(cli=None, cfg=None):
    v = (cli or (cfg or {}).get("lang") or "en").lower()
    return "ko" if v.startswith("ko") else "en"


# ---------------------------------------------------------------- DART (Korea's disclosure system, OpenDART API)
# A free API key (40 characters) from https://opendart.fss.or.kr: sign up, 인증키 신청.
# Put it in krx/config.json ("dart_api_key") or the DART_API_KEY environment variable.

DART_BASE = "https://opendart.fss.or.kr/api/"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s"
DART_CORPS = os.path.join(DATA_DIR, "state", "dart_corpcodes.json")
DART_STATUS = {
    "010": "the DART key is not registered", "011": "the DART key is not allowed to use this",
    "012": "the DART key is not allowed from this IP address", "020": "DART's daily request limit is used up",
    "100": "DART says a field is wrong", "101": "DART says the request is not allowed",
    "800": "DART is in maintenance", "900": "DART had an error", "901": "the DART key has expired",
}


def need_key(key):
    if not key:
        raise KRXError("this needs a free OpenDART API key: get one at https://opendart.fss.or.kr (회원가입, "
                       "인증키 신청), then put it in krx/config.json as \"dart_api_key\" or set DART_API_KEY")
    return key


def dart_json(endpoint, key, **params):
    """GET an OpenDART JSON endpoint -> the 'list' rows ([] when DART has none)."""
    q = dict(params, crtfc_key=need_key(key))
    url = DART_BASE + endpoint + "?" + urllib.parse.urlencode(q)
    try:
        doc = json.loads(http_get(url))
    except urllib.error.HTTPError as e:
        raise KRXError("DART answered HTTP %d for %s" % (e.code, endpoint))
    except (urllib.error.URLError, OSError) as e:
        raise KRXError("could not reach opendart.fss.or.kr: %s" % getattr(e, "reason", e))
    except ValueError:
        raise KRXError("DART returned something that is not JSON for %s" % endpoint)
    status = str(doc.get("status", ""))
    if status == "013":
        return []
    if status != "000":
        raise KRXError("%s (DART status %s: %s)" % (DART_STATUS.get(status, "DART refused the request"), status,
                                                    doc.get("message", "")))
    return doc.get("list") or []


def parse_corpcodes(data):
    """corpCode.xml (zipped, as DART sends it, or the XML itself) -> {stock_code: {corp_code, name}}."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
            data = z.read(name)
    root = ET.fromstring(data)
    out = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = {"corp_code": (el.findtext("corp_code") or "").strip(),
                       "name": (el.findtext("corp_name") or "").strip()}
    return out


def corp_codes(key, path=DART_CORPS, max_age_days=7, offline=None):
    """{stock_code: {corp_code, name}}, cached for a week in krx/state/dart_corpcodes.json."""
    if offline:
        with open(offline, "rb") as fh:
            return parse_corpcodes(fh.read())
    doc = read_json(path, None)
    if doc and doc.get("saved_at"):
        age = (utc_now() - datetime.datetime.strptime(doc["saved_at"], "%Y-%m-%dT%H:%M:%SZ")).days
        if age < max_age_days:
            return doc["corps"]
    url = DART_BASE + "corpCode.xml?" + urllib.parse.urlencode({"crtfc_key": need_key(key)})
    try:
        data = http_get(url, binary=True, timeout=60)
    except Exception as e:
        if doc:
            return doc["corps"]
        raise KRXError("could not download DART's company list: %s" % e)
    if not data[:2] == b"PK" and b"<status>" in data[:200]:
        m = re.search(rb"<status>(\d+)</status>", data)
        st = m.group(1).decode() if m else "?"
        raise KRXError("%s (DART status %s)" % (DART_STATUS.get(st, "DART refused the request"), st))
    corps = parse_corpcodes(data)
    write_json(path, {"saved_at": iso_utc(utc_now()), "count": len(corps), "corps": corps})
    return corps


def corp_of(code, corps):
    c = corps.get(code)
    if not c:
        raise KRXError("DART has no listed company with stock code %s" % code)
    return c


def dart_amount(s):
    """'1,234,567' / '-1,234' / '(1,234)' / '-' -> float or None."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if t in ("", "-"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v
