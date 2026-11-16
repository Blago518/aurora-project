#!/usr/bin/env python3
"""販売実績の参考データをまとめて reference_prices.csv を作る.

- 古物台帳（Googleスプレッドシートの「古物台帳」シートを CSV でダウンロードしたもの）
    販売日が入っている行 = 自分の実績。販売金額($) は送料込み。
- 頒布会の商品リスト（xlsx の「カメラ商品一覧」シート）
    販売金額($) と 頒布金額(円) を参考相場として使う。

使い方:
    python3 tools/build_reference_prices.py --ledger 古物台帳.csv --hanpu 頒布会_2026_09.xlsx 頒布会_2026_10.xlsx -o reference_prices.csv

標準ライブラリのみ（openpyxl 不要）。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
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


def load_fx_fix(path: Path) -> dict[str, list[tuple[float, float, str]]]:
    """eBay の取引レポート（data/ebay_report.csv）→ No. ごとの（取引通貨での金額, ドルへの換算レート, 通貨）.

    豪ドル・ポンドで売れた注文だけ。古物台帳の「販売金額($)」にその通貨の数字がそのまま入っている行を、ドルに直すのに使う。"""
    out: dict[str, list[tuple[float, float, str]]] = {}
    if not path.exists():
        return out
    for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
        cur = (r.get("Transaction currency") or "").strip()
        gross, rate = _num(r.get("Gross transaction amount")), _num(r.get("Exchange rate"))
        no = unicodedata.normalize("NFKC", r.get("Custom label") or "").strip().upper()
        if r.get("Type") == "Order" and cur not in ("", "--", "USD") and gross and rate and no not in ("", "--"):
            out.setdefault(no, []).append((gross, rate, cur))
    return out


def from_ledger(path: Path, fx_fix: dict | None = None, fixed: list | None = None) -> list[dict]:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        lines = f.read().splitlines()
    start = next((i for i, ln in enumerate(lines) if "販売日" in ln), 0)
    for row in csv.DictReader(lines[start:]):
        # 見出しは全角・半角をそろえる（Claude更新版は「品目（商品名）」と全角カッコ）
        row = {unicodedata.normalize("NFKC", k or "").replace("\n", "").strip(): (v or "").strip()
               for k, v in row.items() if not isinstance(v, list)}
        title = row.get("品目(商品名)") or row.get("品目") or ""
        price = _num(next((v for k, v in row.items() if k.startswith("販売金額")), ""))
        # 仕入額は送料込み: Claude更新版は「仕入合計(円)」、元の台帳は「仕入金額(円) 送料含む」
        cost = _num(next((v for k, v in row.items() if k.startswith("仕入合計")), "")
                    or next((v for k, v in row.items() if k.startswith("仕入金額")), ""))
        if not row.get("販売日") or not price or not title:
            continue  # 未販売
        # 台帳の金額が、豪ドル・ポンドで売れた注文の金額と同じ（1セント以内）なら、その通貨のまま記入されている → ドルに直す
        no = unicodedata.normalize("NFKC", row.get("No.") or row.get("管理番号") or "").strip().upper()
        for gross, rate, cur in (fx_fix or {}).get(no, []):
            if abs(price - gross) < 0.011:
                if fixed is not None:
                    fixed.append((no, title, price, cur, round(price * rate, 2)))
                price = round(price * rate, 2)
                break
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
    data = Path(__file__).resolve().parent.parent / "data"
    ap.add_argument("--ledger", type=Path, nargs="*", default=None, help="古物台帳CSV（省略時は data/ledger.csv）")
    ap.add_argument("--hanpu", type=Path, nargs="*", default=None, help="頒布会の商品リスト xlsx（省略時は data/hanpukai_*.xlsx）")
    ap.add_argument("-o", "--out", type=Path, default=data / "reference_prices.csv")
    ap.add_argument("--ebay-report", type=Path, default=data / "ebay_report.csv",
                    help="eBay の取引レポート（ledger_sync.py が ebay売上シートから作る）。豪ドル・ポンドの販売をドルに直すのに使う")
    args = ap.parse_args(argv)
    if args.ledger is None:
        args.ledger = [p for p in [data / "ledger.csv"] if p.exists()]
    if args.hanpu is None:
        args.hanpu = sorted(data.glob("hanpukai_*.xlsx"))
    rows: list[dict] = []
    fx_fix = load_fx_fix(args.ebay_report)
    for p in args.ledger:
        fixed: list = []
        got = from_ledger(p, fx_fix, fixed)
        print(f"{p.name}: 販売済み {len(got)}件", file=sys.stderr)
        if fixed:
            print(f"  豪ドル・ポンドで記入されていた {len(fixed)}件をドルに直しました（eBay の取引レポートの換算レート）:", file=sys.stderr)
            for no, title, was, cur, now in fixed:
                print(f"    {no} {title[:40]}: {was:,.2f} {cur} → ${now:,.2f}", file=sys.stderr)
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
