"""python -m unittest test_appraise で実行."""
import json
import tempfile
import unittest
from pathlib import Path

import appraise as ba

CFG = ba.load_config(None)


class TitleToQuery(unittest.TestCase):
    def test_japanese_brand_and_model(self):
        q = ba.title_to_query("【美品】ニコン Nikon FM2 ブラック ボディ 動作確認済 1円スタート")
        self.assertEqual(q, "Nikon FM2 Black")

    def test_lens(self):
        q = ba.title_to_query("キヤノン New FD 50mm F1.4 単焦点 #123456")
        self.assertEqual(q, "Canon NewFD 50mm F1.4")  # eBay のリンクでは ebay_search_query が ("New FD",nFD) にする

    def test_flags(self):
        self.assertIn("ジャンク", ba.detect_flags("ジャンク ペンタックス 67 部品取り"))
        self.assertIn("光学難あり", ba.detect_flags("レンズ 薄クモリあり"))


class ReadItems(unittest.TestCase):
    def test_bookmark_html(self):
        html_doc = (
            '<DL><DT><A HREF="https://page.auctions.yahoo.co.jp/jp/auction/k123456789">'
            "Nikon F3 - Yahoo!オークション</A>"
            '<DT><A HREF="https://example.com/">other</A>'
            '<DT><A HREF="https://auctions.yahoo.co.jp/jp/auction/k123456789">dup</A></DL>'
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.html"
            p.write_text(html_doc, encoding="utf-8")
            items = ba.read_items(p)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].auction_id, "k123456789")
        self.assertEqual(items[0].title, "Nikon F3")


class YahooParse(unittest.TestCase):
    def test_next_data(self):
        data = {"props": {"pageProps": {"item": {
            "auctionId": "x1", "title": "Nikon F3", "price": 10000, "taxinPrice": 11000,
            "bids": 4, "endTime": "2026-11-20T21:00:00+09:00", "bidorbuy": 30000}}}}
        page = (
            '<meta property="og:title" content="Nikon F3 - Yahoo!オークション">'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
            "<div>送料無料</div>"
        )
        info = ba.parse_yahoo_page(page)
        self.assertEqual(info["title"], "Nikon F3")
        self.assertEqual(info["current_price_jpy"], 11000)
        self.assertEqual(info["buy_now_jpy"], 30000)
        self.assertEqual(info["bids"], 4)
        self.assertEqual(info["domestic_shipping_jpy"], 0)


class Calculation(unittest.TestCase):
    def item(self, **kw):
        it = ba.Item(url="u", current_price_jpy=20000, domestic_shipping_jpy=1000, ship_size="medium")
        it.expected_price_usd = 255
        for k, v in kw.items():
            setattr(it, k, v)
        return it

    def test_max_bid_meets_criteria(self):
        it = self.item()
        c = ba.calculate(it, CFG, 150)
        # 上限入札額で落札しても利益・利益率の基準を満たすこと
        at_max = ba.calculate(self.item(current_price_jpy=c["max_bid_jpy"]), CFG, 150)
        self.assertGreaterEqual(at_max["profit_now_jpy"], CFG["judge"]["min_profit_jpy"])
        self.assertGreaterEqual(at_max["margin_now_pct"], CFG["judge"]["min_margin_pct"])
        # 損益分岐で利益ほぼ0
        be = ba.calculate(self.item(current_price_jpy=c["breakeven_bid_jpy"]), CFG, 150)
        self.assertTrue(0 <= be["profit_now_jpy"] < 1000)

    def test_store_bid_is_tax_excluded(self):
        # ストア: 上限入札額（税抜）で落札 → 支払いは×1.1。それでも基準の利益が残ること
        c = ba.calculate(self.item(store_tax=True), CFG, 150)
        paid_display = round(c["max_bid_jpy"] * 1.1)  # ヤフオクの表示（税込）
        at_max = ba.calculate(self.item(store_tax=True, current_price_jpy=paid_display), CFG, 150)
        self.assertGreaterEqual(at_max["profit_now_jpy"], CFG["judge"]["min_profit_jpy"] - 5)
        self.assertGreaterEqual(at_max["margin_now_pct"], CFG["judge"]["min_margin_pct"] - 0.1)
        # 個人出品より上限が約1割低い
        indiv = ba.calculate(self.item(), CFG, 150)
        self.assertLess(c["max_bid_jpy"], indiv["max_bid_jpy"] * 0.95)

    def test_increment(self):
        self.assertEqual(ba.round_down_to_increment(999), 990)
        self.assertEqual(ba.round_down_to_increment(4999), 4900)
        self.assertEqual(ba.round_down_to_increment(9999), 9750)
        self.assertEqual(ba.round_down_to_increment(21234), 21000)
        self.assertEqual(ba.round_down_to_increment(62191), 62000)

    def test_judge(self):
        it = self.item(current_price_jpy=1000)
        it.calc = ba.calculate(it, CFG, 150)
        self.assertEqual(ba.judge(it)[0], "◎")
        it = self.item(current_price_jpy=100000)
        it.calc = ba.calculate(it, CFG, 150)
        self.assertEqual(ba.judge(it)[0], "×")


