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
4. 勝ちやすさ（win_score.py）: 入札リスト・売れ筋・負けた機種のヤフオクの終わった出品から、今の上限額以下で終わった割合 → data/win_score.csv
   入札リスト: data/search_urls.txt の検索をまとめて鑑定（robots.txt で Disallow の引数は外して読む） → reports/bid_report_*.html を開く（◎○の件数を通知）
   一番上に「今日の予約リスト（目標8本）」→ 古物台帳「Mac_予約リスト」
   → スマホ用データ（store_data.json・iCloud・暗号化して自動配信。入札候補もスマホの「入札」タブに届く）→ ナレッジ
   → eBay の出品中一覧・注文・取引明細（eBay API 設定済みなら）→ 数字のグラフ（reports/dashboard.html）
5. 落札相場（拡張機能を作る前）: 前回の入札リスト・拡張機能（ダウンロード/aurora_seen.csv）の ◎○・仮◎○・確認してから の出品を見張り、
   終わった出品の最終価格・入札数を data/yahoo_results.csv に記録 → 古物台帳「Mac_落札相場」（yahoo_results.py）
   入札の結果: 拡張機能の［入札した］の記録を取り込み、終わった出品の最終価格から落札率などを集計 → reports/bid_results.md
   （落札かどうかは古物台帳にそのオークションIDがあるかで決める）
   → 入札の記録を 古物台帳の「入札結果」シートに送る（ゼニガメが負け方を学ぶ）

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

# ---- 更新の記録 → 古物台帳「Mac_更新ログ」（2026-11-20: Mac で止まっても、クラウドのサトシが毎朝自分で気づけるように）
LOG_SHEET = "Mac_更新ログ"
LOG_LINES: list[str] = []
PROBLEM_RE = re.compile(r"⚠|Traceback|Error|エラー|できませんでした|送れませんでした|失敗")
NOISE_RE = re.compile(r"落札相場: 読めませんでした|HTTP Error 404|^[◎○×]\s")  # 終わって消えた出品（毎日ある）と、入札リストの行（商品名の「エラー」）は問題にしない
SUMMARY_RE = re.compile(r"^(=====|今日の予約リスト|説明文チェック|仮◎○|📋 入札リスト|eBay の取引|返品の一覧|落札相場:|勝ちやすさ:|"
                        r"相場メモ|出力:|✅ 完了|入札結果:|直近\d+日の(注文|取引)|古物台帳:|スマホへの自動配信)")


class _Tee:
    """画面に出しつつ、1行ずつ LOG_LINES にためる（Mac_更新ログ に送る）."""

    def __init__(self, base):
        self.base, self.buf = base, ""

    def write(self, t):
        self.base.write(t)
        self.buf += t
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            LOG_LINES.append(line.rstrip())
        return len(t)

    def flush(self):
        self.base.flush()

    def __getattr__(self, name):
        return getattr(self.base, name)


