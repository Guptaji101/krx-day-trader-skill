#!/usr/bin/env python3
"""News Monitor for krx-news (Korean market): headlines, DART disclosures and the
day's move for every stock you watch. Standard library only.

    python3 news.py                                  # everything in krx/portfolio.json, plan and watchlist
    python3 news.py --tickers 005930,000660 --hours 12
    python3 news.py --tickers 삼성전자 --all          # include items shown before
    python3 news.py --no-dart                        # headlines and moves only (no API key needed)
    python3 news.py --json

Three sources:
  headlines   Google News RSS in Korean, searched by the company's name (free, no key).
              Short headlines with a link; open the link to read the story.
  disclosures DART (전자공시) filings for each company from the last --days days,
              through the free OpenDART API (needs "dart_api_key"). These are the
              filings that move Korean stocks; each is tagged:
                earnings    영업(잠정)실적, 매출액또는손익구조 30% 변경, 분기/반기/사업보고서
                dilution    유상증자, 전환사채, 신주인수권부사채, 교환사채   (often a drop)
                contract    단일판매ㆍ공급계약체결                          (often a pop)
                buyback     자기주식 취득 / 처분 / 소각
                bonus       무상증자, 주식분할                              (also 감자: capital cut)
                control     최대주주 변경, 합병, 분할, 공개매수, 영업양수도
                danger      횡령ㆍ배임, 회생절차, 부도, 감사의견, 관리종목, 상장폐지, 거래정지,
                            불성실공시
                insider     임원ㆍ주요주주 소유상황, 주식등의 대량보유 (see /krx-insider)
                correction  [기재정정]: an earlier filing was corrected
  moves       today's change from yesterday's close (Yahoo); flagged at --move-pct (3 %).

Items already shown are remembered in krx/state/news.json and left out next time
(--all shows them again). Times are Seoul (KST).
"""

import argparse
import datetime
import email.utils
import hashlib
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

GNEWS = "https://news.google.com/rss/search?q=%s&hl=ko&gl=KR&ceid=KR:ko"
STATE = "krx/state/news.json"
KEEP_DAYS = 30
TAGS = [
    ("danger", ("횡령", "배임", "회생", "부도", "감사의견", "관리종목", "상장폐지", "거래정지", "매매거래정지",
                "불성실공시", "투자주의환기")),
    ("dilution", ("유상증자", "전환사채", "신주인수권부사채", "교환사채", "제3자배정")),
    ("earnings", ("잠정", "실적", "영업이익", "어닝", "매출액또는손익구조", "손익구조", "분기보고서", "반기보고서", "사업보고서")),
    ("contract", ("단일판매", "공급계약")),
    ("buyback", ("자기주식", "자사주", "소각")),
    ("bonus", ("무상증자", "주식분할", "감자", "주식병합")),
    ("control", ("최대주주", "합병", "분할", "공개매수", "영업양수", "영업양도", "주식교환")),
    ("insider", ("임원ㆍ주요주주", "임원·주요주주", "대량보유")),
]
IMPORTANT = {"danger", "dilution", "earnings", "contract", "control"}


class NewsError(Exception):
    pass


def tags_for(title):
    t = title.replace(" ", "")
    out = [name for name, words in TAGS if any(w in t for w in words)]
    if "기재정정" in t or "[정정]" in t:
        out.append("correction")
    return out


def watch_list(a, universe):
    raw = []
    if a.tickers:
        raw = [x for x in re.split(r"[,\s]+", a.tickers) if x]
    else:
        pf = krx.read_json("krx/portfolio.json", {}) or {}
        raw += list((pf.get("positions") or {}).keys())
        plan = krx.read_json("krx/plan.json", {}) or {}
        if plan.get("code"):
            raw.append(plan["code"])
        wl = krx.read_json("krx/watchlist.json", {}) or {}
        raw += [h.get("code") or h.get("symbol") for h in (wl.get("hits") or [])[:a.max_watch]]
    out = []
    for r in raw:
        try:
            info = krx.resolve(r, universe)
        except krx.KRXError as e:
            raise NewsError(str(e))
        if info["code"] not in [x["code"] for x in out]:
            out.append(info)
    if not out:
        raise NewsError("nothing to watch: pass --tickers 005930,000660, or run /krx-scanner first")
    return out


def parse_rss(data, code):
    """Google News RSS -> items."""
    try:
        root = ET.fromstring(data.encode("utf-8") if isinstance(data, str) else data)
    except ET.ParseError:
        raise NewsError("the news feed is not valid RSS")
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        when = None
        pd = it.findtext("pubDate")
        if pd:
            try:
                dt = email.utils.parsedate_to_datetime(pd)
                when = (dt - dt.utcoffset()).replace(tzinfo=None) if dt.utcoffset() is not None else dt
            except (TypeError, ValueError):
                when = None
        src = it.find("source")
        items.append({"kind": "headline", "code": code, "title": title, "link": (it.findtext("link") or "").strip(),
                      "source": src.text.strip() if src is not None and src.text else "Google News",
                      "time": when, "tags": []})
    return items


