#!/usr/bin/env python3
"""勝ちやすさ（勝てる機種スコア）: ヤフオクの終わった出品のうち、今の上限入札額以下で終わった割合を機種ごとに出す.

2026-11-19 ユーザー決定（落札を増やすための提案書）: 勝ちやすさが1割未満の機種は入札リスト・拡張機能の下に回す。
判定（◎○×）・上限入札額・利益の基準（2,000円・8%）は変えない（並べ方と表示だけに使う）。

1. 機種を選ぶ（1回に [win_score] max_models まで。同じ機種は refresh_days 日ごと）
     前回の入札リストの ◎○（確認してから も含む）→ 売れ筋（販売履歴で利益が出ていた機種）→ 直近60日に競り負けた機種
2. 終わった出品の検索 `/closedsearch/closedsearch?p=機種名&b=…`（robots.txt で Allow の形。n=・aucminprice= などは付けない）を
   1機種 pages_per_model ページまで、delay_sec 秒（1.5秒以上）空けて読む
3. 読んだ出品を今の判定ロジック（appraise.analyze）でそのまま鑑定し、上限入札額を出す。
   「上限以下で終わった」＝落札額（ストアは税抜にして）≦ 上限入札額。
   除くもの（提案書と同じ）: 入札0件（流れた）・難あり語（ジャンク・カビ・現状・動作未確認・セット・まとめ・＋ など）・
   付属品だけ／アダプター等（バッテリーグリップ・純正バッテリーだけの出品も）・上限額の4割より安く終わった出品（付属品・別物の混入）・
   相場が当たらない出品・機種（model_key）が違う出品
4. data/win_score.csv（機種ごとの件数・上限以下の件数・割合・月の件数）に保存（data/ はコミットしない）

使い方（aurora フォルダで。update_all.py が毎朝、入札リストを作る前に実行する）:
    python3 tools/win_score.py
    python3 tools/win_score.py --max-models 5    # 試しに少しだけ
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
SCORES = DATA / "win_score.csv"
REPORTS = ROOT / "reports"
JST = timezone(timedelta(hours=9))
FIELDS = ["model_key", "model", "query", "n", "won", "rate", "per_month", "span_days", "med_price", "med_max_bid", "checked_at"]

sys.path.insert(0, str(HERE))
import appraise as ba  # noqa: E402

# 難あり語・まとめ売り（提案書 ana_closed.py と同じ）。この語がある出品は勝ちやすさに数えない
BAD_RE = re.compile(r"ジャンク|部品|難あり|訳あり|カビ|クモリ|曇り|バルサム|現状|動作未確認|不動|故障|キャップのみ|フードのみ|"
                    r"ケースのみ|説明書のみ|箱のみ|セット|まとめ|\+|＋|レンズ付|ボディ.*レンズ", re.I)
SKIP_FLAGS = {"付属品のみ?", "アダプター等", "セット品"}
# 判定の「付属品のみ?」に入っていない付属品（バッテリーグリップ・純正バッテリー・充電器だけの出品）。勝ちやすさの集計だけで除く（判定は変えない）
ACC_EXTRA_RE = re.compile(r"バッテリー\s*グリップ|グリップのみ|バッテリーのみ|充電器のみ|チャージャーのみ|"
                          r"用\s*(?:バッテリー|電池|充電器|チャージャー|グリップ|リモコン|ケーブル)|"
                          r"(?<![A-Za-z])(?:MB|BG)-[A-Z]{0,3}\d", re.I)  # 縦位置グリップの型番（MB-D15・BG-E14）。電池の型番はボディに付属でも書かれるので見ない
# 上限入札額のこの割合より安く終わった出品は、付属品・別物の混入とみなして数えない（提案書 1-3 のリスク欄と同じ）
MIN_PRICE_RATIO = 0.4


def settings(cfg: dict) -> dict:
    s = cfg.get("win_score", {}) or {}
    return {"max_models": int(s.get("max_models", 30)), "pages": int(s.get("pages_per_model", 2)),
            "delay": max(ba.MIN_DELAY, float(s.get("delay_sec", 3.0))), "refresh_days": int(s.get("refresh_days", 7)),
            "lookback_days": int(s.get("lookback_days", 90)), "min_samples": int(s.get("min_samples", 5)),
            "low_rate": float(s.get("low_rate", 0.10)), "default_rate": float(s.get("default_rate", 0.2))}


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. 機種を選ぶ
# ---------------------------------------------------------------------------


def target_of(title: str) -> dict | None:
    """商品名 → 調べる機種 {key, label, query}. まとめ売り・キット・付属品・メーカーの無い機種名は None."""
    if not title or ba.KIT_RE.search(title) or SKIP_FLAGS & set(ba.detect_flags(title)):
        return None
    label, key = ba.model_key(title)
    words = label.split()
    if len(words) < 2 or not ba._brand_of(label):
        return None
    return {"key": key, "label": label, "query": query_of(label)}


def query_of(label: str) -> str:
    """終わった出品の検索語: 機種名から F値（F1.4・F2D・f/2.8）と1文字の語（L）を外す（書き方の違いで漏れないように。照合は model_key で絞る）.
    ヤフオクの検索は Nikon と ニコン を同じに扱う（2026-11-19 確認: どちらも同じ件数）."""
    toks = [t for t in label.split() if not re.fullmatch(r"f/?\d+(?:\.\d+)?[a-z]?(?:-\d+(?:\.\d+)?)?", t, re.I) and len(t) > 1]
    return " ".join(toks)


def targets(report: list[dict], sales: list[dict], bids: list[dict], cfg: dict, fx: float,
            scores: dict[str, dict] | None = None, now: datetime | None = None,
            max_models: int = 30, refresh_days: int = 7) -> list[dict]:
    """調べる機種（入札リストの ◎○ → 売れ筋 → 負けた機種の順。まだ調べていない機種・古い機種から）."""
    now = now or datetime.now(JST)
    cands: list[dict] = []
    seen: set[str] = set()

    def add(title: str, why: str) -> None:
        t = target_of(title)
        if t and t["key"] not in seen:
            seen.add(t["key"])
            cands.append(t | {"why": why})

    for r in report:
        if r.get("判定") in ("◎", "○"):
            add(r.get("タイトル", ""), "入札リスト")
    hits = sorted((r for r in sales if ba.sold_hit(r, cfg, fx)), key=lambda r: -ba._hit_profit(r, cfg, fx))
    for r in hits:
        add(r.get("title", ""), "売れ筋")
    since = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    for r in sorted(bids, key=lambda r: r.get("end") or r.get("bid_at") or "", reverse=True):
        if (r.get("result") or "") in ("負け", "落札失敗") and (r.get("end") or r.get("bid_at") or "")[:10] >= since:
            add(r.get("title", ""), "負けた機種")
    scores = scores or {}
    limit = (now - timedelta(days=refresh_days)).strftime("%Y-%m-%d %H:%M")
    due = [c for c in cands if (scores.get(c["key"]) or {}).get("checked_at", "") < limit]
    due.sort(key=lambda c: (scores.get(c["key"]) or {}).get("checked_at", ""))  # まだ調べていない機種（""）→ 古い順。同じなら上の順
    return due[:max(0, max_models)]


# ---------------------------------------------------------------------------
# 2. 終わった出品を読む
# ---------------------------------------------------------------------------


def closed_url(query: str) -> str:
    return "https://auctions.yahoo.co.jp/closedsearch/closedsearch?" + urllib.parse.urlencode({"p": query})


def fetch_closed(query: str, pages: int, delay: float, fetch=None, budget: dict | None = None) -> list[ba.Item]:
    """終わった出品（落札相場）の検索を pages ページまで. 1ページの件数はヤフオクの既定（n= は付けない）."""
    if budget is None:
        budget = {"pages": pages}
    sub = {"pages": min(pages, budget.get("pages", pages))}
    before = sub["pages"]
    items = ba.fetch_listing(closed_url(query), 10 ** 6, delay, sub, fetch)
    budget["pages"] = budget.get("pages", pages) - (before - sub["pages"])
    return items


# ---------------------------------------------------------------------------
# 3. 今の判定ロジックで上限額を出して数える
# ---------------------------------------------------------------------------


def usable(it: ba.Item) -> bool:
    """勝ちやすさに数える出品か（入札があって終わった・難あり語なし・付属品やまとめ売りでない）."""
    if not it.title or not it.bids or it.current_price_jpy is None:
        return False
    if BAD_RE.search(it.title) or ba.KIT_RE.search(it.title) or ACC_EXTRA_RE.search(it.title):
        return False
    return not (SKIP_FLAGS & set(ba.detect_flags(it.title)))


def evaluate(groups: dict[str, list[ba.Item]], sales: list[dict], cfg: dict, fx: float,
             kantei: list[dict] | None = None, trusted: dict | None = None) -> dict[str, list[dict]]:
    """{機種キー: 終わった出品}（読んだまま）→ {機種キー: [{price, bid, max_bid, won, end}]}（数える出品だけ）."""
    keep: dict[str, list[ba.Item]] = {}
    for key, items in groups.items():
        seen: set[str] = set()
        for it in items:
            if it.auction_id in seen or not usable(it) or ba.model_key(it.title)[1] != key:
                continue
            seen.add(it.auction_id)
            it.trusted = bool(trusted) and it.seller_id in trusted
            keep.setdefault(key, []).append(it)
    flat = [it for v in keep.values() for it in v]
    if flat:
        ba.analyze(flat, sales, cfg, fx, offline=True, delay=0, kantei=kantei or [])
    out: dict[str, list[dict]] = {}
    for key, items in keep.items():
        for it in items:
            c = it.calc
            if not c or it.expected_price_usd is None or c.get("bid_now") is None:
                continue
            mark = ba.judge(it)[0]
            if mark == "×" and ba.optical_skip_words(it.title):  # 光学難の語（BAD_RE で大半は除いている）
                continue
            if c["bid_now"] < c["max_bid_jpy"] * MIN_PRICE_RATIO:  # 安すぎる＝付属品・別物の混入
                continue
            out.setdefault(key, []).append({
                "price": it.current_price_jpy, "bid": round(c["bid_now"]), "max_bid": c["max_bid_jpy"],
                "won": c["bid_now"] <= c["max_bid_jpy"], "end": it.end_time, "id": it.auction_id})
    return out


def score(rows: list[dict], now: datetime | None = None, lookback_days: int = 90) -> dict | None:
    """数える出品 → {n, won, rate, per_month, span_days, med_price, med_max_bid}. 直近 lookback_days 日だけ."""
    now = now or datetime.now(JST)
    since = now - timedelta(days=lookback_days)
    ends, use = [], []
    for r in rows:
        try:
            d = datetime.fromisoformat(str(r.get("end") or "").replace("Z", "+00:00"))
        except ValueError:
            d = None
        if d is not None and d.tzinfo is None:
            d = d.replace(tzinfo=JST)
        if d is not None and d < since:
            continue
        use.append(r)
        if d is not None:
            ends.append(d)
    if not use:
        return None
    n, won = len(use), sum(1 for r in use if r["won"])
    span = max(1, (max(ends) - min(ends)).days) if len(ends) >= 2 else 1
    return {"n": n, "won": won, "rate": round(won / n, 3), "per_month": round(n / span * 30, 1), "span_days": span,
            "med_price": int(statistics.median(r["price"] for r in use)),
            "med_max_bid": int(statistics.median(r["max_bid"] for r in use))}


# ---------------------------------------------------------------------------
# 4. 保存・読み込み
# ---------------------------------------------------------------------------


def read_scores(path: Path | None = None) -> dict[str, dict]:
    return {r["model_key"]: r for r in _read(path or SCORES) if r.get("model_key")}


def write_scores(scores: dict[str, dict], path: Path | None = None) -> None:
    path = path or SCORES
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(scores.values(), key=lambda r: (-(_num(r.get("rate")) or 0), r.get("model_key", ""))))


def load_scores(path: Path | None = None, cfg: dict | None = None, min_samples: int | None = None) -> dict[str, dict]:
    """入札リスト・拡張機能用: {機種キー: {rate, n, label, per_month}}（件数が min_samples 以上の機種だけ）."""
    st = settings(cfg or ba.load_config(None))
    need = st["min_samples"] if min_samples is None else min_samples
    out = {}
    for key, r in read_scores(path).items():
        n, rate = int(_num(r.get("n")) or 0), _num(r.get("rate"))
        if rate is None or n < need:
            continue
        out[key] = {"rate": rate, "n": n, "label": r.get("model", ""), "per_month": _num(r.get("per_month"))}
    return out


def _sales_and_kantei() -> tuple[list[dict], list[dict]]:
    paths = [p for p in (DATA / n for n in ("reference_prices.csv", "chatwork_sales.csv", "price_memo.csv", "research_prices.csv"))
             if p.exists()]
    sales = [row for p in paths for row in ba.read_sales(p)]
    kp = next(iter(sorted(DATA.glob("kantei_bookmarklet.*"))), None)
    return sales, (ba.read_kantei_table(kp) if kp else [])


def run(cfg: dict | None = None, fetch=None, max_models: int | None = None, now: datetime | None = None,
        fx: float | None = None) -> dict:
    cfg = cfg or ba.load_config(None)
    st = settings(cfg)
    now = now or datetime.now(JST)
    if fx is None:
        fx = (ba.fetch_fx() if cfg["fx"].get("auto_fetch") and fetch is None else None) or float(cfg["fx"]["usd_jpy"])
    sales, kantei = _sales_and_kantei()
    rep = sorted(REPORTS.glob("bid_report_*.csv"))
    report = _read(rep[-1]) if rep else []
    bids = _read(DATA / "bid_log.csv") + _read(DATA / "bid_history.csv")
    scores = read_scores()
    todo = targets(report, sales, bids, cfg, fx, scores, now,
                   st["max_models"] if max_models is None else max_models, st["refresh_days"])
    if not todo:
        print("勝ちやすさ: 調べる機種はありません（どれも直近に調べ済み）", file=sys.stderr)
        return {"models": 0, "pages": 0, "scored": 0}
    budget = {"pages": len(todo) * st["pages"]}
    groups: dict[str, list[ba.Item]] = {}
    for i, t in enumerate(todo):
        try:
            groups[t["key"]] = fetch_closed(t["query"], st["pages"], st["delay"], fetch, budget)
        except Exception as e:  # noqa: BLE001  読めなければその機種は次の朝
            print(f"  勝ちやすさ: 読めませんでした {t['label']}（{e}）", file=sys.stderr)
            continue
        if i + 1 < len(todo) and fetch is None:
            time.sleep(st["delay"])
    counted = evaluate(groups, sales, cfg, fx, kantei, ba.load_trusted())
    stamp = now.strftime("%Y-%m-%d %H:%M")
    scored = 0
    for t in todo:
        if t["key"] not in groups:
            continue
        s = score(counted.get(t["key"], []), now, st["lookback_days"])
        row = {"model_key": t["key"], "model": t["label"], "query": t["query"], "checked_at": stamp}
        row.update(s or {"n": 0, "won": 0, "rate": "", "per_month": 0, "span_days": "", "med_price": "", "med_max_bid": ""})
        scores[t["key"]] = row
        scored += bool(s)
    write_scores(scores)
    read_pages = len(todo) * st["pages"] - budget["pages"]
    low = [r for r in scores.values() if (_num(r.get("n")) or 0) >= st["min_samples"] and (_num(r.get("rate")) or 1) < st["low_rate"]]
    print(f"勝ちやすさ: {len(groups)}機種・{read_pages}ページを読み、{scored}機種を記録 → data/win_score.csv"
          f"（全{len(scores)}機種。{int(st['low_rate'] * 100)}%未満で入札リストの下に回す機種 {len(low)}）", file=sys.stderr)
    return {"models": len(groups), "pages": read_pages, "scored": scored}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="勝ちやすさ（終わった出品のうち、今の上限入札額以下で終わった割合）")
    ap.add_argument("--max-models", type=int, default=None, help="1回に調べる機種数（既定は config.toml [win_score] max_models）")
    args = ap.parse_args(argv)
    run(max_models=args.max_models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