def log_csv(ok: bool, started: datetime) -> str:
    """Mac_更新ログ の中身: 日時・結果、問題の行、まとめの行（新しい問題を先に）."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["種類", "内容"])
    w.writerow(["日時", f"{started:%Y-%m-%d %H:%M}〜{datetime.now():%H:%M}"])
    w.writerow(["結果", "完了" if ok else "一部でエラー"])
    lines = [x.strip() for x in LOG_LINES if x.strip()]
    probs = [x for x in lines if PROBLEM_RE.search(x) and not NOISE_RE.search(x)]
    cell = lambda x: ("'" + x if x[:1] in "=+-@" else x)[:300]   # 「=====」などが式と読まれて #ERROR! にならないように
    for x in probs[:80]:
        w.writerow(["問題", cell(x)])
    for x in [x for x in lines if SUMMARY_RE.search(x) and not NOISE_RE.search(x)][:80]:
        w.writerow(["まとめ", cell(x)])
    return buf.getvalue()


def upload_log(ok: bool, started: datetime) -> None:
    try:
        import ledger_sync
        ledger_sync.post_sheet(LOG_SHEET, log_csv(ok, started))
    except Exception as e:  # noqa: BLE001  送れなくても更新は終わっている
        print(f"  ⚠ 更新の記録を台帳に送れませんでした（{e}）")


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


def move_evidence(downloads: Path, home: Path | None = None) -> int:
    """ヤフオクの仕入れ証跡（取引ナビの PNG）を税理のフォルダに移す（tax_folder.py）. 移した数を返す."""
    import tax_folder
    return tax_folder.move_evidence(downloads, home)["moved"]


def run(script: str, *args: str) -> bool:
    p = HERE / script
    if not p.exists():
        return True
    print(f"\n▶ {script}")
    # 子のプログラムの表示も Mac_更新ログ に残すため、1行ずつ受け取って画面に出す（すぐ出るよう PYTHONUNBUFFERED）
    import os
    proc = subprocess.Popen([sys.executable, str(p), *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
    for line in proc.stdout:
        sys.stdout.write(line)
    ok = proc.wait() == 0
    if not ok:
        print(f"  ⚠ {script} でエラーが出ました（上の表示を Claude に送ってください）")
    return ok


def wait_online(host: str = "github.com", seconds: int = 120, step: int = 5, sleep=time.sleep) -> bool:
    """ネットにつながるまで待つ（最大 seconds 秒）. つながれば True.
    2026-11-24: Mac が起きた直後に更新が始まり、名前が引けず（Could not resolve host）、プログラムの取り込みと古物台帳の読み込みが両方とも失敗した."""
    import socket
    waited = 0
    while True:
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            if waited >= seconds:
                return False
            sleep(step)
            waited += step


def self_update(root: Path | None = None, run_git=None) -> tuple[bool, str]:
    """マージ済みの変更を Mac に取り込む（git pull --ff-only）. 戻り値: (新しくなったか, 更新ログに残す1行).
    2026-11-23: 毎朝の更新は取り込みをしていなかったので、マージのたびにオーナーがコマンドを打っていた。
    早送りできるときだけ取り込む（main でない・つながらない・手元に変更があるときは今の版で続け、「⚠ 取り込めませんでした」を
    Mac_更新ログ に残す＝サトシが朝に読む）. store/core.js は毎朝 Mac が作り直す物なので、取り込みの前に元へ戻す."""
    root = root or Path(__file__).resolve().parent.parent

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=90)

    if run_git is None:
        wait_online()  # つながらないままでも下で「取り込めませんでした」を残して続ける
    git = run_git or git
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            return False, f"⚠ プログラムを取り込めませんでした（今のブランチが main ではない: {branch}）"
        git("checkout", "--", "store/core.js")
        before = git("rev-parse", "HEAD").stdout.strip()
        res = git("pull", "--ff-only", "--quiet")
        after = git("rev-parse", "HEAD").stdout.strip()
    except Exception as e:  # noqa: BLE001
        return False, f"⚠ プログラムを取り込めませんでした（{e}）"
    if res.returncode != 0:
        why = ((res.stderr or "").strip().splitlines() or ["git pull できません"])[-1][:100]
        return False, f"⚠ プログラムを取り込めませんでした（{why}）"
    if before and after and before != after:
        return True, f"プログラムを新しくしました（{before[:7]} → {after[:7]}）"
    return False, f"プログラムは最新です（{after[:7]}）"


def main(argv=None) -> int:
    # 自動実行ではログがファイルなので、見出し（▶ …）がまとめて最後に書かれ、各プログラムのエラー文より後ろに並んでしまう。
    # 1行ずつ書き出して、ログの順番を実行の順番どおりにする
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="相場データを全部まとめて最新にする")
    ap.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    ap.add_argument("--days", type=int, default=90, help="この日数より古いダウンロードは見ない")
    ap.add_argument("--dry-run", action="store_true", help="取り込む内容を表示するだけ（作り直さない）")
    ap.add_argument("--notify", action="store_true", help="終わったら Mac の通知を出す（自動実行用）")
    ap.add_argument("--no-bids", action="store_true", help="入札リスト（data/search_urls.txt の鑑定）を作らない")
    args = ap.parse_args(argv)
    import os
    pull_note = os.environ.get("AURORA_PULLED", "")
    if argv is None and not args.dry_run and not pull_note:
        changed, pull_note = self_update()
        if changed:
            os.environ["AURORA_PULLED"] = pull_note   # 新しいプログラムで最初からやり直す（1回だけ。取り込みの結果は更新ログへ）
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    started = datetime.now()
    if not isinstance(sys.stdout, _Tee):
        sys.stdout, sys.stderr = _Tee(sys.stdout), _Tee(sys.stderr)

    print(f"===== {datetime.now():%Y-%m-%d %H:%M} 相場データの更新 =====")
    if pull_note:
        print(f"  {pull_note}")
    print("▶ 古物台帳（スプレッドシート）")
    import ledger_sync
    ledger_sync.sync(dry_run=args.dry_run)
    print("\n▶ ダウンロードフォルダから取り込み")
    import_downloads(args.downloads, args.days, args.dry_run)
    if not args.dry_run:
        # 輸出許可書: DHL の PDF を数え、FedEx の依頼用 Excel を作り直す（材料が無ければ何もしない。2026-11-23 オーナー「自動化してほしい」）
        try:
            import export_permit
            if export_permit.TEMPLATE.exists() and export_permit.speedpak_files([args.downloads]):
                export_permit.main([])
        except SystemExit:
            pass
        except Exception as e:
            print(f"  輸出許可書の Excel を作れませんでした: {e}")
        try:   # 証跡と輸出許可書を税理のフォルダへ振り分け、行き先・件数を「Mac_税理フォルダ」に送る（2026-11-23 オーナー「その中に振り分けれる？」）
            import tax_folder
            tax_folder.run(args.downloads)
        except Exception as e:
            print(f"  証跡・輸出許可書を振り分けられませんでした: {e}")
        try:   # 保存できた数と足りない番号 → 画面・Mac_更新ログ・古物台帳「Mac_輸出許可書」（2026-11-23 オーナー「ダウンロード先で確認しろ」）
            import export_permit
            export_permit.report_status(args.downloads)
        except Exception as e:
            print(f"  輸出許可書を数えられませんでした: {e}")
    print("\n▶ Chatwork の販売報告")
    import chatwork_sync
    chatwork_sync.sync(dry_run=args.dry_run)
    if args.dry_run:
        return 0

    ok = True
    if (DATA / "ledger.csv").exists() or list(DATA.glob("hanpukai_*.xlsx")):
        ok &= run("build_reference_prices.py")
    try:   # 露出計の不良と書いて売れた記録 → 古物台帳「Mac_露出計実績」（2026-11-23 狙い目の裏づけ。サトシが読んで報告する）
        import appraise
        appraise.report_meter_sales()
    except Exception as e:
        print(f"  露出計の実績を数えられませんでした: {e}")
    # 落札相場は拡張機能を作る前に（前回の入札リストの出品も、終わっていればここで記録 → 拡張機能に出る）
    try:
        import yahoo_results  # 候補に出た出品が、実際にいくらで落札されたか（判定には使わない）
        print("\n▶ 落札相場（候補に出た出品の最終価格）")
        yahoo_results.run(downloads=args.downloads)
    except Exception as e:  # noqa: BLE001  記録の失敗で更新全体を止めない
        print(f"  落札相場を記録できませんでした（{e}）")
    if not args.no_bids:
        try:
            import win_score  # 勝ちやすさ（終わった出品のうち今の上限額以下で終わった割合）→ 入札リスト・拡張機能の並び（判定には使わない）
            print("\n▶ 勝ちやすさ（ヤフオクの終わった出品）")
            win_score.run()
        except Exception as e:  # noqa: BLE001  失敗しても今の並びのまま続ける
            print(f"  勝ちやすさを出せませんでした（{e}）")
    ok &= run("build_extension.py")
    bids = None if args.no_bids else bid_list()
    ok &= run("build_store.py")  # 入札リストの後に（入札候補もスマホに届ける）
    if not args.dry_run:
        try:
            import ledger_sync
            ledger_sync.upload_recs()  # おすすめ全部 → 古物台帳「Mac_おすすめ」（クラウドで枠の誤りを見て直す）
        except Exception as e:  # noqa: BLE001
            print(f"  おすすめの一覧を送れませんでした（{e}）")
    ok &= run("build_knowledge.py")
    dashboard()
    results = None
    try:
        import bid_results  # 入札の結果（落札率・利益が出る落札の率）
        print("\n▶ 入札の結果")
        results = bid_results.run()
    except Exception as e:  # 集計の失敗で更新全体を止めない
        print(f"  入札の結果を集計できませんでした（{e}）")
    if not args.dry_run:
        ledger_sync.upload_bids()  # 入札の記録 → 古物台帳「入札結果」（ゼニガメが負け方を学ぶ）
    print("\n" + ("✅ 完了" if ok else "⚠ 一部でエラーがありました"))
    if not args.dry_run:
        upload_log(ok, started)  # → 古物台帳「Mac_更新ログ」（サトシが毎朝確かめる）
    print("  Chrome: 新しい相場はヤフオクのページを開き直すだけで反映（↻ は拡張機能のプログラムを新しくしたときだけ）")
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
                           ("取引明細（2年分）", ["fees", "--days", "730"]),
                           ("返品・キャンセルの状況（1年分）", ["returns", "--days", "365"]),
                           ("残高（まだ出金していないお金）→ 台帳「Mac_資金」", ["funds", "--upload"])):
            print(f"\n▶ eBay の{what}")
            subprocess.call([sys.executable, str(HERE / "ebay_api.py"), *args], cwd=ROOT)
    print("\n▶ 数字のグラフ")
    if subprocess.call([sys.executable, str(HERE / "build_dashboard.py"), "--no-open"], cwd=ROOT) != 0:
        print("  ⚠ グラフを作れませんでした（上の表示を Claude に送ってください）")
    else:
        try:
            import ledger_sync
            ledger_sync.upload_dashboard()  # → 古物台帳「Mac_グラフ」→ Artifacts のグラフが開くたびに読む
            ledger_sync.upload_transactions()  # → 古物台帳「Mac_取引」→ 管理の月次サマリー（売上・件数・返金）が毎朝最新に
            ledger_sync.upload_returns()  # → 古物台帳「Mac_返品」→ ゴース・フシギダネが返品の理由を分析（購入者の情報は送らない）
        except Exception as e:  # noqa: BLE001  送れなくても他の更新は続ける
            print(f"  ⚠ グラフを台帳に送れませんでした（{e}）")


def bid_list() -> int | None:
    """data/search_urls.txt の検索をまとめて鑑定し、入札リスト（reports/bid_report_*.html）を作って開く. ◎○の件数を返す."""
    urls = DATA / "search_urls.txt"
    if not urls.exists() or not urls.read_text(encoding="utf-8", errors="ignore").strip():
        print("\n入札リスト: data/search_urls.txt が無いので作りません。主要メーカーの検索で始めるなら:\n"
              "  cp tools/search_urls_example.txt data/search_urls.txt")
        return None
    reports = ROOT / "reports"
    before = set(reports.glob("bid_report_*.csv")) if reports.exists() else set()
    # 売れ筋の機種を名指しで探す検索を30本足す（2026-11-18 ユーザー決定。販売履歴のある機種を入札リストに多く出す）
    if not run("appraise.py", "--max-per-search", "500", "--hit-searches", "30"):
        return None
    new = sorted(set(reports.glob("bid_report_*.csv")) - before)
    if not new:
        return None
    import csv
    rows = list(csv.DictReader(new[-1].read_text(encoding="utf-8-sig").splitlines()))
    good = [r for r in rows if r.get("判定") in ("◎", "○") and r.get("根拠の強さ", "確認不要") != "確認してから"]
    html = new[-1].with_suffix(".html")
    print(f"\n📋 入札リスト: ◎○ {len(good)}件（全{len(rows)}件を鑑定）→ {html.relative_to(ROOT)}")
    print("   入札は「上限入札額」をヤフオクの入札欄に入れて放置でOK（自動入札。負けても損はしない）")
    try:
        import ledger_sync
        ledger_sync.upload_unpriced()  # 相場が無かった機種 → 古物台帳「Mac_未登録機種」（夜にクラウドが下調べ）
    except Exception as e:  # noqa: BLE001  送れなくても入札リストはそのまま
        print(f"  ⚠ 未登録機種を台帳に送れませんでした（{e}）")
    upload_reserve(reports)
    if sys.platform == "darwin" and html.exists():
        subprocess.call(["open", str(html)])
    return len(good)


RESERVE_SHEET = "Mac_予約リスト"  # 今日の予約リスト（目標8本。2026-11-19 ユーザー決定）→ 古物台帳（クラウド・スマホから見る）


def upload_reserve(reports: Path, post=None) -> int | None:
    """一番新しい reports/reserve_list_*.csv → 古物台帳「Mac_予約リスト」（まるごと置き換え）. 送った行数."""
    files = sorted(reports.glob("reserve_list_*.csv")) if reports.exists() else []
    if not files:
        return None
    body = files[-1].read_text(encoding="utf-8-sig")
    try:
        import ledger_sync
        if not ledger_sync.post_sheet(RESERVE_SHEET, body, post=post):
            return None
    except Exception as e:  # noqa: BLE001  送れなくても入札リストはそのまま
        print(f"  ⚠ 予約リストを台帳に送れませんでした（{e}）")
        return None
    n = max(0, body.strip().count("\n"))
    print(f"   今日の予約リスト: {n}行 → 古物台帳「{RESERVE_SHEET}」")
    return n


def notify(msg: str) -> None:
    """Mac の通知センターに表示（自動実行のとき）."""
    if sys.platform == "darwin":
        subprocess.call(["osascript", "-e", f'display notification "{msg}" with title "aurora"'])


if __name__ == "__main__":
    sys.exit(main())
