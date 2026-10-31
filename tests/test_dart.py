"""News Monitor, Earnings Analyst and Insider Tracker on DART-shaped data."""

import io
import unittest
import zipfile

from _load import load
import krx

news = load("krx-news", "news")
facts = load("krx-analyst", "facts")
insider = load("krx-insider", "insider")

CORPS_XML = ('<?xml version="1.0" encoding="UTF-8"?><result>'
             '<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list>'
             '<list><corp_code>00999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>'
             '</result>').encode("utf-8")


class Dart(unittest.TestCase):
    def test_corpcodes_zip_and_xml(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("CORPCODE.xml", CORPS_XML)
        for data in (b.getvalue(), CORPS_XML):
            corps = krx.parse_corpcodes(data)
            self.assertEqual(corps, {"005930": {"corp_code": "00126380", "name": "삼성전자"}})

    def test_amounts(self):
        self.assertEqual(krx.dart_amount("1,234,567"), 1234567)
        self.assertEqual(krx.dart_amount("-1,234"), -1234)
        self.assertEqual(krx.dart_amount("(500)"), -500)
        self.assertIsNone(krx.dart_amount("-"))
        self.assertIsNone(krx.dart_amount(""))

    def test_needs_key(self):
        with self.assertRaises(krx.KRXError) as e:
            krx.need_key("")
        self.assertIn("opendart.fss.or.kr", str(e.exception))


class News(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(news.tags_for("연결재무제표기준영업(잠정)실적(공정공시)"), ["earnings"])
        self.assertEqual(news.tags_for("주요사항보고서(유상증자결정)"), ["dilution"])
        self.assertEqual(news.tags_for("단일판매ㆍ공급계약체결"), ["contract"])
        self.assertIn("correction", news.tags_for("[기재정정]주요사항보고서(자기주식취득결정)"))
        self.assertIn("danger", news.tags_for("횡령ㆍ배임혐의발생"))

    def test_rss(self):
        rss = ('<rss><channel><item><title>삼성전자 신고가</title><link>https://n/1</link>'
               '<pubDate>Fri, 30 Oct 2026 00:12:00 GMT</pubDate><source url="x">연합뉴스</source></item>'
               '</channel></rss>')
        items = news.parse_rss(rss, "005930")
        self.assertEqual(items[0]["source"], "연합뉴스")
        self.assertEqual(krx.to_kst(items[0]["time"]).strftime("%H:%M"), "09:12")

    def test_disclosures(self):
        corps = {"005930": {"corp_code": "00126380", "name": "삼성전자"}}
        seen = {}

        def fetch(endpoint, key, **p):
            seen.update(p)
            return [{"report_nm": "유상증자결정", "rcept_no": "20261030000001", "rcept_dt": "20261030", "flr_nm": "삼성전자"}]
        items = news.disclosures("005930", corps, "k", 3, "2026-10-30", fetch)
        self.assertEqual(seen["corp_code"], "00126380")
        self.assertEqual(seen["bgn_de"], "20261027")
        self.assertEqual(items[0]["tags"], ["dilution"])
        self.assertIn("rcpNo=20261030000001", items[0]["link"])


class Analyst(unittest.TestCase):
    def rows(self, rev, op, cum_rev=None, cum_op=None, name_only=False):
        f = lambda x: "{:,}".format(int(x))  # noqa: E731
        return [{"sj_div": "CIS", "account_id": "-" if name_only else "ifrs-full_Revenue", "account_nm": "매출액",
                 "thstrm_amount": f(rev), "thstrm_add_amount": f(cum_rev or rev)},
                {"sj_div": "CIS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익(손실)",
                 "thstrm_amount": f(op), "thstrm_add_amount": f(cum_op or op)}]

    def test_q4_is_year_minus_q1_to_q3(self):
        rep = {(2025, 1): facts.report_values(self.rows(100, 10)),
               (2025, 2): facts.report_values(self.rows(110, 12, 210, 22)),
               (2025, 3): facts.report_values(self.rows(120, 14, 330, 36)),
               (2025, 4): facts.report_values(self.rows(460, 50))}
        q = facts.quarters_from(rep)
        self.assertEqual([r["period"] for r in q], ["2025Q1", "2025Q2", "2025Q3", "2025Q4"])
        self.assertEqual(q[3]["values"]["revenue"], 130)
        self.assertEqual(q[3]["values"]["operating_income"], 14)

    def test_name_fallback_and_yoy(self):
        rep = {(2025, 1): facts.report_values(self.rows(100, 10, name_only=True)),
               (2026, 1): facts.report_values(self.rows(150, -5, name_only=True))}
        rows = facts.analyse(facts.quarters_from(rep), 6)
        self.assertEqual(rows[-1]["values"]["revenue"], 150)
        self.assertAlmostEqual(rows[-1]["yoy"]["revenue"], 50.0)
        self.assertEqual(rows[-1]["values"]["operating_income"], -5)
        self.assertIn("Revenue", " ".join(facts.changes(rows)))

    def test_units(self):
        self.assertEqual(facts.big(101e12), "₩101.0 trillion")
        self.assertEqual(facts.big(101e12, "ko"), "101.0조원")
        self.assertEqual(facts.big(5e9, "ko"), "50억원")


class Insider(unittest.TestCase):
    def test_elestock(self):
        rows = insider.ele_rows([{"rcept_no": "1", "rcept_dt": "20261029", "corp_name": "삼성전자", "repror": "홍길동",
                                  "isu_exctv_ofcps": "사내이사", "sp_stock_lmp_cnt": "25,000",
                                  "sp_stock_lmp_irds_cnt": "5,000"},
                                 {"rcept_no": "2", "rcept_dt": "20261015", "corp_name": "삼성전자", "repror": "김철수",
                                  "isu_exctv_ofcps": "부사장", "sp_stock_lmp_cnt": "3,000",
                                  "sp_stock_lmp_irds_cnt": "-2,000"}], "005930")
        self.assertEqual(rows[0]["change"], 5000)
        self.assertEqual([r["who"] for r in insider.filt(rows, buys=True)], ["홍길동"])
        self.assertEqual([r["who"] for r in insider.filt(rows, sells=True)], ["김철수"])
        self.assertEqual(insider.filt(rows, since="2026-10-20"), [rows[0]])
        self.assertIn("added 5,000 shares", insider.describe(rows[0]))

    def test_market_wide(self):
        def fetch(endpoint, key, **p):
            return [{"stock_code": "005930", "corp_name": "삼성전자", "corp_cls": "Y", "rcept_dt": "20261029",
                     "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "rcept_no": "1", "flr_nm": "홍길동"},
                    {"stock_code": "", "corp_name": "비상장", "corp_cls": "E", "rcept_dt": "20261029",
                     "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서", "rcept_no": "2", "flr_nm": "x"},
                    {"stock_code": "247540", "corp_name": "에코프로비엠", "corp_cls": "K", "rcept_dt": "20261029",
                     "report_nm": "주식등의대량보유상황보고서(일반)", "rcept_no": "3", "flr_nm": "블랙록"}]
        rows = insider.market_wide("k", 3, "2026-10-30", fetch)
        self.assertEqual([(r["code"], r["kind"], r["market"]) for r in rows],
                         [("005930", "insider", "KOSPI"), ("247540", "major", "KOSDAQ")])


if __name__ == "__main__":
    unittest.main()
