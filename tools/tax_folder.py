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

import csv
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
    found = sorted(p for p in desk.glob("税理*") if p.is_dir()) if desk.is_dir() else []
    return found[0] if len(found) == 1 else None


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


def move_evidence(downloads: Path, home: Path | None = None) -> dict:
    """ヤフオクの仕入れ証跡の PNG を税理のフォルダへ. {dest, why, moved, total, left}（dest が None＝動かさなかった。why に理由）."""
    to, why = dest(tax_root(home), EVIDENCE_WORD, EVIDENCE_DEST)
    files = [p for sub in EVIDENCE_SUBS for p in (sorted((downloads / sub).glob("*.png")) if (downloads / sub).is_dir() else [])]
    moved = 0
    if to is not None:
        moved = sum(move_file(p, to / p.name) for p in files)
    left = sum(1 for p in files if p.exists())
    total = len(list(to.glob("*.png"))) if to is not None and to.is_dir() else 0
    return {"dest": to, "why": why, "moved": moved, "total": total, "left": left}


def permit_base(home: Path | None = None, fallback: Path | None = None) -> tuple[Path | None, str]:
    """輸出許可書の置き場所（この下に FedEx・DHL）と、税理のフォルダに決まらなかった理由. 決まらなければ fallback."""
    to, why = dest(tax_root(home), PERMIT_WORD, PERMIT_DEST)
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
    print(f"  ヤフオクの仕入れ証跡: {ev['dest'] or '動かしていない（' + ev['why'] + '）'}（今回 {ev['moved']}件・フォルダの中 {ev['total']}件・ダウンロードに残り {ev['left']}件）")
    print(f"  輸出許可書: {pbase}（FedEx {count['FedEx']}件・DHL {count['DHL']}件。今回 {pmoved}件）" + (f" ※税理のフォルダには入れていない（{pwhy}）" if pwhy else ""))
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
    return {"root": root, "evidence": ev, "permit_base": pbase, "permit_moved": pmoved, "rows": rows}


if __name__ == "__main__":
    run()
    sys.exit(0)
