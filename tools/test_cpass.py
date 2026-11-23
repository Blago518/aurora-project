"""SpeedPAK セラーポータルで輸出許可書を自動で取る（extension_cpass.js）. 番号は作り物."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class CpassRows(unittest.TestCase):
    def test_pending_rows(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_cpass.js").read_text(encoding="utf-8")
        body = src[src.index("// @cpass-start"):src.index("// @cpass-end")]
        rows = [
            {"text": "2026-11-23 10:26:20\tEM1000000000001FE06030071G0N\t6,966\t詳細", "hasCert": False},          # FedEx: 証明書のリンクが無い
            {"text": "2026-11-19 08:39:13\tEX1000000000002DL06020002B0N\t7,205\t詳細 証明書をダウンロードする", "hasCert": True},
            {"text": "2026-11-19 08:33:42\tEX1000000000003DL06020002A0N\t4,736\t詳細 証明書をダウンロードする", "hasCert": True},  # もう押した
            {"text": "作成時間 OrangeConnex追跡番号", "hasCert": False},
        ]
        js = body + "\nconsole.log(JSON.stringify(cpassPending(%s, ['EX1000000000003DL06020002A0N'])));" % json.dumps(rows, ensure_ascii=False)
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["EX1000000000002DL06020002B0N"])

    def test_fixed_column_rows(self):
        """右端の「実行する」列が別の表に描かれる作り（固定列）でも、同じ行の番号とリンクを1つにする（2026-11-23）."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        src = (HERE / "extension_cpass.js").read_text(encoding="utf-8")
        body = src[src.index("// @cpass-start"):src.index("// @cpass-end")]
        rows = [{"key": "a", "text": "2026-11-19 EX1000000000002DL06020002B0N 7,205", "hasCert": False},
                {"key": "b", "text": "2026-11-23 EM1000000000001FE06030071G0N 6,966", "hasCert": False},
                {"key": "a", "text": "詳細 証明書をダウンロードする", "hasCert": True},
                {"key": "b", "text": "詳細", "hasCert": False}]
        js = body + "\nconsole.log(JSON.stringify(cpassPending(cpassGroup(%s), [])));" % json.dumps(rows, ensure_ascii=False)
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["EX1000000000002DL06020002B0N"])


if __name__ == "__main__":
    unittest.main()
