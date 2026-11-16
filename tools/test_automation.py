"""自動化（暗号化配信・古物台帳・頒布会の添付・自動実行）のテスト. python -m unittest discover -s tools で実行."""
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock

import chatwork_sync as cs
import ledger_sync as ls
import schedule
import store_publish as sp

STORE = Path(__file__).resolve().parent.parent / "store"


def node_like_safari() -> bool:
    """iPhone の Safari と同じ機能（atob・WebCrypto・DecompressionStream）がある Node.js か（Node 18 以降）."""
    if not shutil.which("node") or not (STORE / "sync.js").exists():  # Mac の aurora には store/sync.js が無い（アプリは GitHub Pages で動く）
        return False
    js = "process.exit(typeof atob==='function'&&globalThis.crypto&&crypto.subtle&&typeof DecompressionStream==='function'?0:1)"
    return subprocess.run(["node", "-e", js], capture_output=True).returncode == 0


NODE_MSG = "store/sync.js か、Safari と同じ機能のある Node.js（18以降）が無いので飛ばします（アプリの動作には関係ありません）"


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
        if not node_like_safari():
            self.skipTest(NODE_MSG)
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

    def test_setup_code_tolerates_paste(self):
        """メモ経由のコピーで混ざる改行・見えない文字・前後の文があっても読める."""
        if not node_like_safari():
            self.skipTest(NODE_MSG)
        c = sp.setup_code("https://gist.githubusercontent.com/u/abc/raw/store_data.enc", "p-1")
        variants = [c, "\n " + c + " \n", c[:20] + "\n" + c[20:], c[:10] + "\u200b" + c[10:], "設定コード: " + c + "。"]
        js = (f"const s=require({json.dumps(str(STORE / 'sync.js'))});"
              f"console.log(JSON.stringify({json.dumps(variants, ensure_ascii=False)}.map(v=>(s.parseSetupCode(v)||{{}}).pass)))")
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["p-1"] * len(variants))

    def test_setup_code_rejects_garbage(self):
        if not node_like_safari():
            self.skipTest(NODE_MSG)
        js = (f"const s=require({json.dumps(str(STORE / 'sync.js'))});"
              "console.log(JSON.stringify([s.parseSetupCode('x'),s.parseSetupCode('AURORA1:!!'),s.parseSetupCode('')]))")
        self.assertEqual(json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout),
                         [None, None, None])


