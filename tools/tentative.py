"""仮◎○: 相場が未登録の出品を、eBay 出品中の価格（公式 Browse API）から仮に判定する（毎朝の入札リスト用）.

拡張機能の「仮◎○」（extension_background.js の browse と extension_template.js の suggestUsd）と同じ規則:
  - 米国向け・中古の出品中だけ。キャップ・フード・ケース・部品取りなど本体以外は除く（検索語に含まれる語は除かない）
  - 検索語の焦点距離（28mm）と F値（F2.8）がタイトルに入っている出品だけ
  - 3件以上なら、中央値の3割未満（部品など）は外す
  - 各出品の商品代（送料別）= 送料無料なら出品額 − $30（config の active_free_ship_deduction_usd）、送料別なら出品額そのまま
    （送料が分からない出品は保守的に送料無料とみなす）。ほかの相場データと同じく、送料 deduction_usd を足した送料込みの額で持つ
  - 仮の相場 = その最安値（2026-11-16 ユーザー決定）。件数が active_min_count 未満なら出さない
eBay のページの自動取得（スクレイピング）はしない。結果は data/browse_cache.json に1日保存（同じ機種は1日1回）。
売れた価格（sold）は確かめていないので「仮」。sold を見て、拡張機能で［登録］すると ◎○ に上がる。
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

import appraise as ba

CACHE = ba.DATA / "browse_cache.json"
CACHE_SEC = 24 * 3600
EXCLUDE_WORDS = ["cap", "caps", "hood", "case", "filter", "adapter", "adaptor", "parts", "repair", "junk",
                 "manual", "strap", "box only", "teleconverter", "extender", "mutar", "for parts"]


def filter_totals(query: str, summaries: list[dict], cfg: dict | None = None) -> dict:
    """Browse API の itemSummaries から、仮の相場の材料（件数・最安・中央値）を作る."""
    cfg = cfg or ba.load_config(None)
    free_ded = float(cfg["judge"].get("active_free_ship_deduction_usd", 30.0))
    ship_ded = float(cfg["shipping"].get("deduction_usd", 0))
    ql = query.lower()
    words = [w for w in EXCLUDE_WORDS if w not in ql]
    bad = re.compile(r"\b(" + "|".join(w.replace(" ", r"\s+") for w in words) + r")\b", re.I) if words else None
    specs = []
    for m in re.finditer(r"(\d+(?:-\d+)?)\s*mm", query, re.I):
        n = m.group(1).replace("-", r"\s*-\s*")
        specs.append(re.compile(r"(^|[^\d.])" + n + r"\s*mm|/\s*" + n + r"(?![\d.])", re.I))
    for m in re.finditer(r"\bf/?\s*(\d+(?:\.\d+)?)", query, re.I):
        specs.append(re.compile(r"(^|[^\d.])" + m.group(1).replace(".", r"\.") + r"(?!\d|\.\d)", re.I))  # F2 で F2.8 を数えない
    totals = []
    for it in summaries:
        price = it.get("price") or {}
        title = it.get("title") or ""
        if price.get("currency") != "USD" or (bad and bad.search(title)) or not all(r.search(title) for r in specs):
            continue
        ships = [float(s["shippingCost"]["value"]) for s in it.get("shippingOptions") or []
                 if (s.get("shippingCost") or {}).get("value") not in (None, "")]
        value = float(price.get("value") or 0)
        net = value if ships and min(ships) > 0 else value - free_ded  # 送料無料（または不明）は −$30
        totals.append(round(net + ship_ded, 2))
    if len(totals) >= 3:
        m = statistics.median(totals)
        totals = [v for v in totals if v >= m * 0.3]
    return {"n": len(totals), "min": min(totals) if totals else None,
            "med": round(statistics.median(totals), 2) if totals else None}


def suggest_usd(stats: dict, cfg: dict) -> float | None:
    j = cfg["judge"]
    if not stats or not stats.get("med") or stats["n"] < j.get("active_min_count", 5):
        return None
    # min は送料 $40 込み。送料を引いた商品代（画面に出す額）が 0 以下（送料無料 −$30 でマイナスになる $30 以下の出品）や
    # suggest_min_net_usd 以下なら案にしない（2026-11-19 監査: 以前は送料込みの額で 0 と比べていて「案 $-1」「案 $-20」が出た。拡張機能の suggestUsd と同じ）
    sg = int(stats.get("min") or 0)
    net = sg - float(cfg["shipping"].get("deduction_usd", 0))
    return sg if net > 0 and net >= float(j.get("suggest_min_net_usd", 0)) else None  # 送料別 $20 未満は案にしない（2026-11-19 ユーザー決定）


def _browse(query: str) -> dict:
    import ebay_api  # Mac のキーチェーンの App ID・Cert ID を使う
    q = urllib.parse.urlencode({"q": query, "limit": 100,
                                "filter": "buyingOptions:{FIXED_PRICE|AUCTION},conditions:{USED}"})
    d = ebay_api.get_json(f"{ebay_api.API}/buy/browse/v1/item_summary/search?{q}", ebay_api.app_token(),
                          {"X-EBAY-C-MARKETPLACE-ID": "EBAY_US", "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS"})
    return filter_totals(query, d.get("itemSummaries") or [], ba.load_config(None))


def model_label(title: str) -> str | None:
    """仮◎○を調べる機種名（ブランド＋型番）。eBay 検索が当てにならない名前は None（拡張機能と同じ考え方）."""
    label, _ = ba.model_key(title)
    if len(label.split()) < 2 or not re.search(r"\d", label):
        return None
    if sum(1 for t in label.split() if t.isdigit()) >= 2:
        return None
    return label


def add_tentative(items: list, cfg: dict, fx: float, max_models: int = 150, browse=None, delay: float = 0.3) -> int:
    """相場が無い出品に仮の判定（item.tentative）を付ける. 仮◎○の件数を返す."""
    browse = browse or _browse
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    now = time.time()
    groups: dict[str, list] = {}
    for it in items:
        if it.calc or {"付属品のみ?", "アダプター等", "セット品", "光学難あり"} & set(it.flags) or it.desc_hits:
            continue
        label = model_label(it.title)
        if label:
            groups.setdefault(label, []).append(it)
    labels = sorted(groups, key=lambda k: -len(groups[k]))[:max_models]
    found = 0
    for label in labels:
        c = cache.get(label)
        if not c or now - c.get("t", 0) > CACHE_SEC or c.get("v") != 2:  # v2: 送料無料 −$30・最安値（2026-11-16）
            try:
                c = dict(browse(label), t=now, v=2)
            except (Exception, SystemExit) as e:  # キーが無い・通信できない → 仮◎○なしで続ける
                print(f"仮◎○: eBay 出品中の価格を調べられませんでした（{str(e)[:80]}）", file=sys.stderr)
                break
            cache[label] = c
            time.sleep(delay)
        sg = suggest_usd(c, cfg)
        if not sg:
            continue
        for it in groups[label]:
            usd = round(sg * cfg["judge"].get("junk_price_factor", 1.0)) if "ジャンク" in it.flags else sg
            tmp = ba.Item(url=it.url, title=it.title, expected_price_usd=usd, current_price_jpy=it.current_price_jpy,
                          domestic_shipping_jpy=it.domestic_shipping_jpy, store_tax=it.store_tax, trusted=it.trusted,
                          flags=it.flags)
            tmp.calc = ba.calculate(tmp, cfg, fx)
            mark, _ = ba.judge(tmp)
            if mark in ("◎", "○"):
                it.tentative = {"mark": mark, "label": label, "usd": usd, "sg": sg, "n": c["n"], "min": c["min"],
                                "med": c["med"], "max_bid": tmp.calc["max_bid_jpy"], "profit": tmp.calc.get("profit_now_jpy"),
                                "links": ba.ebay_links(label)}
                found += 1
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return found
