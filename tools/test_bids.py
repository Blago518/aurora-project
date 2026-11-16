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
