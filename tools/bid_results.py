#!/usr/bin/env python3
"""入札の結果を集計する（落札率・負けた差・利益が出る落札の率）.

1. 拡張機能の［入札した］の記録（ダウンロード/aurora_bids.csv。全件）を data/bid_log.csv に取り込む
2. 終了時刻を過ぎた出品は、公開の出品ページで最終価格を確かめる（yahoo_results.py が先に読んでいればその記録を使う）
     落札かどうかは古物台帳（data/ledger.csv・data/kanri.csv）で決める（2026-11-19）:
       台帳にそのオークションID（またはヤフオクの URL）がある → 落札
       無い → 終了から ledger_wait_days 日（config.toml [yahoo_results]。既定 10日）までは「確認待ち」、過ぎたら「負け」
       入札0件で終わった → 流れた（自分も入札していない。落札率には数えない）
     max_bid は拡張機能が出した上限額（提示額）で、実際の入札額とは限らないので、落札の判定には使わない。
     ※ 台帳にオークションIDが1件も見つからないときだけ、以前の見方（最終価格 ≦ 上限額 → 落札(見込み)）にする
3. 集計を reports/bid_results.md に書き、要点を表示する（update_all.py が毎回実行）

使い方（aurora フォルダで）:
    python3 tools/bid_results.py
"""
from __future__ import annotations

import csv
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
LOG = DATA / "bid_log.csv"
HISTORY = DATA / "bid_history.csv"  # 以前の入札分析シートの入札履歴（ledger_sync.py が古物台帳「共有_過去の入札」から作る）
REPORT = ROOT / "reports" / "bid_results.md"
FIELDS = ["auction_id", "title", "max_bid", "pay_max", "usd", "profit_at_max", "end", "trusted", "tentative", "store_tax",
          "ship", "bid_at", "result", "final_price", "profit_final", "checked_at", "via", "my_bid", "bid_fail"]
# my_bid: オークファンで実際に入れた額（max_bid は拡張機能が出した上限額＝提示額）。bid_fail: オークファンの失敗コード（入札が入らなかった）
PHONE = DATA / "phone_bids.csv"  # スマホの［予約］の記録（ledger_sync.py が古物台帳「共有_店舗」の「入札予約」の行から作る。2026-11-21）

