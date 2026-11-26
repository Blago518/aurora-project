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
SKIP_FIELDS = SEEN_FIELDS + ["seller", "checked_at"]  # 2026-11-26 から: 出品者と「確認済み」にした時刻
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
    if bids > 0 and price is None and state.get("hidden"):
        # 最終価格をページが見せない（ログインが要る。bid_results._item_state）。落札額の中央値・答え合わせには使わない
        row.update(bids=bids, final_price="", status="落札額不明", checked_at=now.strftime("%Y-%m-%d %H:%M"))
        return True
    if bids > 0 and price is None:
        return False
    row.update(bids=bids, final_price=int(price) if bids > 0 else "", status="落札された" if bids > 0 else "流れた",
               checked_at=now.strftime("%Y-%m-%d %H:%M"))
    return True


def closed_state(row: dict, fetch, cache: dict, delay: float = 0.0) -> dict:
    """終わった出品の検索（/closedsearch/closedsearch?p=。robots.txt の Allow の形）で、そのオークションIDの最終価格・入札数を探す.
    2026-11-26: 終わった出品のページ（/jp/auction/<ID>）がログインしていないと 404 を返すようになり、毎朝の記録が全部「読めませんでした」になった。
    同じ検索語は1回の更新で1回だけ読む（cache）。見つからなければ空."""
    import urllib.parse
    import robots
    q = ba.title_to_query(row.get("title") or "")
    if not q:
        return {}
    if q not in cache:
        url = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p=" + urllib.parse.quote(q)
        cache[q] = {}
        if robots.allowed(url):
            try:
                items, _ = ba.parse_listing_page(fetch(url))
                cache[q] = {it.auction_id: it for it in items}
            except Exception:  # noqa: BLE001  読めなければ次の朝
                pass
            if delay:
                time.sleep(delay)
    it = cache[q].get(row.get("auction_id"))
    if not it:
        return {}
    return {"status": "closed", "price": it.current_price_jpy, "bids": it.bids or 0, "end": it.end_time, "via": "closedsearch"}


