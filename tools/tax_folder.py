#!/usr/bin/env python3
"""税理士に渡すフォルダ（デスクトップの「税理〜」）へ、証跡と輸出許可書を自動で振り分ける.

2026-11-23 オーナー「ヤフオクの証跡も、自動化でローカルにおいてほしい」「デスクトップに税理士フォルダがある。その中に振り分けれる？」

    python3 tools/tax_folder.py      # 今すぐ振り分けて、結果を古物台帳「Mac_税理フォルダ」に送る（毎朝の update_all.py でも動く）

- ヤフオクの仕入れ証跡（拡張機能が ダウンロード/ヤフオク仕入証跡/ に保存した PNG）→ 税理のフォルダの中の、名前に「ヤフオク」を含むフォルダ。
- 輸出許可書（FedEx・DHL の PDF）→ 名前に「輸出許可」を含むフォルダの中の FedEx・DHL。
- **合うフォルダがちょうど1つのときだけ移す。新しいフォルダは作らない**（0個・2個以上なら動かさず、そう表示する。
  税理のフォルダはオーナーが税理士向けに並べている所なので、当てずっぽうで置かない）。行き先を決めたら下の EVIDENCE_DEST・PERMIT_DEST に書く。
- 動かさなかった物は今までどおり（証跡はダウンロード、輸出許可書は data/export_permits の FedEx・DHL）。
- 移すのは名前の形が合う物だけ。同じ名前がもう移し先にあれば、大きさが同じときだけ元を消す。
"""
from __future__ import annotations

import os
import csv
import re
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

STATUS_SHEET = "Mac_税理フォルダ"   # 振り分け先・件数・フォルダの並び（クラウドのサトシが読んで、振り分け先が合っているか確かめる）
EVIDENCE_SUBS = ("ヤフオク仕入証跡", "yahoo_evidence")   # 拡張機能がダウンロードの中に作るフォルダ
EVIDENCE_WORD, PERMIT_WORD = "ヤフオク", "輸出許可"
# 行き先を決め打ちするとき（税理のフォルダから見た道。例 "8. 仕入/ヤフオク"）。空なら名前で探す
EVIDENCE_DEST = ""
PERMIT_DEST = ""


def tax_root(home: Path | None = None) -> Path | None:
    """デスクトップの「税理」で始まるフォルダ（税理・税理士 など）. 無い・2つ以上あるときは None（当てずっぽうで選ばない）."""
    desk = (home or Path.home()) / "Desktop"
    try:
        found = sorted(p for p in desk.glob("税理*") if p.is_dir()) if desk.is_dir() else []
    except OSError:  # 自動実行からは macOS がデスクトップを見せないことがある（iCloud と同じ）
        return None
    return found[0] if len(found) == 1 else None


def why_no_root(home: Path | None = None) -> str:
    """税理のフォルダが決まらない理由（2026-11-25: 「1つに決まらない」だけでは、無いのか・2つあるのか・見えないのか分からなかった）."""
    desk = (home or Path.home()) / "Desktop"
    try:
        names = list(os.listdir(desk))
    except FileNotFoundError:
        return "デスクトップのフォルダが無い"
    except OSError:
        return "Mac の自動実行からデスクトップが見えない（macOS の許可が無い）"
    found = sorted(n for n in names if n.startswith("税理") and (desk / n).is_dir())
    if not found:
        return f"デスクトップに「税理」で始まるフォルダが無い（デスクトップのフォルダ {sum(1 for n in names if (desk / n).is_dir())}個）"
    return f"デスクトップに「税理」で始まるフォルダが{len(found)}つある（{'・'.join(found)}）"


def find_subs(root: Path, word: str, depth: int = 3) -> list[Path]:
    """root の中（depth 階層まで）で、名前に word を含むフォルダを全部（見つけたフォルダの中は探さない）."""
    out, level = [], [root]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                continue
            out.extend(p for p in subs if word in p.name)
            nxt.extend(p for p in subs if word not in p.name)
        level = nxt
    return out


def dest(root: Path | None, word: str, fixed: str = "") -> tuple[Path | None, str]:
    """振り分け先と、決まらなかった理由. 決め打ち（fixed）があればそれ. 無ければ名前に word を含むフォルダがちょうど1つのときだけ."""
    if root is None:
        return None, "デスクトップに「税理」で始まるフォルダが1つに決まらない"
    if fixed:
        return (root / fixed, "") if (root / fixed).is_dir() else (None, f"決めた行き先「{fixed}」が無い")
    hits = find_subs(root, word)
    if len(hits) == 1:
        return hits[0], ""
    return None, f"名前に「{word}」を含むフォルダが {len(hits)}個（1個のときだけ移す）"


