"""落札相場の記録（yahoo_results.py）と、入札の結果を古物台帳で決めるテスト. python -m unittest discover -s tools で実行."""
import csv
import io
import json
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import appraise as ba
import bid_results as br
import yahoo_results as yr

CFG = ba.load_config(None)
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)  # 日本時間 12:00


def page(status, price, bids, end, init=None):
    item = {"status": status, "price": price, "bids": bids, "endTime": end}
    if init is not None:
        item["initPrice"] = init
    return ('<script id="__NEXT_DATA__" type="application/json">' +
            json.dumps({"props": {"pageProps": {"initialState": {"item": {"detail": {"item": item}}}}}}) + "</script>")


def report_csv(path: Path, rows: list[dict]) -> None:
    keys = ["判定", "根拠の強さ", "仮判定", "仮の上限入札額", "タイトル", "ヤフオクURL", "終了", "上限入札額", "入札額の税"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})


class Watch(unittest.TestCase):
    def test_report_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bid_report_20261119_0700.csv"
            u = "https://auctions.yahoo.co.jp/jp/auction/"
            report_csv(p, [
                {"判定": "◎", "根拠の強さ": "確認不要", "タイトル": "Nikon FM2 ボディ", "ヤフオクURL": u + "x100000001",
                 "終了": "2026-11-19T21:00:00+09:00", "上限入札額": "20000", "入札額の税": "個人: このまま入札（税なし）"},
                {"判定": "○", "根拠の強さ": "確認してから", "タイトル": "Canon AE-1", "ヤフオクURL": u + "x100000002",
                 "上限入札額": "8000", "入札額の税": "ストア: このまま入札。支払いは税込8,800円"},
                {"判定": "要確認", "仮判定": "◎", "仮の上限入札額": "12000", "タイトル": "Pentax LX", "ヤフオクURL": u + "x100000003"},
                {"判定": "×", "タイトル": "Olympus OM-1", "ヤフオクURL": u + "x100000004"},
                {"判定": "要確認", "タイトル": "Minolta X-700", "ヤフオクURL": u + "x100000005"},
            ])
            rows = yr.report_rows(p)
        self.assertEqual([(r["auction_id"], r["mark"], r["max_bid"], r["store_tax"]) for r in rows],
                         [("x100000001", "◎", "20000", "0"), ("x100000002", "確認してから○", "8000", "1"),
                          ("x100000003", "仮◎", "12000", "0")])

    def test_add_watch_and_seen_file(self):
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d)
            (dl / "aurora_seen.csv").write_text(
                "﻿auction_id,title,end,mark,max_bid,store_tax,seen_at\n"
                '"x1","Nikon FM2 ボディ","2026-11-19T21:00:00+09:00","◎","20000","0","2026-11-18T10:00:00Z"\n'
                '"x2","Canon AE-1","2026-11-20T21:00:00+09:00","仮○","8000","1","2026-11-18T10:00:00Z"\n', encoding="utf-8")
            new = yr.seen_rows(dl)
        rows, added = yr.add_watch(new, [], NOW)
        self.assertEqual(added, 2)
        self.assertEqual(rows[0]["model_key"], ba.model_key("Nikon FM2 ボディ")[1])
        self.assertEqual(rows[0]["seen_from"], "extension")
        # 朝のリストにも出た → 1行のまま、どこから見たかを足す。自動延長で終了時刻が変わったら直す
        rows, added = yr.add_watch([{"auction_id": "x1", "title": "Nikon FM2 ボディ", "end": "2026-11-19T21:05:00+09:00",
                                     "mark": "◎", "seen_from": "morning"}], rows, NOW)
        self.assertEqual((added, len(rows), rows[0]["seen_from"], rows[0]["end"]),
                         (0, 2, "extension+morning", "2026-11-19T21:05:00+09:00"))