class LedgerSync(unittest.TestCase):
    CSV = '"管理番号","品目(商品名)","仕入金額","販売日","販売金額($)"\n"1","Nikon F3","10000","2026/11/01","200"\n'

    KANRI = ('"番号","商品名","No.","状態","購入日","販売日","アカウント名(仕入元)","ヤフオクURL"\n'
             '"1","Nikon F3","A1","MINT/点カビ","2026/07/26","2026/09/01","someone","https://x"\n'
             '"","","","","",""\n')
    EBAY = ('"※ 8/18 以降"\n"Transaction creation date","Type","Order number","Net amount","Custom label","Description"\n'
            '"17-Aug-26","Order","1-2","43.67","D1","--"\n"12-Nov-26","Other fee","1-2","-6.55","--","Promoted Listings"\n')
    new_script = True

    def setUp(self):
        csv_text, kanri, case = self.CSV, self.KANRI, self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if "key=k1" not in self.path:
                    body = "forbidden"
                elif "sheet=" in self.path and case.new_script:
                    body = kanri if urllib.parse.quote("管理") in self.path else case.EBAY
                else:  # 古いスクリプトは sheet= を無視して古物台帳を返す
                    body = csv_text
                body = body.encode()
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
        kanri = (self.tmp / "kanri.csv").read_text(encoding="utf-8")
        self.assertEqual(kanri.splitlines(), ["No.,状態,購入日,販売日,仕入(円),商品名,アカウント名(仕入元)",
                                              "A1,MINT/点カビ,2026/07/26,2026/09/01,,Nikon F3,someone"])
        self.assertNotIn("https://x", kanri)  # URL は保存しない
        ebay = (self.tmp / "ebay_report.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(ebay), 3)
        self.assertTrue(ebay[0].startswith("Custom label,Transaction creation date,Type,Order number,Net amount"))
        self.assertIn("Promoted Listings", ebay[2])

    def test_old_script_skips_kanri(self):
        self.new_script = False
        self.assertTrue(self._sync("k1"))
        self.assertFalse((self.tmp / "kanri.csv").exists())

    def test_script_text(self):
        t = ls.script_text("abc", "BOOKID", "SRCID")
        self.assertIn("const BOOK = 'BOOKID'", t)
        self.assertIn("const SRC = 'SRCID'", t)
        for name in ("管理", "ebay売上", "J管理", "出品情報"):
            self.assertIn(f'"{name}"', t)
        self.assertIn("Buyer|Ship to", t)
        self.assertNotIn("__", t)


class LedgerCopyScript(unittest.TestCase):
    """Apps Script を仮の Google（SpreadsheetApp など）で動かす: 元の古物台帳には書き込まない・Claude更新版は足す／空欄を埋めるだけ."""
    MOCK = r"""
const writes = [];
function sheet(book, name, rows, formulas) {
  const s = { rows, formulas: formulas || rows.map((r) => r.map(() => '')) };
  const guard = () => { if (book === 'SRC') throw new Error('元の古物台帳に書き込もうとした: ' + name); };
  s.getDataRange = () => ({ getValues: () => s.rows.map((r) => r.slice()), getDisplayValues: () => s.rows.map((r) => r.map(String)),
                            getFormulas: () => s.formulas.map((r) => r.slice()) });
  s.getLastRow = () => s.rows.length;
  s.getRange = (r, c, nr, nc) => ({
    setValue: (v) => { guard(); writes.push([book, name, r, c, v]); s.rows[r - 1][c - 1] = v; },
    setValues: (vs) => { guard(); vs.forEach((row, i) => { s.rows[r - 1 + i] = row.slice(); }); writes.push([book, name, 'rows', r, vs.length]); } });
  s.clearContents = () => { guard(); s.rows = []; };
  return s;
}
const BOOKS = {
  SRC: { '古物台帳': sheet('SRC', '古物台帳', [
      ['#', '購入日', '販売日', '品目(商品名)', '数量', 'No.', '仕入金額(円)\n送料含む', '販売金額($)\n送料含む', 'アカウント名', 'ヤフオクURL', '備考'],
      ['1', '2026/07/26', '2026/9/1', 'Canon FL 50mm', '1', 'A1', '3400', '78.99', 'kur', 'https://auctions.yahoo.co.jp/jp/auction/f1', ''],
      ['2', '2026/07/26', '2026/11/1', 'Canon FD 50mm', '1', 'A6', '5700', '99.99', 'やふ', 'https://auctions.yahoo.co.jp/jp/auction/p1', ''],
      ['3', '2026/11/10', '', 'Nikon FM2', '1', 'Z9', '20000', '', 'new', 'https://auctions.yahoo.co.jp/jp/auction/z9', 'メモ']]),
    '管理': sheet('SRC', '管理', [['見出しの前'], ['No.', '状態', '購入日', '販売日', '仕入(円)', '商品名', 'アカウント名(仕入元)', '口座'],
                                 ['A1', 'MINT', '2026/07/26', '2026/9/1', '3400', 'Canon FL', 'kur', '秘密']]) },
  BOOK: { '古物台帳': sheet('BOOK', '古物台帳', [
      ['管理番号（Custom label）', '購入日', '品目（商品名）', '数量', '仕入先', '仕入元（出品者・店名）', '仕入合計（円）', '販売日', '販売金額（$・送料込・ドル換算）', '利益（円・参考）'],
      ['A1', '2026/07/26', 'Canon FL 50mm f/1.4（手で直した名前）', '1', 'ヤフオク', 'kur', '3400', '2026/9/1', '78.99', '=X'],
      ['A6', '2026/07/26', 'Canon FD 50mm', '1', 'ヤフオク', 'やふ', '5700', '', '', '']],
      [['', '', '', '', '', '', '', '', '', ''], ['', '', '', '', '', '', '', '', '', '=A'], ['', '', '', '', '', '', '', '', '', '=B']]),
    'メモ': sheet('BOOK', 'メモ', [['触らない']]) } };
const props = {};
const SpreadsheetApp = { openById: (id) => ({
  getSheetByName: (n) => BOOKS[id][n] || null,
  insertSheet: (n) => { if (id === 'SRC') throw new Error('元にシートを足そうとした'); BOOKS[id][n] = sheet(id, n, []); return BOOKS[id][n]; } }) };
const ContentService = { MimeType: { CSV: 'csv' }, createTextOutput: (t) => ({ t, setMimeType() { return this; } }) };
const PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => props[k], setProperty: (k, v) => { props[k] = v; } }) };
const LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
"""
    RUN = r"""
const csv1 = doGet({ parameter: { key: 'k' } }).t;
const status = doGet({ parameter: { key: 'k', sheet: '_status' } }).t;
const kanri = doGet({ parameter: { key: 'k', sheet: '管理' } }).t;
doGet({ parameter: { key: 'k' } });  // 2回目: 何も増えない
console.log(JSON.stringify({ book: BOOKS.BOOK['古物台帳'].rows, copy: BOOKS.BOOK['写し_管理'].rows, memo: BOOKS.BOOK['メモ'].rows,
  status, kanri, csv1, bad: doGet({ parameter: { key: 'x' } }).t, writes }));
"""

    def test_copy(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        code = self.MOCK + ls.script_text("k", "BOOK", "SRC") + self.RUN
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        self.assertEqual(r.stderr, "")
        out = json.loads(r.stdout)
        book = out["book"]
        self.assertEqual(len(book), 4)                                   # Z9 だけ足す（2回目は増えない）
        self.assertEqual(book[1][2], "Canon FL 50mm f/1.4（手で直した名前）")  # 入っている値は上書きしない
        self.assertEqual(book[2][7:9], ["2026/11/1", "99.99"])           # 空欄（販売日・販売金額）だけ埋める
        self.assertEqual(book[2][9], "")                                 # 数式のある空欄は埋めない
        self.assertEqual(book[3][:9], ["Z9", "2026/11/10", "Nikon FM2", "1", "ヤフオク", "new", "20000", "", ""])
        self.assertEqual(out["copy"], [["No.", "状態", "購入日", "販売日", "仕入(円)", "商品名", "アカウント名(仕入元)"],
                                       ["A1", "MINT", "2026/07/26", "2026/9/1", "3400", "Canon FL", "kur"]])  # 使う列だけ（口座は写さない）
        self.assertEqual(out["memo"], [["触らない"]])
        self.assertIn("1行を追加・空欄2か所", out["status"])
        self.assertTrue(out["kanri"].startswith('"No.","状態"'))
        self.assertIn('"Z9"', out["csv1"])
        self.assertEqual(out["bad"], "forbidden")
        self.assertFalse([w for w in out["writes"] if w[0] == "SRC"])    # 元の古物台帳には一度も書かない


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
