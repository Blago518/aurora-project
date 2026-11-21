"""店舗アプリの［相場に登録］→「共有_店舗」→ 相場メモ（2026-11-21）. 同じ機種はチャットの相場メモと日付の新しい方."""
import unittest

import ledger_sync as ls

STORE = """日時,種類,商品名,値札（税込・円）,判定,上限（税込・円）,想定$（送料別）,店,メモ,アプリの版
2026-11-21 10:27,相場登録,Sony α7S,115280,,,350,,Near MINT,2026.11.21.15
2026-11-21 10:30,調べた,Nikon F3,30000,×,20000,300,,,2026.11.21.15
2026-11-10 10:00,相場登録,Canon AE-1,5000,,,120,,MINT,2026.11.21.15
"""
MEMO = """機種,相場$（送料別）,状態,日付
Canon AE-1,150,MINT,2026-11-15
"""


class StoreMemo(unittest.TestCase):
    def test_rows(self):
        got = ls.store_memo_rows(STORE, 40.0, ls.sheet_memo_dates(MEMO))
        self.assertEqual(got, {"Sony α7S": (390.0, "相場メモ(Near MINT)")})   # 調べた行は使わない／チャットの方が新しい AE-1 は使わない

    def test_newer_store_wins(self):
        got = ls.store_memo_rows(STORE, 40.0, {"Canon AE-1": "2026-11-01"})
        self.assertEqual(got["Canon AE-1"], (160.0, "相場メモ(MINT)"))


if __name__ == "__main__":
    unittest.main()