class Review(unittest.TestCase):
    """答え合わせ（古物台帳「Mac_答え合わせ」）: 上限額以下で落札された候補と、入札の記録・予約リストに出ていたか（値は作り物）."""

    def test_review(self):
        now = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
        base = {"checked_at": "2026-11-23 09:00", "status": "落札された", "bids": "3", "store_tax": "0", "model": "Nikon F3"}
        rows = [
            dict(base, auction_id="x1000000001", title="Nikon F3 ボディ", end="2026-11-22T21:00:00+09:00", final_price="23000", max_bid="39500", mark="◎"),
            dict(base, auction_id="x1000000002", title="Nikon F3 ジャンク", end="2026-11-22T22:00:00+09:00", final_price="9000", max_bid="39500", mark="○"),
            dict(base, auction_id="x1000000003", title="Nikon F3用 フォーカシングスクリーン", end="2026-11-21T22:00:00+09:00", final_price="2300", max_bid="36500", mark="◎"),
            dict(base, auction_id="x1000000004", title="Nikon F3 HP", end="2026-11-22T22:00:00+09:00", final_price="50000", max_bid="39500", mark="◎"),  # 上限より上 → 出さない
            dict(base, auction_id="x1000000005", title="Nikon F3 P", end="2026-11-22T22:00:00+09:00", final_price="20000", max_bid="39500", mark="仮◎"),  # 仮の相場 → 出さない
            dict(base, auction_id="x1000000006", title="Nikon Z 20mm F1.8", end="2026-11-23T21:00:00+09:00", final_price="82900", max_bid="88000", mark="入札した"),
            dict(base, auction_id="x1000000007", title="Nikon F3 古い", end="2026-10-01T21:00:00+09:00", final_price="20000", max_bid="39500", mark="◎"),  # 期間の外
        ]
        bids = [{"auction_id": "x1000000006", "my_bid": "27800", "bid_fail": "900010010"}]
        body = yr.review_csv(rows, bids, {"x1000000001": "予約"}, {"20261122"}, now=now)
        got = {r["ヤフオク"].rsplit("/", 1)[-1]: r for r in csv.DictReader(io.StringIO(body))}
        self.assertEqual(set(got), {"x1000000001", "x1000000002", "x1000000003", "x1000000006"})
        self.assertEqual((got["x1000000001"]["予約リスト"], got["x1000000001"]["入札の記録"], got["x1000000001"]["上限との差"]), ("予約", "なし", "16500"))
        self.assertEqual(got["x1000000002"]["予約リスト"], "出ていない")  # その日のリストは残っていて、載っていない
        self.assertIn("ジャンク", got["x1000000002"]["商品名で分かる注意"])   # ジャンクの語は外さず、印を付ける
        self.assertEqual(got["x1000000003"]["予約リスト"], "記録なし")    # 11/20・11/21 のリストは残っていない
        self.assertIn("付属品のみ?", got["x1000000003"]["商品名で分かる注意"])
        self.assertEqual((got["x1000000006"]["入札の記録"], got["x1000000006"]["入れた額"], got["x1000000006"]["オークファンの失敗コード"]),
                         ("あり", "27800", "900010010"))

    def test_reserve_history(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "reserve_list_20261122_0946.csv").write_text(
                "順,区分,判定,ヤフオク\n1,即決（今すぐ買える）,◎,https://auctions.yahoo.co.jp/jp/auction/x1000000001\n"
                "2,予約,◎,https://auctions.yahoo.co.jp/jp/auction/X1000000002\n", encoding="utf-8-sig")
            shown, days = yr.reserve_history(d)
        self.assertEqual(shown, {"x1000000001": "即決", "x1000000002": "予約"})
        self.assertEqual(days, {"20261122"})


