#!/usr/bin/env python3
"""古物台帳（Google スプレッドシート）を自動で data/ledger.csv に取り込む.

スプレッドシートに貼った Apps Script（下の APPS_SCRIPT）が、合言葉付きの専用 URL で「古物台帳」シートを CSV で返す。
?sheet=管理 を付けると「管理」シートを返す（2026-11-15 追加）。管理シートからは No.・状態・購入日・販売日 だけを
data/kanri.csv に保存する（売れた商品の状態の割合・状態別の利益のグラフ用。アカウント名・URL は保存しない）。
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
KANRI_OUT = DATA / "kanri.csv"
KANRI_SHEET = "管理"
KANRI_COLUMNS = ["No.", "状態", "購入日", "販売日"]  # これ以外の列（アカウント名・URL など）は保存しない
BACKUP = DATA / "backup"
SERVICE = "aurora-ledger"
SHEET = "古物台帳"

sys.path.insert(0, str(HERE))
import keychain  # noqa: E402

APPS_SCRIPT = """// aurora: 古物台帳を CSV で返す（Mac の tools/ledger_sync.py が読む）。合言葉が一致したときだけ返す
const KEY = '__KEY__';
const SHEETS = ['__SHEET__', '__KANRI__'];  // 返してよいシート（最初のものが既定）
function doGet(e) {
  if (!e || !e.parameter || e.parameter.key !== KEY) {
    return ContentService.createTextOutput('forbidden');
  }
  const name = SHEETS.indexOf(e.parameter.sheet) >= 0 ? e.parameter.sheet : SHEETS[0];
  const sh = SpreadsheetApp.getActive().getSheetByName(name);
  const rows = sh.getDataRange().getDisplayValues();
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\\n');
  return ContentService.createTextOutput(csv).setMimeType(ContentService.MimeType.CSV);
}
"""


def script_text(key: str) -> str:
    return APPS_SCRIPT.replace("__KEY__", key).replace("__SHEET__", SHEET).replace("__KANRI__", KANRI_SHEET)


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
    sync_kanri(url, key, dry_run)
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


def kanri_rows(text: str) -> list[dict] | None:
    """管理シートの CSV → KANRI_COLUMNS だけの行. 管理シートの形でなければ None."""
    rows = list(csv.reader(io.StringIO(text)))
    i = next((i for i, r in enumerate(rows[:10]) if "状態" in r and "No." in r and "販売日" in r), None)
    if i is None:
        return None
    head = [h.replace("\n", "").strip() for h in rows[i]]
    idx = {c: head.index(c) for c in KANRI_COLUMNS if c in head}
    out = []
    for r in rows[i + 1:]:
        d = {c: (r[j].strip() if j < len(r) else "") for c, j in idx.items()}
        if d.get("No."):
            out.append(d)
    return out


def sync_kanri(url: str, key: str, dry_run: bool = False) -> bool | None:
    """管理シートの No.・状態・購入日・販売日 → data/kanri.csv. 古いスクリプト（管理シートを返さない）なら None."""
    try:
        rows = kanri_rows(fetch(url, key, KANRI_SHEET))
    except (urllib.error.URLError, OSError) as e:
        print(f"管理シート: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if rows is None:
        print("管理シート: 取り込めませんでした。スプレッドシートの Apps Script が古いためです"
              "（python3 tools/ledger_sync.py script で貼り替え）", file=sys.stderr)
        return None
    if dry_run:
        print(f"管理シート: {len(rows)}行を取り込み予定", file=sys.stderr)
        return True
    DATA.mkdir(exist_ok=True)
    with open(KANRI_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KANRI_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"管理シート: {len(rows)}行（No.・状態・購入日・販売日）→ data/kanri.csv", file=sys.stderr)
    return True


def cmd_script() -> None:
    """スクリプトを新しくしたとき: 貼り替えて新バージョンでデプロイ（URL・合言葉はそのまま）."""
    key = keychain.get(SERVICE, "key")
    if not key or not keychain.get(SERVICE, "url"):
        print("まだ設定されていません。先に python3 tools/ledger_sync.py setup を実行してください。")
        sys.exit(1)
    script = script_text(key)
    copied = keychain.copy_to_clipboard(script)
    head = "新しいスクリプトをクリップボードにコピーしました。" if copied else "下のスクリプトをコピーしてください:\n" + script
    print(f"""
{head}

 1. 古物台帳のスプレッドシートを開く → 上のメニュー「拡張機能」→「Apps Script」
 2. 中の文字を全部選んで（⌘A）、貼り付け（⌘V）→ 保存（⌘S）
 3. 右上「デプロイ」→「デプロイを管理」→ 鉛筆マーク（編集）
    → バージョン:「新バージョン」→「デプロイ」（URL は変わりません）
 4. 終わったら Enter
""")
    input("Enter を押すと試しに読み込みます…")
    sync(dry_run=True)


def cmd_setup() -> None:
    key = keychain.get(SERVICE, "key") or secrets.token_urlsafe(24)
    keychain.set(SERVICE, "key", key)
    script = script_text(key)
    copied = keychain.copy_to_clipboard(script)
    head = "スクリプトをクリップボードにコピーしました。" if copied else "下のスクリプトをコピーしてください:\n" + script
    print(f"""
古物台帳の自動取り込みを設定します（初回だけ・約5分）。
{head}

 1. 古物台帳のスプレッドシートを開く → 上のメニュー「拡張機能」→「Apps Script」
 2. 最初から入っている文字を全部消して、貼り付け（⌘V）→ 保存（⌘S）
 3. 右上「デプロイ」→「新しいデプロイ」→ 歯車 →「ウェブアプリ」
      次のユーザーとして実行: 自分　／　アクセスできるユーザー: 全員
    →「デプロイ」（初回は「アクセスを承認」→ 自分のアカウント → 詳細 → 安全ではないページに移動 → 許可）
 4. 表示された「ウェブアプリ」の URL（https://script.google.com/macros/s/…/exec）をコピー
""")
    url = input("その URL を貼り付けて Enter: ").strip()
    if not url.startswith("https://script.google.com/"):
        print("URL が違うようです（https://script.google.com/macros/s/…/exec）。もう一度 setup を実行してください。")
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