def disclosures(code, corps, key, days, today, fetch=None):
    """DART filings of one company from the last `days` days. fetch(endpoint, key, **params) -> rows."""
    c = krx.corp_of(code, corps)
    bgn = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).strftime("%Y%m%d")
    rows = (fetch or krx.dart_json)("list.json", key, corp_code=c["corp_code"], bgn_de=bgn,
                                    end_de=today.replace("-", ""), page_count=100)
    items = []
    for r in rows:
        d = r.get("rcept_dt", "")
        title = " ".join(str(r.get("report_nm", "")).split())
        when = datetime.datetime.strptime(d, "%Y%m%d") - krx.KST if len(d) == 8 else None
        items.append({"kind": "disclosure", "code": code, "title": title, "link": krx.DART_VIEW % r.get("rcept_no"),
                      "source": "DART (%s)" % (r.get("flr_nm") or "filer"), "time": when, "date_only": True,
                      "tags": tags_for(title), "rcept_no": r.get("rcept_no")})
    return items


def move_of(info, offline=None):
    try:
        text = krx.read_offline(offline, dict(info), suffix="") if offline else krx.fetch_chart(dict(info), "5d", "1d")
        res = krx.chart_result(text, info["code"])
    except krx.KRXError:
        return None
    meta = res.get("meta") or {}
    bars = krx.raw_bars(res)
    p = meta.get("regularMarketPrice") or (bars[-1][4] if bars else None)
    days = sorted({krx.to_kst(krx.utc_of(b[0])).date() for b in bars})
    prev = None
    if len(days) >= 2:
        prev = [b for b in bars if krx.to_kst(krx.utc_of(b[0])).date() < days[-1]][-1][4]
    prev = prev or meta.get("chartPreviousClose")
    if not p or not prev:
        return None
    return {"code": info["code"], "price": float(p), "prev_close": float(prev), "pct": 100.0 * (float(p) / prev - 1)}


def fp(it):
    return hashlib.sha1(("%s|%s|%s" % (it["code"], it["title"], it.get("link", ""))).encode("utf-8")).hexdigest()[:12]


def offline_file(a, name):
    p = os.path.join(a.offline, name) if a.offline else None
    return p if p and os.path.exists(p) else None


def collect(watch, a, cfg, now_utc):
    today = krx.to_kst(now_utc).date().isoformat()
    items, errors, moves = [], {}, []
    key = krx.dart_key(cfg, a.dart_key)
    corps = None
    if not a.no_dart:
        try:
            corps = krx.corp_codes(key, offline=a.corpcodes)
        except krx.KRXError as e:
            errors["DART"] = str(e)
    for i, info in enumerate(watch):
        code = info["code"]
        if i and not a.offline:
            krx.SLEEP(krx.PAUSE)
        if not a.no_headlines:
            q = info.get("name") or code
            try:
                if a.offline:
                    p = offline_file(a, "news-%s.xml" % code)
                    data = open(p, encoding="utf-8").read() if p else None
                else:
                    data = krx.http_get(GNEWS % urllib.parse.quote(q + " 주가" if a.stock_words else q))
                if data:
                    items += parse_rss(data, code)[:a.count]
            except Exception as e:  # one feed failing never stops the rest
                errors["%s headlines" % code] = str(e)
        if corps is not None:
            fetch = None
            if a.offline:
                p = offline_file(a, "dart-%s.json" % code)
                saved = json.load(open(p, encoding="utf-8")) if p else {"list": []}
                rows = saved.get("list", []) if isinstance(saved, dict) else saved
                fetch = lambda *x, **y: rows  # noqa: E731
            try:
                items += disclosures(code, corps, key or "offline", a.days, today, fetch)
            except krx.KRXError as e:
                errors["%s DART" % code] = str(e)
        chart = offline_file(a, "%s.json" % code)
        m = move_of(info, chart) if (chart or not a.offline) else None
        if m:
            m["name"] = info.get("name", "")
            moves.append(m)
    for it in items:
        if it["kind"] == "headline":
            it["tags"] = [t for t in tags_for(it["title"]) if t != "correction"]
    return items, errors, moves


def select(items, state, now_utc, hours, show_all):
    seen = set(state.get("seen", {}))
    cut = now_utc - datetime.timedelta(hours=hours)
    out = []
    for it in items:
        t = it.get("time")
        if t and not it.get("date_only") and t < cut:
            continue        # filings are already limited to the last --days days
        if not show_all and fp(it) in seen:
            continue
        out.append(it)
    out.sort(key=lambda x: (x.get("time") or datetime.datetime(1970, 1, 1)), reverse=True)
    uniq, keys = [], set()
    for it in out:
        k = (it["code"], re.sub(r"\W+", "", it["title"])[:40])
        if k not in keys:
            keys.add(k)
            uniq.append(it)
    return uniq


