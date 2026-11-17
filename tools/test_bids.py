"""入札の記録と結果の集計のテスト. python -m unittest discover -s tools で実行."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import appraise as ba
import bid_results as br

CFG = ba.load_config(None)


def page(status, price, bids, end="2026-11-10T21:00:00+09:00", taxin=None):
    item = {"status": status, "price": price, "bids": bids, "endTime": end}
    if taxin:
        item["taxinPrice"] = taxin
    return ('<script id="__NEXT_DATA__" type="application/json">' +
            json.dumps({"props": {"pageProps": {"initialState": {"item": {"detail": {"item": item}}}}}}) + "</script>")


ROW = {"auction_id": "a1", "title": "Nikon FM2", "max_bid": "20000", "pay_max": "20000", "usd": "260", "profit_at_max": "3500",
       "end": "2026-11-10T21:00:00+09:00", "trusted": "0", "tentative": "0", "store_tax": "0", "ship": "800",
       "bid_at": "2026-11-10T10:00:00Z"}


class Judge(unittest.TestCase):
    def test_won_lost_pending(self):
        won = br.judge_result(dict(ROW), br._item_state(page("closed", 18000, 5)), CFG, 150.0)
        self.assertEqual(won["result"], "落札(見込み)")
        self.assertGreater(won["profit_final"], 0)
        lost = br.judge_result(dict(ROW), br._item_state(page("closed", 23000, 9)), CFG, 150.0)
        self.assertEqual((lost["result"], lost["final_price"]), ("負け", 23000))
        future = dict(ROW, end="2099-01-01T00:00:00+09:00")
        self.assertIsNone(br.judge_result(future, br._item_state(page("open", 10000, 2, "2099-01-01T00:00:00+09:00")), CFG, 150.0))


class Flow(unittest.TestCase):
    def test_import_check_summary(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            dl, log = d / "dl", d / "bid_log.csv"
            dl.mkdir()
            rows = [dict(ROW), dict(ROW, auction_id="a2", max_bid="5000"), dict(ROW, auction_id="a3", max_bid="9000")]
            head = ",".join(br.FIELDS[:12])
            (dl / "aurora_bids.csv").write_text("﻿" + head + "\n" + "\n".join(
                ",".join(f'"{r.get(k, "")}"' for k in br.FIELDS[:12]) for r in rows), encoding="utf-8")
            with mock.patch.object(br, "LOG", log):
                self.assertEqual(br.import_extension_bids(dl, log), 3)
                pages = {"a1": page("closed", 18000, 4), "a2": page("closed", 5400, 7), "a3": page("closed", 20000, 9)}
                n = br.check_results(CFG, 150.0, delay=0, fetch=lambda u: pages[u.rsplit("/", 1)[1]])
                self.assertEqual(n, 3)
                s = br.summarize(br._read(log), None)
        self.assertEqual((s["bids"], s["won"], s["lost"], s["win_rate"]), (3, 1, 2, 33))
        self.assertEqual(s["near_miss"], 1)  # a2: 5,400 / 5,000 = 1.08 倍の僅差

    def test_cancel_before_result(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            dl, log = d / "dl", d / "bid_log.csv"
            dl.mkdir()
            br._write([dict(ROW), dict(ROW, auction_id="old", result="負け")], log)
            (dl / "aurora_bids.csv").write_text(",".join(br.FIELDS[:12]) + "\n", encoding="utf-8")  # 拡張機能で全部取り消した
            br.import_extension_bids(dl, log)
            self.assertEqual([r["auction_id"] for r in br._read(log)], ["old"])  # 結果が出たものは残る

    def test_advice(self):
        tips = br.advice({"bids": 3, "done": 0, "won": 0, "lost": 0, "win_rate": None, "near_miss": 0, "lost_median_ratio": None})
        self.assertTrue(any("入札がまだ少ない" in t for t in tips))


class Aucfan(unittest.TestCase):
    def test_import_snipes(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            dl, log = d / "dl", d / "bid_log.csv"
            dl.mkdir()
            br._write([dict(ROW)], log)
            (dl / "aurora_snipes.csv").write_text(
                'auction_id,title,max_bid,current,seen_at\n"a1","Nikon FM2","21000","15000","2026-11-15T01:00:00Z"\n'
                '"x9","SONY FE 35mm F1.8","12800","39000","2026-11-15T01:00:00Z"\n', encoding="utf-8")
            self.assertEqual(br.import_aucfan_snipes(dl, log), 1)
            rows = {r["auction_id"]: r for r in br._read(log)}
        self.assertEqual(rows["a1"]["max_bid"], "21000")  # オークファンで変えた入札設定に合わせる
        self.assertEqual(rows["x9"]["max_bid"], "12800")


if __name__ == "__main__":
    unittest.main()


class WinOdds(unittest.TestCase):
    """落札見込み: Python（bid_results.win_odds）と拡張機能（winOdds）が同じ結果になる."""
    ROWS = ([{"max_bid": "8000", "store_tax": "0", "result": "落札(見込み)"}] * 3
            + [{"max_bid": "8000", "store_tax": "0", "result": "負け"}]
            + [{"max_bid": "20000", "store_tax": "1", "result": "負け"}] * 2
            + [{"max_bid": "25000", "store_tax": "0", "result": "落札(見込み)"}]
            + [{"max_bid": "50000", "store_tax": "1", "result": ""}])  # 結果待ちは数えない
    CASES = [(9000, False), (9000, True), (15000, True), (15000, False), (40000, True)]

    def test_python(self):
        import bid_results as br
        st = br.win_stats(self.ROWS)
        self.assertEqual(br.win_odds(st, 9000, False)["label"], "高")   # 個人1万円未満 3/4
        self.assertEqual(br.win_odds(st, 9000, True)["key"], "1万円未満|*")  # ストアは記録なし → 価格帯だけ
        self.assertEqual(br.win_odds(st, 15000, True)["label"], "中")   # 1〜3万円 全体 1/3（30%以上60%未満）
        self.assertIsNone(br.win_odds(st, 40000, True))                # 結果待ちだけ → 出さない

    def test_js_same(self):
        import json
        import shutil
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import bid_results as br
        import build_extension as bx
        from test_trust import run_js
        st = br.win_stats(self.ROWS)
        data = bx.build_data(CFG, 150.0)
        data.update(refs=[], kantei=[], winStats=st)
        out = run_js(data, f"console.log(JSON.stringify({json.dumps(self.CASES)}.map(([b,s])=>m.winOdds(b,s))))")
        self.assertEqual(out, [br.win_odds(st, b, s) for b, s in self.CASES])


class PastBids(unittest.TestCase):
    """以前の入札分析シートの入札履歴 → 落札見込みの元データ（空欄・未入札は使わない）."""

    def test_rows_and_stats(self):
        import tempfile
        import ledger_sync as ls
        text = ("オークションID,商品名,自分の入札額(円),最終落札価格(円),落札結果,入札日,eBay想定売値($)\n"
                "a1,Nikon A,\"5,000\",4500,落札成功,46218,59.99\n"
                "a2,Canon B,5000,7000,落札失敗,2026/08/01,40\n"
                "a3,Canon C,,7000,落札失敗,46218,40\n"
                "a4,Canon D,3000,,未入札,46218,40\n"
                "a2,Canon B,6000,7000,落札失敗,2026/08/02,40\n")
        rows = ls.past_bids_rows(text)
        self.assertEqual([r["auction_id"] for r in rows], ["a1", "a2"])
        self.assertEqual(rows[0]["result"], "落札")
        self.assertEqual(rows[0]["bid_at"], "2026-07-15")
        self.assertEqual(rows[1]["max_bid"], 6000)
        with tempfile.TemporaryDirectory() as d:
            hist = Path(d) / "h.csv"
            hist.write_text("auction_id,max_bid,result,store_tax\na1,5000,落札,\na2,6000,負け,\na9,5000,負け,\n", encoding="utf-8")
            log = Path(d) / "log.csv"
            log.write_text("auction_id,max_bid,result,store_tax\na9,5000,落札,1\n", encoding="utf-8")
            st = br.load_win_stats(log, hist)
        self.assertEqual(st["1万円未満|*"], [2, 3])      # a9 は今の記録（落札）を優先
        self.assertEqual(st["1万円未満|1"], [1, 1])      # 以前の履歴（ストアか不明）は価格帯だけに数える
        self.assertNotIn("1万円未満|0", st)
