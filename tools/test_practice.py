"""自動入札の練習の3品（2026-11-26 オーナー「鑑定画面のモーダルの一番上に、３つ表示して」）."""
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import build_extension as bx


class PracticeItems(unittest.TestCase):
    def _write(self, d, rows):
        p = Path(d) / "practice.csv"
        cols = ["日付", "ヤフオクURL", "商品名", "出品者の良い評価の割合", "サトシの推定ランク", "推定の根拠（説明文の言葉）",
                "動作の書き方", "サトシの判断", "オーナーの答え", "オーナーのメモ", "当たり", "あとで届いた実物のランク"]
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, restval=""); w.writeheader()
            for r in rows:
                w.writerow(r)
        return p

    def test_latest_day_only_and_https(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, [
                {"日付": "2026-01-01", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/x1000000001", "商品名": "古い日"},
                {"日付": "2026-01-02", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/x1000000002", "商品名": "カメラA", "サトシの推定ランク": "Near MINT", "サトシの判断": "入札する"},
                {"日付": "2026-01-02", "ヤフオクURL": "javascript:alert(1)", "商品名": "不正なリンク"},
                {"日付": "2026-01-02", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/x1000000003", "商品名": "レンズB", "オーナーの答え": "入札しない"},
            ])
            got = bx.practice_items(p)
            self.assertEqual([g["title"] for g in got], ["カメラA", "レンズB"])
            self.assertEqual(got[0]["rank"], "Near MINT")
            self.assertEqual(got[1]["ans"], "入札しない")

    def test_missing_file(self):
        self.assertEqual(bx.practice_items(Path("/nonexistent/practice.csv")), [])

    def test_extension_shows_box_at_top(self):
        """鑑定画面の本文の一番上（為替の行より前）に枠を出す。商品名は文字として出す."""
        t = (Path(__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        self.assertIn('<div class="bd">${practiceBox()}<div class="s">為替 1USD=', t)
        self.assertIn("function practiceBox()", t)
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import subprocess
        start, end = t.index("  function practiceBox() {"), t.index("  function render() {")
        fn = t[start:end]
        js = ("const esc=(s)=>String(s).replace(/[&<>\"]/g,(c)=>({\"&\":\"&amp;\",\"<\":\"&lt;\",\">\":\"&gt;\",'\"':\"&quot;\"}[c]));"
              "const DATA={practice:" + json.dumps([{"d": "2026-01-02", "url": "https://auctions.yahoo.co.jp/jp/auction/x1", "title": "<b>カメラ</b>", "rate": "99.7%", "rank": "Near MINT", "judge": "入札する", "ans": ""}], ensure_ascii=False) + "};"
              + fn + "console.log(practiceBox());")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
        self.assertIn("今日の課題", out)
        self.assertIn("&lt;b&gt;カメラ&lt;/b&gt;", out)
        self.assertIn("未回答", out)


if __name__ == "__main__":
    unittest.main()


class Skipped(unittest.TestCase):
    """確認済みにして入札しなかった候補（2026-11-26 オーナー「確認済にチェックをいれて入札しなかったものには、入札しなかった意図があります」）."""

    def test_checked_and_not_bid(self):
        import yahoo_results as yr
        seen = [
            {"auction_id": "x1000000001", "title": "カメラA ジャンク", "mark": "◎", "max_bid": "10000", "seller": "カメラ店A / s1", "checked_at": "2026-01-02T10:00:00Z", "end": ""},
            {"auction_id": "x1000000002", "title": "レンズB", "mark": "○", "max_bid": "8000", "seller": "", "checked_at": "2026-01-02T11:00:00Z", "end": ""},
            {"auction_id": "x1000000003", "title": "見ていない物", "mark": "◎", "max_bid": "9000", "seller": "", "checked_at": "", "end": ""},
        ]
        bids = [{"auction_id": "x1000000002"}]
        out = yr.skipped_csv(seen, bids, [{"auction_id": "x1000000001", "final_price": "12000"}])
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)   # 見出し＋見送り1件（入札した物・確認していない物は出さない）
        self.assertIn("カメラA ジャンク", lines[1]); self.assertIn("カメラ店A / s1", lines[1]); self.assertIn("12000", lines[1])

    def test_aucfan_snipes_count_as_bid(self):
        """［入札した］を押していなくても、オークファンに予約がある物は見送りにしない（2026-11-26 オーナー「正確な入札金額は、オークファンの履歴で」）."""
        import tempfile
        from unittest import mock
        import yahoo_results as yr
        import ledger_sync
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d)
            (dl / "aurora_seen.csv").write_text(",".join(yr.SKIP_FIELDS) + "\n" +
                "x1000000001,カメラA,,◎,10000,,,,2026-01-02T10:00:00Z\nx1000000002,レンズB,,○,8000,,,,2026-01-02T11:00:00Z\n", encoding="utf-8")
            (dl / "aurora_snipes.csv").write_text("auction_id,title,max_bid,current,seen_at\nx1000000002,レンズB,7000,,\n", encoding="utf-8")
            sent = []
            with mock.patch.object(ledger_sync, "post_sheet", lambda sheet, body, post=None: sent.append(body) or True):
                n = yr.upload_skipped([], downloads=dl, log=dl / "none.csv", phone_seen=dl / "none2.csv")
            self.assertEqual(n, 1)
            self.assertIn("カメラA", sent[0]); self.assertNotIn("レンズB", sent[0])

    def test_phone_seen(self):
        """スマホの入札タブの［確認済み］も見送りに使う（2026-11-26 オーナー「スマホアプリの入札タブでも、確認済みボタンほしいね」）."""
        import tempfile
        from unittest import mock
        import ledger_sync
        import yahoo_results as yr
        sheet = ("日時,種類,商品名,値札（税込・円）,判定,上限（税込・円）,想定$（送料別）,店,メモ,版\n"
                 "2026-01-02 10:00,確認済み,カメラA,9000,◎,10000,100,ヤフオク,ID x1000000001／終了 2026-01-03 21:00,v\n"
                 "2026-01-02 10:01,確認済み,レンズB,9000,○,8000,100,ヤフオク,ID x1000000002／終了 ,v\n"
                 "2026-01-02 10:02,確認済み取消,レンズB,,,,,ヤフオク,ID x1000000002／終了 ,v\n"
                 "2026-01-02 10:03,入札予約,カメラC,,,,,ヤフオク,ID x1000000003／終了 ,v\n")
        rows = ledger_sync.phone_seen_rows(sheet)
        self.assertEqual([r["auction_id"] for r in rows], ["x1000000001"])   # 取り消した物・予約は入らない
        self.assertEqual((rows[0]["mark"], rows[0]["max_bid"], rows[0]["checked_at"]), ("◎", "10000", "2026-01-02 10:00"))
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d)
            ps = dl / "phone_seen.csv"
            with mock.patch.object(ledger_sync, "fetch", lambda u, k, sh=None: sheet), mock.patch.object(ledger_sync, "DATA", dl):
                self.assertEqual(ledger_sync.sync_phone_seen("u", "k"), 1)
            sent = []
            with mock.patch.object(ledger_sync, "post_sheet", lambda sh, b, post=None: sent.append(b) or True):
                n = yr.upload_skipped([], downloads=dl, log=dl / "none.csv", phone_seen=ps)   # パソコンの記録が無くても動く
            self.assertEqual(n, 1)
            self.assertIn("カメラA", sent[0])

    def test_store_has_seen_button(self):
        t = (Path(__file__).resolve().parents[1] / "store" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-seen="', t)
        self.assertIn('"確認済み")', t)

    def test_extension_exports_checked_at(self):
        t = (Path(__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        self.assertIn('"seller", "checked_at"]', t)
        self.assertIn("w.checked_at = box.checked", t)


class YahooListingTemplate(unittest.TestCase):
    """ヤフオク流しの説明文の型（2026-11-26 オーナー「商品名の箇所は1つでいいからね。2箇所にしていたせいで、誤表示して出品しています」）."""

    def test_product_name_once(self):
        t = (Path(__file__).parent / "yahoo_listing_template.html").read_text(encoding="utf-8")
        body = t.split("-->", 1)[1]   # 先頭の説明のコメントは除く
        self.assertEqual(body.count("{{商品名"), 1)
        self.assertNotIn("<script", body.lower())
