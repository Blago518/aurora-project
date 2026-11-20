"""実績の売値の割合（表示だけ。2026-11-20 ユーザー決定）のテスト."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import appraise as ba  # noqa: E402

CFG = ba.load_config(Path(__file__).parent / "config.toml")


class Realized(unittest.TestCase):
    def test_ratio_bands(self):
        self.assertEqual(ba.realized_ratio(80, 7, CFG), CFG["realized"]["under_100"])
        self.assertEqual(ba.realized_ratio(150, 7, CFG), CFG["realized"]["under_200"])
        self.assertEqual(ba.realized_ratio(300, 7, CFG), CFG["realized"]["over_200"])
        self.assertEqual(ba.realized_ratio(150, 9, CFG), CFG["realized"]["mint"])

    def test_display_only(self):
        it = ba.Item(url="", auction_id="x", title="Nikon Ai-S 50mm f/1.4", current_price_jpy=5000)
        it.expected_price_usd = 140
        c = ba.calculate(it, CFG, 150)
        ratio, profit = c["realized"]
        self.assertLess(profit, c["profit_now_jpy"])  # 実績どおりなら利益は下がる
        # 上限額・損益分岐は [realized] が無くても同じ（判定には使わない）
        cfg2 = dict(CFG)
        cfg2.pop("realized")
        c2 = ba.calculate(it, cfg2, 150)
        self.assertEqual(c2["max_bid_jpy"], c["max_bid_jpy"])
        self.assertEqual(c2["breakeven_bid_jpy"], c["breakeven_bid_jpy"])
        self.assertEqual(c2["realized"], (None, None))


if __name__ == "__main__":
    unittest.main()


class OldPythonConfig(unittest.TestCase):
    """Mac の Python 3.9（tomllib なし）用の読み取りが、tomllib と同じ結果になる（2026-11-20: 配列で止まった）."""

    def test_simple_toml_matches_tomllib(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib がありません")
        path = Path(__file__).parent / "config.toml"
        self.assertEqual(ba._parse_simple_toml(path.read_text(encoding="utf-8")), tomllib.loads(path.read_text(encoding="utf-8")))


class InstantLaneJunk(unittest.TestCase):
    """2026-11-20 予約リストの即決に並んだ付属品・部品・本・別物（実際の商品名）. Python と拡張機能（flagRules）で同じ."""
    ACC = [
        "[箱だけ] Leica SUMMICRON M 50mm F2 BLACK 11826 ライカ",
        "10銅製　ホットシューカバー　Leica Q3 M11 M10 MP M3 Nikon Zf DF FUJI X-T5 X100VI富士フイルム",
        "新品 Rolleiflex ローライ 二眼 2.8F/3.5F/FW用 スプリット イメージ スクリーン",
        "ニコンDf WORLD 原点回帰。写真の楽しみ無限大 日本カメラ社 Dfの使い方と一眼レフの基礎/最新ニッコールレンズベストセレクション",
        "CHQ788#防水一眼レフカメラバッグ写真ニコン D5600 D5300 D5500 D3400 D3300 D3100 D750 D7200",
        "★純正品★ニコン NIKON 三脚座 AF-S NIKKOR 80-400mm F4.5-5.6G ED VR用 #11244",
        "新品 HASSELBLAD ハッセルブラッド Planar C 80mm F2.8 クロームレンズ用 シャッタースピードスケール 補修用パーツ",
        "極上品☆Nikon MB-D16 MS-D14EN マルチバッテリーパワーパック グリップ D750用 ##14505",
        "フェイクライカ Luftwaffen ボディ ELMAR エルマー 50mm F3.5 #5824W0992",
    ]
    BODY = [  # 付属品が「付き」の本体は付属品扱いしない
        "Nikon F3 HP フォーカシングスクリーン付",
        "Nikon AF-S 80-400mm 三脚座付き 美品",
        "Nikon F3 カメラバッグ付き",
        "Leica Summicron 50mm F2 沈胴 ケース付",
    ]

    def test_python(self):
        for t in self.ACC:
            self.assertIn("付属品のみ?", ba.detect_flags(t), t)
        for t in self.BODY:
            self.assertNotIn("付属品のみ?", ba.detect_flags(t), t)

    def test_js_same(self):
        import json
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node がありません")
        rules = [[n, p] for n, p in ba.FLAG_RULES]
        code = ("const R=%s;const T=%s;console.log(JSON.stringify(T.map(t=>R.filter(([n,p])=>new RegExp(p,'i').test(t)).map(([n])=>n))));"
                % (json.dumps(rules), json.dumps(self.ACC + self.BODY)))
        out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True).stdout)
        self.assertEqual(out, [ba.detect_flags(t) for t in self.ACC + self.BODY])

    def test_soviet_lens_not_leica(self):
        q = ba.title_to_query("インダスター10 50mm f3.5 旧ソ連製 Lライカマウント FED INDUSTER-10 エルマー5cm パルナックライカ")
        refs = [{"title": "Leica Elmar 50mm F3.5", "price": 300.0, "tier": 0, "source": "販売実績", "_tokens": ba._tokens("Leica Elmar 50mm F3.5")}]
        self.assertEqual(ba.match_sales(q, refs), [], q)

    def test_instant_lane_skips_too_cheap(self):
        rows = [{"判定": "◎", "根拠の強さ": "確認不要", "上限入札額": 100000, "即決価格": 30000, "ヤフオクURL": "a", "終了": ""},
                {"判定": "◎", "根拠の強さ": "確認不要", "上限入札額": 100000, "即決価格": 60000, "ヤフオクURL": "b", "終了": ""}]
        instant, _ = ba.reserve_list(rows)
        self.assertEqual([r["ヤフオクURL"] for r in instant], ["b"])


class JstDates(unittest.TestCase):
    """月は日本時間で分ける（2026-11-20 ユーザー決定）。eBay の日時（世界標準時）→ 日本時間の日付."""

    def test_jst_date(self):
        import ebay_api
        self.assertEqual(ebay_api.jst_date("2026-08-31T16:30:00.000Z"), "2026-09-01")  # 日本では9月1日 1:30
        self.assertEqual(ebay_api.jst_date("2026-08-31T14:59:59.000Z"), "2026-08-31")
        self.assertEqual(ebay_api.jst_date("2026-11-19T10:00:00.123456+00:00"), "2026-11-19")
        self.assertEqual(ebay_api.jst_date("2026-11-16"), "2026-11-16")
        self.assertEqual(ebay_api.jst_date(""), "")


class NewestMemoWins(unittest.TestCase):
    """2026-11-20 ユーザー指摘: 相場メモ「Canon X10 $540」と新しく直した「$320」の真ん中 $430 で計算され、直しても変わらなかった."""

    def test_python(self):
        rows = [{"title": "Canon X10", "price": 580.0, "date": "2026-10-01", "source": "相場メモ"},
                {"title": "Canon EOS Kiss X10", "price": 360.0, "date": "2026-11-20", "source": "相場メモ"}]
        for r in rows:
            r["_tokens"] = ba._tokens(r["title"])
        m = ba.match_prefer_memo(ba.title_to_query("Canon EOS Kiss X10 ボディ"), rows, [])
        self.assertEqual([r["price"] for r in m], [360.0])

    def test_js_local_newest(self):
        import shutil
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        from test_trust import run_js
        data = bx.build_data(CFG, 150.0)
        got = run_js(data, (
            "const tok=(t)=>[...m.tokensOf(t)];"
            "const data={title:'Canon X10',price:580,tier:0,source:'相場メモ',tokens:tok('Canon X10'),d:'2026-10-01'};"
            "const old={title:'Canon EOS Kiss X10',price:400,tier:0,source:'相場メモ',tokens:tok('Canon EOS Kiss X10'),local:true,t:1};"
            "const neu={title:'Canon Kiss X10',price:360,tier:0,source:'相場メモ',tokens:tok('Canon Kiss X10'),local:true,t:2};"
            "const q=m.titleToQuery('Canon EOS Kiss X10 ボディ')+' Canon EOS Kiss X10 ボディ';"
            "console.log(JSON.stringify(m.matchPreferMemo(q,[data,old,neu],[]).map(r=>r.price)));"))
        self.assertEqual(got, [360])


class UpdateLog(unittest.TestCase):
    """Mac_更新ログ（2026-11-20）: Mac の更新で出た問題をクラウドのサトシが毎朝見られる."""

    def test_log_csv(self):
        import update_all as u
        from datetime import datetime
        u.LOG_LINES[:] = ["===== 2026-11-20 13:00 相場データの更新 =====",
                          "  落札相場: 読めませんでした s1（HTTP Error 404: Not Found）",
                          "Traceback (most recent call last):", "SyntaxError: f-string: unmatched '('",
                          "  ⚠ グラフを作れませんでした", "今日の予約リスト: 予約 8本（目標8本）・即決 2件"]
        body = u.log_csv(False, datetime(2026, 11, 20, 13, 0))
        self.assertIn("結果,一部でエラー", body)
        self.assertIn("問題,SyntaxError", body)
        self.assertIn("まとめ,今日の予約リスト", body)
        self.assertNotIn("404", body)  # 終わって消えた出品は毎日あるので載せない
