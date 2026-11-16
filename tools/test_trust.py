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


class AccessoryOnly(unittest.TestCase):
    """説明書・本・フィルターだけの出品は付属品。本体＋付属（説明書付・フィルター付き）は除かない."""
    CASES = {"コンタックス　NX　使用説明書　キ38": True, "ニコン 取扱説明書": True, "キヤノン AE-1 説明書のみ": True,
             "カメラスタイル21 やっぱりこれですね！ カールツァイス・レンズは": True, "【美品】CONTAX 55mm P-FILTER シルバー": True,
             "Nikon F3 取扱説明書付 美品": False, "CONTAX NX ボディ 説明書付き": False, "Contax 50mm F1.4 説明書あり": False,
             "Contax Planar 50mm F1.4 フィルター付き": False,
             "未使用*保管品【CONTAX】72/86■Planar T*135mmF2/Sonnar T*180mmF2.8用*元箱付き": True,
             "CONTAX Carl Zeiss Sonnar T* 2.8/180mm": False,
             "1円～/TAMRON/SP/500mm/F8/TELE MACRO/55BB/for Nikon/ニコン用/ミラーレンズ": False,
             "SIGMA 70-300mm F4-5.6 キヤノン用": False,
             "G062a【中古美品 元箱のみ】Nikon ニコン NIKKOR Z MC 105mm f2.8 VR S レンズ用元箱 複数在庫あり": True,
             "Nikon FM2 ボディ 元箱付き 美品": False, "Canon AE-1 箱付き": False}

    def test_python(self):
        for t, acc in self.CASES.items():
            self.assertEqual("付属品のみ?" in ba.detect_flags(t), acc, t)

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(list(self.CASES), ensure_ascii=False)}.map(t=>m.detectFlags(t).includes('付属品のみ?'))))")
        self.assertEqual(out, list(self.CASES.values()))


class BrandWordRequired(unittest.TestCase):
    """実績側の英字の語（メーカー・レンズ銘）が1つも無い照合はしない（YASHICA 35mm 2.8 に ALPA 35mm 2.8 が当たっていた）."""
    CASES = [("YASHICA LENS 35mm 1：2.8 A1341823", "ALPA 35mm 2.8", False),
             ("ニコン Ai Nikkor 50mm F1.4", "Nikon Ai 50mm f1.4", True),
             ("Carl Zeiss Sonnar 85mm F2.8 AEG", "Sonnar 85mm 2.8 AEG", True)]

    def test_python(self):
        for q, r, ok in self.CASES:
            got = ba.match_sales(q, [{"title": r, "_tokens": ba._tokens(r), "source": "頒布会"}])
            self.assertEqual(bool(got), ok, (q, r))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        cases = json.dumps([[q, r] for q, r, _ in self.CASES], ensure_ascii=False)
        out = run_js(data, f"console.log(JSON.stringify({cases}.map(([q,r])=>m.matchRefs(q,[{{title:r,tokens:[...m.tokensOf(r)],tier:2}}]).length>0)))")
        self.assertEqual(out, [ok for _, _, ok in self.CASES])


class WrongMatches(unittest.TestCase):
    """2026-11-15 の誤判定: 三脚の部品に 600mm F4 の相場、55mm F3.5 に 18mm f/3.5、5cm f2 に Nikon F2（ボディ）."""
    ACC = ["ウィンバリー Wimberley AP-452 ニコン Nikon AF-S NIKKOR 400mm F2.8G ED VR / 600mm F4G ED VR II / ア",
           "カーク KIRK LP-45 ニコン Nikon NIKKOR AF-S 70-200mm F2.8G ED VR I / II アルカスイス互換 レンズフット / Really"]
    # （出品タイトル, 鑑定表の行（機種名, パターン）, 当たるべきか）
    KANTEI = [("★良品★ ニコン NIKON MICRO NIKKOR P AUTO 55mm F3.5 E1893", ("Nikon Ai-S 18mm f/3.5", ["nikon", "3\\.5"]), False),
              ("【TR51】 Nikon NIKKOR-H 5cm f2 NO755073 カメラレンズ", ("Nikon F2", ["nikon", "f2"]), False),
              ("ニコン Nikon Ai-S 18mm F3.5 美品", ("Nikon Ai-S 18mm f/3.5", ["nikon", "18mm"]), True)]

    def test_accessory_python(self):
        for t in self.ACC:
            self.assertIn("付属品のみ?", ba.detect_flags(t), t)

    def _table(self):
        import re as _re
        return [{"title": n, "_price": 100.0, "source": "鑑定表(テスト)", "_patterns": [_re.compile(x, _re.I) for x in pats]}
                for _, (n, pats), _ in self.KANTEI]

    def test_kantei_python(self):
        for (t, _, ok), row in zip(self.KANTEI, self._table()):
            self.assertEqual(bool(ba.match_kantei(t, [row])), ok, t)
        self.assertEqual(ba.match_sales("Nikon NIKKOR-H 5cm f2", [{"title": "Nikon F2", "_tokens": ba._tokens("Nikon F2")}]), [])

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        res = []
        for t, (n, pats), _ in self.KANTEI:
            data = bx.build_data(CFG, 150.0)
            data["refs"] = []
            data["kantei"] = [{"title": n, "price": 100.0, "tier": 3, "source": "鑑定表(テスト)", "patterns": pats,
                               "tokens": sorted(ba._tokens(n))}]
            res.append(run_js(data, f"console.log(JSON.stringify(m.matchKantei({json.dumps(t, ensure_ascii=False)}).length>0))"))
        self.assertEqual(res, [ok for *_, ok in self.KANTEI])
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        acc = run_js(data, f"console.log(JSON.stringify({json.dumps(self.ACC, ensure_ascii=False)}.map(t=>m.detectFlags(t).includes('付属品のみ?'))))")
        self.assertEqual(acc, [True, True])


if __name__ == "__main__":
    unittest.main()
