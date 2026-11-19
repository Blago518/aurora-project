#!/usr/bin/env python3
"""ヤフオクの落札相場を記録する（候補に出た出品が、実際にいくらで落札されたか）.

「自分がいくらで入札したかより、いくらで落札されたかのデータが重要」（2026-11-19 ユーザー）。
入札したかどうかに関係なく、候補に出た出品の終了後の最終価格を data/yahoo_results.csv に残す。
判定（◎○×）・上限入札額・利益の計算には使わない（記録と表示だけ）。

1. 見張りリストに入れる（終了前）
     毎朝の入札リスト（reports/bid_report_*.csv の最新）で ◎○・仮◎○・確認してから に出た出品
     拡張機能で ◎○・仮◎○・確認してから に出た出品（ダウンロード/aurora_seen.csv）
     拡張機能の［入札した］の記録（data/bid_log.csv）
2. 終了時刻を過ぎた出品の公開の出品ページ（ヤフオクだけ。eBay は読まない）を、間隔を空けて読み、
   最終価格・入札数を記録する。入札0件で終わった（流れた）出品も final_price 空・bids 0 で残す。
   1回に読む件数は config.toml の [yahoo_results] max_checks（既定 300）まで。残りは次の朝
3. 機種（appraise.model_key）ごとの直近の落札額 → 拡張機能（build_extension.py）・古物台帳「Mac_落札相場」

final_price はストア出品なら税抜（入札額と同じ単位）。max_bid は拡張機能・入札リストが出した上限額（提示額）で、
実際に入札した額ではない（参考）。

使い方（aurora フォルダで。update_all.py が毎朝実行する）:
    python3 tools/yahoo_results.py
"""
from __future__ import annotations

import csv
import io
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
RESULTS = DATA / "yahoo_results.csv"
REPORTS = ROOT / "reports"
SHEET = "Mac_落札相場"  # 古物台帳に送るシート（クラウドのヒトカゲ・ゼニガメが読む。出品者・購入者は送らない）
FIELDS = ["auction_id", "title", "model", "model_key", "end", "final_price", "bids", "store_tax", "status",
          "seen_from", "mark", "max_bid", "seen_at", "checked_at"]
SEEN_FIELDS = ["auction_id", "title", "end", "mark", "max_bid", "store_tax", "seen_at"]  # 拡張機能の aurora_seen.csv
KEEP_DAYS = 400      # これより前に終わった記録は消す（ファイルを大きくしすぎない）
GIVE_UP_DAYS = 14    # 終了からこの日数たっても読めない出品（削除・取り消し）は「読めず」にして読みに行かない
JST = timezone(timedelta(hours=9))

sys.path.insert(0, str(HERE))
import appraise as ba  # noqa: E402


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def _write(rows: list[dict], path: Path | None = None) -> None:
    path = path or RESULTS
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _when(s) -> datetime | None:
    """ISO の日時（タイムゾーンなしは日本時間とみなす）."""
    try:
        d = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def settings(cfg: dict) -> dict:
    s = cfg.get("yahoo_results", {}) or {}
    return {"max_checks": int(s.get("max_checks", 300)), "delay": max(1.5, float(s.get("delay_sec", 1.5))),
            "days": int(s.get("market_days", 90)), "seen_max_per_day": int(s.get("seen_max_per_day", 300))}


AID_RE = re.compile(r"/auction/([A-Za-z0-9]+)")


def _aid(url_or_id: str) -> str:
    s = str(url_or_id or "").strip()
    m = AID_RE.search(s)
    return m.group(1) if m else (s if re.fullmatch(r"[A-Za-z0-9]+", s) else "")


# ---------------------------------------------------------------------------
# 1. 見張りリスト
# ---------------------------------------------------------------------------


def report_rows(path: Path) -> list[dict]:
    """入札リスト（bid_report_*.csv）の ◎○・確認してから・仮◎○ → 見張りの行."""
    out = []
    for r in _read(path):
        aid = _aid(r.get("ヤフオクURL", ""))
        if not aid:
            continue
        mark, tent = r.get("判定", ""), r.get("仮判定", "")
        if mark in ("◎", "○"):
            mark = ("確認してから" + mark) if r.get("根拠の強さ") == "確認してから" else mark
            max_bid = r.get("上限入札額", "")
        elif tent in ("◎", "○"):
            mark, max_bid = "仮" + tent, r.get("仮の上限入札額", "")
        else:
            continue
        out.append({"auction_id": aid, "title": r.get("タイトル", ""), "end": r.get("終了", ""), "mark": mark,
                    "max_bid": max_bid, "store_tax": "1" if str(r.get("入札額の税", "")).startswith("ストア") else "0",
                    "seen_from": "morning"})
    return out


