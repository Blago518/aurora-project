#!/usr/bin/env python3
"""古物台帳を 元の古物台帳 → Claude更新版 → Mac（data/ledger.csv）と毎日取り込む.

Apps Script（下の APPS_SCRIPT）は script.google.com の独立したプロジェクトに置く（どちらのスプレッドシートの中のスクリプトにも触らない）。
合言葉付きの専用 URL を Mac が読むと、まず 元の古物台帳（読むだけ）→ Claude更新版 へ写す:
    古物台帳: Claude更新版に無い No. の行を足し、ある行は空欄だけ埋める（入っている値・数式は上書きしない。行は消さない）
    管理・ebay売上・J管理・出品情報: Claude更新版の「写し_シート名」に、Mac が使う列だけを毎回写す
    意思決定ログ: Claude更新版の「意思決定ログ」シート（Claude がクラウドから書く）を、knowledge/意思決定ログ.md に追記する
そのあと Claude更新版の古物台帳を CSV で返す。?sheet=… を付けると写しのシートを返す。購入者の名前・住所の列（Buyer…・Ship to…）は
スクリプトの側で取り除いてから返す。ほかのシートからは EXTRA_SHEETS の列だけを data/ に保存する（グラフ用）:
    管理      → data/kanri.csv        No.・状態・購入日・販売日・仕入・商品名・仕入元（状態と、古物台帳に無い商品の仕入額）
    ebay売上  → data/ebay_report.csv  eBay の取引レポートの日付・種類・注文番号・手取り・手数料・Custom label
                                      （eBay API の取引明細が無いときの代わり。data/ebay_sales.csv は ebay_api.py orders が使う）
    J管理     → data/junk.csv         ジャンク品のヤフオク再販の仕入・実販売・日付
    出品情報  → data/item_types.csv   管理番号・商品種別
Mac はその URL を読むだけ。URL と合言葉はキーチェーンに保存し、GitHub やチャットには書かない。
スプレッドシートの ID もこのファイルには書かない（setup で聞いてキーチェーンに保存し、スクリプトに埋め込む）。

使い方（aurora フォルダで）:
    python3 tools/ledger_sync.py setup   # 初回: スクリプトをクリップボードにコピー → 貼って公開 → URL を入力
    python3 tools/ledger_sync.py         # 取り込む（update_all.py が毎回実行する）
    python3 tools/ledger_sync.py script  # スクリプトを新しくしたとき: コピー → 貼り替えて「新バージョン」でデプロイ（URL は同じ）
"""
from __future__ import annotations

import argparse
import csv
import io
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
OUT = DATA / "ledger.csv"
# シート名 → (保存先, 保存する列). 列の1つ目と2つ目がある行を見出しとみなす。これ以外の列（アカウント名・URL など）は保存しない
EXTRA_SHEETS = {
    "管理": ("kanri.csv", ["No.", "状態", "購入日", "販売日", "仕入(円)", "商品名", "アカウント名(仕入元)"]),
    "ebay売上": ("ebay_report.csv", ["Custom label", "Transaction creation date", "Type", "Order number", "Net amount",
                                  "Payout currency", "Quantity", "Item subtotal", "Shipping and handling",
                                  "Final Value Fee - fixed", "Final Value Fee - variable", "International fee",
                                  "Gross transaction amount", "Transaction currency", "Exchange rate", "Description"]),
    "J管理": ("junk.csv", ["No.", "仕入(円)", "実販売(円)", "売上−仕入(円)", "購入日", "販売日", "保管", "ヤフオク出品"]),
    "出品情報": ("item_types.csv", ["管理番号", "商品種別"]),
}
BACKUP = DATA / "backup"
# Claude更新版の「意思決定ログ」シート（Claude がクラウドから書く。列: 日付・決めたこと）→ knowledge/意思決定ログ.md の末尾に追記
DECISION_SHEET = "意思決定ログ"
DECISION_MD = ROOT / "knowledge" / "意思決定ログ.md"
SERVICE = "aurora-ledger"
SHEET = "古物台帳"

