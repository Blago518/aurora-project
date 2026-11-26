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
