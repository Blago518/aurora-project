#!/usr/bin/env python3
"""FedEx の輸出許可通知書の依頼用 Excel を自動で埋める（2026-11-23 オーナー「fedex の excel はあなたが自動入力して作成するように」）.

使い方（aurora フォルダで）:
    python3 tools/export_permit.py ~/Downloads/<FedEx から届いた Excel>.xlsx
        → data/statements/ の CPaSS 注文一覧から FedEx の追跡番号を集め、Excel の「運送状番号」の列に1行ずつ入れて
          reports/fedex_export_permit_<日付>.xlsx に保存する（元の Excel は変えない）。
    python3 tools/export_permit.py <Excel> --list 追跡番号.txt     … 追跡番号を1行に1つ書いたファイルから
    python3 tools/export_permit.py <Excel> --since 2026-11-01      … この日以降の発送だけ

FedEx の書式（2026-11-23 に受け取った「輸出許可通知書-リクエストシート」）: B12「フェデックスアカウント番号」の下の黄色の欄 B13 に
アカウント番号（9桁）、B15「AWB（運送状番号）」の下の B16 は見本（A16「サンプル」。消さない）、B17〜B216（A列 1〜200）に運送状番号（12桁の数字だけ）。
返信先と注意（受付メール）: 記入した Excel をメールに添付して、受付メールに返信（件名・本文は変えない）。送り先は受付メールに書かれたアドレス。
2営業日（20件超はもっとかかる・100件超は2週間程度）。許可日から3年以内の物だけ。送付は輸出者宛だけ（CPaSS 経由でも輸出者はオーナー本人）。
もう持っている輸出許可通知書（data/export_permits/ の「<運送状番号>_RELS_<日付>.pdf」）の番号は入れない。
見出しが見つからなければ見出しを表示して止まる（推測で書かない）。
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
HAVE_DIR = ROOT / "data" / "export_permits"   # 輸出許可通知書の PDF の置き場所（税理のフォルダの中に行き先が決まらないとき。名前の先頭が運送状番号）
ACCOUNT_HEAD_RE = re.compile(r"アカウント番号|Account\s*(?:No|Number)", re.I)
REPORTS = ROOT / "reports"

TRACK_HEAD_RE = re.compile(r"運送状番号|追跡番号|エアウェイビル|AWB|Air\s*Waybill|Tracking\s*(?:No|Number|#)?", re.I)
DATE_HEAD_RE = re.compile(r"発送日|出荷日|Ship(?:ment)?\s*Date", re.I)
# CPaSS 注文一覧で、FedEx（DHL）の追跡番号が入っていそうな列。CPaSS 自身の番号（Trackcode = EX…）は除く
CARRIER_TRACK_COL_RE = re.compile(r"AWB|carrier.*(?:track|no)|last\s*mile|tracking\s*(?:no|number)|運送状|追跡番号", re.I)
CARRIER_COL_RE = re.compile(r"carrier|運送会社|配送業者|logistics|channel|service", re.I)
DHL_TRACK_RE = re.compile(r"^\d{10}$")   # DHL の運送状番号（2026-11-23 の輸出許可通知書「5800000001」）
FEDEX_TRACK_RE = re.compile(r"^\d{12}$")   # FedEx の書式「運送状番号は12桁の数字のみ（ハイフン及びスペース無し）」


def find_header(ws, max_rows: int = 30) -> tuple[int, int, int | None] | None:
    """(見出しの行, 運送状番号の列, 発送日の列) を返す. 1始まり."""
    for row in ws.iter_rows(min_row=1, max_row=max_rows):
        track = date_col = None
        for c in row:
            v = str(c.value or "").strip()
            if not v or len(v) > 30 or v[:1] in "◆※＊*・":   # 注意書きの文（「◆ 運送状番号は12桁…」）は見出しではない
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


PERMIT_NAME_RE = re.compile(r"(\d{12})_RELS|(\d{10})\.pdf$")   # FedEx「870000000001_RELS_20260916.pdf」・DHL「5800000001.pdf」


def carrier_of(name: str) -> str | None:
    """PDF の名前 → 置くフォルダの名前（FedEx／DHL）. 輸出許可通知書の名前でなければ None."""
    m = PERMIT_NAME_RE.match(name)
    return None if not m else ("FedEx" if m.group(1) else "DHL")


def permit_base(home: Path | None = None) -> Path:
    """輸出許可通知書を置く場所（この下に FedEx・DHL のフォルダ）. 2026-11-23 オーナー「fedex と dhl でフォルダ分けて、自動化でローカルに置いて」
    「デスクトップに税理士フォルダがある。その中に振り分けれる？」. 税理のフォルダの中に行き先が1つに決まればそこ、決まらなければ data/export_permits."""
    import tax_folder
    return tax_folder.permit_base(home, HAVE_DIR)[0]


def have_permits(d: Path | None = None) -> set[str]:
    """もう持っている輸出許可通知書の運送状番号（FedEx・DHL のフォルダの中も、前の置き場所も数える）."""
    out = set()
    for base in ([d] if d else [permit_base(), HAVE_DIR]):
        for p in (base.rglob("*.pdf") if base.is_dir() else []):
            m = PERMIT_NAME_RE.match(p.name)
            if m:
                out.add(m.group(1) or m.group(2))
    return out


def collect_from_downloads(src: Path | None = None, dst: Path | None = None) -> int:
    """ダウンロードフォルダ（と前の置き場所）の輸出許可通知書の PDF を、運送会社ごとのフォルダ（FedEx／DHL）に移す. 移した数を返す.
    同じ名前がもう移し先にあれば、ダウンロードの方を消す（もう移した物）."""
    import shutil
    src = src or Path.home() / "Downloads"
    dst = dst or permit_base()
    n = 0
    olds = [x for x in (dst, HAVE_DIR) if x.is_dir()]   # 分ける前に1つのフォルダへ置いた物も分ける
    for p in [x for d in [src] + olds for x in (sorted(d.glob("*.pdf")) if d.is_dir() else [])]:
        car = carrier_of(p.name)
        if not car:
            continue
        to = dst / car / p.name
        if to.exists():
            if to.stat().st_size == p.stat().st_size:   # 同じ物がもうある＝移し終わった物。大きさが違えば消さずに残す
                p.unlink()
            continue
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(to))
        n += 1
    return n


def template_account(template: Path) -> str:
    """シートの「フェデックスアカウント番号」の下の欄に入っている9桁（前回のシートに入っていれば、それを使う）."""
    import openpyxl
    ws = openpyxl.load_workbook(template).active
    for row in ws.iter_rows(min_row=1, max_row=30):
        for c in row:
            if ACCOUNT_HEAD_RE.search(str(c.value or "")) and len(str(c.value)) <= 30:
                v = re.sub(r"\D", "", str(ws.cell(c.row + 1, c.column).value or ""))
                return v if len(v) == 9 else ""
    return ""


def fill(template: Path, shipments: list[dict], out: Path, account: str = "") -> tuple[int, list[str]]:
    """template に shipments（{"track", "date"}）を書いて out に保存. (書いた件数, 書けなかった番号と理由) を返す.
    欄（A列に番号が振られた行）が足りなければ out_2.xlsx・out_3.xlsx … に分ける."""
    import openpyxl
    wb0 = openpyxl.load_workbook(template)
    h = find_header(wb0.active)
    if not h:
        raise SystemExit("Excel に「運送状番号（追跡番号・AWB）」の見出しが見つかりません。見出し:\n  " + "\n  ".join(headers_preview(wb0.active))
                         + "\nこの表示をサトシに送ってください（列を合わせます）。")
    head_row, tcol, dcol = h
    skipped, todo, seen = [], [], set()
    for s_ in shipments:
        t = re.sub(r"[\s-]", "", str(s_["track"]))
        if t in seen:
            continue
        seen.add(t)
        if not FEDEX_TRACK_RE.match(t):
            skipped.append(f"{t}（12桁の数字でない）")
            continue
        todo.append({**s_, "track": t})
    n, part = 0, 1
    while todo or part == 1:
        wb = openpyxl.load_workbook(template)
        ws = wb.active
        if account:
            for row in ws.iter_rows(min_row=1, max_row=head_row):   # 見出し（AWB）より上の「アカウント番号」の下の欄
                c = next((c for c in row if ACCOUNT_HEAD_RE.search(str(c.value or ""))), None)
                if c:
                    ws.cell(c.row + 1, c.column).value = int(account) if account.isdigit() else account   # 前回のシートも数字
                    break
        last = ws.max_row   # 先に決める（セルを読むだけで max_row が伸びるため）
        numbered = any(isinstance(ws.cell(rr, 1).value, (int, float)) for rr in range(head_row + 1, last + 1))
        if numbered:   # 前回の依頼で入れた番号が残ったシート（オーナーが送り返した物）でも、番号の欄を空にしてから入れる（2026-11-23 前回の8件が残った）
            for rr in range(head_row + 1, last + 1):
                if isinstance(ws.cell(rr, 1).value, (int, float)):
                    ws.cell(rr, tcol).value = None
        r = head_row + 1
        while todo:
            a_ = ws.cell(r, 1).value
            v = str(ws.cell(r, tcol).value or "").strip()
            if re.search(r"サンプル|例|sample|example", f"{a_ or ''}{v}", re.I):   # 見本の行は残す
                r += 1
                continue
            if numbered and not isinstance(a_, (int, float)):
                if r > last:
                    break   # 番号の振られた欄（FedEx の書式は 1〜200）を使い切った → 次のファイルへ
                r += 1
                continue
            if v and not numbered:   # 番号の振られていないシート: 既に入った行は残す
                vv = re.sub(r"[\s-]", "", v)
                todo = [x for x in todo if x["track"] != vv]
                r += 1
                continue
            x = todo.pop(0)
            ws.cell(r, tcol).value = x["track"]   # 文字で入れる（2026-11 にオーナーが送って FedEx が受け付けたシートと同じ形）
            if dcol and x.get("date"):
                ws.cell(r, dcol).value = x["date"]
            n += 1
            r += 1
        o = out if part == 1 else out.with_name(f"{out.stem}_{part}{out.suffix}")
        o.parent.mkdir(parents=True, exist_ok=True)
        wb.save(o)
        part += 1
        if not todo:
            break
    return n, skipped

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
            if not name and not (FEDEX_TRACK_RE if carrier == "fedex" else DHL_TRACK_RE).match(t):
                continue   # 運送会社の列が無いときは、番号の形（FedEx 12桁・DHL 10桁）で分ける
            d = next((r[k] for k in r if re.search(r"ship|発送|出荷|create|作成", k, re.I) and r[k]), "")
            dd = d.date() if isinstance(d, datetime) else d if isinstance(d, date) else None
            if since and dd and dd < since:
                continue
            if dd and (date.today() - dd).days > 365 * 3:   # 許可日から3年より前の物は FedEx が出せない
                continue
            seen.add(t)
            out.append({"track": t, "date": dd.isoformat() if dd else ""})
    if not out:
        raise SystemExit("CPaSS 注文一覧に FedEx の追跡番号の列が見つかりません。列名:\n  " + ", ".join(sorted(cols))
                         + "\nこの表示をサトシに送るか、--list で追跡番号のファイルを指定してください。")
    return out


TEMPLATE = ROOT / "data" / "fedex_template.xlsx"   # FedEx のリクエストシート（1回渡せば次から使う。data/ はコミットしない）


def find_fedex_template(dirs: list[Path]) -> Path | None:
    """ダウンロードにある FedEx のリクエストシート（見出し「AWB（運送状番号）」と「フェデックスアカウント番号」がある xlsx）. 新しい物."""
    import openpyxl
    for d in dirs:
        for p in (sorted(d.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True) if d.is_dir() else []):
            try:
                ws = openpyxl.load_workbook(p, read_only=True).active
                text = " ".join(str(v) for row in ws.iter_rows(min_row=1, max_row=20, values_only=True) for v in row if v)
            except Exception:
                continue
            if "AWB" in text and "アカウント番号" in text:
                return p
    return None


def speedpak_files(dirs: list[Path]) -> list[Path]:
    """SpeedPAK セラーポータルの「輸出取引の詳細」（xlsx。シート「オーダー」に 輸送業者名・追跡番号）. 新しい順."""
    import openpyxl
    out = []
    for d in dirs:
        for p in (sorted(d.glob("*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True) if d.is_dir() else []):
            try:
                wb = openpyxl.load_workbook(p, read_only=True)
                if "オーダー" in wb.sheetnames:
                    out.append(p)
            except Exception:
                continue
    return out


def from_speedpak(path: Path, carrier: str = "fedex", since: date | None = None) -> list[dict]:
    """輸出取引の詳細のシート「オーダー」から、その運送会社の運送状番号（FedEx 12桁・DHL 10桁）と作成日（2026-11-23 オーナーが渡した形）."""
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True)["オーダー"]
    rows = [r for r in ws.iter_rows(values_only=True) if any(v not in (None, "") for v in r)]
    if not rows:
        return []
    h = [str(x or "").strip() for x in rows[0]]
    ci, ti, di = h.index("輸送業者名"), h.index("追跡番号"), h.index("作成時間") if "作成時間" in h else None
    rx = FEDEX_TRACK_RE if carrier == "fedex" else DHL_TRACK_RE
    out, seen = [], set()
    for r in rows[1:]:
        if carrier.lower() not in str(r[ci] or "").lower():
            continue
        t = re.sub(r"[\s-]", "", str(r[ti] or ""))
        if not rx.match(t) or t in seen:
            continue
        d = str(r[di] or "")[:10] if di is not None else ""
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            dd = None
        if since and dd and dd < since:
            continue
        if dd and (date.today() - dd).days > 365 * 3:
            continue
        seen.add(t)
        out.append({"track": t, "date": d})
    return out


STATUS_SHEET = "Mac_輸出許可書"  # 保存できたかを Mac が自分で数えて台帳へ（2026-11-23 オーナー「ダウンロード先で確認しろ」。クラウドのサトシが読む）


def permit_status(sp: Path, have: set[str]) -> list[dict]:
    """「輸出取引の詳細」の運送状番号ごとに、輸出許可書の PDF をもう持っているか. [{carrier, track, date, have}]"""
    out = []
    for carrier, label in (("dhl", "DHL"), ("fedex", "FedEx")):
        for x in from_speedpak(sp, carrier):
            out.append({"carrier": label, "track": x["track"], "date": x["date"], "have": x["track"] in have})
    return out


def status_lines(rows: list[dict]) -> list[str]:
    """運送会社ごとの1行: 「DHL: 3件中 2件 保存済み・まだ 1件（8300000003）」."""
    out = []
    for label in ("DHL", "FedEx"):
        mine = [r for r in rows if r["carrier"] == label]
        if not mine:
            continue
        rest = [r["track"] for r in mine if not r["have"]]
        tail = "・まだ {}件（{}{}）".format(len(rest), "、".join(rest[:10]), " ほか" if len(rest) > 10 else "") if rest else "・全部そろっています"
        out.append("{}: {}件中 {}件 保存済み{}".format(label, len(mine), len(mine) - len(rest), tail))
    return out


def report_status(downloads: Path | None = None, post=None, upload: bool = True) -> list[dict] | None:
    """ダウンロードの PDF を数え直し、足りない番号を画面と古物台帳「Mac_輸出許可書」に出す. 材料（輸出取引の詳細）が無ければ None."""
    import csv
    import io
    from datetime import datetime
    downloads = downloads or Path.home() / "Downloads"
    moved = collect_from_downloads(downloads)
    base = permit_base()
    count = {c: len(list((base / c).glob("*.pdf"))) if (base / c).is_dir() else 0 for c in ("FedEx", "DHL")}
    print(f"  輸出許可書の置き場所: {base}（FedEx {count['FedEx']}件・DHL {count['DHL']}件。今回 {moved}件を移した）")
    stray = [x.name for x in (sorted(base.glob("*.pdf")) if base.is_dir() else [])]
    if stray:
        print(f"  ⚠ 名前で FedEx・DHL を見分けられない PDF が {len(stray)}件、{base} に残っています: " + "、".join(stray[:5]))
    sp = speedpak_files([downloads, STATEMENTS])
    if not sp:
        return None
    rows = permit_status(sp[0], have_permits())
    for line in status_lines(rows):
        print("  輸出許可書 " + line)
    if upload:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["運送会社", "運送状番号", "発送日", "輸出許可書", "確かめた日時"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for r in sorted(rows, key=lambda r: (r["have"], r["carrier"], r["date"])):   # まだの物を上に
            w.writerow([r["carrier"], "'" + r["track"], r["date"], "保存済み" if r["have"] else "まだ", now])
        try:
            import ledger_sync
            if ledger_sync.post_sheet(STATUS_SHEET, buf.getvalue(), post=post):
                print(f"  → 古物台帳「{STATUS_SHEET}」に送りました")
        except Exception as e:  # noqa: BLE001  送れなくても数えた結果は画面に出ている
            print(f"  ⚠ 輸出許可書の数を台帳に送れませんでした（{e}）")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FedEx の輸出許可通知書の依頼用 Excel を自動で埋める")
    ap.add_argument("template", type=Path, nargs="?", help="FedEx から届いた依頼用 Excel（xlsx）。1回渡せば data/fedex_template.xlsx に覚える")
    ap.add_argument("--dhl", action="store_true", help="DHL: まだ輸出許可通知書をもらっていない運送状番号の一覧を出す（DHL は公式フォームに貼る）")
    ap.add_argument("--check", action="store_true", help="保存できた輸出許可書を数え、足りない番号を出す（古物台帳「Mac_輸出許可書」にも送る）")
    ap.add_argument("--list", type=Path, help="追跡番号を1行に1つ書いたファイル（無ければ CPaSS 注文一覧から）")
    ap.add_argument("--since", help="この日以降の発送だけ（例 2026-11-01）")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--account", help="出荷に使ったフェデックスアカウント番号（9桁）。無ければキーチェーンの fedex_account")
    a = ap.parse_args(argv)
    since = date.fromisoformat(a.since) if a.since else None
    if a.check:
        if report_status() is None:
            print("ダウンロードに SpeedPAK の「輸出取引の詳細」（xlsx）がありません。")
        return 0
    got = collect_from_downloads()
    if got:
        print(f"輸出許可通知書 {got}件を {permit_base()} の FedEx・DHL のフォルダに移しました（もらった物として数えます）")
    if a.dhl:
        sp = speedpak_files([Path.home() / "Downloads", STATEMENTS])
        ships = from_list(a.list) if a.list else from_speedpak(sp[0], "dhl", since) if sp else from_cpass(STATEMENTS, since, "dhl")
        have = have_permits()
        rest = [x["track"] for x in ships if DHL_TRACK_RE.match(re.sub(r"[\s-]", "", x["track"])) and x["track"] not in have]
        out = a.out or REPORTS / f"dhl_export_permit_{date.today():%Y%m%d}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(rest) + "\n", encoding="utf-8")
        print(f"DHL の運送状番号 {len(rest)}件（もらっていない物だけ）→ {out}\n" + "\n".join(rest))
        print("DHL の「輸出入関連書類送付リクエスト受付フォーム」に貼ってください。")
        return 0
    import shutil
    if a.template:
        TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(a.template.expanduser(), TEMPLATE)   # 次からは指定しなくてよい
    elif TEMPLATE.exists():
        a.template = TEMPLATE
    else:
        found = find_fedex_template([Path.home() / "Downloads"])
        if not found:
            ap.error("ダウンロードに FedEx のリクエストシート（受付メールに付いている Excel）がありません。受付メールの Excel をダウンロードしてから、もう一度動かしてください。")
        TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, TEMPLATE)
        a.template = TEMPLATE
        print(f"FedEx のリクエストシート「{found.name}」を見つけました（次からはこれを使います）")
    sp = speedpak_files([Path.home() / "Downloads", STATEMENTS])
    if not a.list and not sp:
        ap.error("ダウンロードに SpeedPAK の「輸出取引の詳細」（xlsx）がありません。SpeedPAK セラーポータルの「各注文番号の料金明細」を Chrome で開くと、拡張機能が自動で保存します。")
    ships = from_list(a.list) if a.list else from_speedpak(sp[0], "fedex", since)
    out = a.out or REPORTS / f"fedex_export_permit_{date.today():%Y%m%d}.xlsx"
    have = have_permits()
    before = len(ships)
    ships = [x for x in ships if re.sub(r"[\s-]", "", x["track"]) not in have]
    account = a.account or template_account(a.template)   # 前回のシートに入っていれば、それを使う（聞かない）
    if not account:   # 公開リポジトリには書かない。Mac のキーチェーンに1回だけ覚える
        try:
            import keychain
            account = keychain.get("aurora", "fedex_account") or ""
            if not account and sys.stdin.isatty():
                account = input("出荷に使ったフェデックスアカウント番号（9桁。前回のシートの B13）: ").strip()
                if re.fullmatch(r"\d{9}", account):
                    keychain.set("aurora", "fedex_account", account)
                else:
                    account = ""
        except Exception:
            pass
    n, skipped = fill(a.template.expanduser(), ships, out, account)
    print(f"{n}件を書きました → {out}（もう持っている {before - len(ships)}件は入れていません）")
    if not account:
        print("※ アカウント番号の欄は空です。--account 123456789 で入れるか、Excel の黄色の欄に入れてください。")
    for x in skipped:
        print("  入れなかった番号:", x)
    print("確かめてから、FedEx の受付メールに「返信」で、このファイルを添付して送ってください（件名・本文は変えない）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
