#!/usr/bin/env python3
"""古物台帳（Google スプレッドシート）を自動で data/ledger.csv に取り込む.

スプレッドシートに貼った Apps Script（下の APPS_SCRIPT）が、合言葉付きの専用 URL で「古物台帳」シートを CSV で返す。
?sheet=… を付けると、許可したほかのシートも返す（2026-11-15 追加）。購入者の名前・住所の列（Buyer…・Ship to…）は
スクリプトの側で取り除いてから返す。ほかのシートからは EXTRA_SHEETS の列だけを data/ に保存する（グラフ用）:
    管理      → data/kanri.csv        No.・状態・購入日・販売日・仕入・商品名・仕入元（状態と、古物台帳に無い商品の仕入額）
    ebay売上  → data/ebay_report.csv  eBay の取引レポートの日付・種類・注文番号・手取り・手数料・Custom label
                                      （eBay API の取引明細が無いときの代わり。data/ebay_sales.csv は ebay_api.py orders が使う）
    J管理     → data/junk.csv         ジャンク品のヤフオク再販の仕入・実販売・日付
    出品情報  → data/item_types.csv   管理番号・商品種別
Mac はその URL を読むだけ。URL と合言葉はキーチェーンに保存し、GitHub やチャットには書かない。
スプレッドシートの ID もこのファイルには書かない（スクリプトはそのスプレッドシートの中で動く）。

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
                                  "Gross transaction amount", "Transaction currency", "Description"]),
    "J管理": ("junk.csv", ["No.", "仕入(円)", "実販売(円)", "売上−仕入(円)", "購入日", "販売日", "保管", "ヤフオク出品"]),
    "出品情報": ("item_types.csv", ["管理番号", "商品種別"]),
}
BACKUP = DATA / "backup"
SERVICE = "aurora-ledger"
SHEET = "古物台帳"

sys.path.insert(0, str(HERE))
import keychain  # noqa: E402

APPS_SCRIPT = """// aurora: 古物台帳を CSV で返す（読むだけ。スプレッドシートは変更しない）（Mac の tools/ledger_sync.py が読む）。合言葉が一致したときだけ返す
const KEY = '__KEY__';
const BOOK = '__BOOK__';  // 古物台帳のスプレッドシート（この Apps Script は古物台帳とは別のプロジェクト。古物台帳の中のスクリプトには触らない）
const SHEETS = [__SHEETS__];  // 返してよいシート（最初のものが既定）
const DROP = /^(Buyer|Ship to)/;  // 購入者の名前・住所の列は返さない
function doGet(e) {
  if (!e || !e.parameter || e.parameter.key !== KEY) {
    return ContentService.createTextOutput('forbidden');
  }
  const name = SHEETS.indexOf(e.parameter.sheet) >= 0 ? e.parameter.sheet : SHEETS[0];
  const sh = (BOOK ? SpreadsheetApp.openById(BOOK) : SpreadsheetApp.getActive()).getSheetByName(name);
  if (!sh) return ContentService.createTextOutput('NO_SHEET:' + name);  // そのシートが無い（Mac 側は飛ばすだけ）
  let rows = sh.getDataRange().getDisplayValues();
  const head = rows.slice(0, 10).find(r => r.some(v => DROP.test(v)));
  if (head) {
    const keep = head.map((v, i) => DROP.test(v) ? -1 : i).filter(i => i >= 0);
    rows = rows.map(r => keep.map(i => r[i]));
  }
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\\n');
  return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.CSV);
}
"""


def script_text(key: str, book: str = "") -> str:
    names = ", ".join(f"'{n}'" for n in [SHEET, *EXTRA_SHEETS])
    return APPS_SCRIPT.replace("__KEY__", key).replace("__BOOK__", book).replace("__SHEETS__", names)


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
        why = "合言葉が違います（setup をやり直す）" if text.strip() == "forbidden" else "古物台帳シートの形ではありません"
        print(f"古物台帳: {why}", file=sys.stderr)
        return None
    sync_extra(url, key, dry_run)
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


def cmd_script() -> None:
    """スクリプトを新しくしたとき: 貼り替えて新バージョンでデプロイ（URL・合言葉はそのまま）."""
    key = keychain.get(SERVICE, "key")
    if not key or not keychain.get(SERVICE, "url"):
        print("まだ設定されていません。先に python3 tools/ledger_sync.py setup を実行してください。")
        sys.exit(1)
    book = keychain.get(SERVICE, "book") or ""
    script = script_text(key, book)
    copied = keychain.copy_to_clipboard(script)
    head = "新しいスクリプトをクリップボードにコピーしました。" if copied else "下のスクリプトをコピーしてください:\n" + script
    where = ("https://script.google.com/home →「aurora 古物台帳」のプロジェクトを開く" if book
             else "古物台帳のスプレッドシート →「拡張機能」→「Apps Script」（前に aurora のスクリプトを貼った所）")
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
    book = ""
    while not book:
        book = book_id(input("① 取り込む古物台帳のスプレッドシートの URL（ブラウザのアドレス欄）を貼り付けて Enter: "))
        if not book:
            print("   スプレッドシートの URL（https://docs.google.com/spreadsheets/d/…）を貼ってください。")
    keychain.set(SERVICE, "book", book)  # キーチェーンだけに保存（リポジトリには書かない）
    key = keychain.get(SERVICE, "key") or secrets.token_urlsafe(24)
    keychain.set(SERVICE, "key", key)
    script = script_text(key, book)
    copied = keychain.copy_to_clipboard(script)
    head = "② スクリプトをクリップボードにコピーしました。" if copied else "② 下のスクリプトをコピーしてください:\n" + script
    print(f"\n{head}\n{GUIDE}")
    url = input("③ その URL（script.google.com で始まる）を貼り付けて Enter: ").strip()
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
