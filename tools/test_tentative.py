"""仮◎○（eBay 出品中の価格で仮に判定）のテスト. python -m unittest discover -s tools で実行."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import appraise as ba
import tentative as tv

CFG = ba.load_config(None)


def summ(title, price, ship=0, cur="USD"):
    return {"title": title, "price": {"value": str(price), "currency": cur},
            "shippingOptions": [{"shippingCost": {"value": str(ship)}}]}


class Filter(unittest.TestCase):
    def test_rules(self):
        s = tv.filter_totals("Olympus 35mm F2", [
            summ("Olympus OM Zuiko 35mm f/2 lens", 180, 20),
            summ("Olympus Zuiko 35mm F2 MC", 210),
            summ("Olympus Zuiko 35mm F2.8", 90),            # F値が違う
            summ("Olympus 35mm F2 lens cap", 15),            # 付属品
            summ("Olympus Zuiko 35mm 1:2", 230),
            summ("Olympus Zuiko 35mm F2 parts", 40),         # 部品取り
            summ("Olympus Zuiko 35mm F2", 160, cur="GBP"),   # ドル以外
            summ("Olympus Zuiko 35mm F/2 black", 25),        # 中央値の3割未満は外す
        ])
        # 送料別 $20 の出品は商品代 $180 のまま、送料無料は −$30。送料込みで持つので deduction_usd（$40）を足す
        self.assertEqual(s, {"n": 3, "min": 220.0, "med": 220.0})

    def test_suggest(self):
        self.assertEqual(tv.suggest_usd({"n": 6, "min": 139.99, "med": 220}, CFG), 139)  # 最安値
        self.assertEqual(tv.suggest_usd({"n": 6, "min": 200, "med": 220}, CFG), 200)     # 中央値は使わない（2026-11-16）
        self.assertIsNone(tv.suggest_usd({"n": 2, "min": 200, "med": 220}, CFG))         # 件数不足
        self.assertIsNone(tv.suggest_usd({"n": 75, "min": -20.77, "med": 9.99}, CFG))     # 送料無料 −$30 でマイナス（付属品など）は案にしない


class AddTentative(unittest.TestCase):
    def test_flow_and_report(self):
        items = [ba.Item(url="https://auctions.yahoo.co.jp/jp/auction/a1", auction_id="a1",
                         title="オリンパス OM ZUIKO 35mm F2 美品", current_price_jpy=3000,  # 1点の費用（梱包＋オークレボ＋ヤマト）を足しても余裕あり
                          domestic_shipping_jpy=800,
                         end_time="2026-11-16T21:00:00+09:00"),
                 ba.Item(url="u2", auction_id="a2", title="オリンパス レンズキャップ 35mm F2", current_price_jpy=500)]
        for it in items:
            it.flags = ba.detect_flags(it.title)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tv, "CACHE", Path(d) / "c.json"):
            calls = []
            n = tv.add_tentative(items, CFG, 150.0, browse=lambda q: calls.append(q) or {"n": 8, "min": 139.99, "med": 220},
                                 delay=0)
            self.assertEqual(n, 1)
            self.assertEqual(items[0].tentative["mark"], "◎")
            self.assertEqual(items[0].tentative["usd"], 139)
            self.assertFalse(items[1].tentative)  # 付属品は調べない
            tv.add_tentative(items, CFG, 150.0, browse=lambda q: calls.append(q) or {}, delay=0)
            self.assertEqual(len(calls), 1)  # 同じ機種は1日1回（2回目はキャッシュ）
            rows = ba.rows_for_output(items)
            out = Path(d) / "r.html"
            ba.write_html(rows, out, CFG, 150.0, [])
            html = out.read_text(encoding="utf-8")
        self.assertIn("仮◎○（相場は未登録", html)
        self.assertIn("仮◎", html)
        self.assertIn("オリンパス OM ZUIKO 35mm F2", html)

    def test_no_keys(self):
        it = ba.Item(url="u", title="オリンパス OM ZUIKO 35mm F2", current_price_jpy=5000)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tv, "CACHE", Path(d) / "c.json"):
            def boom(q):
                raise SystemExit("eBay API エラー（HTTP 401）")
            self.assertEqual(tv.add_tentative([it], CFG, 150.0, browse=boom, delay=0), 0)  # 止まらずに続ける


if __name__ == "__main__":
    unittest.main()