sys.path.insert(0, str(HERE))
import appraise as ba  # noqa: E402


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def _write(rows: list[dict], path: Path | None = None) -> None:
    path = path or LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def import_extension_bids(downloads: Path | None = None, dest: Path | None = None) -> int:
    """拡張機能の記録（全件）を取り込む。新しい入札は追加、取り消した入札（まだ結果なし）は消す. 追加件数を返す."""
    dest = dest or LOG
    downloads = downloads or Path.home() / "Downloads"
    files = sorted(downloads.glob("aurora_bids*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return 0
    ext = {r["auction_id"]: r for r in _read(files[-1]) if r.get("auction_id")}
    old = {r["auction_id"]: r for r in _read(dest)}
    added = 0
    for aid, r in ext.items():
        if aid not in old:
            old[aid] = r
            added += 1
        elif not old[aid].get("result"):
            old[aid].update({k: v for k, v in r.items() if k in FIELDS and v != ""})
    for aid in [a for a, r in old.items() if a not in ext and not r.get("result") and r.get("via") != "phone"]:   # スマホで予約した分は消さない
        del old[aid]  # 拡張機能で取り消した（結果が出る前）
    _write(list(old.values()), dest)
    return added


def import_phone_bids(src: Path | None = None, dest: Path | None = None) -> int:
    """スマホの［予約］（data/phone_bids.csv）を入札の記録に合わせる. 追加件数を返す.
    同じオークションは足さない。スマホで外した予約（phone_bids.csv から消えた物）は、結果が出ていなければ記録からも消す
    （残すと10日後に「負け」になり落札率がずれる）. phone_bids.csv が無い（まだ取り込んでいない）ときは何もしない."""
    src, dest = src or PHONE, dest or LOG
    if not src.exists():
        return 0
    new = _read(src)
    keep = {(r.get("auction_id") or "").strip() for r in new}
    rows = {r["auction_id"]: r for r in _read(dest)}
    gone = [a for a, r in rows.items() if r.get("via") == "phone" and a not in keep and not r.get("result")]
    for a in gone:
        del rows[a]
    added = 0
    for r in new:
        aid = (r.get("auction_id") or "").strip()
        if aid and aid not in rows:
            rows[aid] = dict(r, via="phone")
            added += 1
    if added or gone:
        _write(list(rows.values()), dest)
    return added


def import_aucfan_snipes(downloads: Path | None = None, dest: Path | None = None) -> int:
    """オークファンの予約一覧（拡張機能が保存した aurora_snipes.csv）を入札の記録に足す. 追加件数を返す."""
    downloads = downloads or Path.home() / "Downloads"
    dest = dest or LOG
    files = sorted(downloads.glob("aurora_snipes*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return 0
    rows = {r["auction_id"]: r for r in _read(dest)}
    added = 0
    for s in _read(files[-1]):
        aid = (s.get("auction_id") or "").strip()
        if not aid:
            continue
        if aid not in rows:
            rows[aid] = {"auction_id": aid, "title": s.get("title", ""), "max_bid": s.get("max_bid", ""),
                         "pay_max": s.get("max_bid", ""), "bid_at": s.get("seen_at", ""), "tentative": "0"}
            added += 1
        elif not rows[aid].get("result") and s.get("max_bid"):
            rows[aid]["max_bid"] = s["max_bid"]  # オークファンで入札設定を変えたら合わせる
    added += _merge_closed_snipes(rows, downloads)
    _write(list(rows.values()), dest)
    return added


def _merge_closed_snipes(rows: dict, downloads: Path) -> int:
    """オークファンの「入札完了」の一覧（aurora_snipes_closed.csv）: 実際に入れた額と失敗コードを入札の記録に足す. 新しく足した件数.

    失敗コードが付いた入札は入っていないので、結果を「入札失敗」にする（落札率・負けの倍率に数えない。落札と分かった物は変えない）."""
    files = sorted(downloads.glob("aurora_snipes_closed*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return 0
    added = 0
    for s in _read(files[-1]):
        aid = (s.get("auction_id") or "").strip()
        if not aid:
            continue
        r = rows.get(aid)
        if r is None:
            r = rows[aid] = {"auction_id": aid, "title": s.get("title", ""), "max_bid": s.get("my_bid", ""),
                             "pay_max": s.get("my_bid", ""), "bid_at": s.get("seen_at", ""), "tentative": "0", "via": "aucfan"}
            added += 1
        if s.get("my_bid"):
            r["my_bid"] = s["my_bid"]
        if s.get("fail_code") and not _outbid(s):
            r["bid_fail"] = s["fail_code"]  # 記録するだけ。結果（負け・確認待ち）は変えない（下の説明）
    return added


FAILED = "入札失敗"


def _outbid(s: dict) -> bool:
    """（2026-11-24 訂正）システムエラーの入札を「入札失敗」として落札率から外すのはやめた: オーナーの全521件のうち、
    最終価格と照らせた11件では10件が「最終価格 ＞ 入れた額」で、入札が入っていても落札できていない。
    エラーの表示だけでは「入っていれば勝てた」と言えないので、bid_fail は記録だけにし、結果は今までどおり台帳と最終価格で決める。

    失敗コードのうち「あなたの入札価格を上回る入札が行われました」（202）は、入札は入って競り負けた物. 入札失敗にしない.

    2026-11-24 にオーナーの一覧で見た2種類: 202＝上回る入札（負け）／900010010＝システムエラー。入札が適切に処理されませんでした（入っていない）."""
    return str(s.get("fail_code") or "").strip() == "202" or "上回る入札" in str(s.get("fail_text") or "")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _item_state(page: str) -> dict:
    """出品ページの状態（status・最終価格・入札数・終了時刻）."""
    import json
    import re
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return {}
    props = json.loads(m.group(1)).get("props") or {}
    st = (props.get("pageProps") or {}).get("initialState") or props.get("initialState") or {}
    it = ((st.get("item") or {}).get("detail") or {}).get("item") or {}
    return {"status": it.get("status"), "price": it.get("price"), "taxin": it.get("taxinPrice"),
            "bids": it.get("bids"), "end": it.get("endTime")}


def load_won_ids(paths: list[Path] | None = None) -> set[str] | None:
    """古物台帳から取り込んだ仕入れ（data/ledger.csv・data/kanri.csv）の中の、ヤフオクのオークションIDらしい語（小文字）.

    列の名前に頼らず、すべてのマスの英数字の語を集める（URL の …/auction/x123 も x123 として入る）。
    オークションIDらしい語（英字1字＋数字9〜11桁、またはヤフオクの URL）が1つも無ければ None（台帳では判定できない）."""
    import unicodedata
    words: set[str] = set()
    has_ids = False
    for p in paths if paths is not None else [DATA / "ledger.csv", DATA / "kanri.csv"]:
        if not p.exists():
            continue
        text = unicodedata.normalize("NFKC", p.read_text(encoding="utf-8-sig", errors="ignore")).lower()
        for w in re.findall(r"[a-z0-9]+", text):
            words.add(w)
            if re.fullmatch(r"[a-z]\d{9,11}", w):
                has_ids = True
        if "/auction/" in text:
            has_ids = True
    return words if has_ids else None


def _aware(text) -> datetime:
    """日時の文字 → 時差付きの日時. 時差が書いていなければ日本時間とみなす（2026-11-23 朝の更新が
    「can't compare offset-naive and offset-aware datetimes」で入札の結果を集計できなかった）."""
    d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=9)))


def _ended_days(row: dict, state: dict) -> float | None:
    try:
        end = _aware(state.get("end") or row.get("end"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - end).total_seconds() / 86400


def wait_days(cfg: dict) -> float:
    return float((cfg.get("yahoo_results") or {}).get("ledger_wait_days", 10))


def judge_result(row: dict, state: dict, cfg: dict, fx: float, won_ids: set[str] | None = None) -> dict | None:
    """終わっていれば結果（result・final_price・profit_final）を返す. まだなら None.

    won_ids: 古物台帳のオークションID（load_won_ids）。None なら以前の見方（最終価格 ≦ 上限額 → 落札(見込み)）."""
    days = _ended_days(row, state)
    ended = days is not None and days >= 0
    if str(state.get("status") or "open") == "open" and not ended:
        return None
    if (_num(state.get("bids")) or 0) <= 0 and state.get("bids") is not None:
        return {"result": "流れた", "final_price": ""}  # 入札0件で終わった（自分も入札していない）
    final = _num(state.get("price"))
    max_bid = _num(row.get("max_bid"))
    if final is None:
        return None
    if won_ids is None:
        if max_bid is None:
            return None
        won = final <= max_bid and (_num(state.get("bids")) or 0) > 0
        result = "落札(見込み)" if won else "負け"
    elif str(row.get("auction_id") or "").lower() in won_ids:
        result = "落札"
    else:
        result = "確認待ち" if days is None or days < wait_days(cfg) else "負け"
    out = {"result": result, "final_price": int(final)}
    usd = _num(row.get("usd"))
    if usd is not None:
        tax = str(row.get("store_tax")) == "1"
        paid = _num(state.get("taxin")) or (final * (1 + cfg["yahoo"]["store_tax_pct"] / 100) if tax else final)
        it = ba.Item(url="", expected_price_usd=usd, current_price_jpy=round(paid), store_tax=tax,
                     domestic_shipping_jpy=int(_num(row.get("ship")) if _num(row.get("ship")) is not None else
                                               cfg["yahoo"]["default_domestic_shipping_jpy"]),
                     trusted=str(row.get("trusted")) == "1")
        out["profit_final"] = ba.calculate(it, cfg, fx).get("profit_now_jpy")
    return out


def _known_states(path: Path | None = None) -> dict[str, dict]:
    """yahoo_results.py が記録済みの出品の状態（同じページを2回読まない）."""
    out = {}
    for r in _read(path or DATA / "yahoo_results.csv"):
        if r.get("checked_at") and r.get("status") in ("落札された", "流れた"):
            out[r["auction_id"]] = {"status": "closed", "price": r.get("final_price") or None,
                                    "bids": r.get("bids") or 0, "end": r.get("end")}
    return out


def check_results(cfg: dict, fx: float, delay: float = 1.5, rows: list[dict] | None = None, fetch=None,
                  won_ids: set[str] | None | bool = False, known: dict | None = None) -> int:
    """結果が未確定（または確認待ち）で、終了時刻を過ぎた入札の結果を確かめる. 確定した件数を返す.

    won_ids: 古物台帳のオークションID（False なら data/ から読む）。known: 記録済みの出品の状態（yahoo_results）."""
    rows = _read(LOG) if rows is None else rows
    fetch = fetch or ba.http_get
    if won_ids is False:
        won_ids = load_won_ids()
    known = _known_states() if known is None else known
    now = datetime.now(timezone.utc)
    n = 0
    for r in rows:
        res0 = r.get("result") or ""
        if res0 == "確認待ち" or (won_ids is not None and res0 == "落札(見込み)"):
            # 最終価格は読み済み。台帳に載ったら落札、待つ日数を過ぎたら負け（以前の見込みも台帳で確かめる）
            aid = str(r.get("auction_id") or "").lower()
            if won_ids is not None and aid in won_ids:
                r.update(result="落札", checked_at=now.strftime("%Y-%m-%d %H:%M"))
                n += 1
            elif res0 == "確認待ち" and won_ids is None:
                pass  # 台帳でオークションIDが読めなくなった → そのまま
            elif res0 == "確認待ち" and (_ended_days(r, {}) or 0) >= wait_days(cfg):
                r.update(result="負け", checked_at=now.strftime("%Y-%m-%d %H:%M"))
                n += 1
            continue
        if res0:
            continue
        try:
            if _aware(r.get("end")) > now:
                continue
        except ValueError:
            pass
        state = known.get(r.get("auction_id", ""))
        if state is None:
            try:
                state = _item_state(fetch(f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"))
            except Exception as e:  # 読めなければ次回
                print(f"  結果を読めませんでした: {r['auction_id']}（{e}）", file=sys.stderr)
                continue
            time.sleep(delay)
        res = judge_result(r, state, cfg, fx, won_ids)
        if res:
            r.update(res, checked_at=now.strftime("%Y-%m-%d %H:%M"))
            if res["result"] != "確認待ち":
                n += 1
    _write(rows)
    return n


def summarize(rows: list[dict], days: int | None = 30) -> dict:
    """集計（直近 days 日。None なら全期間）."""
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    def recent(r):
        try:
            return not since or _aware(r.get("bid_at")) >= since
        except ValueError:
            return True
    rs = [r for r in rows if recent(r)]
    # 確認待ち（台帳に載るのを待っている）は結果待ちに、流れた（入札0件で終了）は落札率に数えない
    done = [r for r in rs if r.get("result") and r["result"] not in ("確認待ち", "流れた", FAILED)]
    unsold = [r for r in rs if r.get("result") == "流れた"]
    won = [r for r in done if r["result"].startswith("落札")]
    lost = [r for r in done if r["result"] == "負け"]
    profit_won = [r for r in won if (_num(r.get("profit_final")) or 0) > 0]
    ratios = [_num(r["final_price"]) / _num(r["max_bid"]) for r in lost
              if _num(r.get("max_bid")) and _num(r.get("final_price")) is not None]
    near = [x for x in ratios if x <= 1.1]
    return {
        "bids": len(rs), "pending": len(rs) - len(done) - len(unsold) - sum(1 for r in rs if r.get("result") == FAILED), "done": len(done), "won": len(won), "lost": len(lost),
        "unsold": len(unsold), "failed": sum(1 for r in rs if r.get("result") == FAILED),
        "win_rate": round(len(won) / len(done) * 100) if done else None,
        "profit_won": len(profit_won),
        "profit_sum": int(sum(_num(r.get("profit_final")) or 0 for r in won)),
        "lost_median_ratio": round(statistics.median(ratios), 2) if ratios else None,
        "near_miss": len(near),
        "trusted": {k: sum(1 for r in won if str(r.get("trusted")) == k) for k in ("1", "0")},
    }


# 落札見込み: 過去の入札（結果が出たもの）を「上限額の価格帯 × ストア/個人」でまとめ、勝った割合で 高・中・低。
# 拡張機能（extension_template.js の winOdds）と同じ規則。3件未満のまとまりは価格帯だけで見て、それも3件未満なら出さない
def price_band(max_bid: float) -> str:
    return "1万円未満" if max_bid < 10000 else "1〜3万円" if max_bid < 30000 else "3万円以上"


def win_stats(rows: list[dict]) -> dict[str, list[int]]:
    """{"価格帯|store" / "価格帯|*": [勝ち, 件数]}（結果が出た入札だけ）."""
    out: dict[str, list[int]] = {}
    for r in rows:
        res, mb = r.get("result") or "", _num(r.get("max_bid"))
        if not res or res in ("確認待ち", "流れた") or mb is None:
            continue
        won = 1 if res.startswith("落札") else 0
        band = price_band(mb)
        st = str(r.get("store_tax") or "")  # 空欄＝ストアか個人か分からない（以前の入札履歴）→ 価格帯だけに数える
        for k in ([f"{band}|{'1' if st == '1' else '0'}"] if st in ("0", "1") else []) + [f"{band}|*"]:
            v = out.setdefault(k, [0, 0])
            v[0] += won
            v[1] += 1
    return out


def win_odds(stats: dict, max_bid: float, store: bool) -> dict | None:
    """{"label": 高/中/低, "won": n, "n": n, "key": まとまり} か None（記録が足りない）."""
    band = price_band(max_bid)
    for k in (f"{band}|{'1' if store else '0'}", f"{band}|*"):
        won, n = stats.get(k, [0, 0])
        if n >= 3:
            rate = won / n
            return {"label": "高" if rate >= 0.6 else "中" if rate >= 0.3 else "低", "won": won, "n": n, "key": k}
    return None


def load_win_stats(path: Path | None = None, history: Path | None = None) -> dict:
    """今の入札記録＋以前の入札履歴（同じ出品は今の記録を優先）."""
    rows = _read(path or LOG)
    have = {r.get("auction_id") for r in rows}
    rows += [r for r in _read(history or HISTORY) if r.get("auction_id") not in have]
    return win_stats(rows)


def advice(s: dict) -> list[str]:
    tips = []
    if s["bids"] < 10:
        tips.append("入札がまだ少ないです。毎朝の入札リストの◎○は全部、上限額で入札しましょう（1日10〜20件が目安）。")
    if s["done"] >= 5 and s["win_rate"] is not None and s["win_rate"] < 10:
        tips.append("落札率が10%未満です。調べる検索を増やす（data/search_urls.txt）か、利益が残る範囲で安心枠を増やしましょう。")
    if s["lost"] >= 5 and s["near_miss"] / s["lost"] >= 0.4:
        tips.append(f"負けの{s['near_miss']}件は上限額の1.1倍以内の僅差でした。終了直前の入札（予約入札）にすると競り上がりを避けやすくなります。")
    if s["lost_median_ratio"] and s["lost_median_ratio"] >= 1.5:
        tips.append(f"負けた出品の最終価格は上限額の約{s['lost_median_ratio']}倍。その機種はヤフオクで高すぎるので、別の検索に時間を使いましょう。")
    return tips


def write_report(rows: list[dict], path: Path | None = None) -> dict:
    path = path or REPORT
    s30, s_all = summarize(rows, 30), summarize(rows, None)
    lines = [f"# 入札の結果（{datetime.now():%Y-%m-%d %H:%M}）", "",
             "| | 直近30日 | 全期間 |", "|---|---|---|"]
    for label, k in (("入札", "bids"), ("結果待ち（確認待ちを含む）", "pending"), ("落札", "won"), ("負け", "lost"),
                     ("流れた（入札0件）", "unsold"),
                     ("落札率", "win_rate"), ("利益が出る落札", "profit_won"), ("落札の利益合計（最終価格で計算）", "profit_sum")):
        fmt = (lambda v: "-" if v is None else f"{v}%") if k == "win_rate" else \
              (lambda v: f"{v:,}円") if k == "profit_sum" else (lambda v: f"{v}件")
        lines.append(f"| {label} | {fmt(s30[k])} | {fmt(s_all[k])} |")
    tips = [f"- {t}" for t in advice(s30)] or ["- 今のやり方で続けましょう。"]
    lines += ["", "## 次の打ち手", *tips, "",
              "## 結果の一覧（新しい順）", "", "| 入札日 | 結果 | 上限 | 最終 | 利益（見込み） | 商品 |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: str(r.get("bid_at")), reverse=True)[:100]:
        lines.append(f"| {str(r.get('bid_at'))[:10]} | {r.get('result') or '結果待ち'} | {r.get('max_bid')} | {r.get('final_price') or '-'} | "
                     f"{r.get('profit_final') or '-'} | [{str(r.get('title'))[:40]}](https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}) |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return s30


def run(cfg: dict | None = None, fx: float | None = None) -> dict | None:
    cfg = cfg or ba.load_config(None)
    fx = fx or float(cfg["fx"]["usd_jpy"])
    added = import_extension_bids() + import_aucfan_snipes() + import_phone_bids()
    rows = _read(LOG)
    if not rows:
        print("入札の結果: まだ記録がありません（拡張機能で入札したら［入札した］を押す）", file=sys.stderr)
        return None
    n = check_results(cfg, fx)
    s = write_report(_read(LOG))
    print(f"入札の結果: 記録 +{added}件・結果確定 +{n}件 ／ 直近30日 入札{s['bids']}件・落札{s['won']}件"
          f"（落札率 {'-' if s['win_rate'] is None else str(s['win_rate']) + '%'}）・利益合計（最終価格で計算）{s['profit_sum']:,}円"
          f" → reports/bid_results.md", file=sys.stderr)
    for t in advice(s):
        print("  💡 " + t, file=sys.stderr)
    return s


if __name__ == "__main__":
    run()
