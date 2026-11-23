"""FedEx の輸出許可通知書の依頼用 Excel を自動で埋める（export_permit.py）."""
import tempfile
import unittest
from pathlib import Path

import export_permit as ep

try:
    import openpyxl  # noqa: F401  Mac では pip3 install openpyxl（無ければこのテストは飛ばす）
    HAS_XL = True
except ImportError:
    HAS_XL = False


@unittest.skipUnless(HAS_XL, "openpyxl がありません（pip3 install openpyxl）")
class FillTemplate(unittest.TestCase):
    def _fedex(self, d, rows=200):
        """2026-11-23 に受け取った FedEx の「輸出許可通知書-リクエストシート」と同じ形."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["B5"] = "お申込み頂く際の注意事項"
        ws["B6"] = "◆ 運送状番号は12桁の数字のみとなります。（ハイフン及びスペース無し）"
        ws["B12"] = "フェデックスアカウント番号"
        ws["C13"] = "※出荷時にご使用されたフェデックスアカウント番号(9桁の数字)を入力ください。"
        ws["B15"] = "AWB（運送状番号）"
        ws["A16"], ws["B16"] = "サンプル", 123456789012
        for i in range(1, rows + 1):
            ws.cell(16 + i, 1).value = i
        p = Path(d) / "fedex.xlsx"
        wb.save(p)
        return p

    def test_fedex_format(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.xlsx"
            n, skipped = ep.fill(self._fedex(d), [{"track": "877-270329193", "date": ""}, {"track": "876 802794910", "date": ""},
                                                  {"track": "877270329193", "date": ""}, {"track": "EX0001234", "date": ""}], out, "123456789")
            self.assertEqual(n, 2)
            self.assertEqual(skipped, ["EX0001234（12桁の数字でない）"])
            ws = openpyxl.load_workbook(out).active
            self.assertEqual(ws["B13"].value, 123456789)          # アカウント番号は黄色の欄（見出しの下）
            self.assertEqual(ws["B16"].value, 123456789012)       # 見本は消さない
            self.assertEqual(ws["B17"].value, 877270329193)       # 1番の行から、ハイフン・空白を外した数字で
            self.assertEqual(ws["B18"].value, 876802794910)
            self.assertIsNone(ws["B19"].value)

    def test_fedex_split_when_full(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.xlsx"
            ships = [{"track": f"8760000000{i:02d}", "date": ""} for i in range(5)]
            n, _ = ep.fill(self._fedex(d, rows=3), ships, out)
            self.assertEqual(n, 5)
            self.assertEqual([openpyxl.load_workbook(out).active.cell(r, 2).value for r in (17, 18, 19, 20)], [876000000000, 876000000001, 876000000002, None])
            self.assertEqual(openpyxl.load_workbook(Path(d) / "o_2.xlsx").active["B17"].value, 876000000003)

    def test_have_permits(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("877270329193_RELS_20260916.pdf", "876083438637_RELS_20260825.pdf", "5821307612.pdf", "memo.txt"):
                (Path(d) / name).write_text("x")
            self.assertEqual(ep.have_permits(Path(d)), {"877270329193", "876083438637", "5821307612"})   # FedEx・DHL の名前の形

    def test_collect_from_downloads(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "dl", Path(d) / "have"
            src.mkdir()
            for name in ("877270329193_RELS_20260916.pdf", "5821307612.pdf", "請求書.pdf"):
                (src / name).write_text("x")
            self.assertEqual(ep.collect_from_downloads(src, dst), 2)
            self.assertEqual(ep.collect_from_downloads(src, dst), 0)   # 2回目は写さない
            self.assertTrue((src / "5821307612.pdf").exists())          # 元は消さない

    def test_no_header_stops(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            wb = openpyxl.Workbook()
            wb.active.append(["番号", "金額"])
            p = Path(d) / "t.xlsx"
            wb.save(p)
            with self.assertRaises(SystemExit) as e:
                ep.fill(p, [{"track": "876000000000", "date": ""}], Path(d) / "o.xlsx")
            self.assertIn("見出し", str(e.exception))

    def test_list_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.txt"
            p.write_text("追跡番号\n8760 0000 0004\n876000000005,メモ\n\n", encoding="utf-8")
            self.assertEqual([s["track"] for s in ep.from_list(p)], ["876000000004", "876000000005"])


if __name__ == "__main__":
    unittest.main()
