import datetime
import json
import os
import subprocess
import sys
import unittest

import _load  # noqa: F401
import fakes
import krx


class Ticks(unittest.TestCase):
    def test_table(self):
        for price, tick in ((1999, 1), (2000, 5), (4995, 5), (5000, 10), (19990, 10), (20000, 50), (49950, 50),
                            (50000, 100), (199900, 100), (200000, 500), (499500, 500), (500000, 1000)):
            self.assertEqual(krx.tick_size(price), tick, price)

    def test_rounding(self):
        self.assertEqual(krx.round_tick(98750, "down"), 98700)
        self.assertEqual(krx.round_tick(98750, "up"), 98800)
        self.assertEqual(krx.round_tick(98749), 98700)
        self.assertEqual(krx.round_tick(1999.6), 2000)
        self.assertEqual(krx.round_tick(49990, "up"), 50000)
        self.assertEqual(krx.round_tick(200250, "down"), 200000)
        self.assertTrue(krx.on_grid(98700))
        self.assertFalse(krx.on_grid(98750))
        self.assertTrue(krx.on_grid(4995))
        self.assertFalse(krx.on_grid(5005))

    def test_stop_and_target_rounding(self):
        self.assertEqual(krx.round_away(97950, True), 97900)
        self.assertEqual(krx.round_toward(99550, True), 99500)

    def test_price_limits(self):
        lo, hi = krx.price_limits(100000)
        self.assertEqual((lo, hi), (70000, 130000))
        lo, hi = krx.price_limits(73400)
        self.assertEqual(hi, krx.round_tick(73400 * 1.3, "down"))
        self.assertTrue(krx.on_grid(lo) and krx.on_grid(hi))
        self.assertLessEqual(hi, 73400 * 1.3)
        self.assertGreaterEqual(lo, 73400 * 0.7)


class Calendar(unittest.TestCase):
    def test_holidays_2026(self):
        self.assertEqual(sum(1 for d in krx.HOLIDAYS if d.startswith("2026")), 17)
        for d in ("2026-02-17", "2026-06-03", "2026-07-17", "2026-09-25", "2026-10-09", "2026-12-31"):
            self.assertIsNone(krx.session(d), d)
        self.assertIsNotNone(krx.session("2026-12-30"))
        self.assertEqual(krx.holiday("2026-10-31"), "weekend")

    def test_sessions(self):
        s = krx.session("2026-10-30")
        self.assertEqual((s["open"], s["cont_end"], s["close"]), (540, 920, 930))
        self.assertEqual(krx.SESSION_BARS, 76)
        exam = krx.session("2026-11-19")
        self.assertEqual((krx.hm(exam["open"]), krx.hm(exam["cont_end"]), krx.hm(exam["close"])),
                         ("10:00", "16:20", "16:30"))
        self.assertEqual(krx.hm(krx.session("2026-01-02")["open"]), "10:00")

    def test_market_status(self):
        st = lambda t: krx.market_status(krx.parse_now(t))["phase"]  # noqa: E731
        self.assertEqual(st("2026-10-30 08:00"), "pre-open")
        self.assertEqual(st("2026-10-30 08:45"), "opening-auction")
        self.assertEqual(st("2026-10-30 09:00"), "open")
        self.assertEqual(st("2026-10-30 15:19"), "open")
        self.assertEqual(st("2026-10-30 15:20"), "closing-auction")
        self.assertEqual(st("2026-10-30 15:30"), "closed")
        self.assertEqual(krx.market_status(krx.parse_now("2026-10-31 10:00"))["next_open"], "2026-11-02")
        self.assertEqual(krx.next_trading_day("2026-09-23").isoformat(), "2026-09-28")

    def test_time(self):
        utc = krx.parse_now("2026-10-30 09:00")
        self.assertEqual(utc, datetime.datetime(2026, 10, 30, 0, 0))
        self.assertEqual(krx.parse_now("2026-10-30T00:00:00Z"), utc)
        self.assertEqual(krx.hhmm_min("15:15"), 915)
        with self.assertRaises(krx.KRXError):
            krx.hhmm_min("3:15 PM")


