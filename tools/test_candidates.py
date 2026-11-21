"""ゼニガメの下調べ（共有_下調べ相場）→ 店舗アプリの仮の相場（2026-11-21）."""
import tempfile
import unittest
from pathlib import Path

import build_store as bs


class CandidateRefs(unittest.TestCase):
    def test_refs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            p.write_text("機種,候補$（送料別）,確度,日付,根拠\nNikon Z6 II,1050,中,2026-11-21,MPB\nbad,,低,,\n", encoding="utf-8")
            refs = bs.candidate_refs(40.0, p)
        self.assertEqual(len(refs), 1)
        self.assertEqual((refs[0]["title"], refs[0]["price"], refs[0]["tier"], refs[0]["source"]), ("Nikon Z6 II", 1090.0, 3, "下調べ"))


if __name__ == "__main__":
    unittest.main()