class Sales(unittest.TestCase):
    def test_ebay_order_report(self):
        csv_text = (
            "Seller Hub Orders Report\n\n"
            "Sales Record Number,Item Number,Item Title,Sold For,Shipping And Handling,Sale Date\n"
            '1,123,"Nikon FM2 Black Body",$250.00,$0.00,Oct-01-26\n'
            '2,124,"Canon AE-1 Program",$150.00,$10.00,Oct-02-26\n'
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            p.write_text(csv_text, encoding="utf-8")
            sales = ba.read_sales(p)
        self.assertEqual(len(sales), 2)
        m = ba.match_sales("Nikon FM2 Black", sales)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["ebay_url"], "https://www.ebay.com/itm/123")
        self.assertEqual(sales[1]["_price"], 160.0)


class SearchUrls(unittest.TestCase):
    def test_find(self):
        text = (
            '<A HREF="https://auctions.yahoo.co.jp/search/search?p=Nikon+F3&amp;va=Nikon+F3">F3</A>\n'
            "https://auctions.yahoo.co.jp/jp/auction/x123456789\n"
            "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p=Nikon\n"
            "https://auctions.yahoo.co.jp/seller/abc123\n"
        )
        self.assertEqual(ba.find_search_urls(text), [
            "https://auctions.yahoo.co.jp/search/search?p=Nikon+F3&va=Nikon+F3",
            "https://auctions.yahoo.co.jp/seller/abc123",
        ])


class MatchingRules(unittest.TestCase):
    SALES = [
        {"title": t, "source": src, "_price": p, "_tokens": ba._tokens(t)}
        for t, src, p in [
            ("Contax TVS", "Chatwork販売実績", 399.99),
            ("Contax T2 Gold", "頒布会リスト", 1899.99),
            ("Pentax 645 strap", "自分の販売実績", 79.99),
            ("Pentax 645 本体", "自分の販売実績", 398.10),
            ("Nikon Ai-s 50mm f/1.8", "頒布会リスト", 179.99),
            ("Nikon Ai-s 50mm f/1.8", "自分の販売実績", 139.99),
        ]
    ]

    def titles(self, q):
        return [m["title"] for m in ba.match_sales(q, self.SALES)]

    def test_generation(self):
        self.assertEqual(self.titles("Contax TVS III"), [])
        self.assertEqual(self.titles("Contax TVS"), ["Contax TVS"])

    def test_variant_and_accessory(self):
        self.assertEqual(self.titles("Contax T2"), [])
        self.assertEqual(self.titles("Pentax 645 ボディ"), ["Pentax 645 本体"])

    def test_own_sales_first(self):
        m = ba.match_sales("ニコン Ai-S NIKKOR 50mm F1.8", self.SALES)
        self.assertEqual([x["_price"] for x in m], [139.99])


