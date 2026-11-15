#!/usr/bin/env python3
"""店舗仕入れアプリ（store/）のデータを作る.

- store/core.js      … 拡張機能（extension_template.js）の照合・利益計算をそのままコピー（公開してよい。データは入らない）
- data/store_data.json … 相場データ＋おすすめ機種（非公開。iPhone に1回読み込ませる）

iCloud Drive がある Mac では、store_data.json を iCloud Drive の「aurora」フォルダにもコピーする
（iPhone の「ファイル」アプリ → iCloud Drive → aurora から選べる）。

使い方:
    python3 tools/build_store.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import sys
from datetime import datetime
from pathlib import Path

import appraise as ba
import build_extension as bx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORE_JS = ROOT / "store" / "core.js"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "aurora"
CORE_START, CORE_END = "  // @core-start", "  // @core-end"
REC_SOURCES = ("reference_prices.csv", "chatwork_sales.csv")  # おすすめは実際の販売（相場メモは除く）


# ---------------------------------------------------------------------------
# core.js（拡張機能と同じ判定ロジック）
# ---------------------------------------------------------------------------


def core_js() -> str:
    tpl = (HERE / "extension_template.js").read_text(encoding="utf-8")
    a, b = tpl.index(CORE_START), tpl.index(CORE_END)
    body = tpl[tpl.index("\n", a) + 1:b]
    names = ("titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, "
             "modelKey, ebay, median, roundDown, matchPreferMemo, isMemo, conditionOf, conditionAdjusted, conditionNote")
    return (
        "// 自動生成ファイル: tools/build_store.py が tools/extension_template.js の @core 部分をコピーしたもの。\n"
        "// 直接編集しない（拡張機能と判定を一致させるため）。データは入っていない。\n"
        "function makeCore(DATA) {\n"
        '  "use strict";\n'
        "  const C = DATA.consts;\n"
        f"{body}"
        f"  return {{ {names} }};\n"
        "}\n"
        'if (typeof module !== "undefined" && module.exports) module.exports = { makeCore };\n'
    )


# ---------------------------------------------------------------------------
# おすすめ機種（販売実績を機種ごとに集計）
# ---------------------------------------------------------------------------

BRAND_NAMES = {b.lower(): b for b in ba.BRANDS_EN.values()}


CAMERA_WORDS = {"slr", "camera", "body", "rangefinder", "tlr", "film", "compact", "point"}
# レンズの機種を分ける語（マウント・世代）。例: Canon FD 50mm F1.4 と Canon EF 50mm F1.4 は別機種
LENS_LINE_WORDS = {"fd", "newfd", "new", "ef", "ef-s", "rf", "ai", "ai-s", "nonai", "af", "af-s", "af-d", "smc",
                   "takumar", "m42", "md", "mc", "xf", "fe", "l", "macro", "planar", "sonnar", "summicron",
                   "summilux", "elmarit", "noctilux"}
CAMERA_LINE_WORDS = ba.GENERATIONS | {"program", "pro", "super", "classic", "dx", "quartz", "date"}


def rec_key(title: str) -> tuple[str, str, str] | None:
    """おすすめ用の機種まとめ（表示名, 比較キー, camera/lens）. ブランド不明なら None."""
    toks = ba.title_to_query(title, 20).split()
    if not toks or toks[0].lower() not in BRAND_NAMES:
        return None
    brand, rest = BRAND_NAMES[toks[0].lower()], toks[1:]
    def norm(t: str) -> list[str]:
        t = t.lower()
        return ["newfd"] if t in ("nfd", "fdn") else ["ai-s"] if t == "ais" else [t]
    low = [x for t in rest for x in norm(t)]
    has_mm = any(re.fullmatch(r"\d+(-\d+)?mm", t) for t in low)
    has_f = any(re.fullmatch(r"f\d+(\.\d+)?(-\d+(\.\d+)?)?", t) for t in low)
    kind = "lens" if has_mm and (has_f or "lens" in low) and not CAMERA_WORDS & set(low) else "camera"

    def wanted(t: str) -> bool:
        if kind == "lens":
            return bool(t in LENS_LINE_WORDS or re.fullmatch(r"\d+(-\d+)?mm", t) or re.fullmatch(r"f\d.*", t))
        return bool((re.search(r"\d", t) and t != "35mm") or t in CAMERA_LINE_WORDS)

    keep, shown = [], []
    for orig in rest:
        ks = [t for t in norm(orig) if wanted(t) and t not in keep]
        if ks:
            keep += ks
            shown.append(orig)
    if not any(re.search(r"\d", t) for t in keep):
        return None
    label = " ".join([brand] + shown)
    return label, " ".join([brand.lower()] + keep), kind


def store_calc(usd: float, cfg: dict, fx: float, price_jpy: int | None = None) -> dict:
    """店頭仕入れ: 国内送料なし・消費税は値札（税込）にそのまま含まれる."""
    item = ba.Item(url="", expected_price_usd=usd, domestic_shipping_jpy=0, current_price_jpy=price_jpy)
    return ba.calculate(item, cfg, fx)


def recommendations(rows: list[dict], cfg: dict, fx: float) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        title = r.get("ebay_query") or r.get("title") or ""
        if not title or ba.ACCESSORY_RE.search(title):
            continue
        flags = ba.detect_flags(r.get("title", ""))
        if {"付属品のみ?", "アダプター等", "セット品"} & set(flags):
            continue
        k = rec_key(title)
        if not k:
            continue
        label, key, kind = k
        g = groups.setdefault(key, {"label": label, "brand": label.split()[0], "kind": kind,
                                    "sales": [], "lists": []})
        tier = ba.source_tier(r)
        rec = {"title": r.get("title", ""), "usd": round(r["_price"], 2), "junk": "ジャンク" in flags,
               "date": r.get("date", ""), "purchase": ba._float_or_none(r.get("purchase_jpy")), "tier": tier}
        (g["sales"] if tier <= 1 else g["lists"]).append(rec)

    out = []
    for g in groups.values():
        good = [s for s in g["sales"] if not s["junk"]]
        base = good or [s for s in g["lists"] if not s["junk"]]
        if not base:
            continue
        usd = round(statistics.median([s["usd"] for s in base]), 2)
        calc = store_calc(usd, cfg, fx)
        # 実際の利益（販売額と仕入値の両方がある自分・Chatworkの実績だけ）
        profits = [store_calc(s["usd"], cfg, fx, int(s["purchase"]))["profit_now_jpy"]
                   for s in g["sales"] if s["purchase"]]
        profit = round(statistics.median(profits)) if profits else None
        n = len(g["sales"])
        out.append({
            "label": g["label"], "brand": g["brand"], "kind": g["kind"],
            "sales": n, "lists": len(g["lists"]), "usd": usd,
            "basis": "販売実績" if good else "頒布会リスト（出品価格）",
            "maxBuy": calc["max_bid_jpy"], "breakeven": calc["breakeven_bid_jpy"],
            "profit": profit, "profitN": len(profits),
            "score": profit * n if profit is not None and profit > 0 else None,
            "last": max((s["date"] for s in g["sales"]), default=""),
            "examples": [s["title"] for s in (g["sales"] or g["lists"])[:3]],
        })
    # 並び順: 利益×販売回数 → 販売回数 → 上限額
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), -x["sales"], -x["maxBuy"]))
    return out


# ---------------------------------------------------------------------------
# 入札候補（毎朝の入札リスト reports/bid_report_*.csv → スマホの「入札」タブ）
# ---------------------------------------------------------------------------

BID_MAX_AGE_H = 48  # これより古い入札リストは届けない（終わった出品ばかりになるため）
SKIP_FLAGS = ("付属品のみ?", "アダプター等")


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def bid_candidates(reports: Path | None = None, now: datetime | None = None) -> dict | None:
    """最新の入札リストから ◎○ と 仮◎○ を取り出す. 無い・古いときは None."""
    reports = reports or ROOT / "reports"
    files = sorted(reports.glob("bid_report_*.csv"), key=lambda p: p.stat().st_mtime) if reports.exists() else []
    if not files:
        return None
    f = files[-1]
    made = datetime.fromtimestamp(f.stat().st_mtime)
    if ((now or datetime.now()) - made).total_seconds() > BID_MAX_AGE_H * 3600:
        return None
    items = []
    for r in csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()):
        flags = [x for x in (r.get("注意") or "").split(" / ") if x]
        if any(x in SKIP_FLAGS for x in flags):
            continue
        good = r.get("判定") in ("◎", "○")
        tent = not good and r.get("仮判定") in ("◎", "○")
        if not (good or tent):
            continue
        weak = good and r.get("根拠の強さ") == "確認してから"  # 頒布会リスト・鑑定表が根拠 → 確認してから
        url = r.get("ヤフオクURL") or ""
        m = re.search(r"/auction/([^/?#]+)", url)
        items.append({
            "id": m.group(1) if m else url, "url": url, "title": r.get("タイトル", ""),
            "mark": r["判定"] if good else r["仮判定"], "tent": tent, "check": tent or weak, "trusted": r.get("枠") == "安心",
            "price": _num(r.get("現在価格")), "bids": _num(r.get("入札数")), "end": r.get("終了", ""),
            "ship": _num(r.get("国内送料")), "shipBasis": r.get("国内送料の根拠", ""),
            "usd": _num(r.get("仮の想定USD") if tent else r.get("想定販売価格USD")),
            "basis": r.get("仮の根拠") if tent else r.get("価格の根拠", ""),
            "maxBid": _num(r.get("仮の上限入札額") if tent else r.get("上限入札額")),
            "profit": _num(r.get("仮の利益(現在価格)") if tent else r.get("利益(現在価格)")),
            "margin": None if tent else _num(r.get("利益率%")),
            "breakeven": None if tent else _num(r.get("損益分岐入札額")),
            "taxNote": r.get("入札額の税", ""),
            "sold": r.get("仮のsoldURL") if tent else r.get("eBay販売済みURL", ""),
            "now": r.get("仮の出品中URL") if tent else r.get("eBay出品中URL", ""),
            "flags": [x for x in flags if x not in ("動作品", "美品")],
            "desc": r.get("説明文", ""),
        })
    items.sort(key=lambda x: (x["check"], x["tent"], x["end"] or "9"))
    return {"built": f"{made:%Y-%m-%d %H:%M}", "items": items}


def build(cfg: dict, fx: float) -> dict:
    data = bx.build_data(cfg, fx)
    rows = []
    for name in REC_SOURCES:
        p = ROOT / "data" / name
        if p.exists():
            rows += ba.read_sales(p)
    data["recs"] = recommendations(rows, cfg, fx)
    data["bids"] = bid_candidates()
    data["kind"] = "aurora-store"
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="店舗仕入れアプリのデータを作る")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "data" / "store_data.json")
    ap.add_argument("--fx", type=float, help="為替（省略時は自動取得、失敗時は config.toml）")
    ap.add_argument("--no-icloud", action="store_true", help="iCloud Drive にコピーしない")
    ap.add_argument("--no-publish", action="store_true", help="iPhone への自動配信をしない")
    args = ap.parse_args(argv)

    CORE_JS.parent.mkdir(exist_ok=True)
    CORE_JS.write_text(core_js(), encoding="utf-8")
    ba.import_extension_memo()  # 拡張機能で「直す」した相場（ダウンロード/aurora_price_memo.csv）
    cfg = ba.load_config(None)
    fx = args.fx or (cfg["fx"].get("auto_fetch") and ba.fetch_fx()) or float(cfg["fx"]["usd_jpy"])
    data = build(cfg, fx)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nb = len(data["bids"]["items"]) if data.get("bids") else 0
    print(f"出力: {args.out}（相場 {len(data['refs'])}件・おすすめ候補 {len(data['recs'])}機種・入札候補 {nb}件・"
          f"為替 {fx:.2f}円・{args.out.stat().st_size // 1024}KB）", file=sys.stderr)
    if not args.no_icloud and ICLOUD.parent.exists():
        # 自動実行（launchd）からは macOS が iCloud Drive への書き込みを止めることがある。その場合も配信は続ける
        try:
            ICLOUD.mkdir(exist_ok=True)
            shutil.copy(args.out, ICLOUD / args.out.name)
            print(f"iCloud Drive にコピー: iCloud Drive › aurora › {args.out.name}", file=sys.stderr)
        except OSError as e:
            print(f"iCloud Drive にコピーできませんでした（{e.__class__.__name__}）。スマホへは暗号化配信で届けます", file=sys.stderr)
    if not args.no_publish and args.out == ROOT / "data" / "store_data.json":
        import store_publish  # 暗号化して iPhone へ自動配信（未設定なら何もしない）
        store_publish.publish(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