def remember(items, state, now_utc):
    seen = state.setdefault("seen", {})
    stamp = krx.iso_utc(now_utc)
    for it in items:
        seen[fp(it)] = stamp
    cut = krx.iso_utc(now_utc - datetime.timedelta(days=KEEP_DAYS))
    state["seen"] = {k: v for k, v in seen.items() if v >= cut}
    return state


def render(items, errors, moves, watch, now_utc, move_pct, lang="en"):
    ko = lang == "ko"
    names = {w["code"]: w.get("name") or "" for w in watch}
    L = ["%s %s KST, %d stock(s)." % ("뉴스, 공시, 등락" if ko else "News, DART filings and moves,",
                                    krx.to_kst(now_utc).strftime("%Y-%m-%d %H:%M"), len(watch))]
    big = [m for m in moves if abs(m["pct"]) >= move_pct]
    for m in sorted(moves, key=lambda x: -abs(x["pct"])):
        flag = "  <- moving" if m in big else ""
        lim = krx.price_limits(m["prev_close"])
        if lim and m["price"] >= lim[1]:
            flag = "  <- AT THE UPPER LIMIT (상한가)"
        elif lim and m["price"] <= lim[0]:
            flag = "  <- AT THE LOWER LIMIT (하한가)"
        L.append("  %s %-12s %10s  %+6.2f%%%s" % (m["code"], (m.get("name") or "")[:12], krx.px(m["price"]), m["pct"], flag))
    if not items:
        L.append("새 항목이 없습니다." if ko else "Nothing new.")
    for it in items:
        when = krx.to_kst(it["time"]).strftime("%m-%d" if it.get("date_only") else "%m-%d %H:%M") if it.get("time") else "?"
        tg = (" [%s]" % ", ".join(it["tags"])) if it["tags"] else ""
        star = "!" if set(it["tags"]) & IMPORTANT else " "
        L.append("%s %-11s %s %-10s %s%s" % (star, when, it["code"], names.get(it["code"], "")[:10], it["title"], tg))
        L.append("               %s  %s" % (it["source"], it["link"]))
    for k, e in errors.items():
        L.append("(%s: %s)" % (k, e))
    L.append("! = a filing type that often moves the price. Headlines are leads, not facts: open the filing.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="News Monitor (krx-news): headlines, DART filings, moves.")
    ap.add_argument("--tickers", help="codes or names, comma-separated")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--days", type=int, default=3, help="DART filings from the last N days")
    ap.add_argument("--count", type=int, default=8, help="headlines per stock")
    ap.add_argument("--max-watch", type=int, default=15)
    ap.add_argument("--move-pct", type=float, default=3.0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-dart", action="store_true")
    ap.add_argument("--no-headlines", action="store_true")
    ap.add_argument("--stock-words", action="store_true", help="search '<name> 주가' (stock news only)")
    ap.add_argument("--dart-key")
    ap.add_argument("--corpcodes", help="a saved corpCode.xml (or its zip) instead of downloading it")
    ap.add_argument("--offline", metavar="DIR", help="news-<CODE>.xml, dart-<CODE>.json and <CODE>.json files")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--now")
    ap.add_argument("--lang", choices=["en", "ko"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = krx.load_config(a.config)
        now = krx.parse_now(a.now)
        universe = krx.load_universe()
        if not universe and a.tickers and re.search(r"[^0-9A-Za-z.,\s]", a.tickers) and not a.no_dart:
            # names without the KRX list: DART's company list knows them too
            try:
                corps = krx.corp_codes(krx.dart_key(cfg, a.dart_key), offline=a.corpcodes)
                universe = {c: {"name": v["name"], "market": ""} for c, v in corps.items()}
            except krx.KRXError:
                pass
        watch = watch_list(a, universe)
        items, errors, moves = collect(watch, a, cfg, now)
        state = krx.read_json(a.state, {}) or {}
        shown = select(items, state, now, a.hours, a.all)
        krx.write_json(a.state, remember(shown, state, now))
        if a.json:
            out = [dict(it, time=krx.iso_utc(it["time"]) if it.get("time") else None, fingerprint=fp(it))
                   for it in shown]
            print(json.dumps({"items": out, "moves": moves, "errors": errors}, indent=2, ensure_ascii=False))
        else:
            print(render(shown, errors, moves, watch, now, a.move_pct, krx.lang_of(a.lang, cfg)))
        return 0 if shown or moves else 1
    except (NewsError, krx.KRXError) as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
