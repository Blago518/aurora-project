"""店舗リサーチアプリ（store/）のテスト. python -m unittest discover -s tools で実行."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import appraise as ba
import build_store as bs

CFG = ba.load_config(None)
SAMPLES = Path(__file__).resolve().parent / "samples"


class CoreSync(unittest.TestCase):
    def test_core_js_is_up_to_date(self):
        """store/core.js が拡張機能の判定ロジックのコピーのままか（ずれたら build_store.py を実行）."""
        self.assertEqual(bs.CORE_JS.read_text(encoding="utf-8"), bs.core_js())

    def test_no_data_in_public_files(self):
        """公開する store/ に相場データが入っていないこと."""
        for p in bs.CORE_JS.parent.iterdir():
            if p.suffix in (".js", ".html", ".webmanifest"):
                self.assertNotIn('"refs":[', p.read_text(encoding="utf-8").replace(" ", ""), p.name)


class RecKey(unittest.TestCase):
    def test_mount_separates_lenses(self):
        fd = bs.rec_key("Canon New FD 50mm f/1.4 Lens")
        self.assertEqual(fd[1:], ("canon newfd 50mm f1.4", "lens"))
        self.assertEqual(bs.rec_key("Canon NFD 50mm F1.4")[1], fd[1])
        self.assertNotEqual(bs.rec_key("Canon EF 50mm F1.4 USM")[1], fd[1])

    def test_camera(self):
        self.assertEqual(bs.rec_key("Olympus OM-1 35mm SLR")[1:], ("olympus om-1", "camera"))
        self.assertEqual(bs.rec_key("Nikon FM2 Black Body Exc+++")[0], "Nikon FM2")
        self.assertNotEqual(bs.rec_key("Canon AE-1 Program")[1], bs.rec_key("Canon AE-1")[1])

    def test_unknown_brand(self):
        self.assertIsNone(bs.rec_key("Some Camera"))


class Recommendations(unittest.TestCase):
    def test_sample(self):
        recs = bs.recommendations(ba.read_sales(SAMPLES / "sales_history.csv"), CFG, 150.0)
        self.assertEqual([r["label"] for r in recs], ["Nikon FM2", "Canon NewFD 50mm F1.4"])
        fm2 = recs[0]
        self.assertEqual((fm2["sales"], fm2["usd"], fm2["kind"]), (3, 255.0, "camera"))
        # 上限額で買えば基準を満たす
        c = bs.store_calc(fm2["usd"], CFG, 150.0, fm2["maxBuy"])
        self.assertGreaterEqual(c["profit_now_jpy"], CFG["judge"]["min_profit_jpy"])
        self.assertGreaterEqual(c["margin_now_pct"], CFG["judge"]["min_margin_pct"])
        # ジャンク販売だけの機種（Pentax 67 For Parts）はおすすめに出さない
        self.assertNotIn("Pentax 67", [r["label"] for r in recs])


class StoreParity(unittest.TestCase):
    """スマホアプリ（core.js）の上限額・利益が Python と一致すること."""

    def test_same_as_python(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        cases = [(255.0, 18000), (81.5, 1500), (120.0, 30000)]
        py = []
        for u, price in cases:
            c = bs.store_calc(u, CFG, 150.0, price)
            py.append([c["max_bid_jpy"], c["breakeven_bid_jpy"], c["profit_now_jpy"]])
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const D=require({json.dumps(str(dp))});"
                    f"const c=makeCore(D);console.log(JSON.stringify({json.dumps(cases)}.map(([u,p])=>"
                    "{const r=c.calculate({price:p,ship:0,tax:false},u,150);return [r.maxBid,r.breakeven,r.profit];})))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), py)


class MemoAndCondition(unittest.TestCase):
    """手で直した相場（相場メモ）が過去データより優先されること（Python と JS で同じ）と、状態の読み取り."""

    REFS = [("Olympus Lens 35mm f/2", "頒布会リスト(A 美品)", 229.99),
            ("Olympus 35mm F2", "相場メモ", 139.99),
            ("Nikon FM2 Black Near Mint", "自分の販売実績", 265.0)]
    TITLES = ["オリンパス OM ZUIKO 35mm F2 美品", "オリンパス ZUIKO 35mm F2.8", "ニコン Nikon FM2"]

    def test_python_prefers_memo(self):
        sales = [{"title": t, "source": s, "_price": p, "_tokens": ba._tokens(t)} for t, s, p in self.REFS]
        items = [ba.Item(url=f"u{i}", title=t, current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True)
                 for i, t in enumerate(self.TITLES)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        self.assertEqual([it.expected_price_usd for it in items], [139.99, None, 265.0])

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        data["refs"] = [{"title": t, "price": p, "tier": ba.source_tier({"source": s}), "source": s,
                         "tokens": sorted(ba._tokens(t)), "acc": False} for t, s, p in self.REFS]
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const D=require({json.dumps(str(dp))});"
                    "const c=makeCore(D);const mp=D.refs.filter(c.isMemo),op=D.refs.filter(r=>!c.isMemo(r));"
                    f"console.log(JSON.stringify({json.dumps(self.TITLES, ensure_ascii=False)}.map(t=>{{"
                    "const m=c.matchPreferMemo(c.titleToQuery(t)+' '+t,mp,op);return m.length?c.median(m.map(r=>r.price)):null;})))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [139.99, None, 265.0])

    def test_condition(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        cases = {"Nikon F3 TOP MINT": "TOP MINT", "Canon AE-1 [N MINT]": "Near MINT", "Leica M6 Exc+5": "Exc+5",
                 "Pentax 67 Exc++++": "Exc++++", "Mamiya 7 MINT-": "MINT", "Olympus OM-1 For Parts": "ジャンク",
                 "Nikon FE": ""}
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    f"console.log(JSON.stringify({json.dumps(list(cases), ensure_ascii=False)}.map(t=>c.conditionOf({{title:t}}))"
                    ".concat([c.conditionOf({title:'Olympus 35mm',source:'頒布会リスト(AB 小傷)'}),c.conditionOf({title:'Olympus 35mm',source:'頒布会リスト(Near Mint 小傷あり)'}),c.conditionOf({title:'Nikon FM2 MINT',source:'頒布会リスト(Exc+5)'})])))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), list(cases.values()) + ["AB", "Near MINT", "Exc+5"])

class MemoAutoSave(unittest.TestCase):
    """拡張機能の相場メモ（ダウンロード/aurora_price_memo.csv）を data/price_memo.csv に取り込む."""

    def test_import(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "price_memo.csv"
            dl.mkdir()
            dest.write_text("title,sold_price_usd,source,memo\nOlympus F2 35mm,180,相場メモ,前\nLeica M6,1500,Product Research,手入力\n",
                            encoding="utf-8-sig")
            (dl / "aurora_price_memo.csv").write_text(
                '\ufefftitle,sold_price_usd,source\n"Olympus F2 35mm",139.99,相場メモ\n"Nikon ""FM2""",230,相場メモ\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (1, 1))
            rows = {r["title"]: r for r in ba.read_sales(dest)}
            self.assertEqual(rows["Olympus F2 35mm"]["_price"], 139.99)
            self.assertEqual(rows['Nikon "FM2"']["source"], "相場メモ")
            self.assertEqual(rows["Leica M6"]["_price"], 1500)  # 相場メモ以外はそのまま
            self.assertEqual(ba.import_extension_memo(dl, dest), (0, 0))  # 2回目は変化なし
            self.assertEqual(ba.import_extension_memo(Path(d) / "none", dest), (0, 0))

    def test_manifest_has_downloads(self):
        import build_extension as bx
        with tempfile.TemporaryDirectory() as d:
            bx.write_chrome_extension("//", Path(d))
            m = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
            bg = (Path(d) / "background.js").read_text(encoding="utf-8")
        self.assertIn("downloads", m["permissions"])
        self.assertNotIn("host_permissions", m)  # キーが無ければ eBay への通信権限は付けない
        self.assertIn("const APP_ID = null;", bg)
        self.assertIn("aurora_price_memo.csv", bg)


