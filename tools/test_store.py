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
        self.assertEqual(fd[1:], ("canon new fd 50mm f1.4", "lens"))
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
        self.assertEqual([r["label"] for r in recs], ["Nikon FM2", "Canon New FD 50mm F1.4"])
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


if __name__ == "__main__":
    unittest.main()
