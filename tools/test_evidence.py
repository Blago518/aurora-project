"""ヤフオクの仕入れ証跡（取引ナビを開いたら PNG を保存。extension_evidence.js）と、税理のフォルダへの移し（update_all.move_evidence）."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 2026-11-23 オーナーが送った取引ナビの画面写真4枚の文字（店の取引ナビ・個人の取引ナビ）
PAGES = [
    ("▽ CANON LENS EF 50mm 1:1.8 II 単焦点レンズ\\n個数：1　商品金額（税込）：6,000円\\n終了日時：9月1日 23時07分\\nオークションID：m1000000001\\n出品者：カメラ店A",
     "20260901_カメラ店A_6000_m1000000001"),
    ("【1円】 コンタックス CONTAX Tessar T* 45mm F2.8 AEJ\\n個数：1　商品金額（税込）：28,000円\\n終了日時：9月4日 22時48分\\nオークションID：v1000000002\\n出品者：カメラ店B ヤフオク!店",
     "20260904_カメラ店B ヤフオク!店_28000_v1000000002"),
    ("Canon EOS Kiss N レンズキット 完全動作品 キヤノン #0410\\n数量：1　落札価格：10,500円\\n終了日時：9月6日 21時03分\\nオークションID：d1000000003\\n出品者：カメラ店C 対応♪（1104）",
     "20260906_カメラ店C 対応♪_10500_d1000000003"),
    ("RICOH AUTOHALF SE\\n個数：1　商品金額（税込）：1,200円\\n終了日時：9月16日 20時38分\\nオークションID：w1000000004\\n出品者：カメラ店D ヤフオク!ショップ",
     "20260916_カメラ店D ヤフオク!ショップ_1200_w1000000004"),
]


class EvidenceName(unittest.TestCase):
    def test_names_like_the_owner_files(self):
        """名前は今までのショートカットと同じ形（終了日_出品者_金額_オークションID）."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence.js").read_text(encoding="utf-8")
        body = src[src.index("// @evidence-start"):src.index("// @evidence-end")]
        js = body + "\nconst now = new Date(2026, 10, 23);\nconsole.log(JSON.stringify(%s.map((t) => (evidenceInfo(t.replace(/\\\\n/g, '\\n'), now) || {}).name)));" % json.dumps([p for p, _ in PAGES], ensure_ascii=False)
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [n for _, n in PAGES])
        # 年をまたいだ「12月30日」を1月に開いたら去年
        js2 = body + "\nconsole.log(evidenceInfo('商品金額（税込）：1,000円\\n終了日時：12月30日 20時00分\\nオークションID：x1234567890\\n出品者：A', new Date(2027, 0, 5)).ymd);"
        self.assertEqual(subprocess.run(["node", "-e", js2], capture_output=True, text=True, check=True).stdout.strip(), "20261230")

    def test_no_auction_id_no_save(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence.js").read_text(encoding="utf-8")
        body = src[src.index("// @evidence-start"):src.index("// @evidence-end")]
        out = subprocess.run(["node", "-e", body + "\nconsole.log(JSON.stringify(evidenceInfo('取引ナビ 使い方ガイド')));"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "null")


class MoveEvidence(unittest.TestCase):
    def test_moves_into_tax_folder(self):
        import update_all as ua
        with tempfile.TemporaryDirectory() as d:
            home, dl = Path(d), Path(d) / "Downloads"
            (dl / "ヤフオク仕入証跡").mkdir(parents=True)
            dest = home / "Desktop" / "税理" / "8. 仕入" / "ヤフオク"
            dest.mkdir(parents=True)
            (dl / "ヤフオク仕入証跡" / "20260901_A_6000_m1000000001.png").write_bytes(b"png")
            (dest / "20260823_B_8800_k1241213312.png").write_bytes(b"old")
            self.assertEqual(ua.move_evidence(dl, home=home), 1)
            self.assertTrue((dest / "20260901_A_6000_m1000000001.png").exists())
            self.assertFalse((dl / "ヤフオク仕入証跡" / "20260901_A_6000_m1000000001.png").exists())

    def test_no_tax_folder_keeps_files(self):
        import update_all as ua
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / "Downloads" / "ヤフオク仕入証跡"
            dl.mkdir(parents=True)
            (dl / "x.png").write_bytes(b"png")
            self.assertEqual(ua.move_evidence(dl.parent, home=Path(d)), 0)
            self.assertTrue((dl / "x.png").exists())

    def test_moves_only_when_one_place_fits(self):
        """税理のフォルダの中に合う場所がちょうど1つのときだけ移す。新しいフォルダは作らない（2026-11-23 オーキド: 中を読む前に作らない・迷ったら動かさない）."""
        import tax_folder as tf
        with tempfile.TemporaryDirectory() as d:
            home, dl = Path(d), Path(d) / "Downloads"
            (dl / "ヤフオク仕入証跡").mkdir(parents=True)
            png = dl / "ヤフオク仕入証跡" / "20260901_A_6000_m1000000001.png"
            png.write_bytes(b"png")
            root = home / "Desktop" / "税理士"
            (root / "1. 売上").mkdir(parents=True)
            r = tf.move_evidence(dl, home)                                   # 合うフォルダが無い
            self.assertEqual((r["dest"], r["moved"], r["left"]), (None, 0, 1))
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["1. 売上"])   # 何も作っていない
            (root / "2025" / "ヤフオク").mkdir(parents=True)
            (root / "2026" / "ヤフオク").mkdir(parents=True)
            r = tf.move_evidence(dl, home)                                   # 2つある → 動かさない
            self.assertEqual((r["dest"], r["moved"]), (None, 0))
            self.assertIn("2個", r["why"])
            self.assertTrue(png.exists())
            with mock.patch.object(tf, "EVIDENCE_DEST", "2026/ヤフオク"):     # 行き先を決めたら、そこへ
                r = tf.move_evidence(dl, home)
            self.assertEqual((r["moved"], r["total"], r["left"]), (1, 1, 0))
            self.assertTrue((root / "2026" / "ヤフオク" / png.name).exists())
            (home / "Desktop" / "税理（旧）").mkdir()                          # 税理のフォルダが2つ → 動かさない
            self.assertIsNone(tf.tax_root(home))

    def test_run_reports_places_and_folder_names(self):
        """振り分けの結果（行き先・件数・フォルダ名だけの並び）を「Mac_税理フォルダ」へ。輸出許可書は移した先を数える."""
        import export_permit as ep
        import tax_folder as tf
        with tempfile.TemporaryDirectory() as d:
            home, dl = Path(d), Path(d) / "Downloads"
            (dl / "ヤフオク仕入証跡").mkdir(parents=True)
            (dl / "ヤフオク仕入証跡" / "20260901_A_6000_m1000000001.png").write_bytes(b"png")
            (dl / "5800000001.pdf").write_bytes(b"pdf")
            (dl / "870000000001_RELS_20260916.pdf").write_bytes(b"pdf")
            root = home / "Desktop" / "税理"
            (root / "8. 仕入" / "ヤフオク").mkdir(parents=True)
            (root / "9. 輸出許可書").mkdir()
            (root / "8. 仕入" / "領収書.pdf").write_bytes(b"x")
            sent = []
            with mock.patch.object(ep, "HAVE_DIR", home / "old"), \
                    mock.patch("ledger_sync.post_sheet", lambda sheet, body, post=None: sent.append((sheet, body)) or True):
                out = tf.run(dl, home)
                self.assertEqual(ep.have_permits(root / "9. 輸出許可書"), {"5800000001", "870000000001"})
            self.assertEqual(out["evidence"]["moved"], 1)
            self.assertTrue((root / "9. 輸出許可書" / "DHL" / "5800000001.pdf").exists())
            self.assertTrue((root / "9. 輸出許可書" / "FedEx" / "870000000001_RELS_20260916.pdf").exists())
            self.assertEqual(sent[0][0], "Mac_税理フォルダ")
            self.assertIn("8. 仕入", sent[0][1])
            self.assertNotIn("領収書", sent[0][1])        # ファイル名は送らない


if __name__ == "__main__":
    unittest.main()