def tree(root: Path, depth: int = 2) -> list[str]:
    """フォルダの並び（フォルダ名だけ。ファイル名は出さない）."""
    out = []

    def walk(d: Path, lv: int) -> None:
        try:
            subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for p in subs:
            out.append("　" * lv + p.name)
            if lv + 1 < depth:
                walk(p, lv + 1)

    walk(root, 0)
    return out


def move_file(p: Path, to: Path) -> bool:
    """p を to へ移す. 同じ名前がもうあれば、大きさが同じときだけ p を消す（移し終わった物）. 移したら True."""
    if to.exists():
        if to.stat().st_size == p.stat().st_size:
            p.unlink()
        return False
    to.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(to))
    return True


ID_RE = re.compile(r"(?<![a-z0-9])([a-z]\d{9,11})(?![a-z0-9])")


def ledger_ids(paths: list[Path] | None = None) -> list[str]:
    """古物台帳（data/ledger.csv・data/kanri.csv）の中のヤフオクのオークションID（小文字）. 台帳の下ほど新しいので新しい順に.
    読み方は bid_results.load_won_ids と同じ（英字1字＋数字9〜11桁。…/auction/ID の URL の中も）."""
    import unicodedata
    root = Path(__file__).resolve().parent.parent
    seen: list[str] = []
    for p in paths if paths is not None else [root / "data" / "ledger.csv", root / "data" / "kanri.csv"]:
        if not p.exists():
            continue
        text = unicodedata.normalize("NFKC", p.read_text(encoding="utf-8-sig", errors="ignore")).lower()
        for m in ID_RE.finditer(text):
            if m.group(1) in seen:
                seen.remove(m.group(1))
            seen.append(m.group(1))
    return list(reversed(seen))


def evidence_ids(downloads: Path, home: Path | None = None) -> set[str]:
    """証跡の PNG がある ID. 名前の _ID（まとめて取引は _ID_ID…）を全部. ダウンロードと、デスクトップの「税理〜」の下（何階層でも）."""
    desk = (home or Path.home()) / "Desktop"
    dirs = [downloads / sub for sub in EVIDENCE_SUBS]
    try:
        dirs += sorted(p for p in desk.glob("税理*") if p.is_dir())
    except OSError:
        pass
    out: set[str] = set()
    for d in dirs:
        try:
            files = list(d.rglob("*.png")) if d.is_dir() else []
        except OSError:
            continue
        for f in files:
            out.update(ids_of(f))
    return out


def ids_of(f: Path) -> set[str]:
    """証跡の名前のオークションID（まとめて取引は全部）. Chrome が付ける「 (1)」は外して読む."""
    stem = re.sub(r"\s*\(\d+\)$", "", f.stem.lower())
    return {m.group(1) for m in re.finditer(r"_([a-z]?\d{8,12})(?=_|$)", stem)}


def missing_evidence(downloads: Path, home: Path | None = None, paths: list[Path] | None = None) -> dict:
    """台帳の落札分のうち、証跡が無いID（新しい順）. {ids, have, missing}. 「全部そろったか」はここで数える（2026-11-26 オーナー「全て取得できているか確認しなさい」）."""
    ids = ledger_ids(paths)
    have = evidence_ids(downloads, home)
    return {"ids": ids, "have": [i for i in ids if i in have], "missing": [i for i in ids if i not in have]}


EVIDENCE_LOG = "aurora_evidence_log.csv"   # 拡張機能が書く、1件ごとの結果と理由（extension_background.js の evWriteLog）
NOT_IN_LIST = "落札分の一覧で見つからなかった（一覧に載らない古い落札か、別の Yahoo! ID・まだ一覧を開いていない）"


