"""スマホの入札タブの［予約］→ 共有_店舗 → 入札の記録（2026-11-21）."""
import tempfile
import unittest
from pathlib import Path

import bid_results as br
import ledger_sync as ls

SHEET = """日時,種類,商品名,値札（税込・円）,判定,上限（税込・円）,想定$（送料別）,店,メモ,アプリの版
2026-11-21 22:10,入札予約,Nikon AF-S 80-200mm F2.8D,25000,◎,33000,354.99,ヤフオク,ID 1251500226／終了 2026-11-22T21:30:00+09:00,2026.11.21.27
2026-11-21 22:11,調べた,Nikon F3,30000,×,20000,300,,,2026.11.21.27
2026-11-21 22:12,入札予約,Contax T2,40000,○,52000,600,ヤフオク,ID x123456789／終了 2026-11-22T22:00:00+09:00／仮,2026.11.21.27
"""


class PhoneBids(unittest.TestCase):
    def test_rows(self):
        rows = ls.phone_bid_rows(SHEET)
        self.assertEqual([r["auction_id"] for r in rows], ["1251500226", "x123456789"])
        self.assertEqual(rows[0]["max_bid"], "33000")
        self.assertEqual(rows[1]["tentative"], "1")
        self.assertEqual(rows[0]["end"], "2026-11-22T21:30:00+09:00")

    def test_cancel_removes(self):
        """スマホで［予約済み］を外す → 入札予約取消 の行でその予約を消す."""
        sheet = SHEET + "2026-11-21 22:20,入札予約取消,Contax T2,,,,,ヤフオク,ID x123456789／終了 ,2026.11.21.29\n"
        rows = ls.phone_bid_rows(sheet)
        self.assertEqual([r["auction_id"] for r in rows], ["1251500226"])
        self.assertEqual(rows[0]["pay_max"], "")

    def test_import_kept_by_extension_sync(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            phone, log, dl = d / "phone.csv", d / "log.csv", d / "dl"
            dl.mkdir()
            import csv
            rows = ls.phone_bid_rows(SHEET)
            with open(phone, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
            self.assertEqual(br.import_phone_bids(phone, log), 2)
            self.assertEqual(br.import_phone_bids(phone, log), 0)   # 2回目は足さない
            (dl / "aurora_bids.csv").write_text("auction_id,title,max_bid\nzz1,Other,1000\n", encoding="utf-8")
            br.import_extension_bids(dl, log)   # 拡張機能の記録に無くても、スマホの分は消えない
            ids = sorted(r["auction_id"] for r in br._read(log))
        self.assertEqual(ids, ["1251500226", "x123456789", "zz1"])


if __name__ == "__main__":
    unittest.main()
