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
            n, skipped = ep.fill(self._fedex(d), [{"track": "870-000000001", "date": ""}, {"track": "870 000000002", "date": ""},
                                                  {"track": "870000000001", "date": ""}, {"track": "EX0001234", "date": ""}], out, "123456789")
            self.assertEqual(n, 2)
            self.assertEqual(skipped, ["EX0001234（12桁の数字でない）"])
            ws = openpyxl.load_workbook(out).active
            self.assertEqual(ws["B13"].value, 123456789)          # アカウント番号は黄色の欄（見出しの下）
            self.assertEqual(ws["B16"].value, 123456789012)       # 見本は消さない
            self.assertEqual(ws["B17"].value, "870000000001")       # 1番の行から、ハイフン・空白を外した数字で
            self.assertEqual(ws["B18"].value, "870000000002")
            self.assertIsNone(ws["B19"].value)

    def test_old_numbers_cleared_and_account_kept(self):
        """前回の依頼で番号とアカウント番号が入ったシートを使っても、古い番号は消し、アカウント番号はそのまま使う（2026-11-23）."""
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            p = self._fedex(d)
            wb = openpyxl.load_workbook(p)
            ws = wb.active
            ws["B13"] = 100000001
            ws["B17"], ws["B18"] = "870000000091", "870000000092"   # 前回の分
            wb.save(p)
            self.assertEqual(ep.template_account(p), "100000001")
            out = Path(d) / "o.xlsx"
            n, _ = ep.fill(p, [{"track": "870000000001", "date": ""}], out, ep.template_account(p))
            ws = openpyxl.load_workbook(out).active
            self.assertEqual((n, ws["B17"].value, ws["B18"].value, ws["B13"].value), (1, "870000000001", None, 100000001))

    def test_fedex_split_when_full(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.xlsx"
            ships = [{"track": f"8760000000{i:02d}", "date": ""} for i in range(5)]
            n, _ = ep.fill(self._fedex(d, rows=3), ships, out)
            self.assertEqual(n, 5)
            self.assertEqual([openpyxl.load_workbook(out).active.cell(r, 2).value for r in (17, 18, 19, 20)], ["876000000000", "876000000001", "876000000002", None])
            self.assertEqual(openpyxl.load_workbook(Path(d) / "o_2.xlsx").active["B17"].value, "876000000003")

    def test_have_permits(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("870000000001_RELS_20260916.pdf", "870000000003_RELS_20260825.pdf", "5800000001.pdf", "memo.txt"):
                (Path(d) / name).write_text("x")
            self.assertEqual(ep.have_permits(Path(d)), {"870000000001", "870000000003", "5800000001"})   # FedEx・DHL の名前の形

    def test_collect_from_downloads(self):
        """ダウンロードの PDF を FedEx／DHL のフォルダに分けて移す（2026-11-23 オーナー「fedex と dhl でフォルダ分けて、自動化でローカルに置いて」）."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            src, dst, old = Path(d) / "dl", Path(d) / "have", Path(d) / "old"
            src.mkdir(), old.mkdir()
            for name in ("870000000001_RELS_20260916.pdf", "5800000001.pdf", "請求書.pdf", "5800000001 (1).pdf"):
                (src / name).write_text("x")
            (old / "5800000002.pdf").write_text("x")          # 分ける前に1つのフォルダへ置いた物
            with mock.patch.object(ep, "HAVE_DIR", old):
                self.assertEqual(ep.collect_from_downloads(src, dst), 3)
                self.assertEqual(sorted(x.name for x in (dst / "FedEx").iterdir()), ["870000000001_RELS_20260916.pdf"])
                self.assertEqual(sorted(x.name for x in (dst / "DHL").iterdir()), ["5800000001.pdf", "5800000002.pdf"])
                self.assertEqual(sorted(x.name for x in src.iterdir()), ["5800000001 (1).pdf", "請求書.pdf"])   # 許可書でない物・写しは触らない
                (src / "5800000001.pdf").write_text("x")       # もう一度ダウンロードされた
                self.assertEqual(ep.collect_from_downloads(src, dst), 0)
                self.assertFalse((src / "5800000001.pdf").exists())                                             # もう移した物は消す
                self.assertEqual(ep.have_permits(dst), {"870000000001", "5800000001", "5800000002"})          # フォルダの中も数える
            self.assertEqual([ep.carrier_of(x) for x in ("870000000001_RELS_20260916.pdf", "5800000001.pdf", "請求書.pdf")], ["FedEx", "DHL", None])

    def test_collect_fedex_zip_and_folder(self):
        """FedEx はメールの ZIP で届く。ZIP のままでも、開いたフォルダの中でも拾う（2026-11-26 オーナー「輸出許可証が届いたけど？」）."""
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "dl", Path(d) / "have"
            (src / "TANAKA-RELS-20260101000000000").mkdir(parents=True)
            (src / "TANAKA-RELS-20260101000000000" / "870000000011_RELS_20261006.pdf").write_text("x")
            (src / "TANAKA-RELS-20260101000000000" / "5800000009.pdf").write_text("x")      # フォルダの中の DHL 形は拾わない
            with zipfile.ZipFile(src / "TANAKA-RELS-20260102000000000.zip", "w") as zf:
                zf.writestr("TANAKA-RELS-20260102000000000/870000000012_RELS_20261007.pdf", "y")
                zf.writestr("TANAKA-RELS-20260102000000000/870000000011_RELS_20261006.pdf", "x")
                zf.writestr("memo.txt", "z")
            self.assertEqual(ep.collect_from_downloads(src, dst), 2)
            self.assertEqual(sorted(x.name for x in (dst / "FedEx").iterdir()), ["870000000011_RELS_20261006.pdf", "870000000012_RELS_20261007.pdf"])
            self.assertFalse((dst / "DHL").exists())
            self.assertTrue((src / "TANAKA-RELS-20260102000000000.zip").exists())                 # ZIP は消さない
            self.assertEqual(ep.collect_from_downloads(src, dst), 0)                               # 2回目は何もしない

    def test_permit_base(self):
        """税理のフォルダの中に「輸出許可」を含むフォルダがちょうど1つあればそこ、決まらなければ data/export_permits."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            self.assertEqual(ep.permit_base(home), ep.HAVE_DIR)
            (home / "Desktop" / "税理士" / "1. 売上").mkdir(parents=True)
            self.assertEqual(ep.permit_base(home), ep.HAVE_DIR)                       # 合うフォルダが無い → 作らない
            one = home / "Desktop" / "税理士" / "5. 輸出許可通知書"
            one.mkdir()
            self.assertEqual(ep.permit_base(home), one)
            (home / "Desktop" / "税理士" / "1. 売上" / "輸出許可（旧）").mkdir()
            self.assertEqual(ep.permit_base(home), ep.HAVE_DIR)                       # 2つある → 当てずっぽうで選ばない

    def test_speedpak_detail(self):
        """SpeedPAK の「輸出取引の詳細」（シート「オーダー」）から運送会社ごとの番号（2026-11-23 の形。番号は作り物）."""
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "オーダー"
            ws.append(["作成時間", "OrangeConnex追跡番号", "ご請求金額（JPY）", "実支払額（JPY）", "割引（JPY）", "還元金額（JPY）", "輸送業者名", "追跡番号"])
            ws.append(["2026-11-23 10:26:20", "EM1000000000001FE06030071G0N", "6,966", "6,966", "0", "0", "FedEx", "380000000001"])
            ws.append(["2026-11-19 08:33:42", "EX1000000000002DL06020002A0N", "4,736", "4,736", "0", "0", "DHL", "8300000001"])
            ws.append(["2026-11-18 08:33:42", "EM1000000000003FE06030071G0N", "4,736", "4,736", "0", "0", "FedEx", "380000000001"])
            wb.create_sheet("SKUの詳細")
            p = Path(d) / "1795399601275.xlsx"
            wb.save(p)
            self.assertEqual(ep.speedpak_files([Path(d)]), [p])
            self.assertEqual(ep.from_speedpak(p, "fedex"), [{"track": "380000000001", "date": "2026-11-23"}])
            self.assertEqual([x["track"] for x in ep.from_speedpak(p, "dhl")], ["8300000001"])

    def test_status_counts_missing(self):
        """ダウンロードにある PDF を数え、足りない番号を出す（2026-11-23 オーナー「ダウンロード先で確認しろ」。番号は作り物）."""
        import openpyxl
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            dl, have = Path(d) / "dl", Path(d) / "have"
            dl.mkdir()
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "オーダー"
            ws.append(["作成時間", "OrangeConnex追跡番号", "輸送業者名", "追跡番号"])
            for day, car, t in (("2026-11-19", "DHL", "8300000001"), ("2026-11-18", "DHL", "8300000002"), ("2026-11-17", "DHL", "8300000003"),
                                ("2026-11-23", "FedEx", "380000000001")):
                ws.append([day + " 08:00:00", "EX1000000000002DL06020002A0N", car, t])
            wb.save(dl / "1000000000001.xlsx")
            for name in ("8300000001.pdf", "8300000002.pdf", "8300000002 (1).pdf"):
                (dl / name).write_text("x")
            sent = []
            with mock.patch.object(ep, "HAVE_DIR", have), mock.patch.object(ep, "STATEMENTS", Path(d) / "none"), \
                    mock.patch("tax_folder.tax_root", lambda home=None: None), \
                    mock.patch("ledger_sync.post_sheet", lambda sheet, body, post=None: sent.append((sheet, body)) or True):
                rows = ep.report_status(dl)
            self.assertEqual(ep.status_lines(rows), ["DHL: 3件中 2件 保存済み・まだ 1件（8300000003）", "FedEx: 1件中 0件 保存済み・まだ 1件（380000000001）"])
            self.assertEqual(sent[0][0], "Mac_輸出許可書")
            self.assertIn("DHL,'8300000003,2026-11-17,まだ", sent[0][1])
            self.assertLess(sent[0][1].index("まだ"), sent[0][1].index("保存済み"))   # まだの物が上

    def test_find_fedex_template(self):
        with tempfile.TemporaryDirectory() as d:
            self._fedex(d)
            self.assertEqual(ep.find_fedex_template([Path(d)]).name, "fedex.xlsx")

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
