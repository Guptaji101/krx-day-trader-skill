#!/usr/bin/env python3
"""Insider Tracker for krx-insider (Korean market): who inside a company is buying
or selling its shares, from DART. Standard library only. Replaces the US pack's
Pelosi (Congress) tracker: Korea's National Assembly members disclose assets once
a year, not trade by trade, so the timely public trail in Korea is the insiders'.

    python3 insider.py 005930                    # executives' and major holders' reports (임원ㆍ주요주주)
    python3 insider.py 005930 --major            # 5 %+ stakes (주식등의 대량보유, 5% 룰)
    python3 insider.py --watch                   # every stock in your portfolio, plan and watchlist
    python3 insider.py --market --days 3         # every insider filing on DART in the last 3 days
    python3 insider.py 005930 --buys             # increases only (or --sells)
    python3 insider.py --watch --new             # only filings not shown before

Two DART reports:
  임원ㆍ주요주주특정증권등소유상황보고서 (elestock): an executive or a 10 %+ holder must
      report a change in their holding within 5 business days. Shows who (repror),
      their role (isu_exctv_ofcps), the change in shares (sp_stock_lmp_irds_cnt) and
      the holding after it.
  주식등의대량보유상황보고서 (majorstock): anyone holding 5 % or more must report it and
      every 1 %-point change, with the purpose (보고사유). A new 5 % holder or a
      "경영참가" (management participation) purpose is the one to read.

How to read it: an executive buying with their own money is a stronger signal than
a sale (people sell for taxes, homes, loans). Grants, conversions and inheritances
also show up as changes: open the filing for the reason. Filings are public, after
the fact (days, sometimes weeks), and not inside information.
Needs a free OpenDART key ("dart_api_key").
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

STATE = "krx/state/insider.json"


class InsiderError(Exception):
    pass


def num(v):
    x = krx.dart_amount(v)
    return x


def ele_rows(rows, code):
    out = []
    for r in rows:
        d = str(r.get("rcept_dt", "")).replace("-", "")
        out.append({"kind": "insider", "code": code, "corp": r.get("corp_name", ""),
                    "date": "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else d,
                    "who": r.get("repror", ""), "role": " ".join(str(r.get("isu_exctv_ofcps") or "").split()),
                    "registered": r.get("isu_exctv_rgist_at", ""), "major": r.get("isu_main_shrholdr", ""),
                    "change": num(r.get("sp_stock_lmp_irds_cnt")), "after": num(r.get("sp_stock_lmp_cnt")),
                    "rate": num(r.get("sp_stock_lmp_rate")), "rate_change": num(r.get("sp_stock_lmp_irds_rate")),
                    "rcept_no": r.get("rcept_no"), "link": krx.DART_VIEW % r.get("rcept_no")})
    return out


def major_rows(rows, code):
    out = []
    for r in rows:
        d = str(r.get("rcept_dt", "")).replace("-", "")
        out.append({"kind": "major", "code": code, "corp": r.get("corp_name", ""),
                    "date": "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else d,
                    "who": r.get("repror", ""), "role": r.get("report_tp", ""),
                    "change": num(r.get("stkqy_irds")), "after": num(r.get("stkqy")), "rate": num(r.get("stkrt")),
                    "rate_change": num(r.get("stkrt_irds")), "reason": " ".join(str(r.get("report_resn") or "").split()),
                    "rcept_no": r.get("rcept_no"), "link": krx.DART_VIEW % r.get("rcept_no")})
    return out


def filt(rows, since=None, buys=False, sells=False):
    out = []
    for r in rows:
        if since and r["date"] < since:
            continue
        if buys and not (r["change"] or 0) > 0:
            continue
        if sells and not (r["change"] or 0) < 0:
            continue
        out.append(r)
    out.sort(key=lambda r: (r["date"], r.get("rcept_no") or ""), reverse=True)
    return out


def describe(r, lang="en"):
    ko = lang == "ko"
    ch = r["change"]
    if ch is None:
        verb = "변동" if ko else "reported a holding of"
    elif ch > 0:
        verb = "매수/증가" if ko else "added"
    elif ch < 0:
        verb = "매도/감소" if ko else "cut"
    else:
        verb = "변동 없음" if ko else "no change in"
    role = r.get("role") or ""
    if r["kind"] == "insider" and r.get("major") and "주요주주" in r["major"] and not role:
        role = r["major"]
    amt = "{:,.0f}".format(abs(ch)) if ch else ""
    if ko:
        s = "%s  %s %s  %s(%s) %s %s주, 보유 %s주" % (r["date"], r["code"], r["corp"], r["who"], role or "-", verb, amt or "-",
                                               "{:,.0f}".format(r["after"]) if r["after"] is not None else "-")
    else:
        s = "%s  %s %s  %s (%s) %s %s shares, now %s" % (
            r["date"], r["code"], r["corp"], r["who"], role or "-", verb, amt or "-",
            "{:,.0f}".format(r["after"]) if r["after"] is not None else "-")
    if r.get("rate"):
        s += " (%.2f%%%s)" % (r["rate"], (", %+.2f%%p" % r["rate_change"]) if r.get("rate_change") else "")
    if r.get("reason"):
        s += " - " + r["reason"][:60]
    return s


def watch_codes(a, universe):
    raw = []
    if a.symbols:
        raw = a.symbols
    elif a.watch:
        pf = krx.read_json("krx/portfolio.json", {}) or {}
        raw += list((pf.get("positions") or {}).keys())
        plan = krx.read_json("krx/plan.json", {}) or {}
        if plan.get("code"):
            raw.append(plan["code"])
        wl = krx.read_json("krx/watchlist.json", {}) or {}
        raw += [h.get("code") or h.get("symbol") for h in (wl.get("hits") or [])[:15]]
    out = []
    for r in raw:
        c = krx.resolve(r, universe)["code"]
        if c not in out:
            out.append(c)
    return out


def market_wide(key, days, today, fetch):
    bgn = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).strftime("%Y%m%d")
    rows = []
    for page in range(1, 6):
        got = fetch("list.json", key, bgn_de=bgn, end_de=today.replace("-", ""), pblntf_ty="D",
                    page_no=page, page_count=100)
        rows += got
        if len(got) < 100:
            break
        krx.SLEEP(0.2)
    out = []
    for r in rows:
        name = " ".join(str(r.get("report_nm", "")).split())
        if r.get("corp_cls") not in ("Y", "K"):
            continue
        kind = "insider" if "임원" in name else "major" if "대량보유" in name else None
        if not kind:
            continue
        d = r.get("rcept_dt", "")
        out.append({"kind": kind, "code": r.get("stock_code", ""), "corp": r.get("corp_name", ""),
                    "market": {"Y": "KOSPI", "K": "KOSDAQ"}[r["corp_cls"]], "date": "%s-%s-%s" % (d[:4], d[4:6], d[6:]),
                    "who": r.get("flr_nm", ""), "title": name, "rcept_no": r.get("rcept_no"),
                    "link": krx.DART_VIEW % r.get("rcept_no")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Insider Tracker (krx-insider): executives' and 5%+ holders' trades.")
    ap.add_argument("symbols", nargs="*", help="codes or names")
    ap.add_argument("--watch", action="store_true", help="your portfolio, plan and watchlist")
    ap.add_argument("--market", action="store_true", help="every insider / 5%% filing on DART in the last --days")
    ap.add_argument("--major", action="store_true", help="5%%+ stake reports (대량보유) instead of executives'")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--buys", action="store_true")
    ap.add_argument("--sells", action="store_true")
    ap.add_argument("--new", action="store_true", help="only filings not shown before")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--dart-key")
    ap.add_argument("--corpcodes")
    ap.add_argument("--offline", metavar="DIR", help="elestock-<CODE>.json, majorstock-<CODE>.json, list.json")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--now")
    ap.add_argument("--lang", choices=["en", "ko"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = krx.load_config(a.config)
        key = krx.dart_key(cfg, a.dart_key) or ("offline" if a.offline else "")
        now = krx.parse_now(a.now)
        today = krx.to_kst(now).date().isoformat()
        since = (krx.to_kst(now).date() - datetime.timedelta(days=a.days)).isoformat()
        lang = krx.lang_of(a.lang, cfg)
        fetch = krx.dart_json
        if a.offline:
            def fetch(endpoint, k, **p):
                name = endpoint.replace(".json", "")
                f = "%s-%s.json" % (name, p["corp_code"]) if "corp_code" in p else "%s.json" % name
                path = os.path.join(a.offline, f)
                if not os.path.exists(path):
                    return []
                doc = json.load(open(path, encoding="utf-8"))
                return doc.get("list", []) if isinstance(doc, dict) else doc
            krx.SLEEP = lambda s: None
        if a.market:
            rows = market_wide(key, min(a.days, 90), today, fetch)
            if a.major:
                rows = [r for r in rows if r["kind"] == "major"]
        else:
            corps = krx.corp_codes(key, offline=a.corpcodes)
            universe = krx.load_universe() or {c: {"name": v["name"], "market": ""} for c, v in corps.items()}
            codes = watch_codes(a, universe)
            if not codes:
                raise InsiderError("give codes (insider.py 005930), --watch or --market")
            rows = []
            for i, code in enumerate(codes):
                if i and not a.offline:
                    krx.SLEEP(krx.PAUSE)
                corp = krx.corp_of(code, corps)
                if a.major:
                    rows += major_rows(fetch("majorstock.json", key, corp_code=corp["corp_code"]), code)
                else:
                    rows += ele_rows(fetch("elestock.json", key, corp_code=corp["corp_code"]), code)
            rows = filt(rows, since, a.buys, a.sells)
        state = krx.read_json(a.state, {}) or {}
        seen = set(state.get("seen", []))
        if a.new:
            rows = [r for r in rows if r.get("rcept_no") not in seen]
        state["seen"] = sorted(seen | {r.get("rcept_no") for r in rows if r.get("rcept_no")})[-5000:]
        krx.write_json(a.state, state)
        rows = rows[:a.limit] if not a.market else rows
        if a.json:
            print(json.dumps({"rows": rows}, indent=2, ensure_ascii=False))
            return 0 if rows else 1
        if a.market:
            print(("DART 지분 공시, 최근 %d일: %d건" if lang == "ko" else "DART insider and 5%% filings, last %d days: %d")
                  % (min(a.days, 90), len(rows)))
            for r in rows[:a.limit]:
                print("  %s  %s %-12s %-6s %-10s %s" % (r["date"], r["code"], r["corp"][:12], r["market"], r["who"][:10],
                                                        r["title"][:40]))
                print("              %s" % r["link"])
        else:
            what = ("5% 이상 대량보유" if a.major else "임원ㆍ주요주주 소유") if lang == "ko" else \
                ("5%+ stake reports (대량보유)" if a.major else "executive and major-holder reports (임원ㆍ주요주주)")
            print(("%s, %s 이후:" if lang == "ko" else "%s, since %s:") % (what, since))
            for r in rows:
                print("  " + describe(r, lang))
                print("      %s" % r["link"])
            if not rows:
                print("  (none)")
            buys = sum(1 for r in rows if (r["change"] or 0) > 0)
            sells = sum(1 for r in rows if (r["change"] or 0) < 0)
            print(("증가 %d건, 감소 %d건. 공시 사유(장내매수, 증여, 전환 등)를 꼭 확인하세요." if lang == "ko" else
                   "%d increases, %d decreases. Open the filing for the reason: an on-market buy (장내매수) means more than "
                   "a grant, a gift or a conversion.") % (buys, sells))
        return 0 if rows else 1
    except (InsiderError, krx.KRXError) as e:
        print("insider.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
