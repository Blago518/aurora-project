#!/usr/bin/env python3
"""発送の明細（CPaSS・オークレボ）を、古物台帳の管理番号ごとの費用にまとめる.

入力（data/statements/ に置く。ファイル名に下の語が入っていれば自動で見つける）:
    CPaSS 注文一覧（xlsx）        … 名前に「cpass」を含み、列 Trackcode / eBay order ID / SKU がある
    CPaSS 請求明細（xlsx）        … 名前に「InvoiceDetails」  列: 注文番号・費用タイプ・金額
    CPaSS 関税明細（xlsx）        … 名前に「DutyTax」          列: 注文番号・料金タイプ・小計(円)
    オークレボ 商品一覧（csv）    … 列: 管理ID・ナンバリング・出荷ID（Shift-JIS でも可）
    オークレボ 請求書（pdf/txt）  … 名前に「aucrevo-invoice」。pdf は pdftotext があれば読む。無ければ txt にして置く

出力:
    data/ledger_costs.csv           管理番号ごとの 国際送料（CPaSS）・関税等・発送代行費（オークレボ）・eBay注文番号・国
    reports/ledger_costs_unmatched.csv  管理番号に結び付かなかった明細（要確認）

購入者の名前・住所・電話・メールは読み込んでも保存しない（国と注文番号だけ）。
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATEMENTS = ROOT / "data" / "statements"


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").replace("¥", "").strip() or 0)
    except ValueError:
        return 0.0


def _xlsx_rows(path: Path) -> list[dict]:
    import openpyxl  # 標準ライブラリに無いので、使うときだけ読み込む（Mac では pip3 install openpyxl）
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h or "").strip() for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(c not in (None, "") for c in r)]


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def load_cpass_orders(paths: list[Path]) -> dict[str, dict]:
    """追跡番号（EX…）→ {sku, ebay_order_id, country}。個人情報の列は捨てる."""
    out = {}
    for p in paths:
        for r in _xlsx_rows(p):
            code = str(r.get("Trackcode") or "").strip()
            if code:
                out[code] = {"sku": str(r.get("SKU") or "").strip(),
                             "ebay_order_id": str(r.get("eBay order ID") or "").strip(),
                             "country": str(r.get("Recipient address countryCode") or "").strip()}
    return out


def load_cpass_charges(paths: list[Path], kind: str) -> list[dict]:
    rows = []
    for p in paths:
        for r in _xlsx_rows(p):
            order = str(r.get("注文番号") or "").strip()
            if kind == "shipping":
                amount, label = _num(r.get("金額")), f"{r.get('費用タイプ', '')}/{r.get('支払いタイプ', '')}"
            else:
                amount, label = _num(r.get("小計(円)")), f"{r.get('料金タイプ', '')}/{r.get('支払いタイプ', '')}"
            rows.append({"order": order, "amount": amount, "label": label, "file": p.name})
    return rows


def load_aucrevo_items(paths: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    """管理ID → ナンバリング、出荷ID → ナンバリング."""
    by_id, by_ship = {}, {}
    for p in paths:
        for r in csv.DictReader(_read_text(p).splitlines()):
            num = (r.get("ナンバリング") or "").strip()
            if (r.get("管理ID") or "").strip():
                by_id[r["管理ID"].strip()] = num
            if (r.get("出荷ID") or "").strip():
                by_ship[r["出荷ID"].strip()] = num
    return by_id, by_ship


def aucrevo_invoice_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return _read_text(path)
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout
    raise SystemExit(f"{path.name} を読むには pdftotext が必要です。Claude に「この PDF を txt にして」と頼み、"
                     f"同じ名前の .txt を {STATEMENTS} に置いてください。")


def load_aucrevo_charges(paths: list[Path]) -> list[dict]:
    """請求書の明細行: 日付 管理番号(IS-…) 品目 金額 税率 … 次の行に 商品ID:nnnn."""
    rows = []
    line_re = re.compile(r"^\s*(20\d\d-\d\d-\d\d)\s+(\S+)\s+(\S+)\s+([\d,]+)\s+(10%|8%|非課税)")
    for p in paths:
        lines = aucrevo_invoice_text(p).splitlines()
        for i, ln in enumerate(lines):
            m = line_re.match(ln)
            if not m:
                continue
            item_id = ""
            for nxt in lines[i + 1:i + 3]:
                # 通常は「商品ID:nnnn」、米国関税の行は「FedexAWB:…/#nnnn」の形
                mm = re.search(r"商品ID[:：]\s*(\d+)", nxt) or re.search(r"AWB[:：]\S*/#(\d+)", nxt)
                if mm:
                    item_id = mm.group(1)
                    break
            rate = {"10%": 1.10, "8%": 1.08}.get(m.group(5), 1.0)  # 明細は税抜。支払額（税込）で集計する
            rows.append({"date": m.group(1), "ship_id": m.group(2), "label": m.group(3),
                         "amount": round(_num(m.group(4)) * rate), "item_id": item_id, "file": p.name})
    return rows


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def build(statements: Path) -> tuple[list[dict], list[dict]]:
    files = sorted(p for p in statements.glob("*") if p.is_file())
    pick = lambda pat: [p for p in files if re.search(pat, p.name, re.I)]
    orders = load_cpass_orders([p for p in pick(r"cpass") if p.suffix == ".xlsx" and "Invoice" not in p.name])
    ship = load_cpass_charges(pick(r"InvoiceDetails.*\.xlsx$"), "shipping")
    duty = load_cpass_charges(pick(r"DutyTax.*\.xlsx$"), "duty")
    by_id, by_ship = load_aucrevo_items(pick(r"\.csv$"))
    seen_txt = {p.stem for p in pick(r"aucrevo-invoice.*\.txt$")}
    auc_files = pick(r"aucrevo-invoice.*\.txt$") + [p for p in pick(r"aucrevo-invoice.*\.pdf$") if p.stem not in seen_txt]
    auc = load_aucrevo_charges(auc_files)

    total = defaultdict(lambda: {"cpass_shipping_jpy": 0, "duty_jpy": 0, "aucrevo_jpy": 0,
                                 "ebay_order_id": "", "country": ""})
    unmatched = []
    for kind, rows in (("cpass_shipping_jpy", ship), ("duty_jpy", duty)):
        for r in rows:
            o = orders.get(r["order"])
            if not o or not o["sku"]:
                unmatched.append({"明細": "CPaSS" + ("送料" if kind.startswith("cpass") else "関税"), "番号": r["order"],
                                  "内容": r["label"], "金額": r["amount"], "ファイル": r["file"],
                                  "理由": "CPaSS注文一覧に無い追跡番号" if not o else "SKU（管理番号）が空"})
                continue
            t = total[o["sku"]]
            t[kind] += r["amount"]
            t["ebay_order_id"] = t["ebay_order_id"] or o["ebay_order_id"]
            t["country"] = t["country"] or o["country"]
    for r in auc:
        sku = by_id.get(r["item_id"]) or by_ship.get(r["ship_id"], "")
        if not sku:
            unmatched.append({"明細": "オークレボ", "番号": f"{r['ship_id']} 商品ID:{r['item_id']}", "内容": r["label"],
                              "金額": r["amount"], "ファイル": r["file"], "理由": "オークレボ商品一覧に無い"})
            continue
        total[sku]["aucrevo_jpy"] += r["amount"]
    rows = [{"管理番号": k, **{kk: (round(v) if isinstance(v, float) else v) for kk, v in t.items()}}
            for k, t in sorted(total.items())]
    print(f"CPaSS注文 {len(orders)}件・送料明細 {len(ship)}行・関税明細 {len(duty)}行・オークレボ明細 {len(auc)}行", file=sys.stderr)
    return rows, unmatched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="発送明細を管理番号ごとの費用にまとめる")
    ap.add_argument("--statements", type=Path, default=STATEMENTS)
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "data" / "ledger_costs.csv")
    args = ap.parse_args(argv)
    rows, unmatched = build(args.statements)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["管理番号", "cpass_shipping_jpy", "duty_jpy", "aucrevo_jpy", "ebay_order_id", "country"])
        w.writeheader(); w.writerows(rows)
    um = ROOT / "reports" / "ledger_costs_unmatched.csv"
    um.parent.mkdir(exist_ok=True)
    with open(um, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["明細", "番号", "内容", "金額", "ファイル", "理由"])
        w.writeheader(); w.writerows(unmatched)
    s = lambda k: sum(r[k] for r in rows)
    print(f"管理番号 {len(rows)}件: 国際送料 {s('cpass_shipping_jpy'):,}円・関税等 {s('duty_jpy'):,}円・発送代行 {s('aucrevo_jpy'):,}円")
    print(f"結び付かなかった明細 {len(unmatched)}行（合計 {sum(u['金額'] for u in unmatched):,.0f}円）→ {um}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