class Costs(unittest.TestCase):
    def test_tax(self):
        self.assertEqual(krx.sell_tax_rate(2026), 0.0020)
        self.assertEqual(krx.sell_tax_rate(2025), 0.0015)
        self.assertEqual(krx.sell_tax_rate(2027), 0.0020)

    def test_round_trip(self):
        c = krx.trade_costs(100000, 101000, 10, 2026, 0.015)
        self.assertEqual(c["tax"], round(101000 * 10 * 0.002))
        self.assertEqual(c["commission"], round((100000 + 101000) * 10 * 0.00015))
        self.assertAlmostEqual(krx.cost_per_share(100000, 101000, 2026, 0.015),
                               (201000 * 0.00015) + 101000 * 0.002)


class Symbols(unittest.TestCase):
    U = {"005930": {"name": "삼성전자", "market": "KOSPI"}, "005935": {"name": "삼성전자우", "market": "KOSPI"},
         "247540": {"name": "에코프로비엠", "market": "KOSDAQ"}}

    def test_codes(self):
        self.assertEqual(krx.resolve("005930", self.U)["yahoo"], "005930.KS")
        self.assertEqual(krx.resolve("A005930", self.U)["code"], "005930")
        self.assertEqual(krx.resolve("5930", self.U)["code"], "005930")
        self.assertEqual(krx.resolve("247540.KQ", {})["market"], "KOSDAQ")
        self.assertEqual(krx.resolve("0080G0", {})["code"], "0080G0")

    def test_names(self):
        self.assertEqual(krx.resolve("에코프로비엠", self.U)["yahoo"], "247540.KQ")
        self.assertEqual(krx.resolve("삼성전자", self.U)["code"], "005930")   # exact beats partial
        with self.assertRaises(krx.KRXError):
            krx.resolve("삼성", self.U)                                       # ambiguous
        with self.assertRaises(krx.KRXError):
            krx.resolve("Samsung", {})


class Bars(unittest.TestCase):
    def test_session_bars(self):
        doc = fakes.chart(days=3, end="2026-10-30")
        res = krx.chart_result(json.dumps(doc))
        days, dropped = krx.session_bars(res, krx.parse_now("2026-10-31 10:00"))
        self.assertEqual(sorted(days), ["2026-10-28", "2026-10-29", "2026-10-30"])
        d = days["2026-10-30"]
        self.assertEqual(len(d["bars"]), 76)
        self.assertEqual(d["bars"][0]["start"], "09:00")
        self.assertEqual(d["bars"][75]["start"], "15:15")
        self.assertIsNotNone(d["auction"])

    def test_not_complete(self):
        doc = fakes.chart(days=2, end="2026-10-30", until="10:00")
        days, dropped = krx.session_bars(krx.chart_result(doc), krx.parse_now("2026-10-30 10:02"))
        self.assertEqual(max(days["2026-10-30"]["bars"]), 11)          # the 09:55 bar; 10:00 still forming
        self.assertEqual(dropped["not_complete"], 1)

    def test_gaps_filled(self):
        doc = fakes.chart(days=2, end="2026-10-30", missing=0.05, seed=5)
        days, _ = krx.session_bars(krx.chart_result(doc), krx.parse_now("2026-10-31 10:00"))
        d = days["2026-10-30"]
        full = krx.complete_day(d)
        self.assertEqual(len(full), 76)
        filled = [b for b in full if b.get("filled")]
        self.assertTrue(filled)
        for b in filled:
            self.assertEqual(b["v"], 0)
            self.assertEqual(b["o"], b["c"])

    def test_too_many_gaps(self):
        doc = fakes.chart(days=2, end="2026-10-30", missing=0.4, seed=5)
        days, _ = krx.session_bars(krx.chart_result(doc), krx.parse_now("2026-10-31 10:00"))
        self.assertIsNone(krx.complete_day(days["2026-10-30"]))


class Copies(unittest.TestCase):
    def test_every_skill_has_the_same_krx(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, os.path.join(root, "tools", "sync_krx.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