sys.path.insert(0, str(HERE))
import keychain  # noqa: E402

APPS_SCRIPT = r"""// aurora: 元の古物台帳 → Claude更新版 へ毎日写し、Claude更新版を CSV で返す（Mac の tools/ledger_sync.py が読む）
// ・元の古物台帳は読むだけ（書き込み・削除は一切しない）
// ・Claude更新版の「古物台帳」: 無い No. の行を下に足す／ある行は空欄だけ埋める（入っている値・数式は上書きしない・行は消さない）
// ・ほかのシートは Claude更新版に「写し_シート名」を作り、Mac が使う列だけを毎回写す（Claude更新版のほかのシートには触らない）
// ・合言葉が一致したときだけ動く。購入者の名前・住所の列（Buyer…・Ship to…）は返さない
const KEY = '__KEY__';
const BOOK = '__BOOK__';  // Claude更新版（写し先・Mac が読む）
const SRC = '__SRC__';    // 元の古物台帳（読むだけ）
const LEDGER = '古物台帳';
const EXTRA = __EXTRA__;  // シート名 → Mac が使う列
const PAIRS = [  // Claude更新版の列（見出しの先頭）← 元の古物台帳の列（見出しの先頭）
  ['管理番号', 'No.'], ['購入日', '購入日'], ['品目', '品目'], ['数量', '数量'], ['仕入元', 'アカウント名'],
  ['仕入合計', '仕入金額'], ['仕入URL', 'ヤフオクURL'], ['販売日', '販売日'], ['販売金額', '販売金額'], ['備考', '備考']];
const OWN = ['意思決定ログ'];  // Claude更新版にあるシートをそのまま返す（Claude が書く。Mac が knowledge/ に追記する）
const DROP = /^(Buyer|Ship to)/;
const nh = (s) => String(s).normalize('NFKC').replace(/\s+/g, '');
const colOf = (head, p) => head.findIndex((h) => h === p || h.startsWith(p));
const empty = (v) => v === '' || v === null || v === undefined;

function syncLedger(dstBook, srcBook) {
  const s = srcBook.getSheetByName(LEDGER), d = dstBook.getSheetByName(LEDGER);
  if (!s || !d) return '古物台帳シートが見つかりません';
  const sv = s.getDataRange().getValues(), dr = d.getDataRange(), dv = dr.getValues(), df = dr.getFormulas();
  const sh = sv[0].map(nh), dh = dv[0].map(nh);
  const pairs = PAIRS.map(([a, b]) => [colOf(dh, a), colOf(sh, b)]).filter(([a, b]) => a >= 0 && b >= 0);
  const dKey = colOf(dh, '管理番号'), sKey = colOf(sh, 'No.');
  if (dKey < 0 || sKey < 0) return 'No. の列が見つかりません';
  const supp = colOf(dh, '仕入先'), sUrl = colOf(sh, 'ヤフオクURL');
  const at = {};
  dv.forEach((r, i) => { const k = String(r[dKey]).trim(); if (i > 0 && k && !(k in at)) at[k] = i; });
  let filled = 0;
  const add = [];
  for (let i = 1; i < sv.length; i++) {
    const r = sv[i], k = String(r[sKey]).trim();
    if (!k) continue;
    if (k in at) {
      if (at[k] < 0) continue;  // 今回足した行
      for (const [a, b] of pairs) {
        if (empty(dv[at[k]][a]) && !df[at[k]][a] && !empty(r[b])) {  // 空欄（数式なし）だけ埋める
          d.getRange(at[k] + 1, a + 1).setValue(r[b]);
          filled++;
        }
      }
    } else {
      const row = new Array(dv[0].length).fill('');
      for (const [a, b] of pairs) row[a] = r[b];
      if (supp >= 0 && sUrl >= 0 && /auctions\.yahoo/.test(String(r[sUrl]))) row[supp] = 'ヤフオク';
      add.push(row);
      at[k] = -1;
    }
  }
  if (add.length) d.getRange(d.getLastRow() + 1, 1, add.length, add[0].length).setValues(add);
  return `古物台帳: ${add.length}行を追加・空欄${filled}か所を埋めた`;
}

function copyExtra(dstBook, srcBook) {
  const out = [];
  for (const name of Object.keys(EXTRA)) {
    const s = srcBook.getSheetByName(name);
    if (!s) { out.push(`${name}: 元に無い`); continue; }
    const want = EXTRA[name], rows = s.getDataRange().getValues();
    const hi = rows.slice(0, 10).findIndex((r) => { const h = r.map(nh); return h.includes(nh(want[0])) && h.includes(nh(want[1])); });
    if (hi < 0) { out.push(`${name}: 見出しが見つからない`); continue; }
    const head = rows[hi].map(nh), idx = want.map((c) => head.indexOf(nh(c)));
    const body = rows.slice(hi + 1).map((r) => idx.map((j) => (j >= 0 ? r[j] : ''))).filter((r) => r.some((v) => !empty(v)));
    const table = [want].concat(body);
    const t = dstBook.getSheetByName('写し_' + name) || dstBook.insertSheet('写し_' + name);
    t.clearContents();
    t.getRange(1, 1, table.length, want.length).setValues(table);
    out.push(`${name}: ${body.length}行`);
  }
  return '写し ' + out.join('・');
}

function syncAll() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const dst = SpreadsheetApp.openById(BOOK), src = SpreadsheetApp.openById(SRC);
    return syncLedger(dst, src) + ' ／ ' + copyExtra(dst, src);
  } catch (err) {
    return '写せませんでした: ' + err;
  } finally {
    lock.releaseLock();
  }
}

function csvOf(rows) {
  const head = rows.slice(0, 10).find((r) => r.some((v) => DROP.test(v)));
  if (head) {
    const keep = head.map((v, i) => (DROP.test(v) ? -1 : i)).filter((i) => i >= 0);
    rows = rows.map((r) => keep.map((i) => r[i]));
  }
  return rows.map((r) => r.map((v) => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\n');
}

function doGet(e) {
  if (!e || !e.parameter || e.parameter.key !== KEY) return ContentService.createTextOutput('forbidden');
  const p = e.parameter.sheet || '';
  const props = PropertiesService.getScriptProperties();
  if (p === '_status') return ContentService.createTextOutput(props.getProperty('last') || '');
  if (!p) props.setProperty('last', new Date().toISOString() + ' ' + syncAll());  // 古物台帳を読むときに、先に元から写す
  const name = !p ? LEDGER : (p in EXTRA ? '写し_' + p : (OWN.includes(p) ? p : null));
  const sh = name && SpreadsheetApp.openById(BOOK).getSheetByName(name);
  if (!sh) return ContentService.createTextOutput('NO_SHEET:' + p);
  return ContentService.createTextOutput(csvOf(sh.getDataRange().getDisplayValues())).setMimeType(ContentService.MimeType.CSV);
}
"""


