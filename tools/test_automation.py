"""自動化（暗号化配信・古物台帳・頒布会の添付・自動実行）のテスト. python -m unittest discover -s tools で実行."""
import csv
import http.server
import io
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
  s.appendRow = (r) => { guard(); s.rows.push(r.slice()); writes.push([book, name, 'append']); };
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
  getSheets: () => Object.values(BOOKS[id]),
  insertSheet: (n, at) => { if (id === 'SRC') throw new Error('元にシートを足そうとした');
    if (at !== Object.keys(BOOKS[id]).length) throw new Error('右端以外にシートを足した: ' + n);
    BOOKS[id][n] = sheet(id, n, []); return BOOKS[id][n]; } }) };
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


class StoreInbox(unittest.TestCase):
    """店舗アプリ用の鍵（2026-11-20）: 共有_店舗 に行を足すことだけできる。数式は文字にする。元の古物台帳には書かない."""
    SK = "storekey-0123456789abcdef"

    def run_js(self, run: str, store_key: str = SK) -> dict:
        if not shutil.which("node"):
            self.skipTest("node がありません")
        code = LedgerCopyScript.MOCK + ls.script_text("k", "BOOK", "SRC", store_key) + run
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True)
        self.assertEqual(r.stderr, "")
        return json.loads(r.stdout)

    def test_append_only(self):
        out = self.run_js(r"""
const post = (q, body) => doPost({ parameter: q, postData: { contents: JSON.stringify(body) } }).t;
const ok = post({ store: '%s' }, { rows: [['2026-11-20 10:00', '買った', '=HYPERLINK("x")', '30000', '○', '31500', '300', 'キタムラ', '+メモ', 'v'],
                                          ['2026-11-20 10:01', 'あとで調べる', 'Pentax MX']] });
const bad = post({ store: 'wrong' }, { rows: [['x']] });
const other = post({ store: '%s', sheet: '入結果' }, { rows: [['y']] });
const emp = post({ store: '%s' }, { rows: [] });
const noKey = doGet({ parameter: { store: '%s' } }).t;
console.log(JSON.stringify({ ok, bad, other, empty: emp, noKey, rows: BOOKS.BOOK['共有_店舗'].rows, writes }));
""" % (self.SK, self.SK, self.SK, self.SK))
        self.assertEqual(out["ok"], "OK:2")
        self.assertEqual(out["bad"], "forbidden")
        self.assertEqual(out["noKey"], "forbidden")      # 店舗の鍵では読めない
        self.assertEqual(out["empty"], "EMPTY")
        rows = out["rows"]
        self.assertEqual(rows[0][:3], ["日時", "種類", "商品名"])
        self.assertEqual(rows[1][2], "'=HYPERLINK(\"x\")")  # 数式にしない
        self.assertEqual(rows[1][8], "'+メモ")
        self.assertEqual(rows[2][:3], ["2026-11-20 10:01", "あとで調べる", "Pentax MX"])
        self.assertEqual(len(rows[2]), 10)
        self.assertEqual(len(rows), 4)                    # other の1行も 共有_店舗 にだけ入る（ほかのシートへは書けない）
        self.assertFalse([w for w in out["writes"] if w[0] == "SRC" or w[1] != "共有_店舗"])

    def test_empty_store_key_is_off(self):
        out = self.run_js(r"""
console.log(JSON.stringify({ r: doPost({ parameter: { store: '' }, postData: { contents: '{"rows":[["x"]]}' } }).t }));
""", store_key="")
        self.assertEqual(out["r"], "forbidden")


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


class DecisionLog(unittest.TestCase):
    """Claude更新版の「意思決定ログ」シート → knowledge/意思決定ログ.md に、まだ無い行だけ追記."""

    def test_append(self):
        import tempfile
        from pathlib import Path
        import ledger_sync as ls
        csv_text = '"日付","決めたこと"\n"2026-11-16","入札は利益2,500円以上"\n"2026-11-17","sold も送料無料なら−$30"\n'
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / "意思決定ログ.md"
            md.write_text("# 意思決定ログ\n- 2026-11-16 入札は利益2,500円以上\n", encoding="utf-8")
            self.assertEqual(ls.append_decisions(csv_text, md), 1)
            self.assertEqual(ls.append_decisions(csv_text, md), 0)  # 2回目は足さない
            self.assertIn("- 2026-11-17 sold も送料無料なら−$30（Claude が記録）", md.read_text(encoding="utf-8"))


