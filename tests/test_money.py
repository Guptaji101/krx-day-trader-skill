"""Budget Manager, Risk Manager, Paper Broker and Alerts."""

import os
import shutil
import tempfile
import unittest

from _load import load
import krx

size = load("krx-budget", "size")
risk = load("krx-risk", "check")
ticket = load("krx-broker", "ticket")
alerts = load("krx-alerts", "alerts")

PLAN = {"code": "005930", "symbol": "005930", "name": "삼성전자", "side": "buy", "date": "2026-10-30",
        "setup": "orb", "entry": 120500, "stop": 119700, "target": 122100, "entry_time": "09:25", "flat_by": "15:15"}
NOW = krx.parse_now("2026-10-30 10:30")


def sized(**cfg):
    c = dict(krx.DEFAULTS)
    c.update(cfg)
    s = size.size_trade(PLAN, c, today="2026-10-30")
    return s, size.bracket_order(s)


class Budget(unittest.TestCase):
    def test_costs_in_the_max_loss(self):
        s, o = sized(max_position_pct=100, max_order_krw=10 ** 9, budget=10 ** 8)
        per = 800 + krx.cost_per_share(120500, 119700, 2026, 0.015)
        self.assertAlmostEqual(s["loss_per_share"], round(per, 1))
        self.assertEqual(s["qty"], int(50000 / per))          # 46, not 62 (= 50,000 / 800)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertLessEqual(s["max_loss"], 50000)

    def test_position_cap_binds(self):
        s, o = sized()
        self.assertEqual((s["qty"], s["bound_by"]), (31, "position cap"))
        self.assertEqual(o["limit_price"], 120500)
        self.assertEqual((o["stop_loss_price"], o["take_profit_price"]), (119700, 122100))

    def test_refusals(self):
        s, o = sized(max_loss_per_trade_krw=1000)
        self.assertIsNone(o)
        self.assertIn("max loss", s["refusal"])
        with self.assertRaises(size.SizeError):
            size.size_trade(PLAN, krx.DEFAULTS, today="2026-11-02")
        with self.assertRaises(size.SizeError):
            size.size_trade(dict(PLAN, side="sell"), krx.DEFAULTS, today="2026-10-30")
        s, o = sized(min_reward_risk=3)
        self.assertIsNone(o)


