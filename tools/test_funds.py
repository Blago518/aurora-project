"""eBay の未出金の残高 → 古物台帳「Mac_資金」の CSV（数字のグラフの「いまの運用資金」が読む。2026-11-21）."""
import csv
import unittest

import ebay_api


class FundsCsv(unittest.TestCase):
    def test_rows(self):
        d = {"totalFunds": {"value": "1234.50", "currency": "USD"}, "availableFunds": {"value": "1000", "currency": "USD"},
             "processingFunds": {"value": "234.5", "currency": "USD"}}
        rows = list(csv.reader(ebay_api.funds_csv(d, "2026-11-21 08:00").splitlines()))
        self.assertEqual(rows[0], ["項目", "金額", "通貨", "取得日時"])
        self.assertEqual(rows[1], ["eBayの残高（まだ出金していない合計）", "1234.50", "USD", "2026-11-21 08:00"])
        self.assertEqual(len(rows), 4)   # 保留中の額は無いので出さない


if __name__ == "__main__":
    unittest.main()