def script_text(key: str, book: str, src: str) -> str:
    """book＝Claude更新版（写し先・Mac が読む）、src＝元の古物台帳（読むだけ）."""
    import json
    extra = json.dumps({k: cols for k, (_, cols) in EXTRA_SHEETS.items()}, ensure_ascii=False)
    return (APPS_SCRIPT.replace("__KEY__", key).replace("__BOOK__", book).replace("__SRC__", src)
            .replace("__EXTRA__", extra))


def book_id(text: str) -> str:
    """スプレッドシートの URL（…/spreadsheets/d/<ID>/edit…）から ID を取り出す. ID だけでもよい."""
    import re
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text) or re.fullmatch(r"\s*([A-Za-z0-9_-]{20,})\s*", text)
    return m.group(1) if m else ""


GUIDE = """
 1. https://script.google.com/home を開く →［新しいプロジェクト］
    （古物台帳のスプレッドシートの「拡張機能 → Apps Script」は使いません。今あるスクリプトには一切触りません）
 2. 左上の「無題のプロジェクト」を「aurora 古物台帳」に変える
 3. 新しいプロジェクトに最初から入っている3行（function myFunction() {…}）だけを消して、貼り付け（⌘V）→ 保存（⌘S）
 4. 右上「デプロイ」→「新しいデプロイ」→ 歯車 →「ウェブアプリ」
      次のユーザーとして実行: 自分　／　アクセスできるユーザー: 全員
    →「デプロイ」（初回は「アクセスを承認」→ 自分のアカウント → 詳細 → 安全ではないページに移動 → 許可）
 5. 最後に出る「ウェブアプリ」の URL（https://script.google.com/macros/s/…/exec）をコピー
"""


