#!/usr/bin/env python3
"""Budget Manager for krx-budget (Korean market): sizes a planned day trade to your
budget and your max loss per trade, in won and whole shares, with Korean costs
counted. Standard library only. It never places an order.

    python3 size.py                              # krx/plan.json + krx/config.json
    python3 size.py --write                      # also write krx/orders.json for krx-risk
    python3 size.py --budget 3000000 --max-loss 30000
    python3 size.py --market                     # a market order (시장가) instead of a limit (지정가)
    python3 size.py --lang ko

The size is the smallest of four limits, in whole shares:
  max loss      max_loss_per_trade_krw / (entry - stop + the round-trip cost at the stop)
                The cost is commission on both legs plus the 0.20 % sell tax: the loss
                at the stop is bigger than entry minus stop, and the size accounts for it.
  position cap  max_position_pct of the budget / the entry
  max order     max_order_krw / the entry
  cash          the budget (or cash in krx/portfolio.json) minus cash_buffer_pct, / the entry
Refused, with the reason: zero shares, reward:risk under min_reward_risk, an order
under min_order_krw, a plan for another day, a blocked code, a short sale.

The order is a bracket: buy at the limit (default: the plan's entry, a 지정가 order),
with a stop-loss and a take-profit. Many Korean brokers do not take bracket orders
directly; they offer an automatic stop / take-profit watch (자동감시주문, 스탑로스)
that does the same job. krx-broker in this pack is paper only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx  # noqa: E402

SETUP_WORDS = {"orb": "opening-range breakout", "level": "close above a level", "flag": "flag",
               "bounce": "oversold bounce"}


class SizeError(Exception):
    pass


def shares_for(krw, per_share):
    if not per_share or per_share <= 0:
        return 0
    return max(0, int(float(krw) / per_share + 1e-9))


def size_trade(plan, cfg, budget=None, limit=None, market=False, cash=None, today=None):
    """One plan + your rules -> the sizing (a dict). qty may be 0 with a refusal."""
    c = dict(krx.DEFAULTS)
    c.update(cfg or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    try:
        info = krx.resolve(plan.get("code") or plan.get("symbol"))
    except krx.KRXError as e:
        raise SizeError(str(e))
    code = info["code"]
    if str(plan.get("side") or "buy").lower() != "buy":
        raise SizeError("this pack sizes buys only; short selling is not open to ordinary retail day traders")
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in Seoul: make a new plan with krx-exit"
                        % (plan["date"], today))
    try:
        entry_plan, stop, target = (krx.num(plan.get(k), k) for k in ("entry", "stop", "target"))
    except krx.KRXError as e:
        raise SizeError("the plan needs entry, stop and target (run krx-exit): %s" % e)
    if code in {str(b).zfill(6) for b in c.get("blocked") or []}:
        raise SizeError("%s is on your blocked list" % code)
    budget = float(budget if budget is not None else c.get("budget") or 0)
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in krx/config.json (in won)")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if limit is not None:
        ref, ref_from = krx.round_tick(limit), "limit"
    else:
        ref, ref_from = krx.round_tick(entry_plan), "plan"
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s" % (krx.px(stop), krx.px(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with krx-exit" % (krx.px(target), krx.px(ref)))
    year = int(str(plan.get("date") or today or "2026")[:4])
    comm = float(c.get("commission_pct", krx.DEFAULT_COMMISSION_PCT))
    risk = ref - stop
    cost_stop = krx.cost_per_share(ref, stop, year, comm) if c.get("include_costs", True) else 0.0
    cost_target = krx.cost_per_share(ref, target, year, comm) if c.get("include_costs", True) else 0.0
    loss_per_share = risk + cost_stop
    cash_buffer = round(budget * buf_pct / 100.0)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s buffer" % (krx.won(budget), krx.won(cash_buffer))
    if cash is not None and cash - cash_buffer < available:
        available = cash - cash_buffer
        cash_note = "cash %s in krx/portfolio.json minus the %s buffer" % (krx.won(cash), krx.won(cash_buffer))
    available = max(available, 0.0)
    # the cash limit must also cover the buy commission
    per_share_cash = ref * (1 + comm / 100.0)
    limits = []
    max_loss = float(c.get("max_loss_per_trade_krw") or 0)
    if max_loss:
        limits.append({"name": "max loss", "krw": max_loss, "shares": shares_for(max_loss, loss_per_share),
                       "how": "%s / (%s risk + %s costs) a share" % (krx.won(max_loss), krx.px(risk),
                                                                     "{:,.0f}".format(cost_stop))})
    cap_pct = float(c.get("max_position_pct") or 0)
    if cap_pct:
        cap = budget * cap_pct / 100.0
        limits.append({"name": "position cap", "krw": cap, "shares": shares_for(cap, ref),
                       "how": "%g%% of %s = %s / %s" % (cap_pct, krx.won(budget), krx.won(cap), krx.px(ref))})
    max_order = float(c.get("max_order_krw") or 0)
    if max_order:
        limits.append({"name": "max order", "krw": max_order, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (krx.won(max_order), krx.px(ref))})
    limits.append({"name": "cash", "krw": available, "shares": shares_for(available, per_share_cash),
                   "how": "%s = %s / %s with commission" % (cash_note, krx.won(available), krx.px(ref))})
    qty = max(0, min(l["shares"] for l in limits))
    bound = next(l["name"] for l in limits if l["shares"] == qty)
    for l in limits:
        l["binds"] = l["name"] == bound
    cost = qty * ref
    loss_at_stop = qty * loss_per_share
    gain_at_target = qty * (target - ref - cost_target)
    rr = round((target - ref) / float(risk), 2)
    rr_net = round((target - ref - cost_target) / loss_per_share, 2) if loss_per_share else None
    refusal = None
    if qty == 0:
        if bound == "max loss":
            refusal = ("one share loses %s at the stop (%s risk + %s costs), more than your %s max loss per trade. "
                       "No order. Skip it, or plan a closer stop with krx-exit."
                       % (krx.won(loss_per_share), krx.px(risk), "{:,.0f}".format(cost_stop), krx.won(max_loss)))
        else:
            refusal = "one share of %s costs %s, more than your %s limit. No order." % (code, krx.won(ref), bound)
    elif c.get("min_reward_risk") and rr < float(c["min_reward_risk"]) - 1e-9:
        refusal = ("reward:risk %.2f is under your minimum %g, so krx-risk would block it. No order."
                   % (rr, float(c["min_reward_risk"])))
    elif cost < float(c.get("min_order_krw") or 0):
        refusal = "%d share(s) of %s is %s, under your %s minimum order. No order." % (
            qty, code, krx.won(cost), krx.won(c["min_order_krw"]))
    return {
        "code": code, "symbol": code, "name": plan.get("name") or info.get("name", ""), "side": "buy",
        "setup": plan.get("setup", ""), "date": plan.get("date"), "entry_time": plan.get("entry_time") or "",
        "entry_plan": entry_plan, "entry": ref, "entry_from": ref_from, "stop": int(stop), "target": int(target),
        "risk_per_share": risk, "cost_at_stop": round(cost_stop, 1), "cost_at_target": round(cost_target, 1),
        "loss_per_share": round(loss_per_share, 1), "reward_risk": rr, "reward_risk_net": rr_net,
        "budget": budget, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_krw": max_loss, "limits": limits, "qty": qty, "bound_by": bound,
        "cost": cost, "max_loss": round(loss_at_stop), "gain_at_target": round(gain_at_target),
        "flat_by": plan.get("flat_by") or c.get("flat_by"), "order_type": "market" if market else "limit",
        "refusal": refusal, "commission_pct": comm, "sell_tax_pct": round(100 * krx.sell_tax_rate(year), 3),
    }


def bracket_order(s):
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"code": s["code"], "symbol": s["code"], "name": s["name"], "side": "buy", "qty": int(s["qty"]),
         "type": s["order_type"], "time_in_force": "day", "order_class": "bracket",
         "take_profit_price": s["target"], "stop_loss_price": s["stop"]}
    if s["order_type"] == "limit":
        o["limit_price"] = int(s["entry"])
    o["why"] = ("%s on %s: %d shares, bound by your %s (%d x %s = %s at the stop, costs included)"
                % (SETUP_WORDS.get(s["setup"], s["setup"] or "day trade"), s["code"], s["qty"], s["bound_by"],
                   s["qty"], krx.px(s["loss_per_share"]), krx.won(s["max_loss"])))
    o["plan"] = {"date": s["date"], "entry": s["entry"], "stop": s["stop"], "target": s["target"],
                 "risk_per_share": s["risk_per_share"], "loss_per_share": s["loss_per_share"],
                 "max_loss": s["max_loss"], "gain_at_target": s["gain_at_target"], "setup": s["setup"],
                 "entry_time": s["entry_time"], "flat_by": s["flat_by"],
                 "reward_risk": s["reward_risk"], "reward_risk_net": s["reward_risk_net"]}
    return o


def report(s, order, lang="en"):
    ko = lang == "ko"
    who = ("%s %s" % (s["name"], s["code"])) if s["name"] else s["code"]
    L = []
    if ko:
        L.append("%s 매수, 진입 %s%s, 손절 %s, 목표 %s." % (who, krx.px(s["entry_plan"]),
                                                     (" (%s)" % s["entry_time"]) if s["entry_time"] else "",
                                                     krx.px(s["stop"]), krx.px(s["target"])))
        L.append("주당 위험 %s원 + 왕복 비용 %s원 = 손절 시 주당 손실 %s원. 손익비 %.2f (비용 후 %.2f)." % (
            krx.px(s["risk_per_share"]), "{:,.0f}".format(s["cost_at_stop"]), krx.px(s["loss_per_share"]),
            s["reward_risk"], s["reward_risk_net"] or 0))
        L.append("")
        L.append("한도 (정수 주, 가장 작은 값이 적용):")
    else:
        L.append("%s buy, %s, entry %s%s. Stop %s, target %s." % (
            who, SETUP_WORDS.get(s["setup"], s["setup"] or "setup"), krx.px(s["entry_plan"]),
            (" at " + s["entry_time"]) if s["entry_time"] else "", krx.px(s["stop"]), krx.px(s["target"])))
        L.append("Risk %s won a share + about %s won of costs at the stop (%.3f%% commission each way, %.2f%% sell "
                 "tax) = %s won lost a share at the stop. Reward:risk %.2f, %.2f after costs." % (
                     krx.px(s["risk_per_share"]), "{:,.0f}".format(s["cost_at_stop"]), s["commission_pct"],
                     s["sell_tax_pct"], krx.px(s["loss_per_share"]), s["reward_risk"], s["reward_risk_net"] or 0))
        L.append("")
        L.append("Limits (whole shares; the smallest one wins):")
    names_ko = {"max loss": "1회 최대 손실", "position cap": "종목 비중", "max order": "1회 주문 한도", "cash": "현금"}
    for l in s["limits"]:
        L.append("  %-13s %-62s %5s shares%s" % (names_ko[l["name"]] if ko else l["name"], l["how"],
                                                "{:,}".format(l["shares"]), "  <- binds" if l["binds"] else ""))
    L.append("")
    if order is None:
        L.append(("주문 없음: " if ko else "No order: ") + s["refusal"])
    elif ko:
        L.append("수량: %s주 = %s. 손절 시 최대 손실 %s (비용 포함), 목표 도달 시 순이익 약 %s." % (
            "{:,}".format(s["qty"]), krx.won(s["cost"]), krx.won(s["max_loss"]), krx.won(s["gain_at_target"])))
        L.append("제안 주문 (아직 아무것도 하지 않았습니다): %s %s주 %s, 익절 %s, 손절 %s, 당일." % (
            order["code"], order["qty"], ("지정가 %s" % krx.px(order["limit_price"])) if order["type"] == "limit"
            else "시장가", krx.px(order["take_profit_price"]), krx.px(order["stop_loss_price"])))
        L.append("손절 주문은 약속이 아닙니다: 급락, 갭, VI, 하한가에서는 %s보다 낮게 체결될 수 있습니다." % krx.px(s["stop"]))
        L.append("%s까지 청산. 다음: /krx-risk 가 점검하고, /krx-broker 가 예/아니오를 묻습니다 (모의투자)." % s["flat_by"])
    else:
        L.append("Size: %s shares = %s. Max loss at the stop: %s with costs. At the target: about %s net." % (
            "{:,}".format(s["qty"]), krx.won(s["cost"]), krx.won(s["max_loss"]), krx.won(s["gain_at_target"])))
        L.append("Proposed bracket (nothing placed): buy %s %s, %s, day, take-profit %s, stop-loss %s." % (
            order["qty"], order["code"], ("limit %s" % krx.px(order["limit_price"])) if order["type"] == "limit"
            else "market", krx.px(order["take_profit_price"]), krx.px(order["stop_loss_price"])))
        L.append("A stop is an order, not a promise: a fast drop, a gap, a VI halt or a limit-down day can fill "
                 "below %s." % krx.px(s["stop"]))
        L.append("Flat by %s KST. Next: /krx-risk checks it, then /krx-broker asks you yes or no (paper)." % s["flat_by"])
    L.append("투자 조언이 아닌 교육용 도구입니다." if ko else "Education and tooling, not financial advice.")
    return "\n".join(L)


def write_orders(path, orders, cfg, now_utc):
    krx.write_json(path, {"created_by": "krx-budget", "created_at": krx.iso_utc(now_utc), "status": "proposed",
                          "paper": True, "orders": orders,
                          "note": "Proposals only. Nothing happens until krx-risk passes each order and you say "
                                  "yes to it in the chat. This pack trades on paper only."})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Budget Manager (krx-budget): size a planned trade in won.")
    ap.add_argument("--plan", default="krx/plan.json")
    ap.add_argument("--config", default=krx.CONFIG_PATH)
    ap.add_argument("--portfolio", default="krx/portfolio.json")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--max-loss", type=float, help="override max_loss_per_trade_krw")
    ap.add_argument("--limit", help="limit price, or 'plan' (default: the plan's entry)")
    ap.add_argument("--market", action="store_true", help="a market order (시장가) instead of a limit")
    ap.add_argument("--now")
    ap.add_argument("--lang", choices=["en", "ko"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", nargs="?", const="krx/orders.json", metavar="PATH")
    a = ap.parse_args(argv)
    try:
        cfg = krx.load_config(a.config)
        if a.max_loss is not None:
            cfg["max_loss_per_trade_krw"] = a.max_loss
        now = krx.parse_now(a.now)
        today = krx.to_kst(now).date().isoformat()
        plan = krx.read_json(a.plan)
        if plan is None:
            raise SizeError("no plan at %s: run /krx-exit first" % a.plan)
        limit = None
        if a.limit and a.limit != "plan" and not a.market:
            limit = krx.num(a.limit, "--limit")
            if not krx.on_grid(limit):
                raise SizeError("--limit %s is not on the KRX tick grid" % krx.px(limit))
        pf = krx.read_json(a.portfolio)
        cash = float(pf["cash"]) if isinstance(pf, dict) and pf.get("cash") is not None else None
        s = size_trade(plan, cfg, a.budget, limit, a.market, cash, today)
        order = bracket_order(s)
        if a.write and order:
            write_orders(a.write, [order], cfg, now)
        if a.json:
            print(json.dumps({"sizing": s, "order": order}, indent=2, ensure_ascii=False))
        else:
            print(report(s, order, krx.lang_of(a.lang, cfg)))
            if a.write and order:
                print("Wrote %s." % a.write)
        return 0 if order else 1
    except (SizeError, krx.KRXError) as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