class Check(unittest.TestCase):
    def rows(self):
        mk = lambda aid, end: {k: "" for k in yr.FIELDS} | {"auction_id": aid, "title": "Nikon FM2", "end": end}  # noqa: E731
        return [mk("a1", "2026-11-18T21:00:00+09:00"), mk("a2", "2026-11-18T22:00:00+09:00"),
                mk("a3", "2026-11-20T21:00:00+09:00"), mk("a4", "2026-11-01T21:00:00+09:00")]

    def test_ended_unsold_and_future(self):
        rows = self.rows()
        pages = {"a1": page("closed", 18000, 5, "2026-11-18T21:00:00+09:00"),
                 "a2": page("closed", 3000, 0, "2026-11-18T22:00:00+09:00")}
        seen = []

        def fetch(u):
            aid = u.rsplit("/", 1)[1]
            seen.append(aid)
            if aid not in pages:
                raise OSError("404")
            return pages[aid]
        res = yr.check_watch(rows, 300, 0, fetch, NOW)
        by = {r["auction_id"]: r for r in rows}
        self.assertNotIn("a3", seen)  # まだ終わっていない出品は読まない
        self.assertEqual((by["a1"]["final_price"], by["a1"]["bids"], by["a1"]["status"]), (18000, 5, "落札された"))
        self.assertEqual((by["a2"]["final_price"], by["a2"]["bids"], by["a2"]["status"]), ("", 0, "流れた"))
        self.assertEqual(by["a4"]["status"], "読めず")  # 終了から14日以上たって読めない（削除など）
        self.assertEqual((res["sold"], res["unsold"], res["gave_up"]), (1, 1, 1))
        # 記録済みはもう読まない
        seen.clear()
        yr.check_watch(rows, 300, 0, fetch, NOW)
        self.assertEqual(seen, [])

    def test_hidden_final_price(self):
        # 2026-11-25: 終わった出品はログインしないと最終価格が見えず、開始価格（1円）が出る。入札26件で 1円 を落札額にしていた
        import bid_results as br
        rows = self.rows()[:2]
        pages = {"a1": page("closed", 1, 26, "2026-11-18T21:00:00+09:00", init=1),
                 "a2": page("closed", 1000, 1, "2026-11-18T22:00:00+09:00", init=1000)}  # 入札1件は開始価格で落札
        res = yr.check_watch(rows, 300, 0, lambda u: pages[u.rsplit("/", 1)[1]], NOW)
        by = {r["auction_id"]: r for r in rows}
        self.assertEqual((by["a1"]["final_price"], by["a1"]["status"]), ("", "落札額不明"))
        self.assertEqual((by["a2"]["final_price"], by["a2"]["status"]), (1000, "落札された"))
        self.assertEqual(res["sold"], 2)
        self.assertEqual(br._item_state(page("closed", 1, 26, "2026-11-18T21:00:00+09:00", init=1))["price"], None)
        # 中央値に入れない
        for r in rows:
            r["model_key"] = "nikon fm2"
        self.assertEqual(yr.model_stats(rows, 90, NOW)["nikon fm2"]["unsold"], 0)
        # 以前に 1円 で記録した物は直す
        old = [dict(by["a2"], auction_id="a9", final_price="1", bids="20", status="落札された"),
               dict(by["a2"], auction_id="a8", final_price="1000", bids="37", status="落札された"),
               dict(by["a2"], auction_id="a7", final_price="1000", bids="1", status="落札された")]  # 入札1件は本物
        yr.check_watch(old, 300, 0, lambda u: "", NOW)
        self.assertEqual([(r["status"], r["final_price"]) for r in old],
                         [("落札額不明", ""), ("落札額不明", ""), ("落札された", "1000")])
        log = [{"auction_id": "b1", "final_price": "1000", "result": "負け", "profit_final": "-5"},
               {"auction_id": "b2", "final_price": "1000", "result": "落札", "profit_final": "9000"}]
        from unittest import mock
        with mock.patch.object(br, "_write"):
            br.check_results(CFG, 150.0, 0, log, lambda u: "", None, {})
        self.assertEqual([r["final_price"] for r in log], ["", "1000"])
        # 入札の結果: 落札かどうかは台帳で決め、最終価格は空
        st = br._item_state(page("closed", 1, 26, "2026-11-01T21:00:00+09:00", init=1))
        self.assertEqual(br.judge_result({"auction_id": "zzz", "max_bid": "30000", "end": "2026-11-01T21:00:00+09:00"},
                                         st, CFG, 150.0, {"zzz"}), {"result": "落札", "final_price": ""})
        self.assertEqual(br.judge_result({"auction_id": "yyy", "max_bid": "30000", "end": "2026-11-01T21:00:00+09:00"},
                                         st, CFG, 150.0, {"zzz"})["result"], "負け")

    def test_limit_and_extended(self):
        rows = self.rows()[:2]
        # 自動延長でまだ終わっていない → 終了時刻だけ直して記録しない
        later = "2026-11-19T23:00:00+09:00"
        res = yr.check_watch(rows, 1, 0, lambda u: page("open", 9000, 3, later), NOW)
        self.assertEqual((res["due"], res["read"]), (2, 1))  # 1回の上限
        self.assertEqual((rows[0]["end"], rows[0]["checked_at"]), (later, ""))

    def test_model_stats_and_sheet(self):
        rows = []
        for aid, p, b, end in (("a1", 18000, 5, "2026-11-10T21:00:00+09:00"), ("a2", 22000, 9, "2026-11-11T21:00:00+09:00"),
                               ("a3", 20000, 3, "2026-11-12T21:00:00+09:00"), ("a4", "", 0, "2026-11-13T21:00:00+09:00"),
                               ("a5", 99000, 4, "2026-06-01T21:00:00+09:00")):  # 90日より前は数えない
            rows.append({k: "" for k in yr.FIELDS} | {
                "auction_id": aid, "title": "Nikon FM2 ボディ", "model": "Nikon FM2", "model_key": "nikon fm2", "end": end,
                "final_price": p, "bids": b, "status": "落札された" if b else "流れた", "checked_at": "2026-11-19 07:00"})
        st = yr.model_stats(rows, 90, NOW)
        self.assertEqual(st["nikon fm2"], {"label": "Nikon FM2", "n": 3, "med": 20000, "min": 18000, "max": 22000, "unsold": 1})
        body = yr.sheet_csv(rows, 90, NOW)
        self.assertEqual(body.count("\n"), 1 + 4)
        self.assertNotIn("seller", body.lower())
        self.assertIn("2026-11-13,Nikon FM2,,0", body)
        import ledger_sync
        sent = []
        with mock.patch.object(ledger_sync.keychain, "get", lambda s, k: "x"):
            self.assertEqual(yr.upload(rows, 90, post=lambda b: sent.append(b) or "OK"), 4)
        self.assertTrue(sent[0].startswith("終了日,機種,落札額"))


