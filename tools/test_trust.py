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
        # 2026-11-23 実際の出品の書き方: 二重の打ち消しは「ある」（◎ を出していた）
        "レンズ内にクモリがないわけではないですが、実用には問題ないかと思います。": ["クモリ"],
        "カビが無いわけではありません": ["カビ"],
        "クモリはなくはないです": ["クモリ"],
        "カビ・クモリなしとは言えません": ["カビ", "クモリ"],
        "目立つキズ・カビ・クモリはないです。": [],
        "カビ・クモリなし、動作確認済みです。": [],
        # 2026-11-23 オーナー決定: 説明文の動作の不良は要確認（実際の出品の書き方）
        "ズームリングが回りません。": ["回りません"],
        "ピントリングがかなり固いです": ["リングがかなり固い"],
        "完全にピントが合いません": ["ピントが合いません"],
        "AF動作しますがカタカタします。ピントは合う時と合わない時があり不安定です": ["カタカタ", "合わない時", "不安定"],
        "前枠側にヒビがあります": ["ヒビ"],
        "シャッターが切れません": ["切れません"],
        "電源が入りません": ["電源が入りません"],
        "ヒビ・割れはありません。AF・絞りも問題なし。": [],
        "外観きれい、動作確認済み、不具合なし。": [],
        "ジャンク品として出品します": [],
        # オーキドが実物の差分に通して見つけた誤り（2026-11-23）
        "割れ物ですので丁寧に梱包します": [],
        "万が一動作しない場合は3日以内にご連絡ください": [],
        "届いて動かない場合は返品可": [],
        "到着後すぐ電源が入らないなどの初期不良は対応します": [],
        "途切れず撮影できました": [],
        "巻き上げができません": ["巻き上げができません"],
        "ミラーアップしたまま戻りません": ["戻りません"],
        "絞り羽根が開かない": ["開かない"],
        "シャッター幕が開きっぱなしです": ["開きっぱなし"],
        "電池がないため動作確認できていません": ["動作確認できていません"],
        "露出計が動きません": [],
        "クラックなし、異音はありません": [],
        # オーキドの2回目（決まり文句の外し方が広すぎて、不良を見逃した）
        "シャッターが切れませんので返品不可でお願いします": ["切れません"],
        "低速の場合シャッターが切れません": ["切れません"],
        "寒い場合にピントが合わない時があります": ["ピントが合わない"],
        "フードにひび割れあり、保証はありません": ["ひび割れ"],
        "切れません。返品はお受けできません。": ["切れません"],
        "電池蓋が開かない": ["開かない"],
        "裏蓋が開かないことはありません": [],
        "ヒビ割れはありません": [],
        "カビあり、クモリなし": ["カビ"],
        "カビ、クモリはありません": [],
        "シャッターが動かない場合があります": ["動かない"],
        "シャッターが切れない場合もございます": ["切れない"],
        "動かない場合は返品可": [],
        # 2026-11-23 オーナー「露出計不良はオッケーって言わなかったか？」: 露出計の不良は拾わない
        "露出計不動です。": [],
        "露出計は動作未確認です": [],
        "露出計が動かない場合があります": [],
        "内蔵メーターは動きません。シャッターは全速切れます。": [],
        "露出計不良、シャッターが切れません": ["切れません"],
        # 露出計不良の出品を実際に開いて見つけた誤り（ストアの決まり文句・「なく」の打ち消し）
        "※撮影に影響のない傷やすれ、\n　カビくもりの有無等の\n　状態の程度の問題関しては": [],
        "・フィルムカメラにおけるデートバック(日付機能)は\n2021年は対応外のため、全て動作未確認です。": [],
        "レンズはカビや曇りなくクリアです。": [],
        "薄くもりのみで撮影には影響ない程度です。": ["くもり"],
        "シャッター全速切れますが、露出計不良です。": [],
        "巻き上げレバーが固着しており動かない": ["動かない"],
        "すれ・きず・くもり等の主観による過多など、\n撮影に影響のない状態に": [],
        "極小のカビや薄い曇りなど「無」と記載されていても\n見落としや角度、光量によって": [],
        "ごみ・薄かび、汚れあります。": ["かび"],
        # オーキドが見つけた見逃し
        "シャッター、露出計不良です": ["シャッター、露出計不良"],
        "シャッター不良のためジャンク": ["シャッター不良"],
        "カビくもりの有無は未確認です": ["カビ", "くもり"],
        "カビやクモリが少なくありません": ["カビ", "クモリ"],
        "カビ・クモリなく、撮影に問題ありません": [],
        "カビやクモリも無く描写に影響はございません。": [],
        "前玉にクモリがありますが実用には問題ないかと思います": ["クモリ"],
        "ファインダ内LCD - 作動しません（点灯もしません）": ["作動しません"],
        "露出計以外は動きません": ["動きません"],
        "露出計を除き動作しません": ["動作しません"],
        "露出計のほかは動きません": ["動きません"],
        "露出計、シャッターともに動きません": ["動きません"],
        "露出計の針が振れません": [],
        # 同じ話の塗装はがれ（補修して記載すれば売れる）も難として拾わない。レンズの剥離は拾う
        "塗装剥離あり": [],
        "ペイントの剥離があります": [],
        "塗装はがれがあります": [],
        "レンズ面に剥離があります": ["剥離"],
        "コーティング剥離あり": ["コーティング剥離"],
        "露出計は動きませんがシャッターも低速で切れません": ["切れません"],
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

    METER = {
        "Nikon F2 フォトミック 露出計不良": True,
        "露出計は動きません。シャッターは全速切れます。": True,
        "露出計不動のためジャンク": True,
        "内蔵メーターは反応なし": True,
        "露出計以外は不動です": False,
        "露出計！シャッター不良 ハーフサイズ": False,
        "ジャンク Rollei 35S 露出計NG ブラック": True,
        "実用難あり■OM-1 ボディ■シャッター全速完動・露出計不良■": True,
        "露出計も正常に動作します": False,
        "露出計OK シャッターOK": False,
        "Canon AE-1 ボディ": False,
    }

    def test_meter_ok(self):
        """露出計の不良は狙い目の印（2026-11-23 オーナー）。判定は変えない."""
        for t, want in self.METER.items():
            self.assertEqual(ba.meter_ok(t), want, t)
        urls = ba.meter_search_urls()
        self.assertTrue(urls)
        import robots
        for u, _ in urls:
            self.assertFalse(robots.removed_params(u), u)

    def test_meter_ok_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(list(self.METER), ensure_ascii=False)}.map(t=>m.meterOk(t))))")
        self.assertEqual(out, list(self.METER.values()))

    def test_read_description(self):
        page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
            {"props": {"pageProps": {"initialState": {"item": {"detail": {"item": {
                "descriptionHtml": "外観は綺麗です。<br>レンズにカビがあります。", "conditionName": "やや傷や汚れあり"}}}}}}})
                + "</script>")
        text, cond = ba.read_description(page)
        self.assertIn("カビ", text)
        self.assertEqual(cond, "やや傷や汚れあり")