class BidUpload(unittest.TestCase):
    """data/bid_log.csv → 「入札結果」シート（必要な列だけ送る）."""

    def test_upload(self):
        from unittest import mock
        import ledger_sync as ls
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "bid_log.csv"
            log.write_text("auction_id,title,max_bid,final_price,result,secret\nx1,Nikon F3,10000,12000,負け,zzz\n",
                           encoding="utf-8")
            sent = []
            with mock.patch.object(ls.keychain, "get", side_effect=lambda s, k: "v"):
                n = ls.upload_bids(log=log, post=lambda body: sent.append(body) or "OK:1")
            self.assertEqual(n, 1)
            self.assertIn("x1,Nikon F3,10000,12000,負け", sent[0])
            self.assertNotIn("zzz", sent[0])
            with mock.patch.object(ls.keychain, "get", side_effect=lambda s, k: "v"):
                self.assertIsNone(ls.upload_bids(log=log, post=lambda body: "NO_SHEET:入札結果"))  # 古いスクリプト


class DashboardUpload(unittest.TestCase):
    """数字のグラフの中身（JSON）→ 古物台帳「Mac_グラフ」. 分けて送り、つなげると元に戻る."""

    def test_roundtrip(self):
        import json
        import ledger_sync as ls
        data = {"generated": "2026/11/17 07:00", "x": "1,234", "big": "あ" * 70000, "q": 'a"b\nc'}
        text = json.dumps(data, ensure_ascii=False)
        rows = list(csv.reader(io.StringIO(ls.dashboard_csv(text))))
        self.assertEqual(rows[0], ["part"])
        self.assertTrue(all(r[0].startswith("j:") and len(r[0]) <= ls.DASH_CHUNK + 2 for r in rows[1:]))
        self.assertEqual(json.loads("".join(r[0][2:] for r in rows[1:])), data)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "dashboard_data.json"
            path.write_text(text, encoding="utf-8")
            sent = []
            with mock.patch.object(ls.keychain, "get", side_effect=lambda s, k: "v"):
                self.assertEqual(ls.upload_dashboard(path, post=lambda body: sent.append(body) or "OK:3"), 3)
                self.assertIsNone(ls.upload_dashboard(path, post=lambda body: "NO_SHEET:Mac_グラフ"))
            with mock.patch.object(ls.keychain, "get", return_value=None):
                self.assertIsNone(ls.upload_dashboard(path, post=lambda body: "OK"))


class SearchUrlSync(unittest.TestCase):
    """「検索URL」シート → data/search_urls.txt に無い URL だけ足す."""

    def test_append(self):
        import ledger_sync as ls
        u1 = "https://auctions.yahoo.co.jp/search/search?vo=a&auccat=23636"
        u2 = "https://auctions.yahoo.co.jp/search/search?vo=b&auccat=23636"
        text = f'"URL","メモ"\n"{u1}","中判"\n"{u2}","コンパクト"\n"https://example.com/x","対象外"\n'
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "search_urls.txt"
            dest.write_text(u1 + "\n", encoding="utf-8")
            self.assertEqual(ls.append_search_urls(text, dest), 1)
            self.assertEqual(ls.append_search_urls(text, dest), 0)
            body = dest.read_text(encoding="utf-8")
            self.assertIn("# コンパクト（Claude が追加）\n" + u2, body)
            self.assertNotIn("example.com", body)

    def test_remove(self):
        import ledger_sync as ls
        u1 = "https://auctions.yahoo.co.jp/search/search?vo=a&auccat=23636"
        u2 = "https://auctions.yahoo.co.jp/search/search?vo=b"
        u3 = "https://auctions.yahoo.co.jp/search/search?vo=b&auccat=23636"
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "search_urls.txt"
            dest.write_text(f"{u1}\n# 中判（Claude が追加）\n{u2}\n", encoding="utf-8")
            text = f'"URL","メモ","状態"\n"{u2}","中判（古い）","削除"\n"{u3}","中判",""\n'
            self.assertEqual(ls.append_search_urls(text, dest), 1)
            body = dest.read_text(encoding="utf-8")
            self.assertNotIn(u2, body.splitlines())
            self.assertEqual(body.count("中判（Claude が追加）"), 1)  # 古い行の見出しは消え、新しい行の見出しだけ
            self.assertIn(u1, body)
            self.assertIn(u3, body)
            self.assertEqual(ls.append_search_urls(text, dest), 0)


