"""build_reference_prices.py のテスト（古物台帳に豪ドル・ポンドのまま記入された販売金額をドルに直す）."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_reference_prices as brp  # noqa: E402

LEDGER = """"#","購入日","販売日","品目(商品名)","数量","No.","仕入金額(円)
送料含む","販売金額($)
送料含む","アカウント名"
"1","2026/09/23","2026/11/14","Nikon AF-S 24-70mm f/2.8 E ED VR","1","N2","87,199","1,070.32","x"
"2","2026/10/05","2026/11/13","Canon EOS 80D Body","1","P2","46,420","296.01","x"
"3","2026/07/26","2026/9/1","Canon FL 50mm f/1.4","1","A1","3,400","78.99","x"
"4","2026/08/10","2026/11/12","Nikon New NIKKOR 135mm f/3.5","1","D4","1,440","70.00","x"
"""
REPORT = """Custom label,Transaction creation date,Type,Order number,Net amount,Gross transaction amount,Transaction currency,Exchange rate
N2,14-Nov-26,Order,O1,599.63,"1,070.32",AUD,0.65714
P2,13-Nov-26,Order,O2,328.56,296.01,GBP,1.28368
A1,1-Sep-26,Order,O3,66.94,78.99,USD,--
D4,12-Nov-26,Order,O4,35.41,63.98,AUD,0.66391
--,12-Nov-26,Other fee,O4,-1.00,-1.00,AUD,0.66391
"""


class TestFxFix(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "ledger.csv").write_text(LEDGER, encoding="utf-8")
        (self.tmp / "ebay_report.csv").write_text(REPORT, encoding="utf-8")

    def test_converts_only_matching_foreign_amounts(self):
        fixed = []
        rows = brp.from_ledger(self.tmp / "ledger.csv", brp.load_fx_fix(self.tmp / "ebay_report.csv"), fixed)
        price = {r["title"].split()[0] + r["date"]: r["sold_price_usd"] for r in rows}
        self.assertEqual(price["Nikon2026/11/14"], 703.35)   # 1,070.32 豪ドル × 0.65714
        self.assertEqual(price["Canon2026/11/13"], 379.98)   # 296.01 ポンド × 1.28368
        self.assertEqual(price["Canon2026/9/1"], 78.99)      # ドル建てはそのまま
        self.assertEqual(price["Nikon2026/11/12"], 70.00)    # 台帳の数字が豪ドルの金額と違う（すでに直してある）→ 触らない
        self.assertEqual([f[0] for f in fixed], ["N2", "P2"])

    def test_without_report_unchanged(self):
        rows = brp.from_ledger(self.tmp / "ledger.csv")
        self.assertEqual(rows[0]["sold_price_usd"], 1070.32)
        self.assertEqual(brp.load_fx_fix(self.tmp / "none.csv"), {})


if __name__ == "__main__":
    unittest.main()