class LedgerJudge(unittest.TestCase):
    """落札かどうかは古物台帳（オークションID・URL）で決める。max_bid（提示額）では決めない."""
    ROW = {"auction_id": "x111111111", "title": "Nikon FM2", "max_bid": "20000", "usd": "260", "end": "2026-11-10T21:00:00+09:00",
           "trusted": "0", "store_tax": "0", "ship": "800", "bid_at": "2026-11-10T10:00:00Z"}

    def test_load_won_ids(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "ledger.csv"
            led.write_text("No.,品目,備考\nA1,Nikon FM2,https://auctions.yahoo.co.jp/jp/auction/x111111111\nA2,Canon AE-1,ｗ２２２２２２２２２\n",
                           encoding="utf-8")
            ids = br.load_won_ids([led, Path(d) / "kanri.csv"])
            self.assertIn("x111111111", ids)
            self.assertIn("w222222222", ids)  # 全角も読む
            led.write_text("No.,品目\nA1,Nikon FM2\n", encoding="utf-8")
            self.assertIsNone(br.load_won_ids([led]))  # IDが1つも無い台帳 → 台帳では判定しない

    def test_judge(self):
        closed = br._item_state(page("closed", 25000, 6, "2026-11-10T21:00:00+09:00"))
        # 最終価格が上限額を超えていても、台帳にあれば落札（実際の入札額は提示額と違うことがある）
        self.assertEqual(br.judge_result(dict(self.ROW), closed, CFG, 150.0, {"x111111111"})["result"], "落札")
        # 台帳に無い・終了から10日以上 → 負け。最終価格が上限額以下でも落札にしない
        cheap = br._item_state(page("closed", 15000, 6, "2026-11-01T21:00:00+09:00"))
        self.assertEqual(br.judge_result(dict(self.ROW, end="2026-11-01T21:00:00+09:00"), cheap, CFG, 150.0, set(["zzz"]))["result"], "負け")
        # 終わった直後で台帳に無い → 確認待ち
        just = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.assertEqual(br.judge_result(dict(self.ROW, end=just), br._item_state(page("closed", 15000, 6, just)), CFG, 150.0,
                                         {"zzz"})["result"], "確認待ち")
        # 入札0件で終わった → 流れた
        self.assertEqual(br.judge_result(dict(self.ROW), br._item_state(page("closed", 5000, 0, "2026-11-10T21:00:00+09:00")),
                                         CFG, 150.0, {"zzz"}), {"result": "流れた", "final_price": ""})

    def test_check_results_waits_then_decides(self):
        just = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        rows = [dict(self.ROW, end=just)]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(br, "LOG", Path(d) / "log.csv"):
            known = {"x111111111": {"status": "closed", "price": "15000", "bids": "4", "end": just}}
            fetched = []
            br.check_results(CFG, 150.0, 0, rows, lambda u: fetched.append(u), won_ids={"zzz"}, known=known)
            self.assertEqual(fetched, [])  # 落札相場で記録済みの出品は読み直さない
            self.assertEqual((rows[0]["result"], rows[0]["final_price"]), ("確認待ち", 15000))
            self.assertEqual(br.summarize(rows, None)["pending"], 1)
            # 翌朝、台帳に載った → 落札
            self.assertEqual(br.check_results(CFG, 150.0, 0, rows, None, won_ids={"x111111111"}, known={}), 1)
            self.assertEqual(rows[0]["result"], "落札")
            # 以前の「落札(見込み)」も台帳にあれば落札に
            old = [dict(self.ROW, result="落札(見込み)", final_price="18000")]
            br.check_results(CFG, 150.0, 0, old, None, won_ids={"x111111111"}, known={})
            self.assertEqual(old[0]["result"], "落札")

    def test_stats_skip_pending_and_unsold(self):
        rows = [{"max_bid": "8000", "store_tax": "0", "result": r} for r in ("落札", "負け", "確認待ち", "流れた", "負け")]
        self.assertEqual(br.win_stats(rows)["1万円未満|*"], [1, 3])
        s = br.summarize([dict(r, bid_at="2026-11-10") for r in rows], None)
        self.assertEqual((s["done"], s["pending"], s["unsold"], s["win_rate"]), (3, 1, 1, 33))


class Extension(unittest.TestCase):
    """拡張機能: 落札相場の行は記録があるときだけ出す。見張りは1日の上限まで."""

    def test_yahoo_line(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        from test_trust import run_js
        data = bx.build_data(CFG, 150.0)
        data.update(refs=[], kantei=[], yahooStats={"nikon fm2": {"label": "Nikon FM2", "n": 3, "med": 20000, "min": 18000,
                                                                  "max": 22000, "unsold": 1}})
        out = run_js(data, 'console.log(JSON.stringify([m.yahooLine("nikon fm2"), m.yahooLine("canon ae-1"), m.yahooLine("")]))')
        self.assertIn("ヤフオク落札 中央値 20,000円（3件・直近90日", out[0])
        self.assertEqual(out[1:], ["", ""])

    def test_watch_cap(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        from test_trust import run_js
        data = bx.build_data(CFG, 150.0)
        data.update(refs=[], kantei=[])
        data["cfg"]["seenMaxPerDay"] = 2
        code = ('const L=[1,2,3].map((n)=>({i:{id:"x"+n,title:"Nikon FM"+n,end:"2099-01-01T00:00:00+09:00",tax:n===1},mark:"◎",maxBid:1000*n}));'
                'console.log(JSON.stringify([m.watchAdd(L), m.watchCsv().split("\\n")[0]]))')
        out = run_js(data, code)
        self.assertEqual(out, [2, ",".join(yr.SEEN_FIELDS)])  # 上限2件。CSV の列は Mac の取り込みと同じ


if __name__ == "__main__":
    unittest.main()
