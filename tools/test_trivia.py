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
            self.assertEqual(bs.trivia(p), [{"m": "Contax Aria", "t": "写真家の蜷川実花さんが愛用。女性に人気"}])


if __name__ == "__main__":
    unittest.main()