class TrustedThreshold(unittest.TestCase):
    """安心ストアも基準は全体と同じ（2026-11-16 全体統一）。Python と JS で同じ上限額."""

    def _py(self, trusted):
        it = ba.Item(url="u", title="x", expected_price_usd=150.0, current_price_jpy=10000, domestic_shipping_jpy=800,
                     trusted=trusted)
        return ba.calculate(it, CFG, 150.0)["max_bid_jpy"]

    def test_python(self):
        self.assertEqual(self._py(True), self._py(False))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, "console.log(JSON.stringify([true,false].map(t=>m.calculate({price:10000,ship:800,tax:false,trusted:t},150,150).maxBid)))")
        self.assertEqual(out, [self._py(True), self._py(False)])


class LowPriceThreshold(unittest.TestCase):
    """売値（送料込み）$99以下は利益4,000円以上（2026-11-19 ユーザー決定）。$100以上は今までどおり。Python と JS で同じ上限額."""

    def _py(self, usd):
        it = ba.Item(url="u", title="x", expected_price_usd=usd, current_price_jpy=1000, domestic_shipping_jpy=800)
        return ba.calculate(it, CFG, 150.0)

    def test_python(self):
        j = CFG["judge"]
        low, high = self._py(95.0), self._py(150.0)
        self.assertEqual(ba.low_price_min_profit(95.0, 2000, j), 4000)
        self.assertEqual(ba.low_price_min_profit(150.0, 2000, j), 2000)
        # 同じ売値でも、基準を上げた分だけ上限額が下がる
        j2 = dict(j, low_price_usd=0)
        cfg2 = dict(CFG, judge=j2)
        it = ba.Item(url="u", title="x", expected_price_usd=95.0, current_price_jpy=1000, domestic_shipping_jpy=800)
        self.assertLess(low["max_bid_jpy"], ba.calculate(it, cfg2, 150.0)["max_bid_jpy"])
        self.assertGreater(high["max_bid_jpy"], 0)

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, "console.log(JSON.stringify([95,150].map(u=>m.calculate({price:1000,ship:800,tax:false},u,150).maxBid)))")
        self.assertEqual(out, [self._py(95.0)["max_bid_jpy"], self._py(150.0)["max_bid_jpy"]])


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
             "Nikon FM2 ボディ 元箱付き 美品": False, "Canon AE-1 箱付き": False,
             "【未使用・レア】Nikon ニコン レンズ型タンブラー ステンレスマグ ★ AF-S 24-70mm f/2.8G ED仕様 ★ 専用ケース付": True}

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


class ModelKeyGeneration(unittest.TestCase):
    """［直す］の相場は機種名に結び付く。世代（II）が落ちると、その出品に当たらない（Sony α7 II → Sony α7 だった）."""

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        titles = ["期間限定セール ソニー デジタル一眼カメラ Sony α7 II【中古】", "Nikon F3 Limited ボディ", "ニコン Nikon FM2 ブラック",
                  "NIKON ニコン コンパクトデジタルカメラ COOLPIX P7000 ∞ 776BC-16", "OLYMPUS 単焦点レンズ M.ZUIKO DIGITAL 45mm f1.8 オリンパス 元箱 7771C-1"]
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(titles, ensure_ascii=False)}.map(t=>m.modelKey(t)[0])))")
        self.assertEqual(out, ["Sony α7 II", "Nikon F3 Limited", "Nikon FM2"] + [ba.model_key(t)[0] for t in titles[3:]])
        self.assertTrue(all("776" not in x and "7771" not in x for x in out[3:]))


class StoreCodesAndTripods(unittest.TestCase):
    """リサマイ市場などの管理番号（∞ 776BC-16・7774A-1）は機種名に入れない。三脚・雲台・外部モニターはカメラではない（2026-11-19）."""

    def test_store_codes(self):
        self.assertEqual(ba.model_key("NIKON ニコン コンパクトデジタルカメラ COOLPIX P7000 ∞ 776BC-16")[0], "Nikon COOLPIX P7000")
        self.assertNotIn("7771C-1", ba.title_to_query("OLYMPUS 単焦点レンズ M.ZUIKO DIGITAL 45mm f1.8 7771C-1", 20))
        self.assertIn("X-T2", ba.title_to_query("富士フイルム FUJIFILM X-T2 ボディ", 20))  # 本物の型番は残す

    def test_tripods_are_accessories(self):
        for t in ["Manfrotto マンフロット MVTTWINMC + MVH608AH カーボンツインビデオ三脚 雲台 ケース付き ¶ 7774A-1",
                  "GITZO ジッツオ GM4552L カーボン一脚 5段", "リーベック Libec ビデオ雲台 H45 ヘッド"]:
            self.assertIn("付属品のみ?", ba.detect_flags(t), t)
        for t in ["Nikon F3 HP ボディ 三脚穴キズあり", "Canon EOS 5D 背面モニター傷あり"]:
            self.assertNotIn("付属品のみ?", ba.detect_flags(t), t)


