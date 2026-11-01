"""Time Machine, Entry Timer, Exit Planner and Scanner on generated KRX data."""

import json
import os
import shutil
import tempfile
import unittest

from _load import load
import fakes
import krx

tm = load("krx-timemachine", "timemachine")
entry = load("krx-entry", "entry")
exitp = load("krx-exit", "exit")
scan = load("krx-scanner", "scan")

NOW = krx.parse_now("2026-10-30 10:30")


def plan(**kw):
    p = {"symbol": "005930", "side": "buy", "date": "2026-10-30", "entry": 120500, "stop": 119700, "target": 121300,
         "entry_time": "09:25", "setup": "orb", "opening_range": {"minutes": 15}, "stop_rule": "auto"}
    p.update(kw)
    return tm.normalize_plan(p)


class TimeMachine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = json.dumps(fakes.chart(end="2026-10-30", days=60, until="10:30", seed=3))

    def run_tm(self, p=None, **kw):
        return tm.time_machine(self.text, p or plan(), NOW, replays=kw.pop("replays", 2000), **kw)

    def test_outcomes_add_up_and_repeat(self):
        a = self.run_tm()
        m = a["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=3)
        b = self.run_tm()
        self.assertEqual(a["mc"]["p_target"], b["mc"]["p_target"])
        self.assertEqual(m["pool_sessions"], 59)
        self.assertEqual(m["until"], "15:20")

    def test_costs_cut_r(self):
        m = self.run_tm()["mc"]
        self.assertLess(m["avg_R_net"], m["avg_R"])
        expect = krx.cost_per_share(120500, 121300, 2026, krx.DEFAULT_COMMISSION_PCT) / 800
        self.assertAlmostEqual(m["cost_R"], round(expect, 3))

    def test_entry_bar(self):
        p = plan()
        self.assertEqual(p["entry_bar"], 4)       # the 09:20 bar closes at 09:25
        self.assertEqual(p["target_r"], 1.0)

    def test_refusals(self):
        with self.assertRaises(tm.TMError):
            plan(entry=120550)                       # off the 100-won tick grid
        with self.assertRaises(tm.TMError):
            plan(date="2026-10-09")                  # Hangeul Day
        with self.assertRaises(tm.TMError):
            plan(entry_time="09:27")                 # off the 5-minute grid
        with self.assertRaises(tm.TMError):
            plan(side="sell")
        with self.assertRaises(tm.TMError):
            self.run_tm(until="15:25")               # inside the closing auction

    def test_limit_up_clamps(self):
        p = plan(target=200000)                      # far above the +30 % limit
        doc = self.run_tm(p)
        self.assertEqual(doc["mc"]["p_target"], 0.0)
        self.assertTrue(any("upper limit" in w for w in doc["warnings"]))

    def test_history_and_report(self):
        doc = self.run_tm()
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertGreater(doc["history"]["triggered"], 0)
        for t in doc["history"]["trades"]:
            self.assertTrue(krx.on_grid(t["entry"]) and krx.on_grid(t["stop"]) and krx.on_grid(t["target"]))
        en = "\n".join(tm.report(doc))
        self.assertIn("target first", en)
        doc["lang"] = "ko"
        self.assertIn("목표가 먼저", "\n".join(tm.report(doc)))
        self.assertTrue(tm.render_chart(doc))


class Entry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = krx.chart_result(fakes.chart(end="2026-10-30", days=60, until="10:30", seed=3))
        cls.info = {"code": "005930", "yahoo": "005930.KS", "market": "KOSPI", "name": ""}

    def ev(self, t, **kw):
        return entry.evaluate(self.res, self.info, krx.parse_now(t), **kw)

    def test_phases(self):
        self.assertEqual(self.ev("2026-10-30 08:50")["phase"], "pre-open")
        self.assertEqual(self.ev("2026-10-30 09:12")["phase"], "forming")
        r = self.ev("2026-10-30 10:30")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["trigger"]["time"], "09:25")
        self.assertEqual(r["trigger"]["price"], 120500)
        self.assertEqual(r["opening_range"]["high"], 120300)

    def test_trigger_price_on_tick_grid(self):
        res = krx.chart_result(fakes.chart(end="2026-10-30", days=60, until="10:30", seed=3))
        q = res["indicators"]["quote"][0]
        last_day = [i for i, t in enumerate(res["timestamp"])
                    if krx.to_kst(krx.utc_of(t)).date().isoformat() == "2026-10-30"]
        k = last_day[4]                                  # the trigger bar (09:20)
        q["close"][k] = q["close"][k] + 30               # Yahoo-style off-grid close
        r = entry.evaluate(res, self.info, NOW)
        self.assertEqual(r["trigger"]["bar_index"], 4)
        self.assertTrue(krx.on_grid(r["trigger"]["price"]))
        self.assertNotEqual(r["trigger"]["close"], r["trigger"]["price"])

    def test_waiting_before_trigger(self):
        r = self.ev("2026-10-30 09:20")
        self.assertEqual(r["status"], "waiting")

    def test_holiday(self):
        r = self.ev("2026-10-09 10:00")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("한글날", r["reason"])

    def test_no_entries_before(self):
        cfg = dict(krx.DEFAULTS, no_entries_before="09:30")
        r = self.ev("2026-10-30 10:30", cfg=cfg)
        self.assertTrue(r["trigger"] is None or r["trigger"]["time"] >= "09:30")

    def test_expired(self):
        cfg = dict(krx.DEFAULTS, no_entries_after="09:20")
        r = self.ev("2026-10-30 10:30", cfg=cfg)
        self.assertEqual(r["status"], "expired")

    def test_entry_doc(self):
        r = self.ev("2026-10-30 10:30")
        d = entry.entry_doc(r, NOW)
        self.assertEqual((d["code"], d["status"], d["price"], d["bar_index"]), ("005930", "triggered", 120500, 4))