class SingleBook(unittest.TestCase):
    """1冊運用（写し先＝元の台帳）では「古物台帳_詳細」に写して返す."""

    def test_script(self):
        import ledger_sync as ls
        s = ls.script_text("k", "BOOKID", "BOOKID")
        self.assertIn("const DST = BOOK === SRC ? '古物台帳_詳細' : LEDGER;", s)
        self.assertIn("d = dstBook.getSheetByName(DST)", s)
        self.assertIn("const name = !p ? DST :", s)


class SheetMemoAndKnowledge(unittest.TestCase):
    """チャットで決めた相場（共有_相場メモ）・講座の要点（共有_ナレッジ）・未登録機種（Mac_未登録機種）."""

    def test_sheet_memo(self):
        text = "機種,相場$（送料別）,状態,日付\nNikon FM2,$190,MINT,2026-11-18\n空欄,,,\nCanon AE-1,abc,,\n"
        self.assertEqual(ls.sheet_memo_rows(text, 40.0), {"Nikon FM2": (230.0, "相場メモ(MINT)")})

    def test_research_rows(self):
        # 講師価格を優先、無ければ売値$。送料無料とみなして −$30 し、計算用に送料 $40 を足す（2026-11-19）
        text = ("品名,売値$,講師価格,状態,日付\nContax Planar 50mm f/1.4 AEJ,189.99,,Mint,2026/7/20\n"
                "Canon FD 35mm f/3.5 S.C.,79.00,60.00,N Mint,2026/7/21\n空,,,,\nNikon X,20,,,\n")
        rows = ls.research_rows(text, 40.0, 30.0)
        self.assertEqual([(r["title"], r["sold_price_usd"], r["source"]) for r in rows],
                         [("Contax Planar 50mm f/1.4 AEJ", 199.99, "リサーチ(Mint)"), ("Canon FD 35mm f/3.5 S.C.", 70.0, "リサーチ(N Mint)")])
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ls, "fetch", return_value=text):
            self.assertEqual(ls.sync_research("u", "k", dest=Path(d) / "r.csv"), 2)
            self.assertIn("リサーチ(Mint)", (Path(d) / "r.csv").read_text(encoding="utf-8-sig"))

    def test_transactions_csv(self):
        # eBay API の取引 → 「ebay売上」と同じ列（A 日付・B 種類・K 入金・U SKU・AI 総額・AJ 通貨・AK レート）
        fin = [{"date": "2026-11-16", "type": "SALE", "booking": "CREDIT", "order_id": "O1", "amount": "123.3", "amount_original": ""},
               {"date": "2026-11-14", "type": "SALE", "booking": "CREDIT", "order_id": "O2", "amount": "599.63", "amount_original": "915.00 AUD"},
               {"date": "2026-11-16", "type": "REFUND", "booking": "DEBIT", "order_id": "O1", "amount": "21.88", "amount_original": ""},
               {"date": "2026-11-09", "type": "NON_SALE_CHARGE", "booking": "DEBIT", "order_id": "O1", "amount": "3.39", "amount_original": ""}]
        orders = [{"order_id": "O1", "sku": "S39", "currency": "USD", "item_price": "139.99", "quantity": "1", "shipping": "0"},
                  {"order_id": "O2", "sku": "N2", "currency": "AUD", "item_price": "1070.32", "quantity": "1", "shipping": "0"}]
        rows = list(csv.reader(io.StringIO(ls.transactions_csv(fin, orders))))
        self.assertEqual(rows[1][0], "Transaction creation date")
        a, b, k, u, ai = 0, 1, 10, 20, 34
        self.assertEqual([rows[2][a], rows[2][b], rows[2][k], rows[2][u], rows[2][ai]], ["d:16-Nov-26", "Order", "123.3", "S39", "139.99"])
        self.assertAlmostEqual(float(rows[3][ai]), 1070.32 * 599.63 / 915.00, places=1)   # 豪ドルの注文は入金のレートで USD に
        self.assertEqual([rows[4][b], rows[4][k], rows[4][ai]], ["Refund", "-21.88", "-21.88"])
        self.assertEqual([rows[1][2], rows[2][2], rows[4][2]], ["Order number", "O1", "O1"])   # C 注文番号（管理の「返品」が返金を注文で結ぶ）
        import build_dashboard as bd
        self.assertEqual([bd.is_junk_no(x) for x in ("J1", "J11/B4 K1", "G4M", "S1", "")], [True, True, False, False, False])
        self.assertEqual([rows[5][a], rows[5][b], rows[5][k]], ["d:9-Nov-26", "Other fee", "-3.39"])

    def test_sales_csv(self):
        """管理番号ごとの販売 → 「Mac_販売」（2026-11-23。古物台帳_詳細の販売の欄がここを引く。番号は作り物）."""
        fin = [{"date": "2026-11-16", "type": "SALE", "booking": "CREDIT", "order_id": "O1", "amount": "123.3", "amount_original": ""},
               {"date": "2026-11-16", "type": "NON_SALE_CHARGE", "fee_type": "AD_FEE", "booking": "DEBIT", "order_id": "", "references": "ORDER_ID:O1", "amount": "3.39"},
               {"date": "2026-11-14", "type": "SALE", "booking": "CREDIT", "order_id": "O2", "amount": "599.63", "amount_original": "915.00 AUD"},
               {"date": "2026-11-10", "type": "SALE", "booking": "CREDIT", "order_id": "O3", "amount": "189.6"},      # 全額返金
               {"date": "2026-11-12", "type": "REFUND", "booking": "DEBIT", "order_id": "O3", "amount": "189.6"},
               {"date": "2026-11-20", "type": "SALE", "booking": "CREDIT", "order_id": "O4", "amount": "180.0"},      # 同じ番号で出し直して売れた
               {"date": "2026-11-05", "type": "SALE", "booking": "CREDIT", "order_id": "O5", "amount": "100.0"},      # 一部返金
               {"date": "2026-11-08", "type": "REFUND", "booking": "DEBIT", "order_id": "O5", "amount": "20.0"},
               {"date": "2026-08-10", "type": "SALE", "booking": "CREDIT", "order_id": "O6", "amount": "50.0"},       # SKU が合っていない頃
               {"date": "2026-11-01", "type": "PAYOUT", "booking": "DEBIT", "order_id": "", "amount": "500"}]
        o = lambda oid, sku, day, price, cur="USD", **k: dict({"order_id": oid, "sku": sku, "date": day, "currency": cur, "item_price": price,  # noqa: E731
                                                              "quantity": "1", "shipping": "0", "country": "US"}, **k)
        orders = [o("O1", "S39", "2026-11-16", "139.99"), o("O2", "N2", "2026-11-14", "1070.32", "AUD", country="AU"),
                  o("O3", "Z1", "2026-11-10", "214.99", payment_status="FULLY_REFUNDED"), o("O4", "Z1", "2026-11-20", "204.99"),
                  o("O5", "E3", "2026-11-05", "119.99"), o("O6", "5", "2026-08-10", "55.99"), o("O7", "X9", "2026-11-22", "99.99")]
        rows = {r[0]: r for r in csv.reader(io.StringIO(ls.sales_csv(fin, orders)))}
        self.assertEqual(rows["管理番号"][1:4], ["販売日", "eBay注文番号", "購入者の国"])
        self.assertEqual(rows["S39"][1:9], ["2026/11/16", "O1", "US", "139.99", "16.69", "3.39", "", "119.91"])   # 販売−手数料−広告費＝入金
        self.assertAlmostEqual(float(rows["N2"][4]), 1070.32 * 599.63 / 915.00, places=1)                          # 豪ドルは入金のレートで USD に
        self.assertEqual(rows["Z1"][1:3], ["2026/11/20", "O4"])                                                     # 返金の後に売れた方
        self.assertEqual(rows["E3"][1:9], ["2026/11/5", "O5", "US", "119.99", "19.99", "", "20.0", "80.0"])         # 一部返金は販売日を残す
        self.assertNotIn("5", rows)      # 8/18 より前の SKU は使わない
        self.assertNotIn("X9", rows)     # まだ入金の明細が無い注文は入れない
        only = {r[0]: r for r in csv.reader(io.StringIO(ls.sales_csv(fin[:5], orders[:3])))}
        self.assertEqual(only["Z1"][1], "")                 # 全額返金だけなら販売日は空（在庫に戻る）
        self.assertEqual(only["Z1"][7:9], ["189.6", "0.0"])

    def test_returns_csv(self):
        # 返品の一覧 → 「Mac_返品」: 決めた列だけを送る（ほかの列があっても送らない）
        rows = [{"kind": "返品", "sku": "S1", "title": "Nikon F3", "order_date": "2026-10-01", "date": "2026-10-10",
                 "verdict": "返品・返金済み", "refund": "120", "refund_currency": "USD", "reason": "NOT_AS_DESCRIBED",
                 "status": "CLOSED", "order_id": "X-1", "id": "R1", "return_tracking": "T1"}]
        out = list(csv.reader(io.StringIO(ls.returns_csv(rows))))
        self.assertEqual(out[0], ls.RETURNS_COLS)
        self.assertEqual(out[1][ls.RETURNS_COLS.index("reason")], "NOT_AS_DESCRIBED")
        self.assertNotIn("X-1", out[1])

    def test_knowledge(self):
        text = '"ファイル名","本文"\n"狙い目スペック","# 狙い目\n50mm は f/1.2 より明るい"\n"a/b","x"\n'
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ls, "fetch", return_value=text):
            self.assertEqual(ls.sync_knowledge("u", "k", root=Path(d)), 2)
            self.assertIn("f/1.2", (Path(d) / "講座" / "狙い目スペック.md").read_text(encoding="utf-8"))
            self.assertTrue((Path(d) / "講座" / "a_b.md").exists())  # ファイル名の / は使わない
            self.assertEqual(ls.sync_knowledge("u", "k", root=Path(d)), 0)  # 変化なし

    def test_upload_unpriced(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "unpriced_models_20261118.csv").write_text(
                "title,sold_price_usd,source,件数,ヤフオク現在価格の中央値,出品例,eBay販売済みURL\nNikon FM2,,相場メモ,5,12000,例,https://x\n",
                encoding="utf-8-sig")
            sent = []
            with mock.patch.object(ls.keychain, "get", side_effect=lambda s, k: "v"):
                self.assertEqual(ls.upload_unpriced(Path(d), post=lambda b: sent.append(b) or "OK:1"), 1)
            self.assertIn("Nikon FM2,5,12000", sent[0])


