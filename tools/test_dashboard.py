"""build_dashboard.py のテスト（ダミーの古物台帳・発送明細・出品一覧で）."""
import json
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import appraise  # noqa: E402
import build_dashboard as bd  # noqa: E402

LEDGER = """"#","購入日","販売日","品目(商品名)","数量","No.","仕入金額(円)
送料含む","販売金額($)
送料含む","アカウント名","ヤフオクURL","備考"
"1","2026/07/26","2026/10/3","Canon FL 50mm f/1.4","1","A001","8,000","180","acc1","",""
"2","2026/07/24","2026/10/20","Nikon Zoom-NIKKOR 35-135mm F3.5-4.5 AI-S","1","A002","15,000","120","acc2","",""
"3","2026/07/26","2026/11/2","Canon FD 50mm F1.4 S.S.C.","1","A003","6,000","150","acc1","",""
"4","2026/08/08","2026/11/10","Nikon FM2 Black Body","1","A004","25,000","320","SPM JAPAN 合同会社","",""
"5","2026/08/10","","Pentax MX Body","1","A005","9,000","","acc3","",""
"""
OLD_LEDGER = """古物台帳,,,,,
,,,,,
販売日,品目(商品名),数量,No.,"仕入金額
(円)",販売金額($)
2026/10/03,Canon FL 50mm f/1.4,1,A001,8000,180
,Pentax MX Body,1,A005,9000,
"""
COSTS = """管理番号,cpass_shipping_jpy,duty_jpy,aucrevo_jpy,ebay_order_id,country
A001,3200,0,500,1,US
a003,2800,400,500,2,GB
"""
KANRI = """No.,状態,購入日,販売日
A001,MINT,2026/07/26,2026/10/03
a002,E5/点カビ(前玉),2026/07/24,2026/10/20
A003,AlmostMINT/チリ,2026/07/26,2026/11/02
A005,NMINT,2026/08/10,
"""
LISTINGS = """days_listed,title,site,currency,price,watchers,quantity,sku,ebay_url
90,Nikon F3 Body [Near MINT],US,USD,300,0,1,B1,https://www.ebay.com/itm/1
90,Nikon F3 Body [Near MINT],UK,GBP,240,0,1,B1,https://www.ebay.com/itm/2
10,Canon AE-1 [Exc+5],US,USD,150,3,1,B2,https://www.ebay.com/itm/3
5,Leica M3 TOP MINT,US,USD,1500,8,1,B3,https://www.ebay.com/itm/4
3,Olympus OM-1 for parts,US,USD,40,1,1,B4,https://www.ebay.com/itm/5
3,Minolta X-700,US,USD,90,1,1,B5,https://www.ebay.com/itm/6
"""


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "ledger.csv").write_text(LEDGER, encoding="utf-8-sig")
        (self.tmp / "costs.csv").write_text(COSTS, encoding="utf-8")
        (self.tmp / "kanri.csv").write_text(KANRI, encoding="utf-8")
        (self.tmp / "reports").mkdir()
        (self.tmp / "reports" / "listings_20261114.csv").write_text(LISTINGS, encoding="utf-8-sig")
        self.cfg = appraise.load_config(None)

    def data(self, targets=None):
        ledger, cols = bd.read_ledger(self.tmp / "ledger.csv")
        listings, ld = bd.latest_listings(self.tmp / "reports")
        return bd.build(ledger, cols, bd.read_costs(self.tmp / "costs.csv"), listings, ld, targets or {},
                        self.cfg, 150.0, date(2026, 11, 14), bd.read_kanri(self.tmp / "kanri.csv"))

    def test_columns_real_header(self):
        _, cols = bd.read_ledger(self.tmp / "ledger.csv")
        self.assertEqual(cols["cost_jpy"], "仕入金額(円)送料含む")
        self.assertEqual(cols["price_usd"], "販売金額($)送料含む")
        self.assertEqual(cols["sku"], "No.")
        self.assertEqual(cols["supplier"], "アカウント名")
        self.assertEqual(cols["buy_date"], "購入日")

    def test_old_six_column_ledger(self):
        (self.tmp / "old.csv").write_text(OLD_LEDGER, encoding="utf-8-sig")
        rows, cols = bd.read_ledger(self.tmp / "old.csv")
        self.assertEqual(len(rows), 2)
        self.assertIsNone(cols["supplier"])
        self.assertIsNone(cols["buy_date"])

    def test_supplier_and_days(self):
        d = self.data()
        self.assertEqual(d["supplier"][0], {**d["supplier"][0], "label": "acc1", "count": 2})
        self.assertEqual(d["days_to_sell"]["n"], 4)
        self.assertEqual(d["stock_days"]["n"], 1)  # 未販売の Pentax MX（購入から96日）
        self.assertEqual(d["kpi"]["stock_cost"], 9000)

    def test_kpi_and_matching(self):
        d = self.data()
        self.assertEqual(d["kpi"]["count"], 4)
        self.assertEqual(d["kpi"]["stock"], 1)
        self.assertEqual(d["kpi"]["matched"], 2)  # 大文字小文字の違い（a003）も一致
        self.assertEqual(d["kpi"]["ship_actual"], 2)
        self.assertEqual([m["month"] for m in d["monthly"]], ["2026-10", "2026-11"])

    def test_profit_matches_formula(self):
        p = bd.profit_parts(200, 10000, self.cfg, 150.0, None)
        e = self.cfg["ebay"]
        fees = 200 * (e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]) / 100 + e["per_order_fee_usd"]
        net = (200 - fees) * 150 * (1 - self.cfg["fx"]["conversion_loss_pct"] / 100)
        self.assertAlmostEqual(p["profit"], net - self.cfg["shipping"]["deduction_usd"] * 150 - 10000, places=6)
        bd_total = sum(v for k, v in p.items() if k in ("cost", "fees", "ship", "duty", "aucrevo", "loss", "profit"))
        self.assertAlmostEqual(bd_total, p["sale"], places=6)

    def test_conditions_dedupe(self):
        d = self.data()
        self.assertEqual(d["listings_unique"], 5)  # UK の複製は数えない
        got = {c["label"]: c["count"] for c in d["conditions"]}
        self.assertEqual(got, {"TOP MINT": 1, "Near MINT": 1, "Exc+5": 1, "ジャンク": 1, "表記なし": 1})

    def test_sold_conditions(self):
        d = self.data()
        self.assertEqual([(c["label"], c["count"]) for c in d["sold_conditions"]],
                         [("MINT", 1), ("Almost MINT", 1), ("Exc+5", 1)])  # A004 は管理シートに無い
        self.assertEqual(d["sold_cond_matched"], 3)
        self.assertEqual(d["stock_conditions"], [{"label": "Near MINT", "count": 1}])
        self.assertEqual((d["optical"]["n"], d["optical"]["clean_n"]), (1, 2))

    def test_no_kanri(self):
        ledger, cols = bd.read_ledger(self.tmp / "ledger.csv")
        d = bd.build(ledger, cols, {}, [], "", {}, self.cfg, 150.0, date(2026, 11, 14))
        self.assertIsNone(d["sold_conditions"])

    def test_country_from_costs(self):
        labels = {c["label"] for c in self.data()["country"]}
        self.assertTrue({"アメリカ", "イギリス", "（不明）"} <= labels)

    def test_dates(self):
        self.assertEqual(bd.parse_date("2026/1/5"), date(2026, 1, 5))
        self.assertEqual(bd.parse_date("2026年11月3日"), date(2026, 11, 3))
        self.assertEqual(bd.parse_date("46000"), date(2025, 12, 9))
        self.assertIsNone(bd.parse_date("未"))

    def test_render(self):
        html = bd.render(self.data({"sales_jpy": 300000}))
        self.assertNotIn("/*__DATA__*/null", html)
        blob = re.search(r"const D = (.*?);\n", html).group(1)
        self.assertEqual(json.loads(blob)["cumulative"]["target_sales"], 300000)


if __name__ == "__main__":
    unittest.main()
