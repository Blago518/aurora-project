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
    def _template(self, d, head=("No.", "運送状番号", "発送日", "備考"), sample=True):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["輸出許可通知書 発行依頼書"])
        ws.append(["お客様アカウント番号", ""])
        ws.append(list(head))
        if sample:
            ws.append([1, "123456789012（例）", "", ""])
        p = Path(d) / "t.xlsx"
        wb.save(p)
        return p

    def test_fills_tracking_and_date(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.xlsx"
            n = ep.fill(self._template(d), [{"track": "794600000001", "date": "2026-11-20"},
                                            {"track": "794600000002", "date": ""},
                                            {"track": "794600000001", "date": "2026-11-20"}], out)
            self.assertEqual(n, 2)   # 同じ番号は1回だけ
            ws = openpyxl.load_workbook(out).active
            self.assertEqual(ws.cell(4, 2).value, "794600000001")   # 見本の行に上書き
            self.assertEqual(ws.cell(4, 3).value, "2026-11-20")
            self.assertEqual(ws.cell(5, 2).value, "794600000002")
            self.assertEqual(ws.cell(1, 1).value, "輸出許可通知書 発行依頼書")   # ほかは変えない

    def test_english_header(self):
        with tempfile.TemporaryDirectory() as d:
            n = ep.fill(self._template(d, head=("Tracking Number", "Ship Date"), sample=False), [{"track": "794600000003", "date": ""}],
                        Path(d) / "o.xlsx")
            self.assertEqual(n, 1)

    def test_no_header_stops(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as e:
                ep.fill(self._template(d, head=("番号", "金額")), [{"track": "1", "date": ""}], Path(d) / "o.xlsx")
            self.assertIn("見出し", str(e.exception))

    def test_list_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.txt"
            p.write_text("追跡番号\n7946 0000 0004\n794600000005,メモ\n\n", encoding="utf-8")
            self.assertEqual([s["track"] for s in ep.from_list(p)], ["794600000004", "794600000005"])


if __name__ == "__main__":
    unittest.main()
