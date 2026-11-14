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
EBAY = """Custom label,Transaction creation date,Type,Order number,Net amount,Payout currency,Quantity,Item subtotal,Shipping and handling,Final Value Fee - fixed,Final Value Fee - variable,International fee,Gross transaction amount,Transaction currency,Description
A001,3-Oct-26,Order,O-1,155.00,USD,1,180,0,-0.44,-21,-3,180,USD,--
--,4-Oct-26,Other fee,O-1,-5.00,USD,--,--,--,--,--,--,-5,USD,Promoted Listings - General fee
--,5-Oct-26,Hold,O-1,-155.00,USD,--,--,--,--,--,--,-155,USD,--
A002,20-Oct-26,Order,O-2,100.00,USD,1,120,0,-0.44,-15,-2,120,USD,--
A002,25-Oct-26,Refund,O-2,-100.00,USD,--,--,--,--,--,--,-120,USD,--
--,1-Nov-26,Adjustment,--,-2.00,USD,--,--,--,--,--,--,-2,USD,--
"""
TYPES = """管理番号,商品種別
A001,Lens
A004,Camera Body
A003,要確認
"""
JUNK = """No.,仕入(円),実販売(円),売上−仕入(円),購入日,販売日,保管,ヤフオク出品
J0,"4,868","1,734","-3,134",2026/07/24,2026/10/25,オークレボ,出品：10/22
J1,3300,,,2026/08/16,,オークレボ,
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
        for name, text in (("ebay.csv", EBAY), ("types.csv", TYPES), ("junk.csv", JUNK)):
            (self.tmp / name).write_text(text, encoding="utf-8")
        (self.tmp / "reports").mkdir()
        (self.tmp / "reports" / "listings_20261114.csv").write_text(LISTINGS, encoding="utf-8-sig")
        self.cfg = appraise.load_config(None)

    def data(self, targets=None):
        ledger, cols = bd.read_ledger(self.tmp / "ledger.csv")
        listings, ld = bd.latest_listings(self.tmp / "reports")
        return bd.build(ledger, cols, bd.read_costs(self.tmp / "costs.csv"), listings, ld, targets or {},
                        self.cfg, 150.0, date(2026, 11, 14), bd.read_kanri(self.tmp / "kanri.csv"),
                        bd.read_ebay_sales(self.tmp / "ebay.csv"), bd.read_item_types(self.tmp / "types.csv"),
                        bd.read_junk(self.tmp / "junk.csv"))

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

    def test_actual_net(self):
        e = bd.read_ebay_sales(self.tmp / "ebay.csv")
        self.assertAlmostEqual(e["per"]["A001"]["net"], 150.0)  # 注文 155 − 広告 5（Hold は数えない）
        self.assertAlmostEqual(e["per"]["A002"]["net"], 0.0)    # 返金で相殺
        self.assertEqual(e["unassigned"], {"Adjustment": -2.0})
        self.assertEqual(e["first"], date(2026, 10, 3))
        d = self.data()
        self.assertEqual(d["kpi"]["fee_actual"], 2)
        self.assertEqual(d["actual"]["n"], 2)
        self.assertEqual(d["refunds"], [{"month": "2026-10", "count": 1, "jpy": 15000}])
        # 実額の利益 = 手取り150ドル×150円×(1−円転ロス) − 送料3200 − 発送代行500 − 仕入8000
        a1 = bd.profit_parts(180, 8000, self.cfg, 150.0, {"ship": 3200, "duty": 0, "aucrevo": 500}, 150.0)
        loss = self.cfg["fx"]["conversion_loss_pct"] / 100
        self.assertAlmostEqual(a1["profit"], 150 * 150 * (1 - loss) - 3200 - 500 - 8000, places=6)
        self.assertAlmostEqual(sum(a1[k] for k in ("cost", "fees", "ship", "duty", "aucrevo", "loss", "profit")), a1["sale"], places=6)

    def test_kind_from_sheet_and_junk(self):
        d = self.data()
        kinds = {k["label"]: k["count"] for k in d["kind"]}
        self.assertEqual(kinds["レンズ"], 3)  # A001（シート）＋ A002・A003（要確認 → 品目名の mm で レンズ）
        self.assertEqual(kinds["カメラ（ボディ）"], 1)
        self.assertEqual(d["kind_from_sheet"], 2)
        self.assertEqual((d["junk"]["sold"], d["junk"]["diff"], d["junk"]["stock"]), (1, -3134, 1))

    def test_not_in_ledger_and_ship_check(self):
        (self.tmp / "e2.csv").write_text(EBAY + "Z9,1-Nov-26,Order,O-9,90.00,USD,1,100,0,--,--,--,100,USD,--\n",
                                         encoding="utf-8")
        ledger, cols = bd.read_ledger(self.tmp / "ledger.csv")
        d = bd.build(ledger, cols, {}, [], "", {}, self.cfg, 150.0, date(2026, 11, 14),
                     ebay=bd.read_ebay_sales(self.tmp / "e2.csv"), monthly_costs={"2026-10": 20000.0})
        self.assertEqual(d["ebay_not_in_ledger"], [{"label": "Z9", "usd": 100.0, "in_ledger": False}])
        oct_ = d["ship_check"][0]
        self.assertEqual((oct_["month"], oct_["est"], oct_["actual"]), ("2026-10", 9000, 20000))  # $30×2件×150円
        self.assertEqual(oct_["adjusted"], oct_["profit"] - 11000)
        (self.tmp / "mc.csv").write_text('月,CPaSS,関税,オークレボ\n2026/10,"166,585","95,162","39,930"\n', encoding="utf-8")
        self.assertEqual(bd.read_monthly_costs(self.tmp / "mc.csv"), {"2026-10": 301677.0})

    def test_api_orders_and_finances(self):
        orders = [
            {"date": "2026-10-03", "order_id": "O1", "title": "Canon FL 50mm f/1.4", "sku": "A001", "currency": "USD",
             "item_price": "170", "shipping": "10", "country": "US", "payment_status": "PAID", "cancel_state": "NONE_REQUESTED"},
            {"date": "2026-10-20", "order_id": "O2", "title": "Nikon Zoom", "sku": "A002", "currency": "USD",
             "item_price": "120", "shipping": "0", "country": "GB", "payment_status": "FULLY_REFUNDED", "cancel_state": ""},
            {"date": "2026-11-09", "order_id": "O3", "title": "Leica M3", "sku": "S31", "currency": "USD",
             "item_price": "650", "shipping": "30", "country": "DE", "payment_status": "PAID", "cancel_state": ""},
        ]
        fin_rows = [
            {"date": "2026-10-03", "type": "SALE", "booking": "CREDIT", "order_id": "O1", "amount": "155",
             "amount_currency": "USD", "total_fee": "25", "fee_type": "", "references": "", "status": "PAYOUT"},
            {"date": "2026-10-04", "type": "NON_SALE_CHARGE", "booking": "DEBIT", "order_id": "", "amount": "5",
             "amount_currency": "USD", "total_fee": "", "fee_type": "AD_FEE", "references": "ORDER_ID:O1", "status": ""},
            {"date": "2026-10-25", "type": "REFUND", "booking": "DEBIT", "order_id": "O2", "amount": "100",
             "amount_currency": "USD", "total_fee": "", "fee_type": "", "references": "", "status": ""},
            {"date": "2026-11-01", "type": "NON_SALE_CHARGE", "booking": "DEBIT", "order_id": "", "amount": "21.95",
             "amount_currency": "USD", "total_fee": "", "fee_type": "SUBSCRIPTION_FEE", "references": "", "status": ""},
            {"date": "2026-11-02", "type": "PAYOUT", "booking": "DEBIT", "order_id": "", "amount": "500",
             "amount_currency": "USD", "total_fee": "", "fee_type": "", "references": "", "status": ""},
        ]
        fin = bd.read_api_finances(fin_rows)
        self.assertAlmostEqual(fin["per"]["O1"]["net"], 150.0)   # 入金155 − 広告5
        self.assertAlmostEqual(fin["per"]["O1"]["promo"], -5.0)
        self.assertEqual(fin["unassigned"], {"NON_SALE_CHARGE SUBSCRIPTION_FEE": -21.95})  # 入金（PAYOUT）は数えない
        ledger, cols = bd.read_ledger(self.tmp / "ledger.csv")
        kanri = bd.read_kanri(self.tmp / "kanri.csv")
        kanri["S31"] = {"cost": 71470.0, "buy": date(2026, 10, 26), "supplier": "acc9", "cond": "MINT",
                        "sold": None, "optical": False, "raw": "MINT", "title": "Leica M3"}
        api = {**bd.sales_from_api(orders, fin, ledger, kanri, 150.0), "date": "2026/11/14", "fin": fin}
        self.assertEqual([c["why"] for c in api["cancelled"]], ["返金"])  # 全額返金の注文は販売に数えない
        d = bd.build(ledger, cols, {}, [], "", {}, self.cfg, 150.0, date(2026, 11, 14), kanri=kanri, api=api)
        self.assertEqual(d["source"], "api")
        self.assertEqual(d["kpi"]["count"], 2)
        self.assertEqual(d["kpi"]["fee_actual"], 1)              # O1 だけ取引明細あり
        self.assertEqual({c["label"] for c in d["country"]}, {"アメリカ", "ドイツ"})
        # S31 は古物台帳に無いが、管理シートの仕入額で利益を出す。台帳の記入漏れとして一覧に出る
        self.assertEqual(d["ebay_not_in_ledger"], [{"label": "S31", "usd": 680.0, "in_ledger": False}])
        self.assertEqual(d["kpi"]["no_cost"], 0)
        # 台帳で販売済みでも eBay で売れていない（全額返金）A002 は在庫に数えない（台帳の販売日があるため）
        self.assertEqual(d["kpi"]["stock"], 1)

    def test_ebay_date(self):
        self.assertEqual(bd.parse_date("17-Aug-26"), date(2026, 8, 17))

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