class AdaptallFlag(unittest.TestCase):
    """タムロンの交換マウント（アダプトール）だけの出品は付属品。焦点距離があるレンズ本体は除かない（Python と JS で同じ）."""
    CASES = {"20-47 オリンパスOMマウント タムロン アダプトール マウント": True,
             "Tamron Adaptall-2 mount Canon FD": True,
             "TAMRON SP MACRO 90mm F2.8 72B OLYMPUS アダプトール": False,
             "Tamron SP 70-210mm F3.5 Adaptall-2": False}

    def test_python(self):
        for t, adapter in self.CASES.items():
            self.assertEqual("アダプター等" in ba.detect_flags(t), adapter, t)

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    f"console.log(JSON.stringify({json.dumps(list(self.CASES), ensure_ascii=False)}.map(t=>c.detectFlags(t).includes('アダプター等'))))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), list(self.CASES.values()))


class MemoCondition(unittest.TestCase):
    """［直す］［登録］で入れた状態（相場メモ(MINT)）が取り込まれ、表示される."""

    def test_import_keeps_condition(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "price_memo.csv"
            dl.mkdir()
            (dl / "aurora_price_memo.csv").write_text(
                'title,sold_price_usd,source\n"Olympus F2 35mm",139.99,"相場メモ(Near MINT)"\n"Nikon FM2",230,相場メモ\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (2, 0))
            rows = {r["title"]: r for r in ba.read_sales(dest)}
            self.assertEqual(rows["Olympus F2 35mm"]["source"], "相場メモ(Near MINT)")
            self.assertTrue(ba.is_memo(rows["Olympus F2 35mm"]))  # 状態付きでも相場メモとして優先される
            (dl / "aurora_price_memo.csv").write_text('title,sold_price_usd,source\n"Nikon FM2",230,"相場メモ(MINT)"\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (0, 1))  # 状態だけ変えても更新

    def test_js_condition(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    "console.log(JSON.stringify(['相場メモ(Near MINT)','相場メモ(Exc+++)','相場メモ(ジャンク)','相場メモ']"
                    ".map(s=>[c.conditionOf({title:'Olympus F2 35mm',source:s}),c.isMemo({source:s})])))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [["Near MINT", True], ["Exc+++", True], ["ジャンク", True], ["", True]])


class ConditionAdjust(unittest.TestCase):
    """状態の補正（1ランク $10）: 過去データの状態とこの出品の状態の差で販売価格を動かす。Python と JS で同じ."""

    SALES = [("Olympus 35mm F2", "相場メモ(Near MINT)", 139.99),        # Near MINT = 7
             ("[MINT] Nikon FM2 Black", "Chatwork販売実績(キタムラ)", 250.0),  # MINT = 9
             ("Nikon FM2 Black Exc+++", "自分の販売実績", 210.0),             # Exc+3 = 4
             ("Canon AE-1 Program", "自分の販売実績", 120.0)]                 # 状態なし＝補正しない
    TITLES = {"オリンパス OM ZUIKO 35mm F2 美品": 139.99,           # 美品=Near MINT → 差0
              "オリンパス OM ZUIKO 35mm F2 極上": 159.99,           # 極上=MINT → +2（間に Almost MINT）
              "オリンパス OM ZUIKO 35mm F2 動作品": 129.99,         # 動作品=Exc+5 → -1
              "オリンパス OM ZUIKO 35mm F2": 139.99,               # 表記なし=Near MINT
              "オリンパス OM ZUIKO 35mm F2 MINT": 139.99,          # 英語の格付けは使わない
              "ニコン Nikon FM2 ブラック 未使用に近い": 270.0,        # TOP MINT=10: 自分の実績 Exc+3($210)+6ランク（Chatwork より優先）
              "ニコン Nikon FM2 ブラック ジャンク": 210.0,           # ジャンクは状態の補正をせず、割引もしない（junk_price_factor = 1.0）
              "キヤノン Canon AE-1 Program 美品": 120.0}

    def _python(self):
        sales = [{"title": t, "source": s, "_price": pr, "_tokens": ba._tokens(t)} for t, s, pr in self.SALES]
        items = [ba.Item(url=f"u{i}", title=t, current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True)
                 for i, t in enumerate(self.TITLES)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        return [it.expected_price_usd for it in items]

    def test_python(self):
        self.assertEqual(self._python(), list(self.TITLES.values()))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        data["refs"] = [{"title": t, "price": pr, "tier": ba.source_tier({"source": s}), "source": s,
                         "tokens": sorted(ba._tokens(t)), "acc": False} for t, s, pr in self.SALES]
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};const m=require({json.dumps(str(js))});"
                    f"const it={json.dumps(list(self.TITLES), ensure_ascii=False)}.map((t,i)=>({{id:'x'+i,title:t,price:5000,ship:800,bids:0,end:''}}));"
                    "m.evaluate(it,150);console.log(JSON.stringify(it.map(i=>i.usd)))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), self._python())


class BidCandidates(unittest.TestCase):
    """毎朝の入札リスト（bid_report_*.csv）→ スマホの「入札」タブ."""

    def _write(self, d: Path) -> None:
        import csv as _csv
        rows = [
            {"判定": "◎", "枠": "安心", "タイトル": "Nikon FM2", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a1",
             "現在価格": "10000", "入札数": "2", "終了": "2026-11-17T21:00:00+09:00", "国内送料": "910", "想定販売価格USD": "260.0",
             "上限入札額": "18000", "利益(現在価格)": "9000", "利益率%": "25.1", "注意": "動作品", "説明文": "説明文に光学難・故障の記載なし"},
            {"判定": "×", "仮判定": "○", "タイトル": "Sigma 90mm", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a2",
             "現在価格": "2300", "仮の上限入札額": "18000", "仮の想定USD": "208", "仮のsoldURL": "S", "終了": "2026-11-16T10:00:00+09:00"},
            {"判定": "◎", "タイトル": "フード", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a3", "注意": "付属品のみ?"},
            {"判定": "×", "タイトル": "x", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a4"},
            {"判定": "○", "根拠の強さ": "確認してから", "タイトル": "Canon FD 50mm", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a5",
             "終了": "2026-11-16T09:00:00+09:00"},
        ]
        keys = sorted({k for r in rows for k in r})
        with (d / "bid_report_20261115_0703.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, keys)
            w.writeheader()
            w.writerows(rows)

    def test_pick(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(Path(d))
            b = bs.bid_candidates(Path(d))
        self.assertEqual([(i["id"], i["mark"], i["tent"], i["check"]) for i in b["items"]],
                         [("a1", "◎", False, False), ("a5", "○", False, True), ("a2", "○", True, True)])
        a1, _, a2 = b["items"]
        self.assertEqual((a1["maxBid"], a1["ship"], a1["trusted"], a1["flags"]), (18000, 910, True, []))
        self.assertEqual((a2["maxBid"], a2["usd"], a2["sold"]), (18000, 208, "S"))

    def test_old_list_not_sent(self):
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as d:
            self._write(Path(d))
            self.assertIsNone(bs.bid_candidates(Path(d), now=datetime.now() + timedelta(days=3)))
            self.assertIsNone(bs.bid_candidates(Path(d) / "none"))


if __name__ == "__main__":
    unittest.main()


class ShopList(unittest.TestCase):
    """お店タブの店リスト（store/shops.js）の形が正しいこと."""

    def test_shops_js(self):
        text = (bs.CORE_JS.parent / "shops.js").read_text(encoding="utf-8")
        data = json.loads(text.split("var SHOPS = ", 1)[1].rstrip().rstrip(";"))
        self.assertEqual([a["name"] for a in data["areas"]], ["池袋", "新宿", "吉祥寺", "横浜"])
        for a in data["areas"]:
            self.assertTrue(a["shops"])
            for sh in a["shops"]:
                for k in ("name", "q", "addr", "floor", "ll", "hours", "stock", "min", "url"):
                    self.assertIn(k, sh, sh["name"])
                lat, lng = map(float, sh["ll"].split(","))
                self.assertTrue(35.3 < lat < 35.8 and 139.4 < lng < 139.8, sh["name"])
