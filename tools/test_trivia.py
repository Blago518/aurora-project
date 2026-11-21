"""機種の豆知識（共有_豆知識 → data/trivia.csv → 店舗アプリ）. 2026-11-21."""
import tempfile
import unittest
from pathlib import Path

import build_store as bs


class Trivia(unittest.TestCase):
    def test_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.csv"
            p.write_text("機種,豆知識,出典,日付\nContax Aria,写真家の蜷川実花さんが愛用。女性に人気,オーナー,2026-11-21\n,空,,\n", encoding="utf-8")
            self.assertEqual(bs.trivia(p), [])   # 「確認」列が無い（確かめていない）行は出さない

    def test_checked(self):
        """確認済みの行だけ出す（未確認・空欄は出さない）."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.csv"
            p.write_text("機種,豆知識,出典,日付,確認\nNikon F3,AとB,x,2026-11-22,確認済み\nContax T2,C,y,2026-11-22,未確認\n", encoding="utf-8")
            self.assertEqual(bs.trivia(p), [{"m": "Nikon F3", "t": "AとB"}])


if __name__ == "__main__":
    unittest.main()
