"""ヤフオクの仕入れ証跡（取引ナビを開いたら PNG を保存。extension_evidence.js）と、税理のフォルダへの移し（update_all.move_evidence）."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
