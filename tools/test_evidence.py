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

    def test_bundle_page(self):
        """まとめて取引の画面（2026-11-26 オーナーの写真の文字。名前・金額は作り物）→ 1枚に中の商品のIDを全部付ける."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence.js").read_text(encoding="utf-8")
        body = src[src.index("// @evidence-start"):src.index("// @evidence-end")]
        text = "まとめて取引\nまとめて取引をする商品\n2件 (数量：2)\n合計金額：2,350円\nNikon NIKKOR-Q Auto 135mm F3.5\n数量：1 落札価格：250円\n終了日時：8月16日 21時02分\nCanon Macro FD 50mm F3.5\n数量：1 落札価格：2,100円\n終了日時：8月16日 21時27分"
        hrefs = ["https://auctions.yahoo.co.jp/jp/auction/p1000000001", "https://auctions.yahoo.co.jp/jp/auction/x1000000002"]
        url = "https://contact.auctions.yahoo.co.jp/trade/bundle?aid=p1000000001&seller_auc_user_id=S&buyer_auc_user_id=B"
        js = body + "\nconsole.log(JSON.stringify([evidenceInfo(%s), bundleInfo(%s, %s, %s, new Date(2026, 10, 26))]));" % (json.dumps(text), json.dumps(text), json.dumps(url), json.dumps(hrefs))
        ev, b = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertIsNone(ev)
        self.assertEqual(b["name"], "20260816_まとめて取引_2350_p1000000001_x1000000002")
        self.assertEqual(b["ids"], ["p1000000001", "x1000000002"])

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
            self.assertIn("2つある", tf.move_evidence(dl, home)["why"])
            # 理由を分けて書く（2026-11-25「1つに決まらない」だけでは分からなかった）
            empty = home / "e"
            (empty / "Desktop").mkdir(parents=True)
            self.assertIn("フォルダが無い", tf.why_no_root(empty))
            with mock.patch("tax_folder.os.listdir", side_effect=PermissionError):
                self.assertIn("見えない", tf.why_no_root(home))

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


class EvidenceList(unittest.TestCase):
    """ヤフオクのページの取引ナビのリンク → まとめて開く物（extension_evidence_list.js の navLinks）."""

    def test_nav_links(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence_list.js").read_text(encoding="utf-8")
        body = src[src.index("// @evlist-start"):src.index("// @evlist-end")]
        hrefs = ["https://contact.auctions.yahoo.co.jp/buyer/top?aid=m1000000001&syid=sellerA&bid=buyerB",
                 "https://contact.auctions.yahoo.co.jp/buyer/top?aid=m1000000001&syid=sellerA&bid=buyerB#x",
                 "https://auctions.yahoo.co.jp/jp/auction/v1000000002",
                 "https://contact.auctions.yahoo.co.jp/help",
                 "https://contact.auctions.yahoo.co.jp/seller/top?aid=V1000000003&bid=c"]
        out = json.loads(subprocess.run(["node", "-e", body + "\nconsole.log(JSON.stringify(navLinks(%s).map((l) => l.id)));" % json.dumps(hrefs)],
                                        capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["m1000000001", "v1000000003"])

    def test_plan_makes_links_for_finished_rows(self):
        """［評価］だけの行（取引が終わった物）は、同じページの取引ナビのリンクを型に、出品者のIDを入れ替えて作る."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence_list.js").read_text(encoding="utf-8")
        body = src[src.index("// @evlist-start"):src.index("// @evlist-end")]
        navi = "https://contact.auctions.yahoo.co.jp/buyer/top?aid=m1000000001&syid=storeA&bid=buyerB"
        rows = [{"id": "m1000000001", "hrefs": [navi]},
                {"id": "j1000000002", "hrefs": ["https://auctions.yahoo.co.jp/jp/auction/j1000000002",
                                                "https://auctions.yahoo.co.jp/seller/storeC"]},
                {"id": "k1000000003", "hrefs": ["https://feedback.auctions.yahoo.co.jp/rating/edit?aID=k1000000003&targetID=storeD"]},
                {"id": "s1000000004", "hrefs": ["https://auctions.yahoo.co.jp/jp/auction/s1000000004"]}]
        js = body + "\nconsole.log(JSON.stringify(planLinks(%s, [%s])));" % (json.dumps(rows), json.dumps(navi))
        plan = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual([l["id"] for l in plan["links"]], ["m1000000001", "j1000000002", "k1000000003"])
        self.assertEqual(plan["links"][1]["url"], "https://contact.auctions.yahoo.co.jp/buyer/top?aid=j1000000002&syid=storeC&bid=buyerB")
        self.assertIn("syid=storeD", plan["links"][2]["url"])
        self.assertEqual((plan["direct"], plan["made"], plan["noSeller"]), (1, 2, 1))

    def test_real_bundle_links(self):
        """2026-11-26 本物の形: /trade/bundle?aid=&seller_auc_user_id=&buyer_auc_user_id= → /trade/top で開き、型にもする."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence_list.js").read_text(encoding="utf-8")
        body = src[src.index("// @evlist-start"):src.index("// @evlist-end")]
        b = "https://contact.auctions.yahoo.co.jp/trade/bundle?aid=k1000000001&seller_auc_user_id=SELLA&buyer_auc_user_id=BUYB"
        rows = [{"id": "k1000000001", "hrefs": [b]},
                {"id": "j1000000002", "hrefs": ["https://auctions.yahoo.co.jp/jp/show/rating?seller_auc_user_id=SELLC&aid=j1000000002"]}]
        js = body + "\nconsole.log(JSON.stringify(planLinks(%s, [%s])));" % (json.dumps(rows), json.dumps(b))
        plan = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(plan["links"][0]["url"], b.replace("/trade/bundle", "/trade/top"))
        self.assertEqual(plan["links"][1]["url"], "https://contact.auctions.yahoo.co.jp/trade/top?aid=j1000000002&seller_auc_user_id=SELLC&buyer_auc_user_id=BUYB")

    def test_raw_html_and_next_page(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_evidence_list.js").read_text(encoding="utf-8")
        body = src[src.index("// @evlist-start"):src.index("// @evlist-end")]
        html = '<script>{"url":"https:\\/\\/contact.auctions.yahoo.co.jp\\/trade\\/bundle?aid=p1000000005\\u0026seller_auc_user_id=S\\u0026buyer_auc_user_id=B"}</script>'
        anchors = [{"href": "https://auctions.yahoo.co.jp/my/won?b=1", "text": "1", "rel": ""},
                   {"href": "https://auctions.yahoo.co.jp/my/won?b=2", "text": " 次へ ", "rel": ""}]
        js = body + "\nconsole.log(JSON.stringify([naviUrlsIn(%s), nextPageUrl(%s, 'https://auctions.yahoo.co.jp/my/won')]));" % (json.dumps(html), json.dumps(anchors))
        urls, nxt = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(urls, ["https://contact.auctions.yahoo.co.jp/trade/bundle?aid=p1000000005&seller_auc_user_id=S&buyer_auc_user_id=B"])
        self.assertEqual(nxt, "https://auctions.yahoo.co.jp/my/won?b=2")

    def test_built_into_extension(self):
        src = (HERE / "build_extension.py").read_text(encoding="utf-8")
        self.assertIn('"evidence_list.js"', src)
        # 2026-11-26 検索・ストアの画面まで上下していた → 落札分の一覧だけで動く
        self.assertIn('"matches": ["https://auctions.yahoo.co.jp/my/won*"]', src)
        self.assertIn("/my\\/won/.test(location.pathname)", (HERE / "extension_evidence_list.js").read_text(encoding="utf-8"))
        self.assertIn("evidenceBatch", (HERE / "extension_background.js").read_text(encoding="utf-8"))


class MissingEvidence(unittest.TestCase):
    """台帳の落札分のうち証跡が無いID（tax_folder.missing_evidence）. まとめて取引の名前（_ID_ID）も数える."""

    def test_missing(self):
        import tax_folder
        with tempfile.TemporaryDirectory() as d:
            home, dl = Path(d), Path(d) / "Downloads"
            (dl / "ヤフオク仕入証跡").mkdir(parents=True)
            sub = home / "Desktop" / "税理A" / "8. 仕入" / "ヤフオク"
            sub.mkdir(parents=True)
            led = home / "ledger.csv"
            led.write_text("No,URL\n1,https://auctions.yahoo.co.jp/jp/auction/m1000000001\n2,x1000000002\n3,p1000000003\n4,k1000000004\n", encoding="utf-8")
            (dl / "ヤフオク仕入証跡" / "20260901_カメラ店A_6000_m1000000001.png").write_bytes(b"png")
            (sub / "20260816_まとめて取引_2350_x1000000002_p1000000003.png").write_bytes(b"png")
            r = tax_folder.missing_evidence(dl, home, [led])
            self.assertEqual(r["ids"], ["k1000000004", "p1000000003", "x1000000002", "m1000000001"])
            self.assertEqual(r["missing"], ["k1000000004"])


class EvidenceLogReasons(unittest.TestCase):
    def test_reasons_from_extension_log(self):
        import tax_folder
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d)
            (dl / "aurora_evidence_log.csv").write_text("\ufeffid,結果,理由,日時\nk1000000004,保存できず,20秒待っても画面が読めなかった,2026-11-26\ndone,,,2026-11-26\n", encoding="utf-8")
            log = tax_folder.evidence_log(dl)
            self.assertEqual(log, {"k1000000004": ("保存できず", "20秒待っても画面が読めなかった")})


class DuplicateToTrash(unittest.TestCase):
    def test_same_id_in_dest_goes_to_trash(self):
        """2026-11-26: 移し先に同じ名前（オーナーが前に撮った物）があり、24枚がダウンロードに残った → 重複はゴミ箱へ."""
        import tax_folder
        with tempfile.TemporaryDirectory() as d:
            home, dl = Path(d), Path(d) / "Downloads"
            (dl / "ヤフオク仕入証跡").mkdir(parents=True)
            (home / ".Trash").mkdir()
            dest = home / "Desktop" / "税理士提出書類" / "8. 仕入証跡" / "ヤフオク"
            dest.mkdir(parents=True)
            (dest / "20260731_店A_1_p1000000001.png").write_bytes(b"old-shot")
            (dl / "ヤフオク仕入証跡" / "20260731_店A_1_p1000000001.png").write_bytes(b"new-shot!!")
            (dl / "ヤフオク仕入証跡" / "20261120_店B_5000_s1000000002.png").write_bytes(b"png")
            with mock.patch.object(Path, "home", return_value=home):
                r = tax_folder.move_evidence(dl, home)
            self.assertEqual((r["moved"], r["dup"], r["left"]), (1, 1, 0))
            self.assertTrue((home / ".Trash" / "20260731_店A_1_p1000000001.png").exists())
            self.assertEqual((dest / "20260731_店A_1_p1000000001.png").read_bytes(), b"old-shot")
