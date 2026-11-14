#!/usr/bin/env python3
"""販売実績の参考データをまとめて sales.csv を作る.

- 古物台帳（Googleスプレッドシートの「古物台帳」シートを CSV でダウンロードしたもの）
    販売日が入っている行 = 自分の実績。販売金額($) は送料込み。
- 頒布会の商品リスト（xlsx の「カメラ商品一覧」シート）
    販売金額($) と 頒布金額(円) を参考相場として使う。

使い方:
    python3 build_sales.py --ledger 古物台帳.csv --hanpu 頒布会_2026_09.xlsx 頒布会_2026_10.xlsx -o sales.csv

標準ライブラリのみ（openpyxl 不要）。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
FIELDS = ["date", "title", "ebay_query", "sold_price_usd", "shipping_charged_usd",
          "purchase_jpy", "ebay_url", "source"]


def _num(v) -> float | None:
    s = re.sub(r"[,$¥円\s]", "", str(v or ""))
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# xlsx を標準ライブラリだけで読む
# ---------------------------------------------------------------------------


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx_sheet(path: Path, sheet_name: str) -> list[list]:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        target = None
        for s in wb.find("m:sheets", NS):
            if s.get("name") == sheet_name:
                target = targets[s.get(REL_NS)]
        if target is None:
            raise SystemExit(f"{path.name}: シート「{sheet_name}」が見つかりません")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(z.read(target))
    rows = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        cells: dict[int, object] = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif v is None:
                continue
            elif t == "s":
                val = shared[int(v.text)]
            else:
                val = v.text
            cells[_col_index(c.get("r"))] = val
        if cells:
            rows.append([cells.get(i) for i in range(max(cells) + 1)])
    return rows


# ---------------------------------------------------------------------------
# 変換
# ---------------------------------------------------------------------------


def from_ledger(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = f.read().splitlines()
    start = next((i for i, ln in enumerate(lines) if "販売日" in ln), 0)
    for row in csv.DictReader(lines[start:]):
        row = {(k or "").replace("\n", "").strip(): (v or "").strip() for k, v in row.items()}
        title = row.get("品目(商品名)") or row.get("品目") or ""
        price = _num(next((v for k, v in row.items() if k.startswith("販売金額")), ""))
        cost = _num(next((v for k, v in row.items() if k.startswith("仕入金額")), ""))
        if not row.get("販売日") or not price or not title:
            continue  # 未販売
        out.append({
            "date": row["販売日"], "title": title, "sold_price_usd": price,
            "shipping_charged_usd": 0, "purchase_jpy": int(cost) if cost is not None else "",
            "ebay_url": "", "source": "自分の販売実績",
        })
    return out


def from_hanpu(path: Path) -> list[dict]:
    rows = read_xlsx_sheet(path, "カメラ商品一覧")
    header_idx = next(i for i, r in enumerate(rows) if "商品名" in [str(x) for x in r])
    header = [str(x or "") for x in rows[header_idx]]
    col = {name: header.index(name) for name in header if name}
    m = re.search(r"(20\d\d)[_-]?(\d\d)", path.stem)
    date = f"{m.group(1)}/{m.group(2)}" if m else ""

    def get(r, name):
        i = col.get(name)
        return r[i] if i is not None and i < len(r) else None

    out = []
    for r in rows[header_idx + 1:]:
        title = get(r, "商品名")
        price = _num(get(r, "販売金額"))
        if not title or not price:
            continue
        cond = get(r, "コンディション") or ""
        note = get(r, "備考") or ""
        out.append({
            "date": date, "title": str(title).strip(), "sold_price_usd": price,
            "shipping_charged_usd": 0,
            "purchase_jpy": int(_num(get(r, "頒布金額")) or 0) or "",
            "ebay_url": get(r, "ebay検索URL") or "",
            "source": f"頒布会リスト({cond}{' ' + str(note).splitlines()[0] if note else ''})"[:80],
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="販売実績CSVを作る")
    ap.add_argument("--ledger", type=Path, nargs="*", default=[], help="古物台帳CSV")
    ap.add_argument("--hanpu", type=Path, nargs="*", default=[], help="頒布会の商品リスト xlsx")
    ap.add_argument("-o", "--out", type=Path, default=Path("sales.csv"))
    args = ap.parse_args(argv)
    rows: list[dict] = []
    for p in args.ledger:
        got = from_ledger(p)
        print(f"{p.name}: 販売済み {len(got)}件", file=sys.stderr)
        rows += got
    for p in args.hanpu:
        got = from_hanpu(p)
        print(f"{p.name}: 頒布会リスト {len(got)}件", file=sys.stderr)
        rows += got
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"出力: {args.out}（{len(rows)}件）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
