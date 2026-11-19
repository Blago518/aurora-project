"""実績の売値の割合（表示だけ。2026-11-20 ユーザー決定）のテスト."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import appraise as ba  # noqa: E402

CFG = ba.load_config(Path(__file__).parent / "config.toml")


class Realized(unittest.TestCase):
    def test_ratio_bands(self):
        self.assertEqual(ba.realized_ratio(80, 7, CFG), CFG["realized"]["under_100"])
        self.assertEqual(ba.realized_ratio(150, 7, CFG), CFG["realized"]["under_200"])
        self.assertEqual(ba.realized_ratio(300, 7, CFG), CFG["realized"]["over_200"])
        self.assertEqual(ba.realized_ratio(150, 9, CFG), CFG["realized"]["mint"])

    def test_display_only(self):
        it = ba.Item(url="", auction_id="x", title="Nikon Ai-S 50mm f/1.4", current_price_jpy=5000)
        it.expected_price_usd = 140
        c = ba.calculate(it, CFG, 150)
        ratio, profit = c["realized"]
        self.assertLess(profit, c["profit_now_jpy"])  # 実績どおりなら利益は下がる
        # 上限額・損益分岐は [realized] が無くても同じ（判定には使わない）
        cfg2 = dict(CFG)
        cfg2.pop("realized")
        c2 = ba.calculate(it, cfg2, 150)
        self.assertEqual(c2["max_bid_jpy"], c["max_bid_jpy"])
        self.assertEqual(c2["breakeven_bid_jpy"], c["breakeven_bid_jpy"])
        self.assertEqual(c2["realized"], (None, None))


if __name__ == "__main__":
    unittest.main()