class UserscriptParity(unittest.TestCase):
    """ユーザースクリプト（JS）が Python と同じ判定をすることを確認（node が無ければスキップ）."""

    def test_same_verdicts(self):
        import json, shutil, subprocess
        import build_extension as bu
        if not shutil.which("node"):
            self.skipTest("node がありません")
        titles = ["【美品】ニコン Nikon FM2 ブラック ボディ", "キヤノン Canon FD 28mm f2・8 良品",
                  "7Artisans 50mm F1.2 マイクロフォーサーズ オリンパス", "Contax TVS III",
                  "OLYMPUS OM-SYSTEM G.ZUIKO AUTO-W 35mm F2.8", "Pentax 645 ボディキャップ"]
        sales = [{"title": t, "source": src, "_price": pr, "_tokens": ba._tokens(t)}
                 for t, src, pr in [("Olympus G.Zuiko 35mm f/2.8", "自分の販売実績", 119.99),
                                    ("Nikon FM2 Black", "自分の販売実績", 250.0),
                                    ("Canon FD 28mm f/2.8 S.C.", "自分の販売実績", 79.0),
                                    ("OLYMPUS ZUIKO AUTO-S 50mm 1.2", "頒布会リスト", 469.99),
                                    ("Contax TVS", "Chatwork販売実績", 399.99),
                                    ("Pentax 645 本体", "自分の販売実績", 398.1)]]
        items = [ba.Item(url=f"u{i}", auction_id=f"x{i}", title=t, current_price_jpy=5000,
                         domestic_shipping_jpy=800, prefilled=True) for i, t in enumerate(titles)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        py = [[ba.judge(it)[0], it.expected_price_usd, (it.calc or {}).get("max_bid_jpy")] for it in items]
        data = bu.build_data(CFG, 150.0)
        data["refs"] = [{"title": r["title"], "price": r["_price"], "tier": ba.source_tier(r), "source": r["source"],
                         "tokens": sorted(r["_tokens"]), "acc": bool(ba.ACCESSORY_RE.search(r["title"]))} for r in sales]
        data["kantei"] = []
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"const m=require({json.dumps(str(js))});const it={json.dumps(titles, ensure_ascii=False)}"
                    ".map((t,i)=>({id:'x'+i,title:t,price:5000,ship:800,bids:0,end:''}));m.evaluate(it,150);"
                    "console.log(JSON.stringify(it.map(i=>[i.mark,i.usd,i.calc?i.calc.maxBid:null])))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), py)


class DescriptionCheck(unittest.TestCase):
    """拡張機能の説明文チェック: 光学難・故障の記載を拾い、「〜なし」の打ち消しは拾わない（node が無ければスキップ）."""

    def test_desc_hits(self):
        import shutil, subprocess
        import build_extension as bu
        if not shutil.which("node"):
            self.skipTest("node がありません")
        cases = {
            "【 カビあり | 絞り羽根に油浮きあり | 現状渡し 】": ["カビ", "油浮き"],
            "レンズ光学系 内部にカビやチリのよごれが広く見られます。": ["カビ"],
            "カビ・クモリ・バルサム切れなし。綺麗な光学です。": [],
            "カビ、クモリ、キズ等ありません。\n絞り粘りもございません。": [],
            "カビは見られませんが、薄いクモリがあります。": ["クモリ"],
            "前玉に拭き傷少しあり。動作未確認のためジャンク扱い。": ["拭き傷", "動作未確認"],
            "外観並品。光学良好。": [],
            "（「ジャンク品」「動作未確認品」は対象外です。）": [],
            "【光学】 クモリ：ごくわずか カビ：なし": ["クモリ"],
            "カビ：なし クモリ：なし バルサム：なし": [],
            "初期不良の自然故障は返品対応させていただきます。": [],
            "【返品対象外】\n・微細なゴミやカビ、くもり、サビ、磨耗、劣化など。": [],
            "返品不可です。\n後玉に薄いカビがあります。": ["カビ"],
            "正常なご使用状態で故障した場合、無償修理させていただきます。": [],
            "シャッター故障のためジャンクです。": ["故障"],
            " ◆光学コンデション \n コーティング剥がれあります \n 写真を必ずご確認ください \n": ["コーティング剥がれ", "光学は写真で確認"],
            "◆光学コンディション\n綺麗です\n◆動作\n良好": [],
            "写真を必ずご確認ください。光学良好、カビクモリなし。": [],
        }
        data = bu.build_data(CFG, 150.0)
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"const m=require({json.dumps(str(js))});const c={json.dumps(list(cases), ensure_ascii=False)};"
                    "console.log(JSON.stringify(c.map(t=>m.descHits(t,'').map(h=>h.word))))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), list(cases.values()))
        # 商品の状態が「全体的に状態が悪い」なら拾う
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            out = subprocess.run(["node", "-e", f"const m=require({json.dumps(str(js))});"
                                  "console.log(m.descHits('良品です','全体的に状態が悪い').length)"],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "1")


class Variants(unittest.TestCase):
    """型の違い（Contax の MMG/MMJ/AEG/AEJ など）: 照合しない・eBay 検索語に型と除外語を入れる。Python と JS で同じ."""

    TITLES = ["CONTAX Carl Zeiss Planar T* 50mm F1.4 AE Japan", "コンタックス Distagon 35mm F1.4 MMJ",
              "Contax Sonnar 135mm F2.8 AEG", "Canon FD 50mm F1.4 S.S.C.", "Canon New FD 50mm F1.4",
              "Nikon Ai-s 50mm f1.8", "Hasselblad Planar CF 80mm F2.8", "Minolta MC Rokkor-PF 58mm F1.4",
              "Contax Planar 50mm F1.4 From JAPAN",
              # 出品者の管理番号付き（質みなみ・ストア等）。管理番号は検索語に入れない
              "Tj036 f.64 エフロクジュウヨン カメラバッグ ショルダーバッグ ブラック 中古",
              "IT120751 リコー デジタルカメラ GR IV R09010 RICOH 美品",
              "Ft644461 タムロン レンズ ニコン用 Fマウント SP AF Di 90mm 1:2.8",
              "NA77401 ソニー ビデオカメラ用 充電器 チャージャー AC-VQ800 sony 中古",
              "フジ FUJI GW690 III 90mm F3.5", "Nikon SB800 スピードライト"]

    def test_stock_code_removed(self):
        for t in self.TITLES[9:13]:
            for code in ("Tj036", "IT120751", "R09010", "Ft644461", "NA77401"):
                self.assertNotIn(code, ba.title_to_query(t))
                self.assertNotIn(code, ba.model_key(t)[0])
        self.assertEqual(ba.model_key(self.TITLES[10])[0], "Ricoh GR IV")
        self.assertIn("GW690", ba.title_to_query(self.TITLES[13]))  # 機種名は残す
        self.assertIn("SB800", ba.title_to_query(self.TITLES[14]))

    def test_conflict(self):
        q = ba._tokens("Contax Planar 50mm F1.4 AEJ")
        self.assertTrue(ba.variants_conflict(q, ba._tokens("Contax Planar 50mm f/1.4 MMJ")))
        self.assertTrue(ba.variants_conflict(q, ba._tokens("Contax Planar 50mm F1.4")))  # Contax は型の表記が無い実績も別物
        self.assertFalse(ba.variants_conflict(q, ba._tokens("Contax Planar 50mm F1.4 AE Japan")))
        n = ba._tokens("Nikon Ai-S 50mm F1.8")
        self.assertTrue(ba.variants_conflict(n, ba._tokens("Nikon Ai 50mm F1.8")))
        self.assertFalse(ba.variants_conflict(n, ba._tokens("Nikon 50mm F1.8")))  # Nikon は片方だけなら照合する
        self.assertNotIn("f1.4mmj", ba._tokens("Contax 35mm F1.4 MMJ"))

    def test_query(self):
        q = ba.ebay_search_query(ba.title_to_query(self.TITLES[1]), self.TITLES[1])
        self.assertEqual(q, "Contax Distagon 35mm F1.4 MMJ -MMG -AEG -AEJ")
        q = ba.ebay_search_query(ba.title_to_query(self.TITLES[8]), self.TITLES[8])
        self.assertNotIn("AEJ", q)  # 「From JAPAN」は AEJ ではない

    def test_js_same(self):
        import shutil, subprocess
        import build_extension as bu
        if not shutil.which("node"):
            self.skipTest("node がありません")
        py = [ba.ebay_search_query(ba.title_to_query(t), t) for t in self.TITLES]
        data = bu.build_data(CFG, 150.0)
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"const m=require({json.dumps(str(js))});const t={json.dumps(self.TITLES, ensure_ascii=False)};"
                    "console.log(JSON.stringify(t.map(x=>m.ebaySearchQuery(m.titleToQuery(x),x))))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), py)


class NoScraping(unittest.TestCase):
    """eBay の規約順守: eBay のページを自動取得するコードが無いこと."""

    def test_no_ebay_fetch(self):
        src = Path(ba.__file__).read_text(encoding="utf-8")
        self.assertNotIn("parse_ebay_prices", src)
        self.assertNotIn("auto_price_unpriced", src)
        js = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"fetch\([^)]*ebay")

    def test_mutar_is_adapter(self):
        self.assertIn("アダプター等", ba.detect_flags("CONTAX RTSムター？2X(中古品)"))

    def test_background_uses_official_api_only(self):
        """出品中の相場は公式 Browse API（api.ebay.com）だけ。www.ebay.com のページは取りに行かない."""
        bg = (Path(ba.__file__).parent / "extension_background.js").read_text(encoding="utf-8")
        hosts = set(__import__("re").findall(r"https://([a-z.]+)/", bg))
        # eBay で通信するのは公式 API だけ（tools.aucfan.com は入札予約のタブを開くだけで、読み取りはしない）
        self.assertEqual({h for h in hosts if "ebay" in h}, {"api.ebay.com"})
        self.assertTrue(hosts <= {"api.ebay.com", "tools.aucfan.com"}, hosts)


class ExtensionBrowse(unittest.TestCase):
    """eBay のキーがあるときだけ、出品中の相場を調べる裏方（background.js）が入る."""

    def _build(self, keys):
        import build_extension as bu
        with tempfile.TemporaryDirectory() as d:
            bu.write_chrome_extension("// test", Path(d), keys)
            man = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
            bg = (Path(d) / "background.js").read_text(encoding="utf-8") if (Path(d) / "background.js").exists() else None
        return man, bg

    def test_with_keys(self):
        man, bg = self._build(("APP-X", "CERT-Y"))
        self.assertEqual(man["host_permissions"], ["https://api.ebay.com/*"])
        self.assertIn('const APP_ID = "APP-X";', bg)
        self.assertNotIn("__CERT_ID__", bg)

    def test_without_keys(self):
        man, bg = self._build(None)
        self.assertNotIn("host_permissions", man)  # キーが無ければ eBay への通信権限は付けない
        # background.js は相場メモの自動保存（ダウンロード/aurora_price_memo.csv）のため常に入る。キーは入らない
        self.assertIn("const APP_ID = null;", bg)
        self.assertEqual(man["permissions"], ["downloads", "storage"])


if __name__ == "__main__":
    unittest.main()
