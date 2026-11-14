"""説明文チェック・安心ストア（フォロー中）・安心枠の基準のテスト. python -m unittest discover -s tools で実行."""
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import appraise as ba
import build_extension as bx

CFG = ba.load_config(None)
TPL = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")


def run_js(data: dict, code: str):
    with tempfile.TemporaryDirectory() as d:
        js = Path(d) / "u.js"
        js.write_text(TPL.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
        out = subprocess.run(["node", "-e", f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};"
                                           f"const m=require({json.dumps(str(js))});{code}"],
                             capture_output=True, text=True, check=True).stdout
    return json.loads(out)


class Description(unittest.TestCase):
    TEXTS = {
        "レンズ内に カビ があります。": ["カビ"],
        "カビ：なし クモリ：なし": [],
        "カビ、クモリはございません。": [],
        "動作未確認のためジャンク扱い": ["動作未確認"],
        "「動作未確認品」は返品対象外です": [],
        "返品について: 微細なゴミやカビ、くもり、サビなどは対象外です。": [],
        "光学：\n写真にてご確認ください": ["光学は写真で確認"],
        "前玉に薄いクモリ、後玉にカビ": ["クモリ", "カビ"],
    }

    def test_python(self):
        for t, words in self.TEXTS.items():
            self.assertEqual([h["word"] for h in ba.desc_hits(t)], words, t)

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(list(self.TEXTS), ensure_ascii=False)}"
                           ".map(t=>m.descHits(t,'').map(h=>h.word))))")
        self.assertEqual(out, [[h["word"] for h in ba.desc_hits(t)] for t in self.TEXTS])

    def test_read_description(self):
        page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
            {"props": {"pageProps": {"initialState": {"item": {"detail": {"item": {
                "descriptionHtml": "外観は綺麗です。<br>レンズにカビがあります。", "conditionName": "やや傷や汚れあり"}}}}}}})
                + "</script>")
        text, cond = ba.read_description(page)
        self.assertIn("カビ", text)
        self.assertEqual(cond, "やや傷や汚れあり")


class TrustedThreshold(unittest.TestCase):
    """安心ストアは 利益2,000円・利益率5% の基準（Python と JS で同じ上限額）."""

    def _py(self, trusted):
        it = ba.Item(url="u", title="x", expected_price_usd=150.0, current_price_jpy=10000, domestic_shipping_jpy=800,
                     trusted=trusted)
        return ba.calculate(it, CFG, 150.0)["max_bid_jpy"]

    def test_python(self):
        self.assertGreater(self._py(True), self._py(False))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, "console.log(JSON.stringify([true,false].map(t=>m.calculate({price:10000,ship:800,tax:false,trusted:t},150,150).maxBid)))")
        self.assertEqual(out, [self._py(True), self._py(False)])


class TrustedImport(unittest.TestCase):
    def test_import(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "trusted_sellers.csv"
            dl.mkdir()
            (dl / "aurora_trusted_sellers.csv").write_text(
                '﻿user_id,name,on\n"8GwLRL","ReRe オークションストア",1\n"abc","外したストア",0\n', encoding="utf-8")
            self.assertTrue(ba.import_extension_trusted(dl, dest))
            self.assertEqual(ba.load_trusted(dest), {"8GwLRL": "ReRe オークションストア"})
            self.assertFalse(ba.import_extension_trusted(dl, dest))  # 変化なし

    def test_listing_has_seller(self):
        page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"x": {"items": [
            {"auctionId": "a1", "title": "Nikon F3", "price": 1000, "seller": {"userId": "8GwLRL", "isStore": True}}]}}}})
                + "</script>")
        items, _ = ba.parse_listing_page(page)
        self.assertEqual(items[0].seller_id, "8GwLRL")


class Shipping(unittest.TestCase):
    """出品ページの送料（検索結果に無いとき）. Python と JS で同じ."""
    ITEMS = [
        {"chargeForShipping": "winner", "shipping": {"methods": [{"shippingFee": 1300}, {"shippingFee": 910}]}},  # 安い方
        {"chargeForShipping": "seller", "shipping": {"methods": []}},                                          # 送料無料
        {"chargeForShipping": "winner", "shipping": {"methods": [{"name": "着払い"}]}},                         # 不明
        {},
    ]

    def page(self, item):
        return ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
            {"props": {"pageProps": {"initialState": {"item": {"detail": {"item": item}}}}}}) + "</script>")

    def test_python(self):
        self.assertEqual([ba.item_shipping(self.page(i)) for i in self.ITEMS], [910, 0, None, None])

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(self.ITEMS)}.map(i=>m.shipFee(i))))")
        self.assertEqual(out, [910, 0, None, None])

    def test_recalc_with_page_shipping(self):
        it = ba.Item(url="u", auction_id="a1", title="Nikon FM2", current_price_jpy=10000, expected_price_usd=260.0)
        it.calc = ba.calculate(it, CFG, 150.0)
        before = it.calc["profit_now_jpy"]  # 送料不明 → 1,000円で計算
        with unittest.mock.patch.object(ba, "http_get", lambda u: self.page(self.ITEMS[0])), \
                unittest.mock.patch.object(ba, "judge", lambda i: ("◎", "")):
            ba.check_descriptions([it], 0, CFG, 150.0)
        self.assertEqual(it.domestic_shipping_jpy, 910)
        self.assertEqual(it.calc["profit_now_jpy"], before + 90)  # 1,000円 → 910円


if __name__ == "__main__":
    unittest.main()
