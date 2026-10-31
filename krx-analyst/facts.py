#!/usr/bin/env python3
"""Earnings Analyst for krx-analyst (Korean market): reads a company's quarterly
results from DART and sums up what changed. Standard library only.

    python3 facts.py 005930                     # the last 6 quarters, consolidated (연결)
    python3 facts.py 삼성전자 --quarters 8
    python3 facts.py 000660 --separate          # 별도 (parent-only) statements instead
    python3 facts.py 005930 --json
    python3 facts.py 005930 --write             # add a row to krx/metrics.json

Data: OpenDART's full financial statements (fnlttSinglAcntAll.json), one request per
report: Q1 (1분기보고서), half year (반기보고서), Q3 (3분기보고서) and the annual
report (사업보고서). A quarter's income-statement line is DART's three-month amount
(thstrm_amount); Q4 is the full year minus Q1 to Q3. Accounts it reads (by IFRS id,
then by Korean name):
  revenue           ifrs-full_Revenue                       매출액, 수익(매출액), 영업수익
  operating income  dart_OperatingIncomeLoss                영업이익(손실)
  net income        ifrs-full_ProfitLoss                    당기순이익(손실)
  to owners         ifrs-full_ProfitLossAttributableToOwnersOfParent   지배기업 소유주지분
  basic EPS         ifrs-full_BasicEarningsLossPerShare     기본주당이익(손실)
It also lists the company's preliminary results (영업(잠정)실적 공정공시) from the last
120 days: in Korea they come out weeks before the quarterly report, and they are what
moves the price on results day.

Quarterly reports are due 45 days after the quarter, the annual report 90 days after
the year. Banks, insurers and holding companies use different statement layouts: a
missing line shows as "-". Needs a free OpenDART key ("dart_api_key").
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

REPORTS = (("11013", 1), ("11012", 2), ("11014", 3), ("11011", 4))
ACCOUNTS = {
    "revenue": (("ifrs-full_Revenue",), ("매출액", "수익(매출액)", "영업수익", "매출", "수익")),
    "operating_income": (("dart_OperatingIncomeLoss",), ("영업이익(손실)", "영업이익", "영업손실")),
    "net_income": (("ifrs-full_ProfitLoss",), ("당기순이익(손실)", "당기순이익", "연결당기순이익", "당기순손실")),
    "owners": (("ifrs-full_ProfitLossAttributableToOwnersOfParent",), ("지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 당기순이익",
                                                                       "지배기업소유주지분")),
    "eps": (("ifrs-full_BasicEarningsLossPerShare",), ("기본주당이익(손실)", "기본주당이익", "기본주당순이익")),
}
FLOWS = ("revenue", "operating_income", "net_income", "owners")


class FactsError(Exception):
    pass


def pick(rows, key):
    """The account's row from one report's rows (income statement first)."""
    ids, names = ACCOUNTS[key]
    pool = [r for r in rows if r.get("sj_div") in ("IS", "CIS")]
    for r in pool:
        if r.get("account_id") in ids:
            return r
    for nm in names:
        for r in pool:
            if " ".join(str(r.get("account_nm", "")).split()) == nm:
                return r
    return None


def report_values(rows):
    """{key: (three_month_amount, cumulative_amount)} for one report."""
    out = {}
    for k in ACCOUNTS:
        r = pick(rows, k)
        if r is None:
            out[k] = (None, None)
            continue
        q = krx.dart_amount(r.get("thstrm_amount"))
        cum = krx.dart_amount(r.get("thstrm_add_amount"))
        out[k] = (q, cum if cum is not None else q)
    return out


def quarters_from(reports):
    """{(year, n): values} -> [{period, year, q, values}] with Q4 = year - (Q1..Q3), oldest first."""
    out = []
    years = sorted({y for y, _ in reports})
    for y in years:
        for n in (1, 2, 3, 4):
            v = reports.get((y, n))
            if v is None:
                continue
            row = {"period": "%dQ%d" % (y, n), "year": y, "q": n, "values": {}}
            for k in ACCOUNTS:
                three, cum = v[k]
                if n == 4:
                    full = three if three is not None else cum
                    if k == "eps":
                        prior = [reports.get((y, i), {}).get(k, (None, None))[0] for i in (1, 2, 3)]
                        row["values"][k] = full - sum(prior) if full is not None and None not in prior else None
                        row["values"]["eps_year"] = full
                    else:
                        q3 = reports.get((y, 3))
                        q3cum = q3[k][1] if q3 else None
                        row["values"][k] = full - q3cum if full is not None and q3cum is not None else None
                        row["values"][k + "_year"] = full
                else:
                    row["values"][k] = three
            out.append(row)
    return out


def pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return 100.0 * (a / b - 1) if b > 0 else None


def margin(part, whole):
    if part is None or not whole:
        return None
    return 100.0 * part / whole


def analyse(quarters, keep=6):
    rows = quarters[-keep:]
    by = {(r["year"], r["q"]): r for r in quarters}
    for r in rows:
        v = r["values"]
        prev = by.get((r["year"] - 1, r["q"]))
        r["yoy"] = {k: pct(v.get(k), prev["values"].get(k)) if prev else None for k in FLOWS}
        r["op_margin"] = margin(v.get("operating_income"), v.get("revenue"))
        r["net_margin"] = margin(v.get("net_income"), v.get("revenue"))
    return rows


def big(x, lang="en"):
    """101,000,000,000,000 -> '101.0조원' / '₩101.0 trillion'; under 1조, in 억원 / billion."""
    if x is None:
        return "-"
    a, sign = abs(x), "-" if x < 0 else ""
    if a >= 1e12:
        return ("%s%.1f조원" % (sign, a / 1e12)) if lang == "ko" else ("%s₩%.1f trillion" % (sign, a / 1e12))
    return ("%s%s억원" % (sign, "{:,.0f}".format(a / 1e8))) if lang == "ko" else \
        ("%s₩%s billion" % (sign, "{:,.1f}".format(a / 1e9)))


def changes(rows, lang="en"):
    """Plain sentences about the latest quarter."""
    if not rows:
        return []
    last = rows[-1]
    out = []
    v, y = last["values"], last["yoy"]
    ko = lang == "ko"
    for k, en, kr in (("revenue", "Revenue", "매출"), ("operating_income", "Operating income", "영업이익"),
                      ("net_income", "Net income", "순이익")):
        if v.get(k) is None:
            continue
        g = y.get(k)
        out.append(("%s %s%s" % (kr, big(v[k], "ko"), (", 전년 동기 대비 %+.1f%%" % g) if g is not None else "")) if ko
                   else "%s %s%s." % (en, big(v[k]), (", %+.1f%% year on year" % g) if g is not None else ""))
    if len(rows) >= 2 and rows[-1]["op_margin"] is not None and rows[-2]["op_margin"] is not None:
        d = rows[-1]["op_margin"] - rows[-2]["op_margin"]
        out.append(("영업이익률 %.1f%% (직전 분기 %.1f%%, %+.1f%%p)" if ko else
                    "Operating margin %.1f%% (%.1f%% the quarter before, %+.1f points).")
                   % (rows[-1]["op_margin"], rows[-2]["op_margin"], d))
    oi = v.get("operating_income")
    prev = (rows[-5]["values"].get("operating_income") if len(rows) >= 5 else None)
    if oi is not None and prev is not None and (oi < 0) != (prev < 0):
        out.append(("영업이익이 흑자 전환했습니다." if oi > 0 else "영업이익이 적자 전환했습니다.") if ko else
                   ("Operating income turned positive (흑자 전환)." if oi > 0 else
                    "Operating income turned negative (적자 전환)."))
    return out


def eok(x):
    """Won -> 억원 (100 million won), the unit Korean results are read in."""
    if x is None:
        return "-"
    return "{:,.0f}".format(x / 1e8)


def render(info, rows, prelim, fs, lang="en"):
    ko = lang == "ko"
    who = "%s %s" % (info.get("name") or "", info["code"])
    L = ["%s: %s, %s" % (who.strip(), "분기 실적" if ko else "quarterly results",
                         ("연결" if fs == "CFS" else "별도") if ko else ("consolidated (연결)" if fs == "CFS" else
                                                                        "parent only (별도)")),
         "%-8s %14s %8s %14s %8s %7s %14s %8s %10s" % ("period", "revenue 억원", "YoY", "op. inc 억원", "YoY", "margin",
                                                      "net inc 억원", "YoY", "EPS 원")]
    for r in rows:
        v, y = r["values"], r["yoy"]
        f = lambda g: ("%+.1f%%" % g) if g is not None else "-"  # noqa: E731
        L.append("%-8s %14s %8s %14s %8s %7s %14s %8s %10s" % (
            r["period"], eok(v.get("revenue")), f(y["revenue"]), eok(v.get("operating_income")),
            f(y["operating_income"]), ("%.1f%%" % r["op_margin"]) if r["op_margin"] is not None else "-",
            eok(v.get("net_income")), f(y["net_income"]), krx.px(v["eps"]) if v.get("eps") is not None else "-"))
    L.append("")
    for s in changes(rows, lang):
        L.append(("- " if ko else "- ") + s)
    if prelim:
        L.append("")
        L.append("잠정실적 공시 (최근 120일):" if ko else "Preliminary results filings (잠정실적, last 120 days):")
        for p in prelim[:4]:
            L.append("  %s  %s  %s" % (p["date"], p["title"], p["link"]))
    L.append("")
    L.append("출처: DART 전자공시. 투자 조언이 아닙니다." if ko else
             "Source: DART filings. Numbers are as filed; check the report before acting. Not financial advice.")
    return "\n".join(L)


def fetch_reports(corp_code, key, years, fs, fetch=None):
    fetch = fetch or krx.dart_json
    reports = {}
    for y in years:
        for code, n in REPORTS:
            try:
                rows = fetch("fnlttSinglAcntAll.json", key, corp_code=corp_code, bsns_year=str(y),
                             reprt_code=code, fs_div=fs)
            except krx.KRXError as e:
                if "status 013" in str(e):
                    rows = []
                else:
                    raise
            if rows:
                reports[(y, n)] = report_values(rows)
            krx.SLEEP(0.15)
    return reports


def main(argv=None):
    ap = argparse.ArgumentParser(description="Earnings Analyst (krx-analyst): quarters from DART.")
    ap.add_argument("symbol", help="6-digit code or a company name")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--separate", action="store_true", help="별도 (parent-only) statements")
    ap.add_argument("--dart-key")
    ap.add_argument("--corpcodes")
    ap.add_argument("--offline", metavar="DIR", help="fnltt-<year>-<reprt>.json and list.json files")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--now")
    ap.add_argument("--lang", choices=["en", "ko"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", nargs="?", const="krx/metrics.json", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        cfg = krx.load_config(a.config)
        key = krx.dart_key(cfg, a.dart_key)
        now = krx.parse_now(a.now)
        corps = krx.corp_codes(key, offline=a.corpcodes)
        universe = krx.load_universe() or {c: {"name": v["name"], "market": ""} for c, v in corps.items()}
        info = krx.resolve(a.symbol, universe)
        corp = krx.corp_of(info["code"], corps)
        info["name"] = info.get("name") or corp["name"]
        year = krx.to_kst(now).year
        years = list(range(year - (a.quarters + 7) // 4 - 1, year + 1))
        fetch = None
        if a.offline:
            def fetch(endpoint, k, **p):
                name = "list.json" if endpoint == "list.json" else "fnltt-%s-%s.json" % (p["bsns_year"], p["reprt_code"])
                path = os.path.join(a.offline, name)
                if not os.path.exists(path):
                    return []
                doc = json.load(open(path, encoding="utf-8"))
                return doc.get("list", []) if isinstance(doc, dict) else doc
            krx.SLEEP = lambda s: None
        fs = "OFS" if a.separate else "CFS"
        reports = fetch_reports(corp["corp_code"], key or "offline", years, fs, fetch)
        if not reports and not a.separate:
            fs = "OFS"
            reports = fetch_reports(corp["corp_code"], key or "offline", years, fs, fetch)
        if not reports:
            raise FactsError("DART has no financial statements for %s in %d-%d" % (info["code"], years[0], years[-1]))
        rows = analyse(quarters_from(reports), a.quarters)
        bgn = (krx.to_kst(now).date() - datetime.timedelta(days=120)).strftime("%Y%m%d")
        lst = (fetch or krx.dart_json)("list.json", key or "offline", corp_code=corp["corp_code"], bgn_de=bgn,
                                       end_de=krx.to_kst(now).strftime("%Y%m%d"), page_count=100)
        prelim = [{"date": "%s-%s-%s" % (r["rcept_dt"][:4], r["rcept_dt"][4:6], r["rcept_dt"][6:]),
                   "title": " ".join(r["report_nm"].split()), "link": krx.DART_VIEW % r["rcept_no"]}
                  for r in lst if "잠정" in r.get("report_nm", "") or "손익구조" in r.get("report_nm", "")]
        lang = krx.lang_of(a.lang, cfg)
        if a.write:
            doc = krx.read_json(a.write, {}) or {}
            doc.setdefault("rows", {})[info["code"]] = {"name": info["name"], "fs": fs, "latest": rows[-1],
                                                        "saved_at": krx.iso_utc(now)}
            krx.write_json(a.write, doc)
        if a.json:
            print(json.dumps({"code": info["code"], "name": info["name"], "fs": fs, "quarters": rows,
                              "changes": changes(rows, lang), "preliminary": prelim}, indent=2, ensure_ascii=False))
        else:
            print(render(info, rows, prelim, fs, lang))
        return 0
    except (FactsError, krx.KRXError) as e:
        print("facts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
