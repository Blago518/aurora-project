#!/usr/bin/env python3
"""相場データを全部まとめて最新にする（コマンド1つ）.

0. 古物台帳をスプレッドシートから直接取り込む（ledger_sync.py。未設定なら飛ばす）
1. ダウンロードフォルダから取り込む（中身で見分ける。ファイル名は何でもよい）
     古物台帳（スプレッドシートを CSV でダウンロード。列 品目・販売日 がある） → data/ledger.csv
     頒布会の商品リスト（xlsx。シート「カメラ商品一覧」がある）              → data/hanpukai_YYYY-MM.xlsx
     CPaSS・オークレボの明細                                               → data/statements/（import_downloads.py）
   置き換える前の古いファイルは data/backup/ に残す。
2. Chatwork の販売報告を公式 API で取り込む（chatwork_sync.py。未設定なら飛ばす）
3. 作り直す: reference_prices.csv → 拡張機能（extension/）
   拡張機能で［直す］［登録］した相場（ダウンロード/aurora_price_memo.csv）もここで取り込まれる。
4. 入札リスト: data/search_urls.txt の検索をまとめて鑑定 → reports/bid_report_*.html を開く（◎○の件数を通知）
   → スマホ用データ（store_data.json・iCloud・暗号化して自動配信。入札候補もスマホの「入札」タブに届く）→ ナレッジ
   → eBay の出品中一覧・注文・取引明細（eBay API 設定済みなら）→ 数字のグラフ（reports/dashboard.html）
5. 入札の結果: 拡張機能の［入札した］の記録を取り込み、終わった出品の最終価格から落札率などを集計 → reports/bid_results.md

使い方（aurora フォルダで）:
    python3 tools/update_all.py
    python3 tools/update_all.py --dry-run   # 何を取り込むかだけ表示
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
BACKUP = DATA / "backup"
sys.path.insert(0, str(HERE))


def _text_head(path: Path, n: int = 4000) -> str:
    raw = path.read_bytes()[:n * 3]
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc, errors="strict" if enc == "cp932" else "ignore")[:n]
        except UnicodeDecodeError:
            continue
    return ""


def _sheet_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
        return re.findall(r'<sheet[^>]*\bname="([^"]+)"', xml)
    except (zipfile.BadZipFile, KeyError, OSError):
        return []


def classify(path: Path) -> Path | None:
    """古物台帳・頒布会リストなら data/ の保存先を返す."""
    suf = path.suffix.lower()
    if suf == ".csv" and not path.name.startswith("aurora_price_memo"):
        head = _text_head(path)
        if "販売日" in head and "品目" in head:
            return DATA / "ledger.csv"
    if suf == ".xlsx" and "カメラ商品一覧" in _sheet_names(path):
        m = re.search(r"(20\d\d)[^\d]?(0[1-9]|1[0-2])", path.stem)
        ym = f"{m.group(1)}-{m.group(2)}" if m else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")
        return DATA / f"hanpukai_{ym}.xlsx"
    return None


def import_downloads(downloads: Path, days: int, dry_run: bool) -> int:
    limit = time.time() - days * 86400
    files = sorted((p for p in downloads.glob("*") if p.is_file() and p.stat().st_mtime >= limit),
                   key=lambda p: p.stat().st_mtime)  # 古い順 → 同じ保存先なら新しい方が残る
    n = 0
    for src in files:
        dest = classify(src)
        if not dest:
            continue
        n += 1
        what = "古物台帳" if dest.name == "ledger.csv" else "頒布会リスト"
        print(f"  {what}: {src.name} → data/{dest.name}{'（古い方は data/backup/ へ）' if dest.exists() else ''}")
        if dry_run:
            continue
        DATA.mkdir(exist_ok=True)
        if dest.exists():
            BACKUP.mkdir(exist_ok=True)
            shutil.move(str(dest), str(BACKUP / f"{dest.stem}_{datetime.now():%Y%m%d-%H%M%S}{dest.suffix}"))
        shutil.move(str(src), str(dest))
    if not n:
        print(f"  {downloads} に古物台帳・頒布会リストはありませんでした（直近{days}日）")
    # CPaSS・オークレボの明細（あれば）
    if (HERE / "import_downloads.py").exists():
        args = ["--downloads", str(downloads), "--days", str(days)] + (["--dry-run"] if dry_run else [])
        subprocess.call([sys.executable, str(HERE / "import_downloads.py"), *args])
    return n


def run(script: str, *args: str) -> bool:
    p = HERE / script
    if not p.exists():
        return True
    print(f"\n▶ {script}")
    ok = subprocess.call([sys.executable, str(p), *args], cwd=ROOT) == 0
    if not ok:
        print(f"  ⚠ {script} でエラーが出ました（上の表示を Claude に送ってください）")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="相場データを全部まとめて最新にする")
    ap.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    ap.add_argument("--days", type=int, default=90, help="この日数より古いダウンロードは見ない")
    ap.add_argument("--dry-run", action="store_true", help="取り込む内容を表示するだけ（作り直さない）")
    ap.add_argument("--notify", action="store_true", help="終わったら Mac の通知を出す（自動実行用）")
    ap.add_argument("--no-bids", action="store_true", help="入札リスト（data/search_urls.txt の鑑定）を作らない")
    args = ap.parse_args(argv)

    print(f"===== {datetime.now():%Y-%m-%d %H:%M} 相場データの更新 =====")
    print("▶ 古物台帳（スプレッドシート）")
    import ledger_sync
    ledger_sync.sync(dry_run=args.dry_run)
    print("\n▶ ダウンロードフォルダから取り込み")
    import_downloads(args.downloads, args.days, args.dry_run)
    print("\n▶ Chatwork の販売報告")
    import chatwork_sync
    chatwork_sync.sync(dry_run=args.dry_run)
    if args.dry_run:
        return 0

    ok = True
    if (DATA / "ledger.csv").exists() or list(DATA.glob("hanpukai_*.xlsx")):
        ok &= run("build_reference_prices.py")
    ok &= run("build_extension.py")
    bids = None if args.no_bids else bid_list()
    ok &= run("build_store.py")  # 入札リストの後に（入札候補もスマホに届ける）
    ok &= run("build_knowledge.py")
    dashboard()
    results = None
    try:
        import bid_results  # 入札の結果（落札率・利益が出る落札の率）
        print("\n▶ 入札の結果")
        results = bid_results.run()
    except Exception as e:  # 集計の失敗で更新全体を止めない
        print(f"  入札の結果を集計できませんでした（{e}）")
    print("\n" + ("✅ 完了" if ok else "⚠ 一部でエラーがありました"))
    print("  Chrome: chrome://extensions で「Aurora 仕入れ鑑定」の ↻（新しい相場を拡張機能に反映）")
    if args.notify:
        msg = "相場データを更新しました" if ok else "相場データの更新で一部エラー（reports/update_all.log）"
        wr = results and results.get("win_rate")
        notify(msg + (f"／入札候補 ◎○ {bids}件" if bids is not None else "") + (f"／落札率 {wr}%" if wr is not None else ""))
    return 0 if ok else 1


def dashboard() -> None:
    """eBay の出品中一覧を取り直し（eBay API の設定済みなら）、数字のグラフ（reports/dashboard.html）を作る. 失敗しても更新全体は止めない."""
    try:
        import ebay_api
        has_token = bool(ebay_api.secret_get("refresh_token"))
    except Exception:
        has_token = False
    if has_token:
        for what, args in (("出品中一覧", ["listings"]), ("注文（2年分）", ["orders", "--days", "730"]),
                           ("取引明細（2年分）", ["fees", "--days", "730"])):
            print(f"\n▶ eBay の{what}")
            subprocess.call([sys.executable, str(HERE / "ebay_api.py"), *args], cwd=ROOT)
    print("\n▶ 数字のグラフ")
    if subprocess.call([sys.executable, str(HERE / "build_dashboard.py"), "--no-open"], cwd=ROOT) != 0:
        print("  ⚠ グラフを作れませんでした（上の表示を Claude に送ってください）")


def bid_list() -> int | None:
    """data/search_urls.txt の検索をまとめて鑑定し、入札リスト（reports/bid_report_*.html）を作って開く. ◎○の件数を返す."""
    urls = DATA / "search_urls.txt"
    if not urls.exists() or not urls.read_text(encoding="utf-8", errors="ignore").strip():
        print("\n入札リスト: data/search_urls.txt が無いので作りません。主要メーカーの検索で始めるなら:\n"
              "  cp tools/search_urls_example.txt data/search_urls.txt")
        return None
    reports = ROOT / "reports"
    before = set(reports.glob("bid_report_*.csv")) if reports.exists() else set()
    if not run("appraise.py", "--max-per-search", "500"):
        return None
    new = sorted(set(reports.glob("bid_report_*.csv")) - before)
    if not new:
        return None
    import csv
    rows = list(csv.DictReader(new[-1].read_text(encoding="utf-8-sig").splitlines()))
    good = [r for r in rows if r.get("判定") in ("◎", "○")]
    html = new[-1].with_suffix(".html")
    print(f"\n📋 入札リスト: ◎○ {len(good)}件（全{len(rows)}件を鑑定）→ {html.relative_to(ROOT)}")
    print("   入札は「上限入札額」をヤフオクの入札欄に入れて放置でOK（自動入札。負けても損はしない）")
    if sys.platform == "darwin" and html.exists():
        subprocess.call(["open", str(html)])
    return len(good)


def notify(msg: str) -> None:
    """Mac の通知センターに表示（自動実行のとき）."""
    if sys.platform == "darwin":
        subprocess.call(["osascript", "-e", f'display notification "{msg}" with title "aurora"'])


if __name__ == "__main__":
    sys.exit(main())