if __name__ == "__main__":
    unittest.main()


class SweetSpot(unittest.TestCase):
    """狙い目スペック（単焦点の焦点距離×F値）の印. 判定・上限額は変えない."""

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        cases = {"Nikon Ai-S 50mm f/1.2": True, "Nikon Ai-S 50mm f/1.4": False, "Canon FD 85mm F1.8": True,
                 "Canon FD 35-70mm f/3.5": False, "Nikon Ai 28mm f/2.8": False, "Nikkor 135mm 1:2": True,
                 "Canon EF 200mm F2.8L": True, "Olympus 100mm F2.8": False, "レンズ　５０ｍｍ　Ｆ１．２": True, "Nikon F3 ボディ": False}
        data = bx.build_data(CFG, 150.0)
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(list(cases))}.map((t)=>!!m.sweetSpot(t))))")
        self.assertEqual(out, list(cases.values()))


class BidPriority(unittest.TestCase):
    """入札候補の並び（2026-11-18）: 売れ筋・売値$99超を上に. Python（bid_priority・sold_hit）と拡張機能（bidPrio）で同じ."""

    def test_sold_hit(self):
        own = {"source": "自分の販売実績", "_price": 249.99, "purchase_jpy": "8000"}
        self.assertTrue(ba.sold_hit(own, CFG, 150.0))
        self.assertFalse(ba.sold_hit({**own, "purchase_jpy": "34000"}, CFG, 150.0))  # 仕入が高くて利益が出ていない
        self.assertTrue(ba.sold_hit({**own, "source": "頒布会リスト", "purchase_jpy": ""}, CFG, 150.0))  # 頒布会も売れた記録（仕入値なし）
        self.assertFalse(ba.sold_hit({**own, "source": "相場メモ(MINT)"}, CFG, 150.0))  # 相場メモは販売履歴ではない
        self.assertTrue(ba.sold_hit({**own, "purchase_jpy": ""}, CFG, 150.0))  # 仕入値が分からない販売履歴も売れた機種

    def test_priority_python(self):
        self.assertEqual(ba.bid_priority(250, True), 0)
        self.assertEqual(ba.bid_priority(250, False), 1)
        self.assertEqual(ba.bid_priority(99, True), 2)
        self.assertEqual(ba.bid_priority(60, False), 3)

    def test_priority_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        cases = [(250, True), (250, False), (99, True), (60, False)]
        items = [{"usd": u, "refs": [{"hit": h, "source": "自分の販売実績"}]} for u, h in cases]
        data = bx.build_data(CFG, 150.0)
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(items)}.map((i)=>m.bidPrio(i))))")
        self.assertEqual(out, [ba.bid_priority(u, h) for u, h in cases])


class HitSearches(unittest.TestCase):
    """売れ筋の機種を名指しで探す検索URL（2026-11-18）."""

    def test_urls(self):
        rows = [{"title": "[Near MINT] Nikon Ai Nikkor 28mm f/2 MF Lens From JAPAN", "source": "自分の販売実績",
                 "_price": 249.99, "purchase_jpy": "8000"},
                {"title": "[MINT] Contax T2 Titanium Film Camera", "source": "Chatwork販売実績(店)",
                 "_price": 900.0, "purchase_jpy": "60000"},
                {"title": "[MINT] Canon EF 50mm f/1.8 II", "source": "自分の販売実績", "_price": 74.99, "purchase_jpy": "9000"}]
        got = ba.hit_search_urls(rows, CFG, 150.0)
        labels = [lbl for _, lbl in got]
        self.assertEqual(labels[0], "Contax T2")   # 利益の大きい順
        self.assertIn("Nikon 28mm", labels)
        self.assertNotIn("Canon 50mm", labels)       # 利益が出ていない機種は探さない
        import urllib.parse as up
        q = up.parse_qs(up.urlparse(got[0][0]).query)
        self.assertEqual(q["va"], ["T2"])
        self.assertIn("コンタックス", q["vo"][0])
        self.assertEqual(q["auccat"], ["23636"])


class LostAndStale(unittest.TestCase):
    """負けた機種の検索と、古い相場メモの見直し（2026-11-18）."""

    def test_lost_models(self):
        from datetime import date
        today = date.today().isoformat()
        rows = [{"title": "CONTAX Carl Zeiss Distagon T 35mm F2.8 AEJ コンタックス", "result": "負け", "end": today},
                {"title": "Nikon AF-S 70-200mm f/2.8G ED", "result": "落札(見込み)", "end": today},
                {"title": "ニコン Nikon F3 HP ボディ", "result": "落札失敗", "end": "2020-01-01"}]
        self.assertEqual(ba.lost_models(rows), [("Contax", "35mm")])
        urls = ba.hit_search_urls([], CFG, 150.0, lost=ba.lost_models(rows))
        self.assertEqual([lbl for _, lbl in urls], ["Contax 35mm（負けた機種）"])

    def test_stale_memos(self):
        with tempfile.TemporaryDirectory() as d:
            memo, hist = Path(d) / "m.csv", Path(d) / "h.csv"
            memo.write_text("title,sold_price_usd,source,memo\nA 50mm,140,相場メモ,\nB 28mm,120,相場メモ,\nC T2,900,相場メモ,\n",
                            encoding="utf-8")
            from datetime import date
            hist.write_text(f"title,sold_price_usd,cond,date\nA 50mm,140,,2025-01-01\nB 28mm,120,,{date.today().isoformat()}\n",
                            encoding="utf-8")
            got = [m["title"] for m in ba.stale_memos(memo, hist)]
            self.assertEqual(got, ["C T2", "A 50mm"])  # 日付不明が先、最近直した B は出さない


