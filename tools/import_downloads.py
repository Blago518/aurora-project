#!/usr/bin/env python3
"""ダウンロードフォルダにある CPaSS・オークレボの明細を data/statements/ に取り込み、費用を集計し直す.

使い方（aurora フォルダで）:
    python3 tools/import_downloads.py            # ~/Downloads を見て取り込み → build_ledger_costs.py を実行
    python3 tools/import_downloads.py --dry-run  # 何を取り込むかだけ表示

見分け方はファイル名ではなく**中身**（列名）で判断する:
    CPaSS 注文一覧   … xlsx で列 Trackcode・SKU がある
    CPaSS 請求明細   … xlsx で列 注文番号・費用タイプ・金額
    CPaSS 関税明細   … xlsx で列 注文番号・料金タイプ・小計(円)
    オークレボ 商品一覧 … csv で列 管理ID・ナンバリング
    オークレボ 請求書 … pdf で名前が aucrevo-invoice…（例 aucrevo-invoice26073420261031.pdf）

同じ月の請求明細を2回ダウンロードしても二重に数えないよう、月ごとの名前で保存し、新しい方で置き換える。
取り込んだファイルはダウンロードフォルダから移動する（元の場所には残らない）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATEMENTS = ROOT / "data" / "statements"
sys.path.insert(0, str(HERE))
from build_ledger_costs import _read_text  # noqa: E402


def _xlsx_head(path: Path) -> list[str]:
    import warnings
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        row = next(ws.iter_rows(values_only=True), ())
    return [str(h or "").strip() for h in row]


def _data_month(path: Path) -> str:
    """xlsx の中の日付で一番多い年月（YYYYMM）。CPaSS の関税明細はファイル名の日付＝ダウンロード日時なので中身で決める."""
    import warnings
    from collections import Counter
    import openpyxl
    cnt: Counter = Counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        for r in ws.iter_rows(min_row=2, values_only=True):
            for c in r:
                m = re.match(r"(20\d\d)[-/](\d\d)", str(c or ""))
                if m:
                    cnt[m.group(1) + m.group(2)] += 1
    return cnt.most_common(1)[0][0] if cnt else ""


def _month(name: str) -> str:
    """ファイル名から YYYYMM を取り出す（無ければ空）."""
    m = re.search(r"(20\d\d)-?(0[1-9]|1[0-2])", name)
    if "aucrevo-invoice" in name.lower():  # aucrevo-invoice<顧客番号6桁><YYYYMMDD>
        m2 = re.search(r"(20\d\d)(0[1-9]|1[0-2])\d\d\D*$", Path(name).stem)
        m = m2 or m
    return f"{m.group(1)}{m.group(2)}" if m else ""


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8]


def classify(path: Path) -> str | None:
    """明細の種類と保存名（data/statements/ の中の名前）を返す。対象外なら None."""
    suf = path.suffix.lower()
    try:
        if suf == ".xlsx":
            head = _xlsx_head(path)
            if "Trackcode" in head and "SKU" in head:
                return f"cpass_orders_{_digest(path)}.xlsx"
            if "注文番号" in head and "費用タイプ" in head:
                mon = _data_month(path)
                return f"{mon[:4]}-{mon[4:]}InvoiceDetails.xlsx" if mon else f"InvoiceDetails_{_digest(path)}.xlsx"
            if "注文番号" in head and "小計(円)" in head:
                return f"DutyTax_{_data_month(path) or _digest(path)}.xlsx"  # 年月＝明細の対象月
        elif suf == ".csv":
            head = next(csv.reader(_read_text(path).splitlines()), [])
            if "管理ID" in head and "ナンバリング" in head:
                return f"aucrevo_items_{_digest(path)}.csv"
        elif suf == ".pdf" and "aucrevo-invoice" in path.name.lower():
            return f"aucrevo-invoice{_month(path.name) or _digest(path)}.pdf"
    except Exception as e:  # 壊れたファイル・開けないファイルは飛ばす
        print(f"  読めないので飛ばしました: {path.name}（{e}）", file=sys.stderr)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ダウンロードフォルダの明細を取り込む")
    ap.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    ap.add_argument("--days", type=int, default=90, help="この日数より古いファイルは見ない")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    limit = time.time() - args.days * 86400
    found = []
    for p in sorted(args.downloads.glob("*"), key=lambda p: p.stat().st_mtime):  # 古い順 → 新しい方が後で上書き
        if p.is_file() and p.suffix.lower() in (".xlsx", ".csv", ".pdf") and p.stat().st_mtime >= limit:
            dest = classify(p)
            if dest:
                found.append((p, dest))
    if not found:
        print(f"{args.downloads} に CPaSS・オークレボの明細は見つかりませんでした（直近{args.days}日）。")
        return 0
    STATEMENTS.mkdir(parents=True, exist_ok=True)
    for src, dest in found:
        target = STATEMENTS / dest
        note = "（同じ月の古い分を置き換え）" if target.exists() else ""
        print(f"  {src.name} → data/statements/{dest}{note}")
        if not args.dry_run:
            shutil.move(str(src), str(target))
    if args.dry_run:
        return 0
    print(f"{len(found)}件を取り込みました。費用を集計し直します…")
    return subprocess.call([sys.executable, str(HERE / "build_ledger_costs.py")])


if __name__ == "__main__":
    sys.exit(main())
