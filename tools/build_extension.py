#!/usr/bin/env python3
"""ヤフオク入札鑑定のユーザースクリプト（Safari「Userscripts」拡張用）を作る.

data/ の販売実績・Chatwork・頒布会・相場メモ・鑑定表と config.toml の手数料設定を埋め込んだ
1つの .user.js を出力する。データを更新したら作り直して、Userscripts のフォルダに上書きする。

使い方:
    python3 tools/build_extension.py                                   # → extension/（Chrome拡張機能）
    python3 tools/build_extension.py --userscript ~/Documents/Userscripts/aurora.user.js  # Safari用も作る
"""
from __future__ import annotations

import argparse
import json
import shutil
import math
import sys
from datetime import datetime
from pathlib import Path

import appraise as ba

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ("reference_prices.csv", "chatwork_sales.csv", "price_memo.csv")


def build_data(cfg: dict, fx: float) -> dict:
    refs = []
    for name in SOURCES:
        p = ROOT / "data" / name
        if not p.exists():
            continue
        for r in ba.read_sales(p):
            refs.append({
                "title": r.get("title", ""), "price": round(r["_price"], 2), "tier": ba.source_tier(r),
                "source": r.get("source") or "自分の販売実績", "tokens": sorted(r["_tokens"]),
                "acc": bool(ba.ACCESSORY_RE.search(r.get("title", ""))),
            })
    kantei = []
    kp = next(iter(sorted((ROOT / "data").glob("kantei_bookmarklet.*"))), None)
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
            "junkFactor": j.get("junk_price_factor", 1.0),
            "activeFactor": j.get("active_price_factor", 0.7), "activeMinCount": j.get("active_min_count", 3),
            "browse": False,  # eBay のキーがあれば main() で True にする
        },
        "consts": {
            "brandsJa": ba.BRANDS_JA, "wordsJa": ba.WORDS_JA, "brandsEn": ba.BRANDS_EN,
            "lensLines": ba.LENS_LINES, "lensMakers": sorted(ba.LENS_MAKERS), "noise": sorted(ba.NOISE_TOKENS), "thirdParty": sorted(ba.THIRD_PARTY),
            "colors": sorted(ba.COLOR_WORDS), "generations": sorted(ba.GENERATIONS),
            "premium": sorted(ba.PREMIUM_VARIANTS), "accessoryRe": ba.ACCESSORY_RE.pattern,
            "flagRules": [[n, p] for n, p in ba.FLAG_RULES],
            "increments": [[0 if math.isinf(lim) else lim, step] for lim, step in ba.YAHOO_INCREMENTS],
        },
    }


def ebay_keys() -> tuple[str, str] | None:
    """eBay の App ID・Cert ID（ebay_api.py setup で保存したもの）。無ければ None."""
    try:
        import ebay_api
        app, cert = ebay_api.secret_get("app_id"), ebay_api.secret_get("cert_id")
    except Exception:
        return None
    return (app, cert) if app and cert else None


def write_chrome_extension(js: str, folder: Path, keys: tuple[str, str] | None = None) -> None:
    """自作のChrome拡張機能（Manifest V3）を作る。ヤフオクのページで動く.

    eBay のキーがあれば、出品中の相場を公式 Browse API で調べる background.js も入れる（通信先は api.ebay.com だけ）。
    キーは extension/ の中だけに入る（extension/ はコミットしない）。
    """
    folder.mkdir(parents=True, exist_ok=True)
    version = datetime.now().strftime("%Y.%-m.%-d.%-H%M") if sys.platform != "win32" else "1.0.0"
    manifest = {
        "manifest_version": 3,
        "name": "Aurora 仕入れ鑑定",
        "version": version,
        "description": "ヤフオクの検索結果・出品者ページで、販売実績と照合して上限入札額と利益を表示します。",
        "icons": {str(n): f"icons/icon{n}.png" for n in (16, 32, 48, 128)},
        "action": {"default_title": "Aurora 仕入れ鑑定", "default_icon": {str(n): f"icons/icon{n}.png" for n in (16, 32)}},
        "content_scripts": [{
            "matches": ["https://auctions.yahoo.co.jp/search/*", "https://auctions.yahoo.co.jp/seller/*",
                        "https://auctions.yahoo.co.jp/category/*"],
            "js": ["content.js"],
            "run_at": "document_idle",
        }],
    }
    bg = folder / "background.js"
    if keys:
        manifest["background"] = {"service_worker": "background.js"}
        manifest["host_permissions"] = ["https://api.ebay.com/*"]
        manifest["permissions"] = ["storage"]
        tpl = (HERE / "extension_background.js").read_text(encoding="utf-8")
        bg.write_text(tpl.replace("__APP_ID__", json.dumps(keys[0])).replace("__CERT_ID__", json.dumps(keys[1])),
                      encoding="utf-8")
    elif bg.exists():
        bg.unlink()
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "content.js").write_text(js, encoding="utf-8")
    (folder / "icons").mkdir(exist_ok=True)
    for png in (HERE / "icons").glob("icon*.png"):
        shutil.copy(png, folder / "icons" / png.name)
    print(f"Chrome拡張機能: {folder}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chrome拡張機能（入札鑑定）を作る → extension/")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "extension", help="拡張機能のフォルダ（省略時は extension/）")
    ap.add_argument("--chrome", action="store_true", help="（互換用。指定しなくても Chrome拡張機能を作ります）")
    ap.add_argument("--userscript", type=Path, help="Safari「Userscripts」用の .user.js も作る場合の出力先")
    ap.add_argument("--fx", type=float, help="為替（省略時は自動取得、失敗時は config.toml）")
    args = ap.parse_args(argv)
    cfg = ba.load_config(None)
    fx = args.fx or (cfg["fx"].get("auto_fetch") and ba.fetch_fx()) or float(cfg["fx"]["usd_jpy"])
    data = build_data(cfg, fx)
    keys = ebay_keys()
    data["cfg"]["browse"] = bool(keys)
    tpl = (HERE / "extension_template.js").read_text(encoding="utf-8")
    js = tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    js = js.replace("__VERSION__", datetime.now().strftime("%Y.%m.%d.%H%M"))
    write_chrome_extension(js, args.out, keys)
    if args.userscript:
        args.userscript.parent.mkdir(parents=True, exist_ok=True)
        args.userscript.write_text(js, encoding="utf-8")
    print(f"出力: {args.out}（相場 {len(data['refs'])}件・鑑定表 {len(data['kantei'])}件・為替 {fx:.2f}円・"
          f"{len(js.encode()) // 1024}KB）", file=sys.stderr)
    print("eBay出品中の相場（Browse API）: " + ("有効" if keys else "無効（python3 tools/ebay_api.py setup でキーを保存すると有効）"),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
