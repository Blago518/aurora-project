"""python -m unittest test_bid_analyzer で実行."""
import json
import tempfile
import unittest
from pathlib import Path

import bid_analyzer as ba

CFG = ba.load_config(None)


class TitleToQuery(unittest.TestCase):
    def test_japanese_brand_and_model(self):
        q = ba.title_to_query("【美品】ニコン Nikon FM2 ブラック ボディ 動作確認済 1円スタート")
        self.assertEqual(q, "Nikon FM2 Black")

    def test_lens(self):
        q = ba.title_to_query("キヤノン New FD 50mm F1.4 単焦点 #123456")
        self.assertEqual(q, "Canon New FD 50mm F1.4")

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


class EbayParse(unittest.TestCase):
    def test_prices(self):
        page = (
            '<span class="s-item__price">$1,234.50</span>'
            '<span class="s-item__price">$100.00 to $120.00</span>'
            '<span class="s-card__price">JPY 15,000</span>'
        )
        self.assertEqual(ba.parse_ebay_prices(page, fx=150), [1234.5, 100.0, 100.0])

    def test_trim_outliers(self):
        self.assertEqual(ba.trim_outliers([100, 110, 90, 105, 5, 900]), [100, 110, 90, 105])


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


if __name__ == "__main__":
    unittest.main()