class EbayMessages(unittest.TestCase):
    """eBay の受信メッセージ（見出し）→ 1通1行。購入者の名前は入れない."""

    def test_parse(self):
        import ebay_api
        xml = b"""<?xml version="1.0"?><GetMyMessagesResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>
<Messages><Message><Sender>buyer_x</Sender><Subject>Question about item</Subject><MessageID>111</MessageID>
<ReceiveDate>2026-11-17T03:00:00.000Z</ReceiveDate><ItemID>3775</ItemID><Read>true</Read><Replied>true</Replied><MessageType>AskSellerQuestion</MessageType></Message>
<Message><Sender>buyer_y</Sender><Subject>Hi</Subject><MessageID>112</MessageID><ReceiveDate>2026-11-17T05:00:00.000Z</ReceiveDate><ItemID>3776</ItemID><Read>false</Read></Message>
</Messages></GetMyMessagesResponse>"""
        rows = ebay_api.parse_messages(xml)
        self.assertEqual([r["message_id"] for r in rows], ["112", "111"])
        self.assertEqual((rows[1]["replied"], rows[0]["replied"], rows[0]["read"]), (True, False, False))
        self.assertNotIn("buyer_x", json.dumps(rows))
        self.assertEqual(ebay_api.hide_names("camera_x has sent a question about item #1 - Cap"), "(相手) has sent a question about item #1 - Cap")
        self.assertEqual(ebay_api.hide_names("Re: shop-92 has sent a question about item #2"), "Re: (相手) has sent a question about item #2")
        self.assertEqual(ebay_api.hide_names("jose sent a message about Canon"), "(相手) sent a message about Canon")
        self.assertEqual(ebay_api.hide_names("Order 19-1 has been refunded"), "Order 19-1 has been refunded")

    def test_post_sheet(self):
        with mock.patch.object(ls.keychain, "get", side_effect=lambda s, k: "v"):
            self.assertTrue(ls.post_sheet("Mac_メッセージ", "a\n1\n", post=lambda b: "OK:1"))
            self.assertFalse(ls.post_sheet("Mac_メッセージ", "a\n1\n", post=lambda b: "NO_SHEET:x"))

    def test_msg_plist(self):
        p = schedule.msg_plist()
        self.assertEqual(p["StartInterval"], 3600)
        self.assertIn("--upload", p["ProgramArguments"])
