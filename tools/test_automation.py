"""自動化（暗号化配信・古物台帳・頒布会の添付・自動実行）のテスト. python -m unittest discover -s tools で実行."""
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import chatwork_sync as cs
import ledger_sync as ls
import schedule
import store_publish as sp

STORE = Path(__file__).resolve().parent.parent / "store"


@unittest.skipUnless(sp._aesgcm(), "cryptography がありません")
class Encryption(unittest.TestCase):
    DATA = {"kind": "aurora-store", "built": "2026-11-15 07:03", "refs": [{"title": "Nikon FM2", "price": 255}], "memo": "日本語"}

    def test_python_roundtrip(self):
        enc = sp.encrypt(json.dumps(self.DATA).encode(), "pass-1", iterations=1000)
        self.assertNotIn("Nikon", enc)
        self.assertEqual(json.loads(sp.decrypt(enc, "pass-1")), self.DATA)
        with self.assertRaises(Exception):
            sp.decrypt(enc, "wrong")

    def test_iphone_can_decrypt(self):
        """iPhone のアプリと同じ処理（store/sync.js・WebCrypto）で戻せる."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        enc = sp.encrypt(json.dumps(self.DATA, ensure_ascii=False).encode(), "合言葉-x", iterations=1000)
        code = sp.setup_code("https://gist.githubusercontent.com/u/abc/raw/store_data.enc", "合言葉-x")
        js = (f"const s=require({json.dumps(str(STORE / 'sync.js'))});"
              f"const cfg=s.parseSetupCode({json.dumps(code)});"
              f"s.decryptStore({json.dumps(enc)},cfg.pass).then(d=>console.log(JSON.stringify({{cfg,d}})))"
              ".catch(e=>{console.log('ERR '+e);process.exit(1)})")
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["d"], self.DATA)
        self.assertEqual(out["cfg"]["url"], "https://gist.githubusercontent.com/u/abc/raw/store_data.enc")
        bad = subprocess.run(["node", "-e", f"require({json.dumps(str(STORE / 'sync.js'))}).decryptStore({json.dumps(enc)},'wrong')"
                              ".then(()=>process.exit(0),()=>process.exit(3))"], capture_output=True)
        self.assertEqual(bad.returncode, 3)  # 合言葉が違えば読めない

    def test_setup_code_rejects_garbage(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        js = (f"const s=require({json.dumps(str(STORE / 'sync.js'))});"
              "console.log(JSON.stringify([s.parseSetupCode('x'),s.parseSetupCode('AURORA1:!!'),s.parseSetupCode('')]))")
        self.assertEqual(json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout),
                         [None, None, None])


class LedgerSync(unittest.TestCase):
    CSV = '"管理番号","品目(商品名)","仕入金額","販売日","販売金額($)"\n"1","Nikon F3","10000","2026/11/01","200"\n'

    def setUp(self):
        csv_text = self.CSV

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = (csv_text if "key=k1" in self.path else "forbidden").encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/exec"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp)

    def _sync(self, key):
        with mock.patch.object(ls, "OUT", self.tmp / "ledger.csv"), mock.patch.object(ls, "DATA", self.tmp), \
                mock.patch.object(ls, "BACKUP", self.tmp / "backup"), \
                mock.patch.object(ls.keychain, "get", lambda s, n: {"url": self.url, "key": key}[n]):
            return ls.sync()

    def test_sync(self):
        self.assertIsNone(self._sync("wrong"))  # 合言葉が違えば取り込まない
        self.assertTrue(self._sync("k1"))
        self.assertEqual((self.tmp / "ledger.csv").read_text(encoding="utf-8"), self.CSV)
        self.assertFalse(self._sync("k1"))  # 変化なし


class HanpukaiFiles(unittest.TestCase):
    def test_sync_files(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            src = tmp / "src"
            src.mkdir()
            for name, sheet in (("hp.xlsx", "カメラ商品一覧"), ("other.xlsx", "Sheet1")):
                with zipfile.ZipFile(src / name, "w") as z:
                    z.writestr("xl/workbook.xml", f'<workbook><sheets><sheet name="{sheet}" sheetId="1"/></sheets></workbook>')
            now = 1763164800  # 2025-11-15
            files = [{"file_id": 1, "filename": "頒布会_2026-12.xlsx", "upload_time": now},
                     {"file_id": 2, "filename": "請求書.xlsx", "upload_time": now},
                     {"file_id": 3, "filename": "photo.jpg", "upload_time": now}]
            urls = {"1": (src / "hp.xlsx").as_uri(), "2": (src / "other.xlsx").as_uri()}

            def fake_get(token, path):
                if path.endswith("/files"):
                    return files
                return {"download_url": urls[path.split("/files/")[1].split("?")[0]]}

            data = tmp / "data"
            with mock.patch.object(cs, "_get", fake_get), mock.patch.object(cs, "DATA", data), \
                    mock.patch.object(cs, "FILES_STATE", data / "chatwork_files.json"), \
                    mock.patch.object(cs, "BACKUP", data / "backup"), \
                    mock.patch("chatwork_sync.datetime") as dt:
                dt.now.return_value.timestamp.return_value = now + 10
                dt.fromtimestamp = __import__("datetime").datetime.fromtimestamp
                self.assertEqual(cs.sync_files("t", "r"), 1)
                self.assertEqual(sorted(p.name for p in data.glob("*.xlsx")), ["hanpukai_2026-12.xlsx"])
                self.assertEqual(cs.sync_files("t", "r"), 0)  # 同じファイルは2回取り込まない
        finally:
            shutil.rmtree(tmp)


class Schedule(unittest.TestCase):
    def test_plist(self):
        p = schedule.plist(7, 3)
        self.assertEqual(p["StartCalendarInterval"], {"Hour": 7, "Minute": 3})
        self.assertTrue(p["RunAtLoad"])
        self.assertTrue(p["ProgramArguments"][1].endswith("update_all.py"))
        self.assertIn("--notify", p["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