def fetch(url: str, key: str, sheet: str | None = None) -> str:
    sep = "&" if "?" in url else "?"
    q = f"key={urllib.parse.quote(key)}" + (f"&sheet={urllib.parse.quote(sheet)}" if sheet else "")
    with urllib.request.urlopen(f"{url}{sep}{q}", timeout=60) as res:  # Google は転送（302）してから返す
        return res.read().decode("utf-8-sig")


def explain(text: str) -> str:
    """古物台帳が返らなかったときの理由（Google のエラー画面の文章を短く添える）."""
    import re
    t = text.strip()
    if t == "forbidden":
        return "合言葉が違います（python3 tools/ledger_sync.py setup をやり直す）"
    if t.startswith("NO_SHEET"):
        return "Claude更新版に「古物台帳」シートが見つかりません（setup の①の URL を確認）"
    msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", t))).strip()[:300]
    if re.search(r"Sign in|ログイン|accounts\.google", t):
        hint = "デプロイの「アクセスできるユーザー」を「全員」にして、新バージョンでデプロイし直してください"
    elif re.search(r"[Aa]uthori[sz]ation|承認|権限", t):
        hint = "Apps Script の画面で関数 doGet を選んで［実行］→ 許可してから、もう一度試してください"
    else:
        hint = "下の表示を Claude に送ってください"
    return f"古物台帳ではないものが返りました。{hint}\n  （返ってきた内容: {msg or '空'}）"


