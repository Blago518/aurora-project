#!/usr/bin/env python3
"""ヤフオク入札鑑定のユーザースクリプト（Safari「Userscripts」拡張用）を作る.

data/ の販売実績・Chatwork・頒布会・相場メモ・鑑定表と config.toml の手数料設定を埋め込んだ
1つの .user.js を出力する。データを更新したら作り直して、Userscripts のフォルダに上書きする。

使い方:
    python3 build_userscript.py                 # → out/yahoo_kantei.user.js
    python3 build_userscript.py -o ~/Documents/Userscripts/yahoo_kantei.user.js
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import bid_analyzer as ba

HERE = Path(__file__).resolve().parent
SOURCES = ("sales.csv", "chatwork_sales.csv", "price_memo.csv", "ebay_sold_cache.csv")


def build_data(cfg: dict, fx: float) -> dict:
    refs = []
    for name in SOURCES:
        p = HERE / "data" / name
        if not p.exists():
            continue
        for r in ba.read_sales(p):
            refs.append({
                "title": r.get("title", ""), "price": round(r["_price"], 2), "tier": ba.source_tier(r),
                "source": r.get("source") or "自分の販売実績", "tokens": sorted(r["_tokens"]),
                "acc": bool(ba.ACCESSORY_RE.search(r.get("title", ""))),
            })
    kantei = []
    kp = next(iter(sorted((HERE / "data").glob("kantei_bookmarklet.*"))), None)
    if kp:
        for r in ba.read_kantei_table(kp):
            kantei.append({"title": r["title"], "price": r["_price"], "tier": 3, "source": r["source"],
                           "patterns": [p.pattern for p in r["_patterns"]], "tokens": sorted(ba._tokens(r["title"]))})
    e, y, t, j, s = cfg["ebay"], cfg["yahoo"], cfg["tax"], cfg["judge"], cfg["shipping"]
    return {
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "refs": refs,
        "kantei": kantei,
        "cfg": {
            "fx": round(fx, 2), "convLossPct": cfg["fx"]["conversion_loss_pct"],
            "feePct": e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"],
            "perOrderUsd": e["per_order_fee_usd"], "buyerShippingUsd": e["buyer_shipping_usd"],
            "deductionUsd": s.get("deduction_usd", 0), "packing": s["packing_jpy"],
            "defaultDomestic": y["default_domestic_shipping_jpy"], "storeTaxPct": y["store_tax_pct"],
            "refundRatio": (t["consumption_tax_pct"] / (100 + t["consumption_tax_pct"])
                            if t["include_consumption_tax_refund"] else 0.0),
            "minProfit": j["min_profit_jpy"], "minMarginPct": j["min_margin_pct"],
        },
        "consts": {
            "brandsJa": ba.BRANDS_JA, "wordsJa": ba.WORDS_JA, "brandsEn": ba.BRANDS_EN,
            "lensLines": ba.LENS_LINES, "noise": sorted(ba.NOISE_TOKENS), "thirdParty": sorted(ba.THIRD_PARTY),
            "colors": sorted(ba.COLOR_WORDS), "generations": sorted(ba.GENERATIONS),
            "premium": sorted(ba.PREMIUM_VARIANTS), "accessoryRe": ba.ACCESSORY_RE.pattern,
            "flagRules": [[n, p] for n, p in ba.FLAG_RULES],
            "increments": [[0 if math.isinf(lim) else lim, step] for lim, step in ba.YAHOO_INCREMENTS],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ヤフオク入札鑑定ユーザースクリプトを作る")
    ap.add_argument("-o", "--out", type=Path, default=HERE / "out" / "yahoo_kantei.user.js")
    ap.add_argument("--fx", type=float, help="為替（省略時は自動取得、失敗時は config.toml）")
    args = ap.parse_args(argv)
    cfg = ba.load_config(None)
    fx = args.fx or (cfg["fx"].get("auto_fetch") and ba.fetch_fx()) or float(cfg["fx"]["usd_jpy"])
    data = build_data(cfg, fx)
    tpl = (HERE / "userscript_template.js").read_text(encoding="utf-8")
    js = tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    js = js.replace("__VERSION__", datetime.now().strftime("%Y.%m.%d.%H%M"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(js, encoding="utf-8")
    print(f"出力: {args.out}（相場 {len(data['refs'])}件・鑑定表 {len(data['kantei'])}件・為替 {fx:.2f}円・"
          f"{args.out.stat().st_size // 1024}KB）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