class Risk(unittest.TestCase):
    def order(self, **kw):
        s, o = sized()
        o = dict(o)
        o.update(kw)
        return o

    def check(self, orders, when="2026-10-30 10:30", **kw):
        cfg = dict(krx.DEFAULTS)
        cfg.update(kw.pop("cfg", {}))
        return risk.check(orders, cfg, now=krx.parse_now(when), **kw)

    def test_pass(self):
        r = self.check([self.order()])
        self.assertEqual(r["results"][0]["verdict"], "PASS", r["results"][0]["reasons"])
        self.assertLessEqual(r["results"][0]["max_loss"], 50000)

    def test_time_window(self):
        for when, word in (("2026-10-30 09:05", "no entries before"), ("2026-10-30 15:05", "no entries after"),
                           ("2026-10-30 15:22", "closing auction"), ("2026-10-09 10:00", "closed")):
            r = self.check([self.order()], when)["results"][0]
            self.assertEqual(r["verdict"], "BLOCK", when)
            self.assertTrue(any(word in x for x in r["reasons"]), (when, r["reasons"]))

    def test_blocks(self):
        cases = [
            (self.order(limit_price=120550), "tick grid"),
            (self.order(side="sell"), "short sale"),
            (self.order(stop_loss_price=None), "needs a stop-loss"),
            (self.order(code="ABC"), "6-character"),
            (self.order(qty=200), "max loss"),
        ]
        for o, word in cases:
            r = self.check([o])["results"][0]
            self.assertEqual(r["verdict"], "BLOCK", word)
            self.assertTrue(any(word in x for x in r["reasons"]), (word, r["reasons"]))
        r = self.check([self.order()], cfg={"paper": False})["results"][0]
        self.assertTrue(any("paper only" in x for x in r["reasons"]))
        r = self.check([self.order()], cfg={"blocked": ["005930"]})["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        r = self.check([self.order()], flags={"005930": "관리종목"})["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")

    def test_daily_limits(self):
        state = {"orders": [{"status": "filled", "kind": "entry", "at": "2026-10-30T09:40:00+09:00",
                             "max_loss": 40000}] * 3, "closed": []}
        r = self.check([self.order()], state=state)["results"][0]
        self.assertTrue(any("count" in x for x in r["reasons"]))
        self.assertTrue(any("daily loss" in x for x in r["reasons"]))

    def test_price_limits(self):
        r = self.check([self.order()], prices={"005930": {"price": 120500, "prev_close": 90000}})["results"][0]
        self.assertTrue(any("price limits" in x for x in r["reasons"]))


class Broker(unittest.TestCase):
    O = {"code": "005930", "side": "buy", "qty": 31, "type": "limit", "limit_price": 120500,
         "stop_loss_price": 119700, "take_profit_price": 122100}

    def test_parse_yes(self):
        ok = lambda r: ticket.parse_yes(r, self.O, ["005930", "000660"])[0]  # noqa: E731
        for r in ("yes", "Yes.", "yes, buy 31", "네", "네, 31주 매수", "예 005930", "yes 120,500"):
            self.assertTrue(ok(r), r)
        for r in ("ok", "sure", "go", "yes all", "yes?", "yes but wait", "네 근데 잠깐", "yes 30", "yes 000660",
                  "", "모두 네", "좋아"):
            self.assertFalse(ok(r), r)

    def test_round_trip_pnl(self):
        cfg = dict(krx.DEFAULTS)
        pf = {"cash": 5000000, "positions": {}}
        fee = ticket.fill_buy(pf, self.O, 120500, "2026-10-30T10:30:00+09:00", cfg, 33338)
        self.assertEqual(fee, round(31 * 120500 * 0.00015))
        t = ticket.fill_sell(pf, "005930", 31, 122100, NOW, cfg, "target")
        gross = 31 * (122100 - 120500)
        sfee, tax = round(31 * 122100 * 0.00015), round(31 * 122100 * 0.002)
        self.assertEqual(t["pnl"], gross - fee - sfee - tax)
        self.assertEqual(pf["cash"], 5000000 + t["pnl"])
        self.assertEqual(pf["positions"], {})
        with self.assertRaises(ticket.TicketError):
            ticket.fill_sell(pf, "005930", 1, 100, NOW, cfg, "x")        # no short sales

    def test_no_margin(self):
        pf = {"cash": 1000000, "positions": {}}
        with self.assertRaises(ticket.TicketError):
            ticket.fill_buy(pf, self.O, 120500, "x", dict(krx.DEFAULTS), 0)

    def test_full_flow_cli(self):
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(d)
            krx.write_json("krx/plan.json", PLAN)
            self.assertEqual(size.main(["--write", "--now", "2026-10-30 10:30"]), 0)
            self.assertEqual(risk.main(["--now", "2026-10-30 10:30"]), 0)
            self.assertEqual(ticket.main(["--now", "2026-10-30 10:30", "approve", "--reply", "ok"]), 1)
            self.assertEqual(ticket.main(["--now", "2026-10-30 10:30", "approve", "--reply", "yes"]), 0)
            self.assertEqual(ticket.main(["--now", "2026-10-30 10:31", "approve", "--reply", "yes"]), 1)  # done
            krx.write_json("krx/prices.json", {"prices": {"005930": {"price": 119000}}})   # gapped through the stop
            ticket.main(["--now", "2026-10-30 11:00", "check"])
            st = krx.read_json("krx/state/broker.json")
            self.assertEqual(st["closed"][0]["reason"], "stop")
            self.assertEqual(st["closed"][0]["price"], 119000)       # filled worse than the 119,700 stop
            self.assertLess(st["closed"][0]["pnl"], -31 * 800)
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)


class Alerts(unittest.TestCase):
    def status(self, t):
        return krx.market_status(krx.parse_now(t))

    def test_from_plan(self):
        rules = alerts.rules_from_plan(PLAN)
        self.assertEqual([r["type"] for r in rules], ["stop", "target", "time", "near_limit"])
        self.assertTrue(all(r["date"] == "2026-10-30" for r in rules))

    def test_rings_once(self):
        rules = alerts.rules_from_plan(PLAN)
        q = {"005930": {"price": 119600, "prev_close": 118000, "limits": {"lower": 82600, "upper": 153400}}}
        rings, state = alerts.evaluate(rules, q, self.status("2026-10-30 11:00"), {}, krx.parse_now("2026-10-30 11:00"))
        self.assertEqual([r["id"] for r in rings], ["005930-plan-stop"])
        rings, state = alerts.evaluate(rules, q, self.status("2026-10-30 11:05"), state, krx.parse_now("2026-10-30 11:05"))
        self.assertEqual(rings, [])
        rings, state = alerts.evaluate(rules, q, self.status("2026-10-30 15:15"), state, krx.parse_now("2026-10-30 15:15"))
        self.assertEqual([r["id"] for r in rings], ["005930-flat-by"])

    def test_near_limit(self):
        rule = [{"id": "x", "symbol": "005930", "type": "near_limit", "pct": 3}]
        q = {"005930": {"price": 150000, "limits": {"lower": 82600, "upper": 153400}}}
        rings, _ = alerts.evaluate(rule, q, self.status("2026-10-30 11:00"), {}, NOW)
        self.assertIn("상한가", rings[0]["message"])

    def test_exam_day_shift(self):
        rule = [{"id": "t", "type": "time", "at": "15:15", "label": "flat by"}]
        rings, _ = alerts.evaluate(rule, {}, self.status("2026-11-19 15:20"), {}, krx.parse_now("2026-11-19 15:20"))
        self.assertEqual(rings, [])                              # moved to 16:15 on 수능 day
        rings, _ = alerts.evaluate(rule, {}, self.status("2026-11-19 16:15"), {}, krx.parse_now("2026-11-19 16:15"))
        self.assertEqual(len(rings), 1)


if __name__ == "__main__":
    unittest.main()