def evidence_log(downloads: Path) -> dict[str, tuple[str, str]]:
    """拡張機能の記録 {ID: (結果, 理由)}. 無ければ空."""
    p = downloads / EVIDENCE_LOG
    if not p.exists():
        return {}
    out: dict[str, tuple[str, str]] = {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            i = (row.get("id") or "").strip().lower()
            if i and i != "done":
                out[i] = ((row.get("結果") or "").strip(), (row.get("理由") or "").strip())
    return out


def move_evidence(downloads: Path, home: Path | None = None) -> dict:
    """ヤフオクの仕入れ証跡の PNG を税理のフォルダへ. {dest, why, moved, total, left}（dest が None＝動かさなかった。why に理由）."""
    root = tax_root(home)
    to, why = dest(root, EVIDENCE_WORD, EVIDENCE_DEST)
    why = why_no_root(home) if root is None else why
    files = [p for sub in EVIDENCE_SUBS for p in (sorted((downloads / sub).glob("*.png")) if (downloads / sub).is_dir() else [])]
    moved, dup, notes = 0, 0, []
    if to is not None:
        have: set[str] = set()
        for f in (to.rglob("*.png") if to.is_dir() else []):
            have |= ids_of(f)
        for p in files:
            ids = ids_of(p)
            # 同じオークションIDの証跡がもう移し先にある（オーナーが前に撮った物など）→ 重ねて置かず、ゴミ箱へ（元に戻せる。2026-11-26 オーナー「重複取得しないようにね」）
            if ids and ids <= have:
                trash = Path.home() / ".Trash"
                try:
                    shutil.move(str(p), str(trash / p.name)) if trash.is_dir() else p.unlink()
                    dup += 1
                    continue
                except OSError as e:
                    notes.append(f"{p.name}: 重複をゴミ箱に移せない（{e}）")
                    continue
            if move_file(p, to / p.name):
                moved += 1
                have |= ids
            elif p.exists():
                notes.append(f"{p.name}: 同じ名前の別の画像が移し先にある")
    left = sum(1 for p in files if p.exists())
    total = len(list(to.glob("*.png"))) if to is not None and to.is_dir() else 0
    return {"dest": to, "why": why, "moved": moved, "total": total, "left": left, "dup": dup, "notes": notes}


def permit_base(home: Path | None = None, fallback: Path | None = None) -> tuple[Path | None, str]:
    """輸出許可書の置き場所（この下に FedEx・DHL）と、税理のフォルダに決まらなかった理由. 決まらなければ fallback."""
    root = tax_root(home)
    to, why = dest(root, PERMIT_WORD, PERMIT_DEST)
    why = why_no_root(home) if root is None else why
    return (to, "") if to is not None else (fallback, why)


def run(downloads: Path | None = None, home: Path | None = None, post=None, upload: bool = True) -> dict:
    """証跡と輸出許可書を振り分け、結果を表示して古物台帳「Mac_税理フォルダ」に送る."""
    import export_permit
    downloads = downloads or Path.home() / "Downloads"
    root = tax_root(home)
    ev = move_evidence(downloads, home)
    pbase = export_permit.permit_base(home)
    pwhy = permit_base(home)[1]
    pmoved = export_permit.collect_from_downloads(downloads, pbase)
    count = {c: len(list((pbase / c).glob("*.pdf"))) if (pbase / c).is_dir() else 0 for c in ("FedEx", "DHL")}
    rows = [["種類", "置き場所", "今回移した件数", "フォルダの中の件数", "ダウンロードに残っている件数", "税理のフォルダに入れなかった理由"]]
    rows.append(["ヤフオクの仕入れ証跡", str(ev["dest"] or "ダウンロード/ヤフオク仕入証跡（動かしていない）"), ev["moved"], ev["total"], ev["left"], ev["why"]])
    for c in ("FedEx", "DHL"):
        rows.append([f"輸出許可書 {c}", str(pbase / c), pmoved if c == "FedEx" else "", count[c], "", pwhy])
    print(f"  ヤフオクの仕入れ証跡: {ev['dest'] or '動かしていない（' + ev['why'] + '）'}（今回 {ev['moved']}件・重複でゴミ箱へ {ev.get('dup', 0)}件・フォルダの中 {ev['total']}件・ダウンロードに残り {ev['left']}件）")
    for n in ev.get("notes", [])[:30]:
        print(f"    残った: {n}")
    print(f"  輸出許可書: {pbase}（FedEx {count['FedEx']}件・DHL {count['DHL']}件。今回 {pmoved}件）" + (f" ※税理のフォルダには入れていない（{pwhy}）" if pwhy else ""))
    miss = missing_evidence(downloads, home)
    print(f"  台帳の落札分 {len(miss['ids'])}件のうち、証跡あり {len(miss['have'])}件・無い {len(miss['missing'])}件")
    log = evidence_log(downloads)
    lp = downloads / EVIDENCE_LOG
    for i, (res, note) in list(log.items())[:10]:
        if i not in miss["missing"]:
            print(f"    記録: {i} {res} {note[:80]}")
    print(f"  拡張機能の記録: {datetime.fromtimestamp(lp.stat().st_mtime).strftime('%m/%d %H:%M') + ' に書かれた・' + str(len(log)) + '件' if lp.exists() else 'まだ無い'}")
    why = {i: ("：".join(x for x in log[i] if x) if i in log else NOT_IN_LIST) for i in miss["missing"]}
    groups: dict[str, int] = {}
    for i in miss["missing"]:
        key = log[i][0] if i in log else "一覧で見つからない"
        groups[key] = groups.get(key, 0) + 1
    if groups:
        print("    内訳: " + "・".join(f"{k} {v}件" for k, v in groups.items()))
    for i in miss["missing"][:40]:
        print(f"    証跡が無い: {i}  {why[i]}")
    rows.append(["台帳の落札分の証跡", f"台帳 {len(miss['ids'])}件", "", len(miss["have"]), "", f"無い {len(miss['missing'])}件"])
    rows.extend([["証跡が無いID（新しい順）", i, "", "", f"https://auctions.yahoo.co.jp/jp/auction/{i}", why[i]] for i in miss["missing"][:300]])
    rows.append(["確かめた日時", datetime.now().strftime("%Y-%m-%d %H:%M"), "", "", "", ""])
    if root is not None:
        rows.append(["税理のフォルダの並び（フォルダ名だけ。台帳にだけ送る）", root.name, "", "", "", ""])
        rows.extend([["", name, "", "", "", ""] for name in tree(root)])
    if upload:
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        try:
            import ledger_sync
            if ledger_sync.post_sheet(STATUS_SHEET, buf.getvalue(), post=post):
                print(f"  → 古物台帳「{STATUS_SHEET}」に送りました")
        except Exception as e:  # noqa: BLE001  送れなくても振り分けは済んでいる
            print(f"  ⚠ 振り分けの結果を台帳に送れませんでした（{e}）")
    return {"root": root, "evidence": ev, "permit_base": pbase, "permit_moved": pmoved, "rows": rows, "missing": miss}


def open_won_and_wait(downloads: Path, limit_sec: int = 1800, quiet_sec: int = 300) -> int:
    """Chrome で落札分の一覧を開き（拡張機能が取引ナビを順に開いて保存する）、証跡の PNG が増えなくなるまで待つ. 増えた枚数を返す."""
    import subprocess
    import time
    count = lambda: sum(len(list((downloads / sub).glob("*.png"))) for sub in EVIDENCE_SUBS if (downloads / sub).is_dir())  # noqa: E731
    start = n0 = count()
    # 拡張機能に「Mac にもう証跡があるID」を渡す（拡張機能は毎回このファイルを読み直す。↻ は要らない）
    ext = Path(__file__).resolve().parent.parent / "extension"
    if ext.is_dir():
        import json
        have = sorted(evidence_ids(downloads))
        (ext / "evidence_have.json").write_text(json.dumps({"t": int(time.time() * 1000), "ids": have}), encoding="utf-8")
        print(f"  Mac にある証跡 {len(have)}件のIDを拡張機能に渡しました（これ以外を開いて保存します）")
    subprocess.run(["open", "-a", "Google Chrome", "https://auctions.yahoo.co.jp/my/won"], check=False)
    t0 = last = time.time()
    logp = downloads / EVIDENCE_LOG
    print("  落札分の一覧を Chrome で開きました。拡張機能が全部を開き終わるまで待ちます（最長30分）…")
    # 終わりは拡張機能が書く記録（aurora_evidence_log.csv の done 行）で決める。2026-11-26 は「90秒増えない」で決めて、
    # 保存できない物が続いた所で拡張機能より先に数え終わっていた
    while time.time() - t0 < limit_sec:
        time.sleep(10)
        n = count()
        if n != n0:
            n0, last = n, time.time()
            print(f"    保存 {n - start}枚")
        try:
            fresh = logp.stat().st_mtime > t0
        except OSError:
            fresh = False
        if fresh and time.time() - max(last, logp.stat().st_mtime) > 30:
            break   # 記録が書かれ、その後30秒なにも増えない（2ページ目の分の書き足しも待つ）
        if not fresh and time.time() - last > quiet_sec:
            print("    ⚠ 拡張機能の記録が書かれていません（chrome://extensions で aurora の ↻ が済んでいないかもしれません）")
            break
    return n0 - start


if __name__ == "__main__":
    if "--won" in sys.argv:   # 落札分の一覧を開いて保存が止まるまで待ってから、振り分けと「証跡が無いID」の数え
        open_won_and_wait(Path.home() / "Downloads")
    run()
    sys.exit(0)