class ChatworkShipping(unittest.TestCase):
    """Chatwork の報告額は送料 $30 込み → 商品代＝報告額−$30、送料込みの売値＝商品代＋$40（2026-11-18）."""

    def test_gross(self):
        self.assertEqual(ba.chatwork_gross(169.0, CFG), 179.0)

    def test_read_sales(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cw.csv"
            p.write_text("date,title,ebay_query,sold_price_usd,shipping_charged_usd,purchase_jpy,ebay_url,source\n"
                         "2026-11-01,Nikon F3 HP,,169,0,11000,,Chatwork販売実績(店)\n"
                         "2026-11-01,Nikon FM2,,169,0,11000,,自分の販売実績\n", encoding="utf-8")
            rows = ba.read_sales(p)
            self.assertEqual([r["_price"] for r in rows], [179.0, 169.0])  # 自分の販売実績はそのまま


class ResearchTier(unittest.TestCase):
    """以前の入札分析ファイルのリサーチ（推定）: 鑑定表と同じ「確認してから」の根拠。販売履歴（売れ筋）には数えない（2026-11-19）."""

    def test_tier_and_not_sold(self):
        r = {"source": "リサーチ(Mint)", "title": "Contax Planar 50mm f/1.4 AEJ"}
        self.assertEqual(ba.source_tier(r), 3)
        self.assertGreater(ba.source_tier(r), ba.SURE_TIER)
        self.assertFalse(ba.is_sold_record(r))
        self.assertEqual(ba.condition_of(r), ba.condition_of({"source": "相場メモ(Mint)", "title": "x"}))


class MemoHitsListing(unittest.TestCase):
    """［登録］した相場（機種名＝modelKey）が、その出品自身に必ず当たる（2026-11-19: Rodenstock の登録が当たらなかった）."""
    TITLES = ["RODENSTOCK ローデンシュトック Apo-sironar-N F5.6 150mm レンズ ∴ 7750D-3",
              "【良品・元箱付き】Nikon ニコン NIKKOR Z 20mm F1.8 S 単焦点レンズ Zマウント ロ 777CE-8",
              "Canon EF 50- F1.2L USM キヤノン 単焦点レンズ c 77708-12",
              "SONY ソニー DSC-WX350 デジタルカメラ 》 777D2-1",
              "Schneider シュナイダー Xenar 75mm F3.5"]

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        code = ("const T=" + json.dumps(self.TITLES, ensure_ascii=False) + ";console.log(JSON.stringify(T.map(t=>{const [l]=m.modelKey(t);"
                "const ref={title:l,price:540,tier:0,source:'相場メモ(MINT)',tokens:[...m.tokensOf(l)],acc:false};"
                "return [l, m.matchRefs(t,[ref]).length, m.titleToQuery(t,20)];})))")
        out = run_js(data, code)
        for (label, hits, q), t in zip(out, self.TITLES):
            self.assertEqual(hits, 1, (t, label))
            self.assertNotRegex(q, r"\d{3}[0-9A-Z]{1,3}-\d", t)  # 管理番号は検索語に入れない
        self.assertTrue(out[0][0].lower().startswith("rodenstock"))
        self.assertEqual([ba.model_key(t)[0] for t in self.TITLES], [o[0] for o in out])  # Python と同じ機種名


class SeriesWords(unittest.TestCase):
    """Zuiko Digital（フォーサーズ）と OM Zuiko、Rolleicord と Rolleiflex は別物（2026-11-19 ユーザー指摘）."""
    TITLES = ["OLYMPUS ZUIKO DIGITAL 25mm F2.8 フォーサーズマウント パンケーキレンズ",
              "OLYMPUS M.ZUIKO DIGITAL 45mm f1.8",
              "Rollei ローライ Rolleicord Va type 2 Schneider-Kreuznach Xenar 75mm F3.5",
              "Rolleiflex 3.5F Planar 75mm F3.5"]

    def _ref(self, title, price=200.0):
        return {"title": title, "sold_price_usd": price, "_price": price, "_tokens": ba._tokens(title), "source": "相場メモ(Near MINT)"}

    def test_python(self):
        self.assertIn("DIGITAL", ba.model_key(self.TITLES[0])[0].upper())
        self.assertIn("M.ZUIKO", ba.model_key(self.TITLES[1])[0].upper())
        self.assertIn("Rolleicord", ba.model_key(self.TITLES[2])[0])
        om = self._ref("Olympus 50mm F2")
        self.assertFalse(ba.match_sales("OLYMPUS ZUIKO DIGITAL 50mm F2 Macro", [om]))      # OM の相場をフォーサーズに当てない
        self.assertTrue(ba.match_sales("OLYMPUS OM-SYSTEM ZUIKO AUTO-MACRO 50mm F2", [om]))
        dig = self._ref("Olympus Digital 25mm F2.8")
        self.assertTrue(ba.match_sales(self.TITLES[0], [dig]))
        cord = self._ref("Rollei Rolleicord 75mm F3.5")
        self.assertFalse(ba.match_sales(self.TITLES[3], [cord]))
        self.assertTrue(ba.match_sales(self.TITLES[2], [cord]))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        pairs = [["Olympus 50mm F2", "OLYMPUS ZUIKO DIGITAL 50mm F2 Macro"], ["Olympus 50mm F2", "OLYMPUS OM-SYSTEM ZUIKO AUTO-MACRO 50mm F2"],
                 ["Rollei Rolleicord 75mm F3.5", self.TITLES[3]], ["Rollei Rolleicord 75mm F3.5", self.TITLES[2]]]
        code = ("const P=" + json.dumps(pairs, ensure_ascii=False) + ";console.log(JSON.stringify(P.map(([l,t])=>m.matchRefs(t,[{title:l,price:200,tier:0,"
                "source:'相場メモ',tokens:[...m.tokensOf(l)],acc:false}]).length)))")
        self.assertEqual(run_js(data, code), [0, 1, 0, 1])
        labels = run_js(data, "console.log(JSON.stringify(" + json.dumps(self.TITLES, ensure_ascii=False) + ".map(t=>m.modelKey(t)[0])))")
        self.assertIn("DIGITAL", labels[0].upper())
        self.assertIn("Rolleicord", labels[2])


class MountWords(unittest.TestCase):
    """Canon EF・FD・FL・RF、Nikon Z と F、Sony FE と E、Fujifilm XF と GF は別物。マウントが書いていない側には当てない（2026-11-19）."""
    PAIRS = [  # (相場の商品名, 出品の商品名, 当たるか)
        ("Canon EF 50mm F1.8 II", "キヤノン Canon EF50mm F1.8 II 単焦点", True),
        ("Canon EF 50mm F1.8 II", "Canon New FD 50mm F1.8", False),
        ("Canon RF 50mm F1.8 STM", "Canon EF 50mm F1.8 STM", False),
        ("Nikon Ai-s 50mm f/1.8", "Nikon Ai-S Nikkor 50mm F1.8 パンケーキ", True),
        ("Nikon AF-S NIKKOR 20mm f/1.8G ED", "Nikon NIKKOR Z 20mm F1.8 S", False),
        ("Nikon Ai-s 50mm f/1.8", "ニコン 50mm f1.8", False),
        ("SONY FE 50mm F1.8", "SONY E 50mm F1.8 OSS", False),
        ("FUJIFILM XF 35mm F1.4 R", "フジフイルム FUJIFILM XF35mm F1.4 R", True),
        ("Olympus OM Zuiko 50mm f1.4", "オリンパス OM ZUIKO 50mm F1.4", True),   # マウント語の無いメーカーは今までどおり
    ]

    def _ref(self, title):
        return {"title": title, "sold_price_usd": 200.0, "_price": 200.0, "_tokens": ba._tokens(title), "source": "相場メモ"}

    def test_python(self):
        for ref, q, ok in self.PAIRS:
            self.assertEqual(bool(ba.match_sales(q, [self._ref(ref)])), ok, (ref, q))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        code = ("const P=" + json.dumps([[r, q] for r, q, _ in self.PAIRS], ensure_ascii=False) + ";console.log(JSON.stringify(P.map(([l,t])=>m.matchRefs(t,[{title:l,price:200,tier:0,"
                "source:'相場メモ',tokens:[...m.tokensOf(l)],acc:false}]).length>0)))")
        self.assertEqual(run_js(data, code), [ok for _, _, ok in self.PAIRS])

    def test_memo_label_keeps_mount(self):
        # ［登録］の機種名にマウントが残り、その出品自身に当たる
        for t in ["Canon New FD 50mm F1.4", "Nikon NIKKOR Z 20mm F1.8 S", "SONY FE 50mm F1.8", "FUJIFILM GF 63mm F2.8"]:
            label = ba.model_key(t)[0]
            self.assertTrue(ba.match_sales(t, [self._ref(label)]), (t, label))


class CanonL(unittest.TestCase):
    """Canon の L レンズと L の付かないレンズは別物（2026-11-19 ユーザー指摘「Canon の L は大事」）."""
    PAIRS = [  # (相場の商品名, 出品の商品名, 当たるか)
        ("Canon EF 50mm F1.2L USM", "Canon EF 50- F1.2L USM キヤノン 単焦点レンズ c 77708-12", True),   # 50- は 50mm の書き忘れ
        ("Canon EF 50mm F1.2L USM", "キヤノン EF50mm F1.2 L USM", True),
        ("Canon EF 24-105mm F4L IS USM", "Canon EF 24-105mm F4 L IS USM 標準ズーム", True),
        ("Canon EF 24-105mm F4L IS USM", "Canon EF 24-105mm F4 IS STM", False),
        ("Canon EF 24-105mm F4 IS STM", "Canon EF 24-105mm F4L IS USM", False),
        ("Canon EF 70-200mm F4L USM", "キヤノン EF70-200mm F4 USM", False),
        ("Canon New FD 50mm F1.2 L", "Canon New FD 50mm F1.2", False),
        ("Canon New FD 50mm F1.2 L", "Canon NFD 50mm F1.2L", True),
    ]

    def _ref(self, title):
        return {"title": title, "sold_price_usd": 500.0, "_price": 500.0, "_tokens": ba._tokens(title), "source": "相場メモ"}

    def test_python(self):
        for ref, q, ok in self.PAIRS:
            self.assertEqual(bool(ba.match_sales(q, [self._ref(ref)])), ok, (ref, q))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        code = ("const P=" + json.dumps([[r, q] for r, q, _ in self.PAIRS], ensure_ascii=False) + ";console.log(JSON.stringify(P.map(([l,t])=>m.matchRefs(t,[{title:l,price:500,tier:0,"
                "source:'相場メモ',tokens:[...m.tokensOf(l)],acc:false}]).length>0)))")
        self.assertEqual(run_js(data, code), [ok for _, _, ok in self.PAIRS])

    def test_label_keeps_l(self):
        # 機種名（［登録］・sold/now の検索語）に L と 50mm が残り、その出品自身に当たる
        t = "Canon EF 50- F1.2L USM キヤノン 単焦点レンズ c 77708-12"
        label = ba.model_key(t)[0]
        self.assertEqual(label, "Canon EF 50mm F1.2 L")
        self.assertTrue(ba.match_sales(t, [self._ref(label)]))
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        if shutil.which("node"):
            self.assertEqual(run_js(data, "console.log(JSON.stringify(m.modelKey(" + json.dumps(t, ensure_ascii=False) + ")[0]))"), label)


class DuplicateAuctions(unittest.TestCase):
    """同じオークションが一覧に2回入っていても1件にする（件数2・［全て開く］で同じページが2つ開いていた。2026-11-19）."""
    ITEM = {"auctionId": "q1251095026", "title": "Canon EOS Kiss X10 ダブルズームキット", "price": 55000, "bidCount": 0, "endTime": ""}

    def _page(self):
        nd = {"props": {"pageProps": {"initialState": {"search": {"items": {"listing": {"items": [self.ITEM, dict(self.ITEM)], "totalResultsAvailable": 2}}}}}}}
        return nd

    def test_python(self):
        page = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(self._page()) + "</script>"
        items, _ = ba.parse_listing_page(page)
        self.assertEqual([i.auction_id for i in items], ["q1251095026"])

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        got = run_js(data, "console.log(JSON.stringify(m.parseItems(" + json.dumps(self._page()) + ").map((x)=>x.id)))")
        self.assertEqual(got, ["q1251095026"])


class StoreCodeTitles(unittest.TestCase):
    """実際の出品の商品名で、管理番号を外し・本物の型番は残す（2026-11-19 ユーザー指摘「管理番号は無くならない？他のまで除外しちゃう？」）. Python と拡張機能で同じ."""
    CASES = {
        "【草】ジャンク品 Canon Power Shot S110 (silver) キヤノン パワーショット【K172】K03": "Canon PowerShot S110",  # シリーズ名を残す（2026-11-19）
        "【加】稼働！ 撮影可能！ OLYMPUS オリンパス VH-510 ピンク コンパクト デジタル カメラ【9022NT】K11": "Olympus VH-510",
        "【豊】希少 現状品 SELSI 6×15 Field 7° 小型 双眼鏡 ヴィンテージ ケース付き【2282MK】K14": "SELSI 6x15",
        "【塩】OLYMPUS オリンパス OM10 シャッターOK ZUIKO AUTO-S 50mm F1.8 カビ・チリ一部あり【25490CK】J26": "Olympus OM10 50mm F1.8",
        "【錦】美品！Leica ライカ M3 -1073565 LENS ｆ＝5cm 1:2 レンジファインダー フィルムカメラ【1582-4NT】K17": "Leica M3 5cm F2",
        "【加】美品！Carl Zeiss Planar T* 50mm F1.4 防湿庫保管！ コンタックス CONTAX【9005NT】K7": "Contax 50mm F1.4",
        "OLYMPUS E-410 ボディ": "Olympus E-410",          # E-410・VH-510 は型番（C-2436 のような4桁は管理番号）
        "Nikon 【D750】": "Nikon D750",                    # 括弧の中の型番は消さない
        "Polaroid 【SX-70】 ランドカメラ": "Polaroid SX-70",
        "1円～/Nikon/ニコン/D5100/ボディ/N031": "Nikon D5100",
    }

    def test_python(self):
        self.assertEqual({t: ba.model_key(t)[0] for t in self.CASES}, self.CASES)

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        got = run_js(data, "const T=" + json.dumps(list(self.CASES), ensure_ascii=False) + ";console.log(JSON.stringify(Object.fromEntries(T.map((t)=>[t,m.modelKey(t)[0]]))))")
        self.assertEqual(got, self.CASES)


class PriceTrend(unittest.TestCase):
    """相場の上がり下がり（↑↓。2026-11-19 ユーザー指定）. 判定は変えず、表示だけ."""

    def test_dates(self):
        for raw, want in [("2026-11-16", "2026-11-16"), ("Nov-16-26", "2026-11-16"), ("d:16-Nov-26", "2026-11-16"), ("2026/1/5", "2026-01-05"), ("", ""), ("abc", "")]:
            self.assertEqual(ba.norm_date(raw), want, raw)

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        ref = lambda p, d: {"title": "Nikon F3", "price": p, "tier": 0, "source": "自分の販売実績", "tokens": ["nikon", "f3"], "acc": False, "d": d}  # noqa: E731
        cases = {
            "up": [ref(300, "2026-03-01"), ref(310, "2026-04-01"), ref(400, "2026-10-20"), ref(420, "2026-11-10")],
            "down": [ref(400, "2026-03-01"), ref(300, "2026-11-10")],
            "flat": [ref(300, "2026-03-01"), ref(310, "2026-11-10")],
            "recent_only": [ref(300, "2026-10-01"), ref(400, "2026-11-10")],
            "no_date": [ref(300, ""), ref(400, "")],
        }
        code = "const C=" + json.dumps(cases) + ";console.log(JSON.stringify(Object.fromEntries(Object.entries(C).map(([k,refs])=>{const t=m.trendOf({refs});return [k,t?t.pct:null]}))))"
        got = run_js(data, code)
        self.assertGreater(got["up"], 10)
        self.assertLess(got["down"], -10)
        self.assertLess(abs(got["flat"]), 10)      # 矢印は出ない（±10%未満）
        self.assertIsNone(got["recent_only"])     # 90日より前のデータが無い → 出さない
        self.assertIsNone(got["no_date"])


class KitParts(unittest.TestCase):
    """レンズキットはボディとレンズの2点に分け、中身ごとに相場を当てる（2026-11-19 ユーザー指定）."""
    T = "NIKON ニコン デジタル一眼レフカメラ D70 レンズキット(AF-S DX Zoom-NIKKOR 18-70mm F3.5-4.5G IF ED) ⑤ 7795B-4"

    def test_python_parts(self):
        parts = ba.set_part_titles(self.T)
        self.assertEqual(len(parts), 2)
        labels = [ba.model_key(p)[0] for p in parts]
        self.assertIn("D70", labels[0])
        self.assertTrue(labels[1].startswith("Nikon") and "18-70mm" in labels[1] and "AF-S" in labels[1], labels)

    def test_js_same_parts(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        out = run_js(data, "console.log(JSON.stringify(m.setPartTitles(" + json.dumps(self.T, ensure_ascii=False) + ")))")
        self.assertEqual(len(out), 2)
        self.assertIn("18-70mm", out[1])
        self.assertTrue(out[1].lower().startswith("nikon"))


class AuditTitles(unittest.TestCase):
    """2026-11-19 監査: 古物台帳・相場メモ・ヤフオクの検索結果 約2,000件の商品名で見つけた機種名・検索語の誤り. 実際の商品名そのまま.
    Python（毎朝の入札リスト）と拡張機能・店舗アプリで同じ機種名になること."""
    LABELS = {
        # シリーズ名が抜けていた（ユーザー指摘「Canon Power Shot S110 → Canon S110」「EOS Kiss X10 → Canon X10」）
        "Canon Power Shot G7": "Canon PowerShot G7",
        "キヤノン EOS Kiss X10 ボディ": "Canon EOS Kiss X10",
        "【中古】Canon キヤノン EOS Kiss X7 ダブルズームキット ブラック シャッター回数僅少": "Canon EOS Kiss X7",
        "【中古】Canon キヤノン IXY 620F ゴールド コンパクトデジタルカメラ": "Canon IXY 620F",
        "【中古】Nikon ニコン COOLPIX W100 ブルー コンパクトデジタルカメラ": "Nikon COOLPIX W100",
        "【返品保証】 ニコン Nikon COOLPIX 4500 コンパクトデジタルカメラ e16129": "Nikon COOLPIX 4500",
        "Sony Syber-Shot DSC-RX1R": "Sony Cyber-shot DSC-RX1R",
        "【中古】FUJIFILM 富士フイルム FinePix S9000 ブラック コンパクトデジタルカメラ": "Fujifilm FinePix S9000",
        "Panasonic LUMIX DC-TZ95D Black": "Panasonic LUMIX DC-TZ95D",
        "Olympus OM-D E-M5 Mark II Black": "Olympus OM-D E-M5 Mark II",
        "【返品保証】 オリンパス Olympus PEN F F.Zuiko Auto-S 38mm F1.8 ボディレンズセット e16343": "Olympus PEN F 38mm F1.8",
        "【返品保証】 オリンパス Olympus μ mju ZOOM 105 DELUXE 38-105mm コンパクトカメラ e21932": "Olympus mju 105 38-105mm",
        "%516 OLYMPUS μ-7040 オリンパス ミュー コンパクトデジタルカメラ": "Olympus mju-7040",
        "【返品保証】 ペンタックス Pentax ESPIO 110 38-110mm コンパクトカメラ e16773": "Pentax ESPIO 110 38-110mm",
        "【動作品】OLYMPUS XA オリンパス コンパクトフィルムカメラ C-3157": "Olympus XA",
        "【ジャンク】OLUMPUS XA 2 + A11 #U978": "Olympus XA2 A11",
        "【返品保証】 【シャッター290回】ニコン Nikon 1 J3 レッド 10-100mm F4-5.6 VR ミラーレス一眼 e16905": "Nikon 1 J3 10-100mm F4-5.6",
        "キヤノン Canon EOS RP RF 24-105mm IS STM レンズキット ミラーレス一眼 カメラ 中古": "Canon EOS RP RF 24-105mm",
        "【返品保証】 キャノン Canon EOS 7 ボディ e20645": "Canon EOS 7",
        # 「NIKON D850」が「NIKO」になっていた（[#＃No.] の文字の組で N＋D850 を管理番号として消していた）
        "NIKON D850 ボディ": "Nikon D850",
        "CANON 600D": "Canon 600D",
        "PENTAX-110 F2.8 50mm #U713": "Pentax 110 F2.8 50mm",
        # 管理番号・シャッター回数・個数・ズーム倍率・重さ・Canon の内部型番が機種名に入っていた
        "オリンパス OLYMPUS μ[mju:]-II mju Ⅱ mju2 110 μ ミュー 現状品 #5344A2104": "Olympus mju II mju2 110",
        "【返品保証】 タムロン Tamron SP Di II 10-24mm F3.5-4.5 レンズ e8358": "Tamron II 10-24mm F3.5-4.5",
        "◇美品【SONY ソニー】α77II ボディ ILCA-77M2 デジタル一眼カメラ　シャッター回数：138回": "Sony α77II ILCA-77M2",
        "☆訳あり大特価☆オリンパス OLYMPUS XA2 2個まとめ #140#3442": "Olympus XA2",
        "【返品保証】 リコー Ricoh Caplio R1 4.8x コンパクトデジタルカメラ e12021": "Ricoh Caplio R1",
        "AB-0892 Nikon NIKKOR50mm 1:1.2 レンズ カメラ 部品 パーツ アクセサリー アンティーク レトロ 391g": "Nikon 50mm F1.2",
        "★極上美品★ Canon PowerShot SX60 HS PC2188": "Canon PowerShot SX60",
        "【20260103KNK-002】Nikon Ai AF-S ズームニッコール ED 28-70mm F2.8D (IF) ブラック": "Nikon Ai AF-S 28-70mm F2.8",
        # 判（645・110）を消していた・焦点距離と同じ数字を重ねていた
        "【返品保証】 ペンタックス Pentax smc PENTAX-A 645 200mm F4.5 中判レンズ e11939": "Pentax 645 200mm F4.5",
        "【返品保証】 ペンタックス Pentax 100 MACRO WR smc PENTAX-D FA MACRO 100mm F2.8 レンズ e17136": "Pentax 100mm F2.8",
        # 型（New FD・Ai）を2回書いていた・非Ai を Ai と読んでいた
        "Canon New FD 50mm f/1.8": "Canon New FD 50mm F1.8",
        "Nikon Ai 28mm f/2.8": "Nikon Ai 28mm F2.8",
        "Nikon NIKKOR-S Auto 35mm f/2.8 non Ai": "Nikon 35mm F2.8 Non-Ai",
        "Contax Planar 50mm f/1.4 AEJ": "Contax 50mm F1.4 AEJ",
        # 社外レンズが「〜マウント」のメーカー名になっていた・書き間違いのメーカー名
        "【返品保証】 ROKINON 35mm F1.5 キャノンマウント レンズ e15869": "ROKINON 35mm F1.5",
        "【返品保証】 SEAGULL 24mm F2.8 ミノルタマウント レンズ e18039": "SEAGULL 24mm F2.8",
        "※動作未確認のためジャンク Cannon/キヤノンカメラAE-1 おまけレンズ付きTEFNON/テフノンH/D-MC ZOOM1:4.5ｆ＝80-200㎜": "Canon AE-1 ZOOM1 4.5f",
        "【外観並級】Cannon IXY 930 IS　#u14695": "Canon IXY 930",
        # ボディの型番 FE2 を「FE 2」に分けていた
        "Nikon FE2 Body": "Nikon FE2",
        # 今日直したもの（再発しないこと）
        "【加】稼働！ 撮影可能！ OLYMPUS オリンパス VH-510 ピンク コンパクト デジタル カメラ【9022NT】K11": "Olympus VH-510",
        "Canon EF 200mm f/2.8 L Ⅱ": "Canon EF 200mm F2.8 II L",
    }

    def test_python(self):
        self.assertEqual({t: ba.model_key(t)[0] for t in self.LABELS}, self.LABELS)

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        T = list(self.LABELS)
        got = run_js(data, "const T=" + json.dumps(T, ensure_ascii=False)
                     + ";console.log(JSON.stringify(T.map((t)=>[m.modelKey(t),m.titleToQuery(t),m.titleToQuery(t,20)])))")
        self.assertEqual(got, [[list(ba.model_key(t)), ba.title_to_query(t), ba.title_to_query(t, 20)] for t in T])

    def test_series_not_in_compare_key(self):
        # シリーズ名は表示だけ。「Canon S110」と「Canon PowerShot S110」は同じ機種としてまとめる
        self.assertEqual(ba.model_key("Canon S110")[1], ba.model_key("Canon PowerShot S110")[1])

    def test_matching(self):
        """相場の当たり方: 判（645・110）違い・社外レンズ・非Ai は当てない. 書き間違いのメーカー名・Power Shot・大文字の NIKON は当てる."""
        def hit(listing, ref):
            return bool(ba.match_sales(ba.title_to_query(listing, 20),
                                       [{"title": ref, "sold_price_usd": 100, "source": "自分の販売実績", "_tokens": ba._tokens(ref)}]))
        self.assertFalse(hit("PENTAX-110 F2.8 50mm #U713", "Pentax FA 50mm f/2.8 Macro"))
        self.assertFalse(hit("【返品保証】 ペンタックス Pentax smc PENTAX-A 645 200mm F4 中判レンズ e14661", "Pentax 200mm f/4"))
        self.assertTrue(hit("【返品保証】 ペンタックス Pentax auto 110 PENTAX-110 24mm F2.8 カメラ e15321", "Pentax 110 Auto 24mm f/2.8"))
        self.assertFalse(hit("【外観美品】 【元箱付き】アストロリ AstrHori 50mm F2 ライカLマウント レンズ e19251", "Leica Summicron-M 50mm f/2 silver"))
        self.assertFalse(hit("Nikon NIKKOR-S Auto 35mm f/2.8 non Ai", "Nikon Ai 35mm f/2.8"))
        self.assertTrue(hit("【中古】Canon キヤノン PowerShot G7 コンパクトデジタルカメラ", "Canon Power Shot G7"))
        self.assertTrue(hit("【外観並級】Cannon IXY 930 IS　#u14695", "Canon IXY 930 IS"))
        self.assertTrue(hit("NIKON D850 ボディ", "Nikon D850 Body"))


class SuggestNet(unittest.TestCase):
    """仮の相場の案: 送料 $40 を引いた商品代が 0 以下なら案にしない（2026-11-19 ユーザー指摘「案 $-1」「案 $-20」）."""

    def test_python(self):
        import tentative as tv
        self.assertIsNone(tv.suggest_usd({"n": 6, "min": 39.99, "med": 120}, CFG))   # 送料込み $39.99 → 商品代 −$0.01
        self.assertIsNone(tv.suggest_usd({"n": 6, "min": 20, "med": 120}, CFG))      # 商品代 −$20
        self.assertIsNone(tv.suggest_usd({"n": 6, "min": 40.5, "med": 120}, CFG))    # 切り捨てて $40 → 商品代 $0
        self.assertIsNone(tv.suggest_usd({"n": 6, "min": 41.2, "med": 120}, CFG))     # 商品代 $1 は付属品などの誤ヒット（下限 $20。2026-11-19 ユーザー決定）
        self.assertEqual(tv.suggest_usd({"n": 6, "min": 60.5, "med": 120}, CFG), 60)  # 商品代 $20 は案にする

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import tentative as tv
        data = bx.build_data(CFG, 150.0)
        cases = [39.99, 20, 40.5, 41.2, 59.9, 60.5, 139.99]
        got = run_js(data, "console.log(JSON.stringify(" + json.dumps(cases) + ".map((x)=>m.suggestUsd({n:6,min:x,med:120}))))")
        self.assertEqual(got, [tv.suggest_usd({"n": 6, "min": x, "med": 120}, CFG) for x in cases])