class Exit(unittest.TestCase):
    def test_or_mid_on_ticks(self):
        c = exitp.compute(120500, "or_mid", None, "r", 1.0, 120300, 119200)
        self.assertEqual(c["stop"], 119700)          # 119,750 rounds down to the 100-won tick
        self.assertEqual(c["target"], 121300)
        self.assertEqual(c["risk"], 800)

    def test_target_rounds_toward_entry(self):
        c = exitp.compute(4995, "price", 4900, "r", 1.5)
        self.assertTrue(krx.on_grid(c["target"]))
        self.assertLessEqual(c["target"], 4995 + 1.5 * c["risk"])

    def test_refusals(self):
        with self.assertRaises(exitp.PlanError):
            exitp.compute(120500, "price", 120400, "r", 2)          # one tick: too tight
        with self.assertRaises(exitp.PlanError):
            exitp.compute(120550, "pct", 1, "r", 2)                 # entry off the grid
        with self.assertRaises(exitp.PlanError):
            exitp.compute(120500, "price", 80000, "r", 2, limits=(84300, 156300))   # under 하한가

    def test_flat_by(self):
        self.assertEqual(exitp.resolve_flat_by("2026-10-30", None, krx.DEFAULTS, 565)[0], "15:15")
        with self.assertRaises(exitp.PlanError):
            exitp.resolve_flat_by("2026-10-30", "15:25", krx.DEFAULTS, 565)
        t, note = exitp.resolve_flat_by("2026-11-19", None, krx.DEFAULTS, 625)
        self.assertEqual(t, "16:15")
        self.assertIn("수능", note)


class Scanner(unittest.TestCase):
    def test_kind_parse(self):
        html = ('<table><tr><th>회사명</th><th>종목코드</th><th>업종</th></tr>'
                '<tr><td>삼성전자</td><td>5930</td><td>반도체</td></tr>'
                '<tr><td>미래에셋비전스팩1호</td><td>123450</td><td>-</td></tr></table>').encode("euc-kr")
        rows, dropped = scan.parse_kind(html, "KOSPI")
        self.assertEqual(rows[0]["code"], "005930")
        self.assertEqual(rows[0]["name"], "삼성전자")
        self.assertEqual(dropped, {"SPAC (스팩)": 1})

    def test_same_list_warning(self):
        html = '<table><tr><th>회사명</th><th>종목코드</th></tr><tr><td>A</td><td>000010</td></tr></table>'
        stocks, counts = scan.build_universe({"KOSPI": html, "KOSDAQ": html})
        self.assertIn("warning", counts)
        self.assertEqual(stocks["000010"]["market"], "")

    def test_daily_setups(self):
        closes = [100.0 + i for i in range(40)]
        closes[-1] = closes[-2] * 1.30
        tags, nums = scan.find_setups(closes)
        self.assertIn("limit_up", tags)
        self.assertIn("breakout_20d", tags)

    def test_orb_with_gaps(self):
        doc = fakes.chart(code="247540", market="KOSDAQ", end="2026-10-30", days=1, until="10:30", seed=3)
        res = krx.chart_result(doc)
        q = res["indicators"]["quote"][0]
        for k in ("open", "high", "low", "close", "volume"):
            del q[k][10]                                 # the 09:50 bar: no trade in that window
        del res["timestamp"][10]
        series = scan.series_from_result(res)
        day = krx.to_kst(NOW).date()
        bars = scan.usable(scan.day_bars(series, day, 630), day, 630, 3)
        self.assertIsNotNone(bars)
        self.assertEqual(len(bars), 18)
        self.assertTrue(bars[10].get("filled"))

    def test_orb_window(self):
        with self.assertRaises(scan.ScanError):
            scan.orb_window(krx.parse_now("2026-10-30 09:15"), None, 15, 900)    # range not done
        with self.assertRaises(scan.ScanError):
            scan.orb_window(krx.parse_now("2026-10-30 15:25"), None, 15, 900)    # closing auction
        day, cut = scan.orb_window(krx.parse_now("2026-10-30 10:00"), None, 15, 900)
        self.assertEqual(cut, 600)

    def test_cli_offline(self):
        d = tempfile.mkdtemp()
        try:
            for i, (c, m, p) in enumerate((("005930", "KOSPI", 98000), ("247540", "KOSDAQ", 150000))):
                fakes.dump(fakes.chart(code=c, market=m, start_price=p, seed=i + 3, days=2, until="10:00"),
                           os.path.join(d, "%s.json" % c))
            out = os.path.join(d, "w.json")
            rc = scan.main(["orb", "--offline", d, "--now", "2026-10-30 10:00", "--at", "10:00", "--out", out,
                            "--universe", os.path.join(d, "none.json"), "--quiet"])
            doc = krx.read_json(out)
            self.assertEqual(doc["mode"], "orb")
            self.assertEqual(rc, 0 if doc["hits"] else 1)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