def sync(dry_run: bool = False) -> bool | None:
    """取り込んだら True、変化なし False、未設定・失敗は None."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    if not url or not key:
        print("古物台帳: 未設定のため飛ばしました（python3 tools/ledger_sync.py setup）", file=sys.stderr)
        return None
    try:
        text = fetch(url, key)
    except (urllib.error.URLError, OSError) as e:
        print(f"古物台帳: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if "販売日" not in text[:5000]:
        print(f"古物台帳: {explain(text)}", file=sys.stderr)
        return None
    try:
        st = fetch(url, key, "_status").strip()
        if st[:2] == "20" and st[4:5] == "-":  # 新しいスクリプトだけが返す（「日時 結果」）
            print(f"Claude更新版への写し: {st}", file=sys.stderr)
    except (urllib.error.URLError, OSError):
        pass
    sync_extra(url, key, dry_run)
    sync_decisions(url, key, dry_run)
    old = OUT.read_text(encoding="utf-8-sig") if OUT.exists() else None
    if old == text:
        print("古物台帳: 変化なし", file=sys.stderr)
        return False
    n = max(0, len(text.strip().splitlines()) - 1)
    if dry_run:
        print(f"古物台帳: {n}行を取り込み予定", file=sys.stderr)
        return True
    DATA.mkdir(exist_ok=True)
    if OUT.exists():
        BACKUP.mkdir(exist_ok=True)
        shutil.copy(OUT, BACKUP / f"ledger_{datetime.now():%Y%m%d-%H%M%S}.csv")
    OUT.write_text(text, encoding="utf-8")
    print(f"古物台帳: {n}行を取り込み → data/ledger.csv", file=sys.stderr)
    return True


def pick_columns(text: str, columns: list[str]) -> list[dict] | None:
    """シートの CSV → columns の列だけの行. 見出し（columns の1つ目と2つ目がある行）が無ければ None."""
    rows = list(csv.reader(io.StringIO(text)))
    norm = lambda r: [h.replace("\n", "").strip() for h in r]  # noqa: E731
    i = next((i for i, r in enumerate(rows[:10]) if columns[0] in norm(r) and columns[1] in norm(r)), None)
    if i is None:
        return None
    head = norm(rows[i])
    idx = {c: head.index(c) for c in columns if c in head}
    out = []
    for r in rows[i + 1:]:
        d = {c: (r[j].strip() if j < len(r) else "") for c, j in idx.items()}
        if any(d.values()):
            out.append(d)
    return out


def sync_extra(url: str, key: str, dry_run: bool = False) -> dict[str, int | None]:
    """古物台帳以外のシートの必要な列 → data/*.csv. 古いスクリプト（そのシートを返さない）なら None."""
    result: dict[str, int | None] = {}
    for sheet, (fname, columns) in EXTRA_SHEETS.items():
        try:
            text = fetch(url, key, sheet)
            if text.startswith("NO_SHEET:"):  # 取り込み元のスプレッドシートにそのシートが無い → 前のデータを残して飛ばす
                print(f"{sheet}シート: 取り込み元に無いので飛ばしました（前のデータはそのまま）", file=sys.stderr)
                result[sheet] = None
                continue
            rows = pick_columns(text, columns)
        except (urllib.error.URLError, OSError) as e:
            print(f"{sheet}シート: 読み込めませんでした（{e}）", file=sys.stderr)
            result[sheet] = None
            continue
        if rows is None:
            print(f"{sheet}シート: 取り込めませんでした。スプレッドシートの Apps Script が古いためです"
                  "（python3 tools/ledger_sync.py script で貼り替え）", file=sys.stderr)
            result[sheet] = None
            continue
        result[sheet] = len(rows)
        if dry_run:
            print(f"{sheet}シート: {len(rows)}行を取り込み予定", file=sys.stderr)
            continue
        DATA.mkdir(exist_ok=True)
        with open(DATA / fname, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        print(f"{sheet}シート: {len(rows)}行 → data/{fname}", file=sys.stderr)
    return result


def append_decisions(text: str, md: Path = DECISION_MD, dry_run: bool = False) -> int:
    """「意思決定ログ」シートの CSV → まだ md に無い行を「- 日付 決めたこと」で末尾に足す. 足した行数を返す."""
    rows = pick_columns(text, ["日付", "決めたこと"])
    if not rows:
        return 0
    have = md.read_text(encoding="utf-8") if md.exists() else ""
    new = []
    for r in rows:
        what = " ".join(r.get("決めたこと", "").split())
        if what and what not in have and what not in "\n".join(new):
            new.append(f"- {r.get('日付', '').strip()} {what}（Claude が記録）".replace("-  ", "- "))
    if new and not dry_run:
        md.parent.mkdir(parents=True, exist_ok=True)
        with open(md, "a", encoding="utf-8") as f:
            f.write(("" if not have or have.endswith("\n") else "\n") + "\n".join(new) + "\n")
    return len(new)


def sync_decisions(url: str, key: str, dry_run: bool = False) -> int | None:
    """Claude更新版の「意思決定ログ」シート → knowledge/意思決定ログ.md（シートやスクリプトが無ければ何もしない）."""
    try:
        text = fetch(url, key, DECISION_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"意思決定ログ: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "決めたこと" not in text[:2000]:
        return None  # 古いスクリプト、またはシートがまだ無い
    n = append_decisions(text, dry_run=dry_run)
    if n:
        print(f"意思決定ログ: {n}件を{'追記予定' if dry_run else '追記'} → knowledge/意思決定ログ.md", file=sys.stderr)
    return n


def cmd_script() -> None:
    """スクリプトを新しくしたとき: 貼り替えて新バージョンでデプロイ（URL・合言葉はそのまま）."""
    key = keychain.get(SERVICE, "key")
    if not key or not keychain.get(SERVICE, "url"):
        print("まだ設定されていません。先に python3 tools/ledger_sync.py setup を実行してください。")
        sys.exit(1)
    book, src = keychain.get(SERVICE, "book") or "", keychain.get(SERVICE, "src") or ""
    if not (book and src):
        print("先に python3 tools/ledger_sync.py setup を実行してください（Claude更新版と元の古物台帳の URL を聞きます）。")
        sys.exit(1)
    script = script_text(key, book, src)
    copied = keychain.copy_to_clipboard(script)
    head = "新しいスクリプトをクリップボードにコピーしました。" if copied else "下のスクリプトをコピーしてください:\n" + script
    where = "https://script.google.com/home →「aurora 古物台帳」のプロジェクトを開く"
    print(f"""
{head}

 1. {where}
 2. aurora のスクリプト（先頭が「// aurora:」の部分）だけを選んで、貼り付けて置き換え（⌘V）→ 保存（⌘S）
    （ほかのスクリプトは消さない）
 3. 右上「デプロイ」→「デプロイを管理」→ 鉛筆マーク（編集）
    → バージョン:「新バージョン」→「デプロイ」（URL は変わりません）
 4. 終わったら Enter
""")
    input("Enter を押すと試しに読み込みます…")
    sync(dry_run=True)


def cmd_setup() -> None:
    print("\n古物台帳の自動取り込みを設定します（初回だけ・約5分）。")
    def ask(q: str) -> str:
        while True:
            v = book_id(input(q))
            if v:
                return v
            print("   スプレッドシートの URL（https://docs.google.com/spreadsheets/d/…）を貼ってください。")
    book = ask("① Claude更新版のスプレッドシートの URL（ブラウザのアドレス欄）を貼り付けて Enter: ")
    src = ask("② 元の古物台帳のスプレッドシートの URL を貼り付けて Enter（読むだけ。変更しません）: ")
    if src == book:
        print("①と②が同じスプレッドシートです。もう一度 setup を実行してください。")
        sys.exit(1)
    keychain.set(SERVICE, "book", book)  # キーチェーンだけに保存（リポジトリには書かない）
    keychain.set(SERVICE, "src", src)
    key = keychain.get(SERVICE, "key") or secrets.token_urlsafe(24)
    keychain.set(SERVICE, "key", key)
    script = script_text(key, book, src)
    copied = keychain.copy_to_clipboard(script)
    head = "③ スクリプトをクリップボードにコピーしました。" if copied else "③ 下のスクリプトをコピーしてください:\n" + script
    print(f"\n{head}\n{GUIDE}")
    url = input("④ その URL（script.google.com で始まる）を貼り付けて Enter: ").strip()
    if not url.startswith("https://script.google.com/"):
        print("URL が違うようです。スプレッドシートの URL ではなく、デプロイ後に出る「ウェブアプリ」の URL"
              "（https://script.google.com/macros/s/…/exec）です。もう一度 setup を実行してください。")
        sys.exit(1)
    keychain.set(SERVICE, "url", url)
    print("保存しました。試しに読み込みます…")
    sync(dry_run=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="古物台帳を data/ledger.csv に取り込む")
    ap.add_argument("cmd", nargs="?", choices=["sync", "setup", "script"], default="sync")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "setup":
        cmd_setup()
    elif args.cmd == "script":
        cmd_script()
    else:
        sync(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