def latest_report(reports: Path | None = None) -> Path | None:
    files = sorted((reports or REPORTS).glob("bid_report_*.csv"))
    return files[-1] if files else None


def seen_rows(downloads: Path | None = None) -> list[dict]:
    """拡張機能が保存した ダウンロード/aurora_seen.csv（一番新しいもの）→ 見張りの行."""
    downloads = downloads or Path.home() / "Downloads"
    files = sorted(downloads.glob("aurora_seen*.csv"), key=lambda p: p.stat().st_mtime) if downloads.exists() else []
    if not files:
        return []
    out = []
    for r in _read(files[-1]):
        aid = _aid(r.get("auction_id", ""))
        if aid:
            out.append({k: r.get(k, "") for k in SEEN_FIELDS} | {"auction_id": aid, "seen_from": "extension"})
    return out


def bid_rows(log: Path | None = None) -> list[dict]:
    """拡張機能の［入札した］の記録（data/bid_log.csv）も見張る（入札したものは必ず落札額を残す）."""
    return [{"auction_id": r["auction_id"], "title": r.get("title", ""), "end": r.get("end", ""),
             "mark": "入札した", "max_bid": r.get("max_bid", ""), "store_tax": r.get("store_tax", ""), "seen_from": "bid"}
            for r in _read(log or DATA / "bid_log.csv") if r.get("auction_id")]


def add_watch(new: list[dict], rows: list[dict] | None = None, now: datetime | None = None) -> tuple[list[dict], int]:
    """見張りリストに足す（同じ出品は1行。記録が済んだ行の値は変えない）. (行, 足した件数)."""
    now = now or datetime.now(timezone.utc)
    rows = rows if rows is not None else []
    by = {r["auction_id"]: r for r in rows}
    added = 0
    for n in new:
        aid = n["auction_id"]
        r = by.get(aid)
        if r is None:
            r = {k: "" for k in FIELDS}
            r.update({k: v for k, v in n.items() if k in FIELDS}, seen_at=n.get("seen_at") or now.strftime("%Y-%m-%d %H:%M"))
            label, key = ba.model_key(r["title"]) if r["title"] else ("", "")
            r.update(model=label, model_key=key)
            rows.append(r)
            by[aid] = r
            added += 1
            continue
        froms = [f for f in str(r.get("seen_from") or "").split("+") if f]
        if n.get("seen_from") and n["seen_from"] not in froms:
            r["seen_from"] = "+".join(froms + [n["seen_from"]])
        if r.get("checked_at"):
            continue
        for k in ("title", "end", "mark", "max_bid", "store_tax"):
            if not r.get(k) and n.get(k):
                r[k] = n[k]
        if n.get("end") and n["end"] != r.get("end") and n.get("seen_from") != "bid":
            r["end"] = n["end"]  # 自動延長などで終了時刻が変わった
        if r["title"] and not r.get("model_key"):
            r["model"], r["model_key"] = ba.model_key(r["title"])
    return rows, added


# ---------------------------------------------------------------------------
# 2. 終わった出品の最終価格
# ---------------------------------------------------------------------------


def record(row: dict, state: dict, now: datetime) -> bool:
    """出品ページの状態で、終わっていれば記録する. 記録したら True（まだ終わっていなければ終了時刻だけ直して False）."""
    end = _when(state.get("end")) or _when(row.get("end"))
    if state.get("end"):
        row["end"] = str(state["end"])
    if str(state.get("status") or "open") == "open" and (end is None or end > now):
        return False
    bids = int(_num(state.get("bids")) or 0)
    price = _num(state.get("price"))
    if bids > 0 and price is None:
        return False
    row.update(bids=bids, final_price=int(price) if bids > 0 else "", status="落札された" if bids > 0 else "流れた",
               checked_at=now.strftime("%Y-%m-%d %H:%M"))
    return True


