"""robots.txt に合わせた読み方・勝ちやすさ・並べ方・今日の予約リストのテスト（2026-11-19 ユーザー決定）.
python3 -m unittest discover -s tools で実行."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import unittest.mock
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import appraise as ba
import robots
import win_score as ws

CFG = ba.load_config(None)
JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent

# https://auctions.yahoo.co.jp/robots.txt（2026-11-19 の写し tools/yahoo_robots.txt）の該当行
ROBOTS_LINES = """User-agent: *
Disallow: /closeduser/
Disallow: /closedsearch/
Disallow: /closedsearch
Disallow: /search/*?*mode=
Disallow: /search/*?*n=
Disallow: /search/*?*s1=
Disallow: /search/*?*o1=
Disallow: /search/*?*istatus=
Disallow: /closedsearch/*?*aucminprice=
Disallow: /closedsearch/*?*select=
Disallow: /closedsearch/*?*n=
Disallow: /seller/*?*select=
Disallow: /seller/*?*n=
Disallow: /seller/*?*s1=
Disallow: /seller/*?*o1=
Disallow: /seller/*?*sid=
Disallow: /category/list/*?*s1=
Allow: /closedsearch/closedsearch
"""
RULES = robots.parse(ROBOTS_LINES)
Y = "https://auctions.yahoo.co.jp"


def page(items: list[dict], total: int | None = None) -> str:
    data = {"props": {"pageProps": {"items": items, "totalResultsAvailable": len(items) if total is None else total}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>'


def yitem(aid: str, title: str, price: int, bids: int = 3, end: str = "2026-11-10T21:00:00+09:00", store: bool = False,
          buy_now: int | None = None) -> dict:
    return {"auctionId": aid, "title": title, "price": price, "bidCount": bids, "endTime": end, "buyNowPrice": buy_now,
            "isFreeShipping": False, "currentPriceShippingFee": 1000, "taxFlag": 1 if store else 0,
            "seller": {"isStore": store, "userId": "u" + aid}}


class RobotsRules(unittest.TestCase):
    def test_lines_are_from_the_copy(self):
        """テストの規則は手元の写しにある行そのもの."""
        copy = (HERE / "yahoo_robots.txt").read_text(encoding="utf-8").splitlines()
        for ln in ROBOTS_LINES.splitlines()[1:]:
            self.assertIn(ln, copy)

    def test_allowed(self):
        cases = {
            f"{Y}/search/search?p=Nikon&auccat=23636": True,
            f"{Y}/search/search?p=Nikon&auccat=23636&s1=end&o1=a&n=100": False,
            f"{Y}/search/search?p=Nikon&b=51": True,               # ページ送り b= は Disallow に無い
            f"{Y}/search/search?va=D7100&aucminprice=1000": True,   # /search/ の aucminprice は Disallow に無い
            f"{Y}/search/search?p=Nikon&istatus=2": False,
            f"{Y}/closedsearch/closedsearch?p=Nikon+D7100": True,   # Allow
            f"{Y}/closedsearch/closedsearch?p=Nikon&b=51": True,
            f"{Y}/closedsearch/closedsearch?p=Nikon&n=100": False,  # 引数付きの Disallow は Allow より短くても読まない（保守的）
            f"{Y}/closedsearch/closedsearch?p=Nikon&aucminprice=1000": False,
            f"{Y}/closeduser/jp/show/mystatus": False,
            f"{Y}/seller/abcd?sid=abcd": False,
            f"{Y}/seller/abcd": True,
            f"{Y}/jp/auction/x123456789": True,                     # 出品ページは制限なし
            "https://www.ebay.com/sch/i.html?_nkw=x&n=1": True,     # ヤフオク以外は対象外
        }
        for u, ok in cases.items():
            self.assertEqual(robots.allowed(u, RULES), ok, u)

    def test_safe_url_removes_disallowed_params(self):
        u = f"{Y}/search/search?p=%E3%83%8B%E3%82%B3%E3%83%B3&auccat=23636&s1=end&o1=a&n=100"
        safe = robots.safe_url(u, RULES)
        self.assertEqual(safe, f"{Y}/search/search?p=%E3%83%8B%E3%82%B3%E3%83%B3&auccat=23636")
        self.assertTrue(robots.allowed(safe, RULES))
        self.assertEqual(robots.removed_params(u, RULES), ["s1", "o1", "n"])
        self.assertEqual(robots.safe_url(f"{Y}/seller/abcd?sid=abcd&select=2&b=51", RULES), f"{Y}/seller/abcd?b=51")
        self.assertEqual(robots.removed_params(f"{Y}/search/search?p=a&b=51", RULES), [])

    def test_check_refuses_disallowed_path(self):
        with unittest.mock.patch("sys.stderr"):
            self.assertIsNone(robots.check(f"{Y}/closeduser/jp/show/mystatus", RULES))
        self.assertEqual(robots.check(f"{Y}/search/search?p=a&n=100", RULES), f"{Y}/search/search?p=a")

    def test_bundled_copy(self):
        rules = robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))
        self.assertFalse(robots.allowed(f"{Y}/search/search?p=a&s1=end", rules))
        self.assertTrue(robots.allowed(f"{Y}/closedsearch/closedsearch?p=a&b=51", rules))
        self.assertIn(["^/search/.*", "n="], robots.param_rules(rules))

    def test_named_search_urls_have_no_disallowed_params(self):
        """売れ筋・負けた機種の名指し検索（hit_search_urls）は Disallow の引数を付けない."""
        rules = robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))
        u = ba._model_url("Nikon", "D7100")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))
        self.assertNotIn("s1", q)
        self.assertNotIn("o1", q)
        self.assertNotIn("n", q)
        self.assertEqual(q["va"], "D7100")
        self.assertTrue(robots.allowed(u, rules))

    def test_example_search_urls_become_allowed(self):
        rules = robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))
        urls = ba.find_search_urls((HERE / "search_urls_example.txt").read_text(encoding="utf-8"))
        self.assertTrue(urls)
        for u in urls:
            self.assertTrue(robots.allowed(u, rules), u)  # 例は s1・o1・n を付けない形
            self.assertEqual(robots.safe_url(u, rules), u)


class FetchListing(unittest.TestCase):
    def setUp(self):
        robots._RULES = robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))

    def tearDown(self):
        robots._RULES = None

    def test_reads_allowed_pages_and_steps_by_page_size(self):
        got = []
        pages = {1: [yitem(f"a{i}", f"Nikon D7100 {i}", 20000) for i in range(50)],
                 51: [yitem(f"b{i}", f"Nikon D7100 {i}", 20000) for i in range(50)],
                 101: [yitem(f"c{i}", f"Nikon D7100 {i}", 20000) for i in range(10)]}

        def fetch(u):
            got.append(u)
            b = int(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)).get("b", 1))
            return page(pages.get(b, []), 110)
        items = ba.fetch_listing(f"{Y}/search/search?p=Nikon&s1=end&o1=a&n=100", 1000, 0, None, fetch)
        self.assertEqual(len(items), 110)
        self.assertEqual(len(got), 3)
        for u in got:
            self.assertTrue(robots.allowed(u), u)
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))
            self.assertFalse({"n", "s1", "o1"} & set(q), u)
        self.assertEqual([dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))["b"] for u in got], ["1", "51", "101"])

    def test_page_budget(self):
        got = []

        def fetch(u):
            got.append(u)
            return page([yitem(f"{len(got)}-{i}", "Canon AE-1", 5000) for i in range(50)], 10000)
        budget = {"pages": 2}
        ba.fetch_listing(f"{Y}/search/search?p=Canon", 2000, 0, budget, fetch)
        ba.fetch_listing(f"{Y}/search/search?p=Nikon", 2000, 0, budget, fetch)
        self.assertEqual(len(got), 2)
        self.assertEqual(budget["pages"], 0)

    def test_disallowed_path_is_not_read(self):
        with unittest.mock.patch("sys.stderr"):
            self.assertEqual(ba.fetch_listing(f"{Y}/closeduser/jp/show/mystatus", 100, 0, None,
                                              lambda u: self.fail("読んではいけない")), [])


SALES = [{"title": "Nikon D7100 Body", "source": "相場メモ", "_price": 290.0, "_tokens": ba._tokens("Nikon D7100 Body")},
         {"title": "Nikon FM2 Body", "source": "相場メモ", "_price": 320.0, "_tokens": ba._tokens("Nikon FM2 Body")}]


class WinScore(unittest.TestCase):
    def max_bid(self, title="ニコン Nikon D7100 ボディ", store=False):
        it = ba.Item(url="u", auction_id="m", title=title, current_price_jpy=1000, domestic_shipping_jpy=1000,
                     prefilled=True, store_tax=store)
        ba.analyze([it], SALES, CFG, 150.0, True, 0, [])
        return it.calc["max_bid_jpy"]

    def closed(self, rows):
        items, _, _ = ba.parse_listing_page_full(page(rows))
        return items

    def test_target_and_query(self):
        t = ws.target_of("ニコン Nikon D7100 ボディ 美品")
        self.assertEqual((t["key"], t["query"]), ("nikon d7100", "Nikon D7100"))
        self.assertEqual(ws.query_of("Nikon AF 35mm F2D"), "Nikon AF 35mm")
        self.assertEqual(ws.query_of("Canon EF 70-200mm F4 L"), "Canon EF 70-200mm")
        self.assertIsNone(ws.target_of("Nikon D7100 18-105 レンズキット"))
        self.assertIsNone(ws.target_of("ニコン 純正 レンズキャップ のみ"))
        self.assertTrue(ws.closed_url("Nikon D7100").startswith(f"{Y}/closedsearch/closedsearch?p="))
        self.assertTrue(robots.allowed(ws.closed_url("Nikon D7100"), RULES))

    def test_rate_with_current_logic(self):
        mb = self.max_bid()
        rows = [
            yitem("w1", "ニコン Nikon D7100 ボディ", mb),            # ちょうど上限 → 上限以下
            yitem("w2", "Nikon D7100 ボディ 美品", mb - 3000),
            yitem("l1", "Nikon D7100 ボディ", mb + 1000),
            yitem("l2", "ニコン D7100 ボディ", mb + 8000),
            yitem("j1", "Nikon D7100 ジャンク", 3000),                 # 難あり語 → 数えない
            yitem("s1", "Nikon D7100 + 18-55mm セット", 9000),         # まとめ売り → 数えない
            yitem("z1", "Nikon D7100 ボディ", 5000, bids=0),           # 入札0件（流れた） → 数えない
            yitem("o1", "Nikon D7200 ボディ", 9000),                   # 別の機種 → 数えない
            yitem("a1", "Nikon D7100 用 バッテリーグリップ のみ", 2000),  # 付属品 → 数えない
            yitem("w1", "ニコン Nikon D7100 ボディ", mb),            # 重複
        ]
        out = ws.evaluate({"nikon d7100": self.closed(rows)}, SALES, CFG, 150.0)
        got = out["nikon d7100"]
        self.assertEqual(sorted(r["id"] for r in got), ["l1", "l2", "w1", "w2"])
        s = ws.score(got, datetime(2026, 11, 19, 12, tzinfo=JST))
        self.assertEqual((s["n"], s["won"], s["rate"]), (4, 2, 0.5))
        self.assertEqual(s["med_max_bid"], mb)

    def test_store_price_is_compared_without_tax(self):
        """ストアの落札額（税込で表示）は税抜（入札額の単位）にして上限額と比べる."""
        mb = self.max_bid(store=True)
        rows = [yitem("t1", "Nikon D7100 ボディ", round(mb * 1.1), store=True),       # 税抜で上限ちょうど
                yitem("t2", "Nikon D7100 ボディ", round(mb * 1.1) + 1100, store=True)]
        got = ws.evaluate({"nikon d7100": self.closed(rows)}, SALES, CFG, 150.0)["nikon d7100"]
        self.assertEqual({r["id"]: r["won"] for r in got}, {"t1": True, "t2": False})

    def test_lookback_and_per_month(self):
        now = datetime(2026, 11, 19, 12, tzinfo=JST)
        rows = [{"price": 1, "max_bid": 2, "won": True, "end": "2026-11-18T21:00:00+09:00"},
                {"price": 3, "max_bid": 2, "won": False, "end": "2026-10-19T21:00:00+09:00"},
                {"price": 1, "max_bid": 2, "won": True, "end": "2026-01-01T21:00:00+09:00"}]  # 90日より前
        s = ws.score(rows, now, 90)
        self.assertEqual((s["n"], s["won"], s["span_days"], s["per_month"]), (2, 1, 30, 2.0))
        self.assertIsNone(ws.score([], now))

    def test_targets_order_and_refresh(self):
        now = datetime(2026, 11, 19, 12, tzinfo=JST)
        report = [{"判定": "◎", "タイトル": "Nikon D7100 ボディ"}, {"判定": "×", "タイトル": "Canon AE-1"},
                  {"判定": "○", "タイトル": "Nikon FM2 ボディ"}]
        bids = [{"title": "Contax T2", "result": "負け", "end": "2026-11-10"},
                {"title": "Contax T3", "result": "負け", "end": "2026-08-01"}]   # 60日より前
        scores = {"nikon fm2": {"checked_at": "2026-11-18 06:00"},               # 調べたばかり → 飛ばす
                  "nikon d7100": {"checked_at": "2026-11-01 06:00"}}             # 古い → 調べ直す
        t = ws.targets(report, [], bids, CFG, 150.0, scores, now, max_models=10, refresh_days=7)
        self.assertEqual([x["key"] for x in t], ["contax t2", "nikon d7100"])   # まだ調べていない機種が先
        self.assertEqual(len(ws.targets(report, [], bids, CFG, 150.0, {}, now, max_models=1)), 1)

    def test_save_and_load_min_samples(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "w.csv"
            ws.write_scores({"nikon d7100": {"model_key": "nikon d7100", "model": "Nikon D7100", "n": 12, "won": 5, "rate": 0.417},
                             "nikon fm2": {"model_key": "nikon fm2", "model": "Nikon FM2", "n": 3, "won": 0, "rate": 0.0},
                             "contax t2": {"model_key": "contax t2", "model": "Contax T2", "n": 0, "won": 0, "rate": ""}}, p)
            got = ws.load_scores(p, CFG)
        self.assertEqual(list(got), ["nikon d7100"])  # 5件未満・記録なしは出さない
        self.assertEqual((got["nikon d7100"]["rate"], got["nikon d7100"]["n"]), (0.417, 12))

    def test_run_reads_only_allowed_closed_pages(self):
        """run: 終わった出品の検索だけを Allow の形で読み、data/win_score.csv に書く（ファイルは一時フォルダ）."""
        got = []
        mb = self.max_bid()

        def fetch(u):
            got.append(u)
            return page([yitem(f"x{len(got)}{i}", "Nikon D7100 ボディ", mb + (i % 2) * 5000, end="2026-11-1%dT21:00:00+09:00" % i)
                         for i in range(6)], 6)
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "reports").mkdir()
            (dd / "reports" / "bid_report_20261119_0600.csv").write_text("判定,タイトル\n◎,Nikon D7100 ボディ\n", encoding="utf-8-sig")
            with unittest.mock.patch.object(ws, "DATA", dd), unittest.mock.patch.object(ws, "REPORTS", dd / "reports"), \
                    unittest.mock.patch.object(ws, "SCORES", dd / "win_score.csv"), \
                    unittest.mock.patch.object(ws, "_sales_and_kantei", lambda: (SALES, [])), \
                    unittest.mock.patch.object(ba, "load_trusted", lambda *a: {}), unittest.mock.patch("sys.stderr"):
                res = ws.run(CFG, fetch=fetch, now=datetime(2026, 11, 19, 12, tzinfo=JST), fx=150.0)
                rows = ws.read_scores(dd / "win_score.csv")
        self.assertEqual(res["models"], 1)
        self.assertEqual(len(got), 1)  # 6件（1ページで終わり）
        self.assertTrue(all(u.startswith(f"{Y}/closedsearch/closedsearch?") and "n=" not in u.split("?")[1].replace("p=", "")
                            for u in got))
        self.assertEqual((rows["nikon d7100"]["n"], rows["nikon d7100"]["won"]), ("6", "3"))


class Ordering(unittest.TestCase):
    def test_bid_priority(self):
        self.assertEqual(ba.bid_priority(300, True), 0)
        self.assertEqual(ba.bid_priority(300, True, None), 0)       # 記録なし → 今の並び
        self.assertEqual(ba.bid_priority(300, True, 0.46), 0)
        self.assertEqual(ba.bid_priority(300, True, 0.10), 0)       # ちょうど1割は下げない
        self.assertEqual(ba.bid_priority(300, True, 0.09), 4)       # 1割未満は全部の下
        self.assertEqual(ba.bid_priority(80, False, 0.05), 7)
        self.assertEqual(ba.win_label(0.456, 12), "勝ちやすさ 46%（直近12件）")
        self.assertEqual(ba.win_label(None, None), "")

    def test_low_win_goes_down_but_verdict_and_max_bid_stay(self):
        titles = ["Nikon FM2 ボディ", "Nikon D7100 ボディ"]
        def build(scores):
            items = [ba.Item(url=f"https://auctions.yahoo.co.jp/jp/auction/x{i}", auction_id=f"x{i}", title=t,
                             current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True,
                             end_time=f"2026-11-19T2{i}:00:00+09:00") for i, t in enumerate(titles)]
            ba.analyze(items, SALES, CFG, 150.0, True, 0, [])
            ba.attach_win_scores(items, CFG, scores)
            return ba.rows_for_output(items)
        before = build({})
        after = build({"nikon fm2": {"rate": 0.05, "n": 20}, "nikon d7100": {"rate": 0.46, "n": 12}})
        self.assertEqual([r["タイトル"] for r in before], titles)                         # 今の並び（終了が近い順）
        self.assertEqual([r["タイトル"] for r in after], list(reversed(titles)))          # FM2（5%）が下に
        key = lambda rows: {r["タイトル"]: (r["判定"], r["上限入札額"], r["利益(現在価格)"]) for r in rows}  # noqa: E731
        self.assertEqual(key(before), key(after))                                         # 判定・上限額・利益は同じ
        self.assertEqual(after[1]["勝ちやすさ"], 0.05)
        self.assertEqual(after[0]["勝ちやすさの件数"], 12)


def rrow(i, end, mark="◎", sure=True, profit=5000, rate=None, n=None, buy_now=None, max_bid=30000, store=False):
    return {"判定": mark, "根拠の強さ": "確認不要" if sure else "確認してから", "注意": "", "上限入札額": max_bid,
            "即決価格": buy_now, "利益(現在価格)": profit, "勝ちやすさ": rate, "勝ちやすさの件数": n, "終了": end,
            "ヤフオクURL": f"https://auctions.yahoo.co.jp/jp/auction/r{i}", "タイトル": f"item {i}", "現在価格": 10000,
            "入札額の税": "ストア: このまま入札" if store else "個人: このまま入札（税なし）"}


class ReserveList(unittest.TestCase):
    NOW = datetime(2026, 11, 19, 9, 0, tzinfo=JST)

    def test_pick_eight_and_instant_first(self):
        rows = [rrow(i, f"2026-11-{19 + i % 2}T2{i % 4}:00:00+09:00", profit=1000 * (i + 1)) for i in range(10)]
        rows.append(rrow(20, "2026-11-22T21:00:00+09:00", profit=99999))                # 明後日より後 → 入れない
        rows.append(rrow(21, "2026-11-19T21:00:00+09:00", sure=False, profit=99999))    # 確認してから → 入れない
        rows.append(rrow(22, "2026-11-19T08:00:00+09:00", profit=99999))                # もう終わった → 入れない
        rows.append(rrow(23, "2026-11-19T21:00:00+09:00", mark="要確認", profit=99999))
        rows.append(rrow(24, "2026-11-25T21:00:00+09:00", buy_now=25000))               # 即決が上限以下 → 別枠
        rows.append(rrow(25, "2026-11-19T22:00:00+09:00", buy_now=31000, profit=50))     # 即決が上限より上 → 普通の候補
        instant, picks = ba.reserve_list(rows, self.NOW, target=8, days=2, default_rate=0.2)
        self.assertEqual([r["タイトル"] for r in instant], ["item 24"])
        self.assertEqual(len(picks), 8)
        self.assertEqual([r["タイトル"] for r in picks], [f"item {i}" for i in range(9, 1, -1)])  # 利益の大きい順（勝ちやすさは同じ）
        table = ba.reserve_rows(instant, picks)
        self.assertEqual(table[0]["区分"], "即決（今すぐ買える）")
        self.assertEqual([r["順"] for r in table], list(range(1, 10)))

    def test_light_mold_separate_frame(self):
        """軽いカビは予約の10本に数えず、消さずに別の枠で下に出す（2026-11-26 オーナー「入札しないわけではないが、枠は分けてほしい」）."""
        rows = [rrow(1, "2026-11-19T21:00:00+09:00", profit=3000)]
        mold = rrow(2, "2026-11-19T22:00:00+09:00", profit=9000)
        mold["理由"] = "軽いカビ（写真で程度を確認）・余裕あり"
        rows.append(mold)
        late = rrow(3, "2026-11-25T22:00:00+09:00", profit=9000)
        late["理由"] = "軽いカビ（写真で程度を確認）"
        rows.append(late)                                                               # 明後日より後 → 入れない
        _, picks = ba.reserve_list(rows, self.NOW, target=8)
        self.assertEqual([r["タイトル"] for r in picks], ["item 1"])
        m = ba.reserve_mold(rows, self.NOW)
        self.assertEqual([r["タイトル"] for r in m], ["item 2"])
        table = ba.reserve_rows([], picks, mold=m)
        self.assertEqual([r["区分"] for r in table], ["予約", "カビの記載あり（写真で程度を確認）"])

    def test_win_rate_times_profit(self):
        rows = [rrow(1, "2026-11-19T21:00:00+09:00", profit=10000, rate=0.05, n=20),   # 期待 500
                rrow(2, "2026-11-19T22:00:00+09:00", profit=4000, rate=0.5, n=10),     # 期待 2000
                rrow(3, "2026-11-20T21:00:00+09:00", profit=6000)]                     # 記録なし → 0.2 × 6000 = 1200
        _, picks = ba.reserve_list(rows, self.NOW, target=8)
        self.assertEqual([r["タイトル"] for r in picks], ["item 2", "item 3", "item 1"])

    def test_store_payment_and_csv(self):
        t = ba.reserve_rows([], [rrow(1, "2026-11-19T21:00:00+09:00", max_bid=20000, store=True, rate=0.46, n=12)], 10.0)
        self.assertEqual((t[0]["上限入札額(入札欄に入れる額)"], t[0]["支払い額(ストアは税込)"]), (20000, 22000))
        self.assertEqual(t[0]["勝ちやすさ"], "46%（直近12件）")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            ba.write_reserve_csv(t, p)
            self.assertIn("上限入札額(入札欄に入れる額)", p.read_text(encoding="utf-8-sig").splitlines()[0])
        self.assertIn("目標8本", ba.reserve_html(t, 8))
        self.assertIn("ありません", ba.reserve_html([], 8))

    def test_upload_to_sheet(self):
        import ledger_sync
        import update_all
        sent = {}
        with tempfile.TemporaryDirectory() as d:
            rep = Path(d)
            (rep / "reserve_list_20261119_0600.csv").write_text("順,区分\n1,予約\n2,予約\n", encoding="utf-8-sig")
            with unittest.mock.patch.object(ledger_sync, "post_sheet", lambda s, b, post=None: sent.update({s: b}) or True), \
                    unittest.mock.patch("sys.stdout"):
                self.assertEqual(update_all.upload_reserve(rep), 2)
        self.assertEqual(list(sent), ["Mac_予約リスト"])


class ExtensionSame(unittest.TestCase):
    """拡張機能（JS）の robots の外し方・並び・勝ちやすさの表示が Python と同じ（node が無ければスキップ）."""

    def setUp(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")

    def data(self, win=None):
        import build_extension as bx
        d = bx.build_data(CFG, 150.0)
        d.update(refs=[], kantei=[], winScore=win or {},
                 robotsParams=robots.param_rules(robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))))
        return d

    def test_robots_safe_url(self):
        from test_trust import run_js
        rules = robots.parse((HERE / "yahoo_robots.txt").read_text(encoding="utf-8"))
        urls = [f"{Y}/search/search?p=Nikon&auccat=23636&s1=end&o1=a&n=100&b=101",
                f"{Y}/seller/abcd?sid=abcd&select=2&n=50&b=51", f"{Y}/category/list/23636/?s1=end&o1=a&min=5&fixed=2",
                f"{Y}/search/search?va=D7100&vo=Nikon&aucminprice=1000&istatus=2&mode=2"]
        out = run_js(self.data(), f"console.log(JSON.stringify({json.dumps(urls)}.map(u=>[...new URL(m.robotsSafeUrl(u)).searchParams.keys()])))")
        py = [[k for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(robots.safe_url(u, rules)).query)] for u in urls]
        self.assertEqual(out, py)

    def test_bid_prio_and_label(self):
        from test_trust import run_js
        win = {"nikon fm2": {"rate": 0.05, "n": 20}, "nikon d7100": {"rate": 0.456, "n": 12}}
        items = [{"title": "Nikon FM2 ボディ", "usd": 320}, {"title": "Nikon D7100 ボディ", "usd": 290},
                 {"title": "Canon AE-1", "usd": 80}]
        out = run_js(self.data(win), f"const it={json.dumps(items, ensure_ascii=False)}.map(i=>Object.assign(i,{{refs:[]}}));"
                                     "console.log(JSON.stringify(it.map(i=>[m.bidPrio(i),m.winText(i)])))")
        py = []
        for i in items:
            w = win.get(ba.model_key(i["title"])[1])
            py.append([ba.bid_priority(i["usd"], False, w and w["rate"]), ba.win_label(w and w["rate"], w and w["n"])])
        self.assertEqual(out, py)
        self.assertEqual(out[0][0], 5)


if __name__ == "__main__":
    unittest.main()


class CautionSellers(unittest.TestCase):
    """注意を出す出品者（2026-11-19 ユーザー決定「注意書きだけ出す」）. 判定・上限額は変えない."""

    def test_python(self):
        cfg = {"caution": {"sellers": ["バイセル"], "note": "注意"}}
        self.assertEqual(ba.caution_note("バイセル ヤフオク!店", cfg), "注意")
        self.assertEqual(ba.caution_note("ハローカメラ", cfg), "")
        self.assertEqual(ba.caution_note("", cfg), "")

    def test_good_rating(self):
        """良い評価の割合が目安未満なら注意（2026-11-26 オーナーが選択式で「良い評価の割合で印を出す」）. 表示だけ."""
        cfg = {"caution": {"sellers": ["バイセル"], "note": "注意", "min_good_rating": 99.5}}
        self.assertEqual(ba.good_rating("98.0%"), 98.0)
        self.assertIsNone(ba.good_rating(""))
        self.assertEqual(ba.caution_note("カメラ店A", cfg, good=98.0), "評価 98.0%（99.5% 未満）")
        self.assertEqual(ba.caution_note("カメラ店A", cfg, good=99.5), "")
        self.assertEqual(ba.caution_note("カメラ店A", cfg, good=None), "")
        self.assertEqual(ba.caution_note("バイセル ヤフオク!店", cfg, good=99.2), "注意／評価 99.2%（99.5% 未満）")
        # 検索結果の seller.goodRating を読む
        j = {"props": {"pageProps": {"initialState": {"search": {"items": {"listing": {"totalResultsAvailable": 1, "items": [
            {"auctionId": "x1000000001", "title": "Nikon F3", "price": 10000, "endTime": "2026-11-27T20:00:00+09:00",
             "seller": {"userId": "u1", "displayName": "カメラ店A", "goodRating": "98.3%"}}]}}}}}}}
        page = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(j, ensure_ascii=False) + "</script>"
        items, _ = ba.parse_listing_page(page)
        self.assertEqual(items[0].seller_good, 98.3)

    def test_js(self):
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(ba.load_config(None), 150.0)
        data["caution"] = {"sellers": ["バイセル"], "note": "注意", "minGood": 99.5}
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            out = subprocess.run(["node", "-e", "global.localStorage={getItem(){return null},setItem(){}};const m=require(" + json.dumps(str(js)) +
                                  ");console.log(JSON.stringify([m.cautionLine({sellerName:'バイセル ヤフオク!店'}),m.cautionLine({sellerName:'ハローカメラ'}),m.cautionLine({sellerName:'カメラ店A',sellerGood:98}),m.cautionLine({sellerName:'カメラ店A',sellerGood:99.8}),m.goodRating('98.3%')]))"],
                                 capture_output=True, text=True, check=True).stdout
        a, b, low, high, g = json.loads(out)
        self.assertIn("注意", a)
        self.assertEqual(b, "")
        self.assertIn("評価 98.0%（99.5% 未満）", low)   # Python の caution_note と同じ文
        self.assertEqual(high, "")
        self.assertEqual(g, 98.3)