def check_watch(rows: list[dict], max_checks: int = 300, delay: float = 1.5, fetch=None,
                now: datetime | None = None) -> dict:
    """終了時刻を過ぎた見張りの出品ページを読み、最終価格・入札数を記録する（古い順に max_checks 件まで）."""
    import bid_results as br
    fetch = fetch or ba.http_get
    now = now or datetime.now(timezone.utc)
    for r in rows:  # 2026-11-25 より前に、隠された最終価格（開始価格の1円・1,000円）を落札額として記録した物
        if r.get("status") == "落札された" and int(_num(r.get("bids")) or 0) >= 2 and (_num(r.get("final_price")) or 0) <= 1000:
            r.update(status="落札額不明", final_price="")
    todo = [r for r in rows if not r.get("checked_at") and (_when(r.get("end")) or now) <= now]
    todo.sort(key=lambda r: str(r.get("end") or ""))
    out = {"due": len(todo), "read": 0, "sold": 0, "unsold": 0, "gave_up": 0, "via_closed": 0, "missing": 0}
    cache: dict = {}
    for r in todo[:max(0, max_checks)]:
        try:
            state = br._item_state(fetch(f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"))
        except Exception:  # noqa: BLE001  404 など。終わった出品の検索で探す（1件ずつの「読めませんでした」は出さない）
            state = {}
        if not state:
            state = closed_state(r, fetch, cache, delay)
            if state:
                out["via_closed"] += 1
            else:
                out["missing"] += 1
        out["read"] += 1
        if state and record(r, state, now):
            out["unsold" if r["status"] == "流れた" else "sold"] += 1
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
        if not key or not r.get("checked_at") or end is None or end < since or r.get("status") in ("読めず", "落札額不明"):
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


SKIP_SHEET = "Mac_見送り"  # 確認済みにして入札しなかった候補（2026-11-26 オーナー「確認済にチェックをいれて入札しなかったものには、入札しなかった意図があります」）


def skipped_csv(seen: list[dict], bids: list[dict], results: list[dict] | None = None) -> str:
    """aurora_seen.csv の行のうち「確認済み」にしたのに入札の記録が無い物 → CSV（新しい順）. 落札額が分かれば添える.
    サトシが週1回この表を読み、見送りの型（ストア×語・光学難の書き方）をまとめて、外してよいかをオーナーに選択式で聞く。判定は変えない."""
    import csv, io
    bid = {_aid(b.get("auction_id", "")) for b in bids}
    fin = {r.get("auction_id"): r for r in (results or [])}
    rows = [r for r in seen if r.get("checked_at") and _aid(r.get("auction_id", "")) not in bid]
    rows.sort(key=lambda r: r.get("checked_at", ""), reverse=True)
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["確認済みにした日時", "判定", "上限額(提示額)", "出品者", "商品名", "終了", "落札額", "ヤフオク"])
    for r in rows:
        a = _aid(r.get("auction_id", "")); f = fin.get(a) or {}
        w.writerow([r.get("checked_at", "")[:16].replace("T", " "), r.get("mark", ""), r.get("max_bid", ""), r.get("seller", ""),
                    r.get("title", ""), (r.get("end") or "")[:16].replace("T", " "), f.get("final_price", ""), f"https://auctions.yahoo.co.jp/jp/auction/{a}"])
    return buf.getvalue()


def upload_skipped(results: list[dict], downloads: Path | None = None, log: Path | None = None, post=None,
                   phone_seen: Path | None = None) -> int | None:
    """見送り → 古物台帳「Mac_見送り」（まるごと置き換え）. 送った行数. 確認済みの記録が無ければ None."""
    import ledger_sync
    downloads = downloads or Path.home() / "Downloads"
    files = sorted(downloads.glob("aurora_seen*.csv"), key=lambda p: p.stat().st_mtime) if downloads.exists() else []
    seen = [{k: r.get(k, "") for k in SKIP_FIELDS} for r in _read(files[-1])] if files else []
    # スマホの入札タブの［確認済み］（2026-11-26 オーナー。ledger_sync.sync_phone_seen → data/phone_seen.csv）も足す。同じ出品はパソコンの記録が先
    have = {_aid(r.get("auction_id", "")) for r in seen}
    seen += [{k: r.get(k, "") for k in SKIP_FIELDS} for r in _read(phone_seen or DATA / "phone_seen.csv")
             if _aid(r.get("auction_id", "")) not in have]
    if not any(r.get("checked_at") for r in seen):
        return None
    # 入札したかは、オークファンの予約（予約中 aurora_snipes.csv・入札完了 aurora_snipes_closed.csv）も見る。
    # ［入札した］のボタンは押さないこともあり、額も実際の額とは限らない（2026-11-26 オーナー「正確な入札金額は、オークファンの履歴で確認して」）
    bids = _read(log or DATA / "bid_log.csv")
    for f in downloads.glob("aurora_snipes*.csv"):
        bids += [{"auction_id": r.get("auction_id", "")} for r in _read(f)]
    body = skipped_csv(seen, bids, results)
    if not ledger_sync.post_sheet(SKIP_SHEET, body, post=post):
        return None
    return body.count("\n") - 1


REVIEW_SHEET = "Mac_答え合わせ"  # 上限入札額以下で終わった候補と、入札の記録・予約リストに出ていたか（2026-11-24 オーナーが選択式で決定）
REVIEW_MARKS = ("◎", "○", "入札した")
RESERVE_FILE_RE = re.compile(r"reserve_list_(\d{8})_\d{4}\.csv$")


def reserve_history(reports: Path | None = None) -> tuple[dict[str, str], set[str]]:
    """毎朝の予約リスト（reports/reserve_list_日付_時刻.csv）に出た出品 {オークションID: 区分} と、リストが残っている日付（YYYYMMDD）."""
    reports = reports or REPORTS
    shown, days = {}, set()
    for f in sorted(reports.glob("reserve_list_*.csv")) if reports.exists() else []:
        m = RESERVE_FILE_RE.search(f.name)
        if not m:
            continue
        days.add(m.group(1))
        for r in _read(f):
            aid = _aid(r.get("ヤフオク") or "")
            if aid:
                k = str(r.get("区分") or "")
                shown[aid.lower()] = "即決" if k.startswith("即決") else "カビの枠" if k.startswith("カビ") else "予約"
    return shown, days


def review_csv(rows: list[dict], bids: list[dict] | None = None, shown: dict | None = None, list_days: set | None = None,
               days: int = 14, now: datetime | None = None) -> str:
    """答え合わせ: 候補（◎○・入札した）のうち、上限入札額（提示額）以下で落札された出品（直近 days 日）.

    「上限まで入れていれば取れた」とは言えない（落札者の上限は見えない）。取れた可能性があった出品の一覧.
    商品名から付属品・別物と分かる物、ジャンクなどの語がある物は、外さずに印を付ける（説明文は読んでいない）."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    bid = {str(b.get("auction_id") or "").lower(): b for b in (bids or [])}
    shown, list_days = shown or {}, list_days or set()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["終了日", "機種", "落札額(円・ストアは税抜)", "上限額(提示額)", "上限との差", "入札数", "入札の記録", "入れた額",
                "オークファンの失敗コード", "予約リスト", "商品名で分かる注意", "商品名", "ヤフオク"])
    done = []
    for r in rows:
        end, price, mx = _when(r.get("end")), _num(r.get("final_price")), _num(r.get("max_bid"))
        if (not r.get("checked_at") or r.get("status") != "落札された" or end is None or end < since
                or r.get("mark") not in REVIEW_MARKS or not price or not mx or price > mx):
            continue
        done.append((end, price, mx, r))
    for end, price, mx, r in sorted(done, key=lambda x: x[0], reverse=True):
        aid = str(r["auction_id"]).lower()
        b = bid.get(aid)
        day = end.astimezone(JST)
        if aid in shown:
            listed = shown[aid]
        else:  # その出品が終わる当日か前日の予約リストが残っていれば「出ていない」、残っていなければ「記録なし」
            near = {day.strftime("%Y%m%d"), (day - timedelta(days=1)).strftime("%Y%m%d")}
            listed = "出ていない" if near & list_days else "記録なし"
        flags = [f for f in ba.detect_flags(str(r.get("title") or "")) if f in ("付属品のみ?", "アダプター等", "ジャンク", "光学難あり", "セット品")]
        w.writerow([day.strftime("%Y-%m-%d"), r.get("model", ""), int(price), int(mx), int(mx - price), r.get("bids", ""),
                    "あり" if b or r.get("mark") == "入札した" else "なし", (b or {}).get("my_bid", ""), (b or {}).get("bid_fail", ""),
                    listed, "・".join(flags), str(r.get("title", ""))[:100],
                    f"https://auctions.yahoo.co.jp/jp/auction/{r['auction_id']}"])
    return buf.getvalue()


def upload_review(rows: list[dict], post=None, reports: Path | None = None, log: Path | None = None) -> int | None:
    """答え合わせ → 古物台帳「Mac_答え合わせ」（まるごと置き換え）. 送った行数. 未設定・失敗は None."""
    import ledger_sync
    shown, list_days = reserve_history(reports)
    body = review_csv(rows, _read(log or DATA / "bid_log.csv"), shown, list_days)
    if not ledger_sync.post_sheet(REVIEW_SHEET, body, post=post):
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
    extra = ""
    if res.get("via_closed"):
        extra += "・終わった出品の検索で見つけた%d件" % res["via_closed"]
    if res.get("missing"):
        extra += "・見つからず%d件（次の朝にまた探す）" % res["missing"]
    if left > 0:
        extra += "・残り%d件は次の朝" % left
    print(f"落札相場: 見張り +{added}件・記録 落札{res['sold']}件／流れた{res['unsold']}件{extra}"
          f" ／ 直近{st['days']}日 {len(stats)}機種 → data/yahoo_results.csv", file=sys.stderr)
    sent = upload(rows, st["days"], post=post)
    if sent is not None:
        print(f"落札相場: {sent}行 → 古物台帳「{SHEET}」", file=sys.stderr)
    try:
        n = upload_review(rows, post=post)
        if n is not None:
            print(f"答え合わせ: 上限額以下で落札された候補 {n}件 → 古物台帳「{REVIEW_SHEET}」", file=sys.stderr)
    except Exception as e:  # noqa: BLE001  答え合わせが作れなくても落札相場はそのまま
        print(f"答え合わせを作れませんでした（{e}）", file=sys.stderr)
    try:
        n = upload_skipped(rows, post=post)
        if n is not None:
            print(f"見送り: 確認済みにして入札しなかった候補 {n}件 → 古物台帳「{SKIP_SHEET}」", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"見送りを作れませんでした（{e}）", file=sys.stderr)
    return {"added": added, **res, "models": len(stats)}


if __name__ == "__main__":
    run()
