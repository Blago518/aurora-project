"""店舗アプリの入札の流れを、使う人として通す確かめ（2026-11-22 オーナー「使ってみて、ってあと何回言わないといけない？」）.

オーナーに受けた画面の指摘を、毎回自動で回るテストに変えておく所。指摘を受けたら store_flow_check.js に1つ足す。
データはテスト用に作った物だけ（data/ は使わない・コミットしない）。node と playwright が無い環境では飛ばす。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


def _has_playwright() -> bool:
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=HERE)
    return r.returncode == 0


def fixture() -> dict:
    """テスト用のデータ: 設定は config.toml から、相場と入札候補は作り物."""
    import appraise as ba
    import build_extension as bx
    cfg = ba.load_config(None)
    d = bx.build_data(cfg, float(cfg["fx"]["usd_jpy"]))
    d.update(kind="aurora-store", built="test", sweet=[], trivia=[], trusted=[], kantei=[], memoHist={},
             recs=[{"label": "Canon AE-1", "brand": "Canon", "kind": "camera", "cat": "film", "usd": 400, "maxBuy": 40000, "sales": 3, "profit": 5000, "basis": "販売実績"}])
    d.pop("inbox", None)
    ref = lambda t, usd: {"title": t, "price": usd, "tier": 1, "source": "販売実績", "acc": False, "tokens": sorted(ba._tokens(t))}  # 実際のデータと同じ形
    d["refs"] = [ref("Olympus XA1", 160), ref("Canon AE-1 Program", 180), ref("Nikon D750", 600),
                 ref("Olympus Zuiko 24mm F2", 260), ref("Olympus Zuiko 24mm F2.8", 90)]
    end = lambda h: (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()
    it = lambda i, title, price, mx, pf, check, **k: dict(
        {"id": i, "url": f"https://auctions.yahoo.co.jp/jp/auction/{i}", "title": title, "mark": "◎", "tent": False, "check": check,
         "price": price, "bids": 2, "end": end(5), "ship": 1400, "usd": 200, "basis": "販売実績", "maxBid": mx, "profit": pf,
         "taxNote": "", "flags": []}, **k)
    items = [
        it("x1", "【美品】Olympus XA1 35mm f/4 Point & Shoot Film Camera オリンパス フィルムカメラ #C-128", 11800, 21500, 13288, True),
        it("x2", "★特上美品★OLYMPUS XA1 A9M D.ZUIKO 35mm F4 オリンパス XA1 Point & Shoot フィルムカメラ 動作確認済 #101347", 10300, 21000, 14488, True),
        it("x3", "OLYMPUS XA1 + A9M フラッシュ セット", 4000, 21000, 9000, True),
        it("c1", "Canon AE-1 Program + FD 50mm F1.8 セット", 9000, 21000, 9000, True),
        it("c2", "Canon AE-1 Program ボディ 美品", 6000, 21000, 9000, True),
        it("d1", "★ニコン Nikon D750 ボディ★ W0988＃5829", 35000, 45500, 16390, False),
        it("z1", "ZENZA BRONICA MC 250mm f5・6　良品", 8000, 19500, 14764, False),
        # 2026-11-22 オーナー: Sold・Now に管理番号（E66）が入っていた／F2.8 の別物を「-2.8」で外したい
        it("o1", "OLYMPUS オリンパス ZUIKO AUTO-W 24mm/F2 OMマウント 単焦点 広角レンズ 大口径 前後キャップ/レンズフード付 E66", 15000, 25000, 9000, False),
        it("s1", "ZENZA BRONICA ZENZANON 250mm/f5.6 レンズ レンズ", 11000, 20000, 14408, False,
           taxNote="ストア: このまま入札。支払いは税込22,000円"),
    ]
    d["bids"] = {"built": "2026-11-22 07:21", "items": items, "reserve": [{"id": i["id"], "kind": "即決"} for i in items[5:]]}
    return d


HAS_PW = _has_playwright()
if not HAS_PW:   # 黙って合格にしない（2026-11-22 オーキド）。Mac には playwright が無いので、回るのはクラウドの作業のときだけ
    print("\n※ 店舗アプリの通しテスト（test_store_flow）は飛ばしました: node と playwright がありません", file=sys.stderr)


@unittest.skipUnless(HAS_PW, "node と playwright がありません")
class StoreBidFlow(unittest.TestCase):
    def test_bid_flow_as_user(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "store_data.json"
            p.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")
            r = subprocess.run(["node", str(HERE / "store_flow_check.js"), str(p), str(ROOT / "store" / "index.html")],
                               capture_output=True, text=True, cwd=HERE, timeout=300)
            lines = [x for x in r.stdout.splitlines() if x.startswith("{")]
            self.assertTrue(lines, r.stdout + r.stderr)
            problems = json.loads(lines[-1])["problems"]
            self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