def check_watch(rows: list[dict], max_checks: int = 300, delay: float = 1.5, fetch=None,
                now: datetime | None = None) -> dict:
    """終了時刻を過ぎた見張りの出品ページを読み、最終価格・入札数を記録する（古い順に max_checks 件まで）."""
    import bid_results as br
    fetch = fetch or ba.http_get
    now = now or datetime.now(timezone.utc)
    todo = [r for r in rows if not r.get("checked_at") and (_when(r.get("end")) or now) <= now]
    todo.sort(key=lambda r: str(r.get("end") or ""))
    out = {"due": len(todo), "read": 0, "sold": 0, "unsold": 0, "gave_up": 0}
    for r in todo[:max(0, max_checks)]:
        try:
            state = br._item_state(fetch(f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"))
        except Exception as e:  # noqa: BLE001  読めなければ次の朝
            print(f"  落札相場: 読めませんでした {r['auction_id']}（{e}）", file=sys.stderr)
            state = {}
        out["read"] += 1
        if state and record(r, state, now):
            out["sold" if r["status"] == "落札された" else "unsold"] += 1
        elif not state and (_when(r.get("end")) or now) < now - timedelta(days=GIVE_UP_DAYS):
            r.update(status="読めず", checked_at=now.strftime("%Y-%m-%d %H:%M"))  # 削除・取り消しなど
            out["gave_up"] += 1
        if delay:
            time.sleep(delay)
    return out


def prune(rows: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    limit = now - timedelta(days=KEEP_DAYS)
    return [r for r in rows if (_when(r.get("end")) or now) >= limit]


# ---------------------------------------------------------------------------
# 3. 機種ごとの落札額（拡張機能・古物台帳へ）
# ---------------------------------------------------------------------------


def model_stats(rows: list[dict], days: int = 90, now: datetime | None = None) -> dict:
    """{比較用キー: {"label", "n", "med", "min", "max", "unsold"}}（直近 days 日に終わった出品。流れた出品は件数だけ）."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    groups: dict[str, dict] = {}
    for r in rows:
        key, end = r.get("model_key") or "", _when(r.get("end"))
        if not key or not r.get("checked_at") or end is None or end < since or r.get("status") == "読めず":
            continue
        g = groups.setdefault(key, {"label": r.get("model") or "", "prices": [], "unsold": 0})
        p = _num(r.get("final_price"))
        if r.get("status") == "流れた" or not p:
            g["unsold"] += 1
        else:
            g["prices"].append(p)
    out = {}
    for key, g in groups.items():
        if not g["prices"]:
            continue
        ps = g["prices"]
        out[key] = {"label": g["label"], "n": len(ps), "med": int(statistics.median(ps)), "min": int(min(ps)),
                    "max": int(max(ps)), "unsold": g["unsold"]}
    return out


def load_model_stats(path: Path | None = None, days: int = 90) -> dict:
    return model_stats(_read(path or RESULTS), days)


def sheet_csv(rows: list[dict], days: int = 90, now: datetime | None = None) -> str:
    """古物台帳「Mac_落札相場」に送る CSV（直近 days 日。商品名・機種・終了日・落札額・入札数だけ。出品者・購入者は入れない）."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["終了日", "機種", "落札額(円・ストアは税抜)", "入札数", "ストア", "候補の判定", "上限額(提示額・参考)", "商品名", "ヤフオク"])
    done = [r for r in rows if r.get("checked_at") and r.get("status") in ("落札された", "流れた")
            and (_when(r.get("end")) or since) >= since]
    for r in sorted(done, key=lambda r: str(r.get("end")), reverse=True):
        end = _when(r.get("end"))
        w.writerow([end.astimezone(JST).strftime("%Y-%m-%d") if end else "", r.get("model", ""), r.get("final_price", ""),
                    r.get("bids", ""), "1" if str(r.get("store_tax")) == "1" else "0", r.get("mark", ""),
                    r.get("max_bid", ""), str(r.get("title", ""))[:100],
                    f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"])
    return buf.getvalue()


def upload(rows: list[dict], days: int = 90, post=None) -> int | None:
    """直近の落札相場 → 古物台帳「Mac_落札相場」（まるごと置き換え）. 送った行数. 未設定・失敗は None."""
    import ledger_sync
    body = sheet_csv(rows, days)
    if not ledger_sync.post_sheet(SHEET, body, post=post):
        return None
    return body.count("\n") - 1


def run(cfg: dict | None = None, downloads: Path | None = None, fetch=None, post=None) -> dict:
    cfg = cfg or ba.load_config(None)
    st = settings(cfg)
    rows = _read(RESULTS)
    rep = latest_report()
    new = report_rows(rep) if rep else []
    new += seen_rows(downloads) + bid_rows()  # 判定の印（◎○ など）は入札リスト・拡張機能のものを先に使う
    rows, added = add_watch(new, rows)
    res = check_watch(rows, st["max_checks"], st["delay"], fetch)
    rows = prune(rows)
    _write(rows)
    stats = model_stats(rows, st["days"])
    left = res["due"] - res["read"]
    print(f"落札相場: 見張り +{added}件・記録 落札{res['sold']}件／流れた{res['unsold']}件"
          f"{f'・残り{left}件は次の朝' if left > 0 else ''} ／ 直近{st['days']}日 {len(stats)}機種 → data/yahoo_results.csv",
          file=sys.stderr)
    sent = upload(rows, st["days"], post=post)
    if sent is not None:
        print(f"落札相場: {sent}行 → 古物台帳「{SHEET}」", file=sys.stderr)
    return {"added": added, **res, "models": len(stats)}


if __name__ == "__main__":
    run()
