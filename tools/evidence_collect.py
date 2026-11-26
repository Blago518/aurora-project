#!/usr/bin/env python3
"""ヤフオクの仕入れ証跡をまとめてそろえる（Mac で1回動かす）.

2026-11-26 オーナー「税理士に出す仕入れ証跡をそろえて」。拡張機能（extension_evidence.js）は取引ナビが開かれた時だけ
PNG を保存するので、このプログラムが古物台帳の落札分の取引ナビを Chrome で1件ずつ開く。開いて見るだけで、
取引ナビの中のボタン（連絡・受け取り連絡・評価）は押さない。

    python3 tools/evidence_collect.py            # 足りない分を全部開く → 税理のフォルダへ振り分け（tax_folder.py）
    python3 tools/evidence_collect.py --list     # 足りないIDを数えるだけ（開かない）
    python3 tools/evidence_collect.py --max 5    # 先頭（新しい順）の5件だけ

- 対象: data/ledger.csv・data/kanri.csv の中のオークションID（英字1字＋数字9〜11桁。bid_results.load_won_ids と同じ読み方）。
- 済み: ダウンロード/ヤフオク仕入証跡/ とデスクトップの「税理〜」の下（何階層でも）に、名前の末尾がそのIDの PNG がある物。
- 保存できたかはファイルそのもので確かめる（最大20秒待つ）。最初の1件が保存できなければ、そこで止めて理由の候補を出す。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while not (ROOT / "tools" / "bid_results.py").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "tools"))

ID_RE = re.compile(r"(?<![a-z0-9])([a-z]\d{9,11})(?![a-z0-9])")
NAVI_URL = "https://contact.auctions.yahoo.co.jp/buyer/top?aid={id}"
EVIDENCE_DIR = "ヤフオク仕入証跡"


def ledger_ids(paths: list[Path]) -> list[str]:
    """台帳の中のオークションID（小文字）を新しい順に. 台帳は下ほど新しいので、後に出た物を先に."""
    seen: list[str] = []
    for p in paths:
        if not p.exists():
            continue
        text = unicodedata.normalize("NFKC", p.read_text(encoding="utf-8-sig", errors="ignore")).lower()
        for m in ID_RE.finditer(text):
            if m.group(1) in seen:
                seen.remove(m.group(1))
            seen.append(m.group(1))
    return list(reversed(seen))


def saved_ids(dirs: list[Path]) -> set[str]:
    """名前の末尾がオークションIDの PNG（何階層でも）."""
    out: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*.png"):
            m = re.search(r"_([a-z]?\d{8,12})$", p.stem.lower())
            if m:
                out.add(m.group(1))
    return out


def search_dirs(home: Path) -> list[Path]:
    return [home / "Downloads" / EVIDENCE_DIR, home / "Downloads" / "yahoo_evidence"] + sorted((home / "Desktop").glob("税理*"))


def history_urls(home: Path) -> dict[str, tuple[str, str]]:
    """Chrome の閲覧履歴にある取引ナビの URL（オークションID → (いちばん新しい URL, プロファイル名)）.

    2026-11-26 aid だけの URL（/buyer/top?aid=）は /trade/top に飛んで「取引ナビにアクセスできません」になった。
    落札した物は支払いで取引ナビを開いているので、そのとき開いた URL（出品者・落札者の引数付き）を履歴から使う。
    履歴は読むだけ（写しを作って読む。Chrome が開いていると元のファイルは鍵がかかっている）."""
    import shutil
    import sqlite3
    import tempfile
    out: dict[str, tuple[str, str, int]] = {}
    base = home / "Library" / "Application Support" / "Google" / "Chrome"
    for hist in sorted(base.glob("*/History")):
        prof = hist.parent.name
        try:
            with tempfile.TemporaryDirectory() as td:
                cp = Path(td) / "h"
                shutil.copy2(hist, cp)
                con = sqlite3.connect(str(cp))
                rows = con.execute("select url, last_visit_time from urls where url like '%contact.auctions.yahoo.co.jp%aid=%'").fetchall()
                con.close()
        except Exception as e:  # noqa: BLE001
            print(f"  （Chrome の履歴 {prof} は読めませんでした: {e}）")
            continue
        for url, t in rows:
            m = re.search(r"[?&]aid=([a-z]?\d{8,12})", url.lower())
            if m and (m.group(1) not in out or t > out[m.group(1)][2]):
                out[m.group(1)] = (url, prof, t)
    return {k: (v[0], v[1]) for k, v in out.items()}


def open_and_wait(aid: str, home: Path, wait: float = 20.0, url: str = "") -> bool:
    subprocess.run(["open", "-a", "Google Chrome", url or NAVI_URL.format(id=aid)], check=False)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if aid in saved_ids(search_dirs(home)[:2]):
            return True
    return False


def close_navi_tab() -> None:
    """前に出ている取引ナビのタブだけ閉じる（ほかのタブは触らない）."""
    script = ('tell application "Google Chrome" to if (count of windows) > 0 then '
              'if URL of active tab of front window starts with "https://contact.auctions.yahoo.co.jp" then '
              'close active tab of front window')
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args(argv)
    home = Path.home()
    ids = ledger_ids([ROOT / "data" / "ledger.csv", ROOT / "data" / "kanri.csv"])
    have = saved_ids(search_dirs(home))
    need = [i for i in ids if i not in have]
    if a.max:
        need = need[:a.max]
    print(f"台帳のオークションID {len(ids)}件 / 証跡あり {len(ids) - len([i for i in ids if i not in have])}件 / これから開く {len(need)}件")
    hist = history_urls(home)
    found = [i for i in need if i in hist]
    profs = sorted({hist[i][1] for i in found})
    print(f"Chrome の履歴に取引ナビの URL がある {len(found)}件（プロファイル: {', '.join(profs) or 'なし'}）")
    if found:
        u = hist[found[0]][0]
        print("  URL の形の例:", re.sub(r"=[^&]+", "=…", u))
    need = found + [i for i in need if i not in hist]   # 履歴の URL がある物から開く
    if a.list or not need:
        for i in need:
            print("  ", i)
        if not need:
            _sort()
        return 0
    ok, ng = [], []
    for n, aid in enumerate(need, 1):
        good = open_and_wait(aid, home, url=hist.get(aid, ("", ""))[0])
        print(f"  [{n}/{len(need)}] {aid} … {'保存できた' if good else '保存できなかった'}")
        if good:
            ok.append(aid)
            close_navi_tab()
        else:
            ng.append(aid)
            if not ok:
                print("\n最初の1件が保存できなかったので止めました。前に出ている Chrome の画面を見てください:\n"
                      "  ・ヤフオクのログイン画面 → ログインしてから、もう一度同じコマンド\n"
                      "  ・「取引ナビにアクセスできません」→ 落札したときと違う Yahoo! ID か、違う Chrome のプロファイルで開いています。\n"
                      "    上の「プロファイル」が今の Chrome と違えば、Chrome の右上の顔のアイコンでそのプロファイルに切り替えてから、もう一度\n"
                      "  ・取引ナビの中身が出ている → chrome://extensions で aurora の ↻ を押してから、もう一度\n"
                      "  ・「ページが見つからない」など → この画面のまま、サトシに「開けない」と伝えてください")
                return 1
        time.sleep(3)
    print(f"\n保存できた {len(ok)}件 / できなかった {len(ng)}件")
    for i in ng:
        print("   保存できなかった:", i, "→", NAVI_URL.format(id=i))
    _sort()
    return 0


def _sort() -> None:
    print("\n税理のフォルダへの振り分け:")
    try:
        import tax_folder
        tax_folder.run()
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ 振り分けで止まりました（{e}）")


if __name__ == "__main__":
    sys.exit(main())
