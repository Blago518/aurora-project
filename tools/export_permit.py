#!/usr/bin/env python3
"""FedEx の輸出許可通知書の依頼用 Excel を自動で埋める（2026-11-23 オーナー「fedex の excel はあなたが自動入力して作成するように」）.

使い方（aurora フォルダで）:
    python3 tools/export_permit.py ~/Downloads/<FedEx から届いた Excel>.xlsx
        → data/statements/ の CPaSS 注文一覧から FedEx の追跡番号を集め、Excel の「運送状番号」の列に1行ずつ入れて
          reports/fedex_export_permit_<日付>.xlsx に保存する（元の Excel は変えない）。
    python3 tools/export_permit.py <Excel> --list 追跡番号.txt     … 追跡番号を1行に1つ書いたファイルから
    python3 tools/export_permit.py <Excel> --since 2026-11-01      … この日以降の発送だけ

Excel の形（列名・何行目から書くか）は FedEx の書式を受け取るまで確かめていない。見出しの行を探して、
運送状番号（追跡番号・AWB・Tracking）の列と、あれば発送日の列に入れる。見つからなければ見出しを表示して止まる（推測で書かない）。
購入者の名前・住所は読まない・書かない。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATEMENTS = ROOT / "data" / "statements"
REPORTS = ROOT / "reports"

TRACK_HEAD_RE = re.compile(r"運送状番号|追跡番号|エアウェイビル|AWB|Air\s*Waybill|Tracking\s*(?:No|Number|#)?", re.I)
DATE_HEAD_RE = re.compile(r"発送日|出荷日|Ship(?:ment)?\s*Date", re.I)
# CPaSS 注文一覧で、FedEx（DHL）の追跡番号が入っていそうな列。CPaSS 自身の番号（Trackcode = EX…）は除く
CARRIER_TRACK_COL_RE = re.compile(r"AWB|carrier.*(?:track|no)|last\s*mile|tracking\s*(?:no|number)|運送状|追跡番号", re.I)
CARRIER_COL_RE = re.compile(r"carrier|運送会社|配送業者|logistics|channel|service", re.I)
FEDEX_TRACK_RE = re.compile(r"^\d{12}$|^\d{15}$|^\d{20}$|^\d{22}$")   # FedEx の運送状番号は数字だけ（12桁が普通）


def find_header(ws, max_rows: int = 30) -> tuple[int, int, int | None] | None:
    """(見出しの行, 運送状番号の列, 発送日の列) を返す. 1始まり."""
    for row in ws.iter_rows(min_row=1, max_row=max_rows):
        track = date_col = None
        for c in row:
            v = str(c.value or "").strip()
            if not v:
                continue
            if track is None and TRACK_HEAD_RE.search(v):
                track = c.column
            elif date_col is None and DATE_HEAD_RE.search(v):
                date_col = c.column
        if track is not None:
            return row[0].row, track, date_col
    return None


def headers_preview(ws, max_rows: int = 10) -> list[str]:
    out = []
    for row in ws.iter_rows(min_row=1, max_row=max_rows, values_only=True):
        cells = [str(v) for v in row if v not in (None, "")]
        if cells:
            out.append(" | ".join(cells))
    return out


def fill(template: Path, shipments: list[dict], out: Path) -> int:
    """template の運送状番号の列に shipments（{"track", "date"}）を書き、out に保存. 書いた件数を返す."""
    import openpyxl
    wb = openpyxl.load_workbook(template)
    ws = wb.active
    h = find_header(ws)
    if not h:
        raise SystemExit("Excel に「運送状番号（追跡番号・AWB）」の見出しが見つかりません。見出し:\n  " + "\n  ".join(headers_preview(ws))
                         + "\nこの表示をサトシに送ってください（列を合わせます）。")
    head_row, tcol, dcol = h
    have = set()
    r = head_row + 1
    # 見本の行（「例」「sample」や見本の番号）は上書きする。既に番号が入った行は残し、その続きから書く
    while ws.cell(r, tcol).value not in (None, ""):
        v = str(ws.cell(r, tcol).value).strip()
        if re.search(r"例|sample|example|xxxx", v, re.I) or v.startswith("1234"):
            break
        have.add(v)
        r += 1
    n = 0
    for s in shipments:
        if s["track"] in have:
            continue
        ws.cell(r, tcol).value = s["track"]
        if dcol and s.get("date"):
            ws.cell(r, dcol).value = s["date"]
        have.add(s["track"])
        r += 1
        n += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return n


def from_list(path: Path) -> list[dict]:
    out = []
    for ln in path.read_text(encoding="utf-8-sig").splitlines():
        t = re.sub(r"\s", "", ln.split(",")[0])
        if t and not TRACK_HEAD_RE.search(t):
            out.append({"track": t, "date": ""})
    return out


def from_cpass(statements: Path, since: date | None, carrier: str = "fedex") -> list[dict]:
    """CPaSS 注文一覧（xlsx）から FedEx の追跡番号を集める. 列が分からなければ見出しを表示して止まる."""
    import build_ledger_costs as blc
    files = [p for p in sorted(statements.glob("*")) if re.search(r"cpass", p.name, re.I) and p.suffix == ".xlsx" and "Invoice" not in p.name]
    if not files:
        raise SystemExit(f"{statements} に CPaSS 注文一覧（名前に cpass を含む xlsx）がありません。CPaSS の管理画面から注文一覧を書き出して置いてください。")
    out, seen, cols = [], set(), set()
    for p in files:
        for r in blc._xlsx_rows(p):
            cols.update(r.keys())
            tcol = next((k for k in r if k != "Trackcode" and CARRIER_TRACK_COL_RE.search(k)), None)
            if not tcol:
                continue
            t = re.sub(r"\s", "", str(r.get(tcol) or ""))
            ccol = next((k for k in r if CARRIER_COL_RE.search(k)), None)
            name = str(r.get(ccol) or "") if ccol else ""
            if not t or t in seen:
                continue
            if name and carrier not in name.lower():
                continue
            if not name and carrier == "fedex" and not FEDEX_TRACK_RE.match(t):
                continue   # 運送会社の列が無いときは、FedEx の番号の形（数字12桁など）だけ
            d = next((r[k] for k in r if re.search(r"ship|発送|出荷|create|作成", k, re.I) and r[k]), "")
            dd = d.date() if isinstance(d, datetime) else d if isinstance(d, date) else None
            if since and dd and dd < since:
                continue
            seen.add(t)
            out.append({"track": t, "date": dd.isoformat() if dd else ""})
    if not out:
        raise SystemExit("CPaSS 注文一覧に FedEx の追跡番号の列が見つかりません。列名:\n  " + ", ".join(sorted(cols))
                         + "\nこの表示をサトシに送るか、--list で追跡番号のファイルを指定してください。")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FedEx の輸出許可通知書の依頼用 Excel を自動で埋める")
    ap.add_argument("template", type=Path, help="FedEx から届いた依頼用 Excel（xlsx）")
    ap.add_argument("--list", type=Path, help="追跡番号を1行に1つ書いたファイル（無ければ CPaSS 注文一覧から）")
    ap.add_argument("--since", help="この日以降の発送だけ（例 2026-11-01）")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    since = date.fromisoformat(a.since) if a.since else None
    ships = from_list(a.list) if a.list else from_cpass(STATEMENTS, since)
    out = a.out or REPORTS / f"fedex_export_permit_{date.today():%Y%m%d}.xlsx"
    n = fill(a.template.expanduser(), ships, out)
    print(f"{n}件を書きました → {out}\n確かめてから、FedEx の受付メールに書かれた宛先にこのファイルを添付して送ってください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
