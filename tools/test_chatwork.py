"""Chatwork の販売報告の取り込みのテスト. python -m unittest discover -s tools で実行."""
import tempfile
import unittest
from pathlib import Path

import chatwork_sync as cs

BODY = """[info][title]販売通知[/title][00104] [Top MINT+++w/Box] Nikon AF-S Teleconverter TC-14E II 1.4x Extender
Converter JP
[販売価格] $169.00 (eBay)
https://www.ebay.com/itm/377577821275
[仕入価格] 11000円
[仕入先] キタムラ
[販売先] United States
[販売期間] 18日
[利益] 10525円(概算)
[利益率] 34%

[00127] [Almost Unused] Contax Carl Zeiss Makro-Planar T* 60mm f/2.8 AEJ Lens C/Y JAPAN
[販売価格] $299.00 (eBay)
https://www.ebay.com/itm/377621400233
[仕入価格] 30,000円
[仕入先] ゴーゴー商会
[利益] -1,200円(概算)
[利益率] -2%[/info]"""


class Parse(unittest.TestCase):
    def test_two_items(self):
        rows = cs.parse_sales(BODY, "2026-11-06")
        self.assertEqual(len(rows), 2)
        a, b = rows
        self.assertEqual(a["title"], "[Top MINT+++w/Box] Nikon AF-S Teleconverter TC-14E II 1.4x Extender Converter JP")
        self.assertNotIn("MINT", a["ebay_query"])  # 照合用は状態表記なし
        self.assertEqual((a["sold_price_usd"], a["purchase_jpy"], a["ebay_url"], a["source"]),
                         ("169", 11000, "https://www.ebay.com/itm/377577821275", "Chatwork販売実績(キタムラ)"))
        self.assertEqual((b["purchase_jpy"], b["source"]), (30000, "Chatwork販売実績(ゴーゴー商会・赤字)"))

    def test_no_item_code(self):
        """管理番号 [00104] の無い報告も1件として読む（2026-11-26。前は黙って飛ばしていた）."""
        body = "[info][title]販売通知[/title]Nikon F3 HP ボディ\n[販売価格] $250.00 (eBay)\nhttps://www.ebay.com/itm/100000000001\n[仕入価格] 20,000円\n[仕入先] カメラ店A[/info]"
        rows = cs.parse_sales(body, "2026-11-26")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["title"], rows[0]["sold_price_usd"], rows[0]["purchase_jpy"]), ("Nikon F3 HP ボディ", "250", 20000))

    def test_no_price_is_skipped(self):
        self.assertEqual(cs.parse_sales("[00200] Nikon F3\n[仕入価格] 5000円", "2026-11-06"), [])

    def test_append_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "chatwork_sales.csv"
            p.write_text("date,title,ebay_query,sold_price_usd,shipping_charged_usd,purchase_jpy,ebay_url,source\n"
                         "2026-11-01,Old,Old,100,0,5000,https://www.ebay.com/itm/377577821275,Chatwork販売実績\n",
                         encoding="utf-8-sig")
            new = cs.append_new(cs.parse_sales(BODY, "2026-11-06"), p)
            self.assertEqual([r["ebay_url"][-12:] for r in new], ["377621400233"])  # 同じ商品番号は足さない
            self.assertEqual(cs.append_new(cs.parse_sales(BODY, "2026-11-06"), p), [])
            self.assertEqual(len(p.read_text(encoding="utf-8-sig").strip().splitlines()), 3)


class UpdateAllClassify(unittest.TestCase):
    """update_all.py: ダウンロードフォルダの古物台帳・頒布会リストを中身で見分ける."""

    def test_classify(self):
        import zipfile
        import update_all as ua
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "古物台帳 - シート1.csv").write_text("管理番号,品目(商品名),仕入金額,販売日,販売金額($)\n1,Nikon F3,10000,2026/11/1,200\n",
                                                  encoding="utf-8")
            (d / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (d / "aurora_price_memo.csv").write_text("title,sold_price_usd,source\n", encoding="utf-8")
            with zipfile.ZipFile(d / "頒布会_2026_12.xlsx", "w") as z:
                z.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="カメラ商品一覧" sheetId="1"/></sheets></workbook>')
            with zipfile.ZipFile(d / "cpass.xlsx", "w") as z:
                z.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="Sheet1" sheetId="1"/></sheets></workbook>')
            got = {f.name: (ua.classify(f).name if ua.classify(f) else None) for f in d.iterdir()}
        self.assertEqual(got, {"古物台帳 - シート1.csv": "ledger.csv", "other.csv": None, "aurora_price_memo.csv": None,
                               "頒布会_2026_12.xlsx": "hanpukai_2026-12.xlsx", "cpass.xlsx": None})


if __name__ == "__main__":
    unittest.main()
