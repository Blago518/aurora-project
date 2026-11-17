#!/usr/bin/env python3
"""入札の結果を集計する（落札率・負けた差・利益が出る落札の率）.

1. 拡張機能の［入札した］の記録（ダウンロード/aurora_bids.csv。全件）を data/bid_log.csv に取り込む
2. 終了時刻を過ぎた出品は、公開の出品ページで最終価格を確かめる
     最終価格（入札額。ストアは税抜）≦ 自分の上限額 → 落札（見込み）。上回った → 負け
   ※ 自分が最高額かどうかはログインしないと分からないので「見込み」。ヤフオクの自動入札なら、上限額以内で終われば自分が落札している
3. 集計を reports/bid_results.md に書き、要点を表示する（update_all.py が毎回実行）

使い方（aurora フォルダで）:
    python3 tools/bid_results.py
"""
from __future__ import annotations

import csv
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
          "ship", "bid_at", "result", "final_price", "profit_final", "checked_at"]

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
    for aid in [a for a, r in old.items() if a not in ext and not r.get("result")]:
        del old[aid]  # 拡張機能で取り消した（結果が出る前）
    _write(list(old.values()), dest)
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
    _write(list(rows.values()), dest)
    return added


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


def judge_result(row: dict, state: dict, cfg: dict, fx: float) -> dict | None:
    """終わっていれば結果（result・final_price・profit_final）を返す. まだなら None."""
    end = state.get("end") or row.get("end")
    try:
        ended = datetime.fromisoformat(str(end)) <= datetime.now(timezone.utc)
    except ValueError:
        ended = False
    if str(state.get("status") or "open") == "open" and not ended:
        return None
    final = _num(state.get("price"))
    max_bid = _num(row.get("max_bid"))
    if final is None or max_bid is None:
        return None
    won = final <= max_bid and (_num(state.get("bids")) or 0) > 0
    out = {"result": "落札(見込み)" if won else "負け", "final_price": int(final)}
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


def check_results(cfg: dict, fx: float, delay: float = 1.5, rows: list[dict] | None = None, fetch=None) -> int:
    """結果が未確定で、終了時刻を過ぎた入札の結果を確かめる. 確定した件数を返す."""
    rows = _read(LOG) if rows is None else rows
    fetch = fetch or ba.http_get
    now = datetime.now(timezone.utc)
    n = 0
    for r in rows:
        if r.get("result"):
            continue
        try:
            if datetime.fromisoformat(str(r.get("end"))) > now:
                continue
        except ValueError:
            pass
        try:
            state = _item_state(fetch(f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"))
        except Exception as e:  # 読めなければ次回
            print(f"  結果を読めませんでした: {r['auction_id']}（{e}）", file=sys.stderr)
            continue
        res = judge_result(r, state, cfg, fx)
        if res:
            r.update(res, checked_at=now.strftime("%Y-%m-%d %H:%M"))
            n += 1
        time.sleep(delay)
    _write(rows)
    return n


def summarize(rows: list[dict], days: int | None = 30) -> dict:
    """集計（直近 days 日。None なら全期間）."""
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    def recent(r):
        try:
            return not since or datetime.fromisoformat(str(r.get("bid_at")).replace("Z", "+00:00")) >= since
        except ValueError:
            return True
    rs = [r for r in rows if recent(r)]
    done = [r for r in rs if r.get("result")]
    won = [r for r in done if r["result"].startswith("落札")]
    lost = [r for r in done if r["result"] == "負け"]
    profit_won = [r for r in won if (_num(r.get("profit_final")) or 0) > 0]
    ratios = [_num(r["final_price"]) / _num(r["max_bid"]) for r in lost if _num(r.get("max_bid"))]
    near = [x for x in ratios if x <= 1.1]
    return {
        "bids": len(rs), "pending": len(rs) - len(done), "done": len(done), "won": len(won), "lost": len(lost),
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
        if not res or mb is None:
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
    for label, k in (("入札", "bids"), ("結果待ち", "pending"), ("落札（見込み）", "won"), ("負け", "lost"),
                     ("落札率", "win_rate"), ("利益が出る落札", "profit_won"), ("落札の利益合計（見込み）", "profit_sum")):
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
    added = import_extension_bids() + import_aucfan_snipes()
    rows = _read(LOG)
    if not rows:
        print("入札の結果: まだ記録がありません（拡張機能で入札したら［入札した］を押す）", file=sys.stderr)
        return None
    n = check_results(cfg, fx)
    s = write_report(_read(LOG))
    print(f"入札の結果: 記録 +{added}件・結果確定 +{n}件 ／ 直近30日 入札{s['bids']}件・落札（見込み）{s['won']}件"
          f"（落札率 {'-' if s['win_rate'] is None else str(s['win_rate']) + '%'}）・利益合計（見込み）{s['profit_sum']:,}円"
          f" → reports/bid_results.md", file=sys.stderr)
    for t in advice(s):
        print("  💡 " + t, file=sys.stderr)
    return s


if __name__ == "__main__":
    run()
