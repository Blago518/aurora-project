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
import re
import sys
from datetime import datetime
from pathlib import Path

import appraise as ba

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ("reference_prices.csv", "chatwork_sales.csv", "price_memo.csv", "research_prices.csv")



def _model_query_consts() -> dict:
    import build_store  # build_store は build_extension を読み込むので、ここで読む
    return build_store.model_query_consts()

def build_data(cfg: dict, fx: float) -> dict:
    refs, rows = [], []
    for name in SOURCES:
        p = ROOT / "data" / name
        if not p.exists():
            continue
        for r in ba.read_sales(p):
            rows.append(r)
            refs.append({
                "title": r.get("title", ""), "price": round(r["_price"], 2), "tier": ba.source_tier(r),
                "source": r.get("source") or "自分の販売実績", "tokens": sorted(r["_tokens"]),
                "acc": bool(ba.ACCESSORY_RE.search(r.get("title", ""))),
                "hit": ba.sold_hit(r, cfg, fx),  # 売れ筋（実績で利益が出ていた機種）→ 入札候補の上に出す
                "d": ba.norm_date(r.get("date", "")),  # 売れた日（相場の上がり下がり ↑↓ に使う。分からなければ空）
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
        "trusted": [{"id": k, "name": v} for k, v in ba.load_trusted().items()],  # 安心ストア（data/trusted_sellers.csv）
        "refs": refs,
        "kantei": kantei,
        "winStats": _win_stats(),
        # ヤフオクの落札相場（data/yahoo_results.csv。機種ごとの直近の落札額。表示だけで判定・上限額には使わない）
        "yahooStats": _yahoo_stats(cfg),
        # 勝ちやすさ（data/win_score.csv。機種の比較用キーごと {rate, n}）。1割未満の機種は入札候補の下に回す（判定・上限額には使わない）
        "winScore": _win_scores(cfg),
        # robots.txt で Disallow の引数（［続けて鑑定］で続きのページを読むとき外す）[パスの正規表現, 引数の末尾]
        "robotsParams": _robots_params(),
        "memoHist": ba.load_memo_history(),  # 相場メモの履歴（拡張機能の「相場の動き」。data/price_memo_history.csv）  # 落札見込み（data/bid_log.csv の結果から。bid_results.win_stats）
        "caution": {"sellers": list((cfg.get("caution") or {}).get("sellers", [])), "note": str((cfg.get("caution") or {}).get("note", ""))},  # 注意を出す出品者（表示だけ）
        "cfg": {
            "fx": round(fx, 2), "convLossPct": cfg["fx"]["conversion_loss_pct"],
            "feePct": e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"],
            "perOrderUsd": e["per_order_fee_usd"], "buyerShippingUsd": e["buyer_shipping_usd"],
            "deductionUsd": s.get("deduction_usd", 0), "packing": ba.item_cost_jpy(cfg),  # 梱包＋オークレボの手数料（2026-11-21）
            "chatworkShipUsd": s.get("chatwork_included_usd", 30.0),  # Chatwork の報告額に含まれる送料（売値の表示を報告額に戻す用）
            "defaultDomestic": y["default_domestic_shipping_jpy"], "storeTaxPct": y["store_tax_pct"],
            "refundRatio": (t["consumption_tax_pct"] / (100 + t["consumption_tax_pct"])
                            if t["include_consumption_tax_refund"] else 0.0),
            "minProfit": j["min_profit_jpy"], "minMarginPct": j["min_margin_pct"],
            "lowPriceUsd": j.get("low_price_usd", 0), "lowPriceMinProfit": j.get("low_price_min_profit_jpy", 0),  # 安い物は利益の基準を上げる
            "trustedMinProfit": j.get("trusted_min_profit_jpy", j["min_profit_jpy"]),
            "trustedMinMarginPct": j.get("trusted_min_margin_pct", j["min_margin_pct"]),
            "junkFactor": j.get("junk_price_factor", 1.0),
            "realized": dict(cfg.get("realized") or {}),  # 実績の売値の割合（表示だけ。appraise.realized_ratio）
            # 状態1ランクの補正（%）。実績から推定（appraise.condition_step_pct。相場メモは使わない）
            "condStep": ba.condition_step_pct(cfg, [r for r in rows if not ba.is_memo(r)]),
            "activeMinCount": j.get("active_min_count", 5),
            "suggestMinNetUsd": j.get("suggest_min_net_usd", 0.0),  # 仮の相場の案にする最安値の下限（送料別$）
            "browse": False,  # eBay のキーがあれば main() で True にする
            "autoMaxItems": min(cfg.get("extension", {}).get("auto_max_items", 1000), 1000),  # 一度に読むのは1000件まで
            "browseMaxModels": cfg.get("extension", {}).get("browse_max_models", 400),
            # 落札相場の見張り（拡張機能 → ダウンロード/aurora_seen.csv）: 1日に足す上限と、表示する期間（日）
            "seenMaxPerDay": cfg.get("yahoo_results", {}).get("seen_max_per_day", 300),
            "yahooDays": cfg.get("yahoo_results", {}).get("market_days", 90),
            # 勝ちやすさ: この割合未満は下に回す・記録が無い機種に使う割合（［◎○をまとめて開く］の並び）
            "winLowRate": cfg.get("win_score", {}).get("low_rate", 0.10),
            "defaultWinRate": cfg.get("win_score", {}).get("default_rate", 0.2),
        },
        "consts": {
            "brandsJa": ba.BRANDS_JA, "wordsJa": ba.WORDS_JA, "brandsEn": ba.BRANDS_EN, "modelQuery": _model_query_consts(),
            # 型の語（modelWord・isFNumber・setPartTitles。appraise.py と同じ表）
            "lensWords": ba.LENS_WORDS, "bodyCodeSkip": sorted(ba.BODY_CODE_SKIP), "bodyCodeOk": sorted(ba.BODY_CODE_OK),
            "filmFormatAfter": ba.FILM_FORMAT_AFTER.pattern,
            "lensLines": ba.LENS_LINES, "lensMakers": sorted(ba.LENS_MAKERS), "noise": sorted(ba.NOISE_TOKENS), "thirdParty": sorted(ba.THIRD_PARTY),
            "colors": sorted(ba.COLOR_WORDS), "generations": sorted(ba.GENERATIONS), "series": sorted(ba.SERIES), "mounts": ba.MOUNT_WORDS, "formats": ba.FORMAT_WORDS,
            "premium": sorted(ba.PREMIUM_VARIANTS), "allTitanium": [list(m) for m in ba.ALL_TITANIUM], "accessoryRe": ba.ACCESSORY_RE.pattern,
            "flagRules": [[n, p] for n, p in ba.FLAG_RULES],
            "condRules": [[p, o] for p, o in ba.COND_RULES], "condRank": ba.COND_RANK,
            "listingCondRules": [[p, r] for p, r in ba.LISTING_COND_RULES], "listingDefaultRank": ba.LISTING_DEFAULT_RANK,
            "variantNormalize": [[p, r] for p, r in ba.VARIANT_NORMALIZE], "variantGroups": ba.VARIANT_GROUPS,
            # 商品名の書き方をそろえる表・機種名のシリーズ名（appraise.TITLE_FIXES・model_key と同じ。置き換えの \1 は JS の $1 に）
            "titleFixes": [[p, re.sub(r"\\(\d)", r"$\1", r), f] for p, r, f in ba.TITLE_FIXES],
            "keep4After": sorted(ba.KEEP4_AFTER), "lineWords": ba.LINE_WORDS, "keepNumAfter": sorted(ba.KEEP_NUM_AFTER),
            "formatNums": sorted(ba.FORMAT_NUMS), "variantLabels": ba.VARIANT_LABELS, "displayWords": ba.DISPLAY_WORDS,
            "increments": [[0 if math.isinf(lim) else lim, step] for lim, step in ba.YAHOO_INCREMENTS],
        },
    }


def _yahoo_stats(cfg: dict) -> dict:
    try:
        import yahoo_results
        return yahoo_results.load_model_stats(days=int(cfg.get("yahoo_results", {}).get("market_days", 90)))
    except Exception:  # noqa: BLE001  記録が無い・読めない → 出さないだけ
        return {}


def _win_scores(cfg: dict) -> dict:
    try:
        import win_score
        return {k: {"rate": v["rate"], "n": v["n"]} for k, v in win_score.load_scores(cfg=cfg).items()}
    except Exception:  # noqa: BLE001  記録が無い・読めない → 今の並びのまま
        return {}


def _robots_params() -> list:
    try:
        import robots
        return robots.param_rules()
    except Exception:  # noqa: BLE001  読めなければ拡張機能の予備の規則（n・s1・o1 を外す）
        return []


def _win_stats() -> dict:
    try:
        import bid_results
        return bid_results.load_win_stats()
    except Exception:  # 記録が無い・読めない → 落札見込みを出さないだけ
        return {}


def ebay_keys() -> tuple[str, str] | None:
    """eBay の App ID・Cert ID（ebay_api.py setup で保存したもの）。無ければ None."""
    try:
        import ebay_api
        app, cert = ebay_api.secret_get("app_id"), ebay_api.secret_get("cert_id")
    except Exception:
        return None
    return (app, cert) if app and cert else None


def write_chrome_extension(js: str, folder: Path, keys: tuple[str, str] | None = None, cfg: dict | None = None) -> None:
    """自作のChrome拡張機能（Manifest V3）を作る。ヤフオクのページで動く.

    eBay のキーがあれば、出品中の相場を公式 Browse API で調べる background.js も入れる（通信先は api.ebay.com だけ）。
    キーは extension/ の中だけに入る（extension/ はコミットしない）。
    """
    folder.mkdir(parents=True, exist_ok=True)
    # Chrome の版番号は各数字の頭に 0 を付けられない（0時台の「002」で警告が出る）。時分は 時×100+分 の数にする
    now = datetime.now()
    version = f"{now.year}.{now.month}.{now.day}.{now.hour * 100 + now.minute}"
    manifest = {
        "manifest_version": 3,
        "name": "Aurora 仕入れ鑑定",
        "version": version,
        "description": "ヤフオクの検索結果・出品者ページで、販売実績と照合して上限入札額と利益を表示します。",
        "icons": {str(n): f"icons/icon{n}.png" for n in (16, 32, 48, 128)},
        "action": {"default_title": "Aurora 仕入れ鑑定", "default_icon": {str(n): f"icons/icon{n}.png" for n in (16, 32)}},
        "content_scripts": [{
            "matches": ["https://auctions.yahoo.co.jp/search/*", "https://auctions.yahoo.co.jp/seller/*",
                        "https://auctions.yahoo.co.jp/category/*",
                        "https://auctions.yahoo.co.jp/follow/*"],  # フォロー中の一覧 → 安心ストアにまとめて登録
            "js": ["content.js"],
            "run_at": "document_idle",
        }, {
            # キタムラのネットショップ: 開いているページの商品を判定（content.js の kitStart。通信はしない）
            "matches": ["https://shop.kitamura.jp/*"],
            "js": ["content.js"],
            "run_at": "document_idle",
        }, {
            # ヤフオクの取引ナビ: 開いたら仕入れ証跡（画面の PNG）を自動で保存（extension_evidence.js。2026-11-23 オーナー依頼）
            "matches": ["https://contact.auctions.yahoo.co.jp/*"],
            "js": ["evidence.js"],
            "run_at": "document_idle",
        }, {
            # SpeedPAK セラーポータル: 料金明細を開いたら DHL の輸出許可書と「輸出取引の詳細」を自動で保存（extension_cpass.js。2026-11-23）
            "matches": ["https://ebay-jp.orangeconnex.com/*"],
            "js": ["cpass.js"],
            "run_at": "document_idle",
        }, {
            # オークファンの入札予約: 予約の手伝いと、予約一覧の読み取り（extension_aucfan.js）
            "matches": ["https://tools.aucfan.com/*"],
            "js": ["aucfan.js"],
            "run_at": "document_idle",
        }],
    }
    # 裏方（background.js）は常に入れる: 相場メモを ダウンロード/aurora_price_memo.csv に保存する（downloads 権限）。
    # eBay のキーがあれば、出品中の相場を公式 Browse API で調べる（通信先は api.ebay.com だけ）
    manifest["background"] = {"service_worker": "background.js"}
    manifest["permissions"] = ["downloads", "downloads.ui", "storage"]  # downloads.ui: 自動保存の吹き出しを出さない  # storage: オークファンの予約の受け渡し・eBay 出品中の価格の保存
    # 仕入れ証跡: 取引ナビの画面を写す（tabs.captureVisibleTab）。Chrome はサイトだけの許可では写させず、<all_urls> か activeTab（毎回の操作）が要る
    # （2026-11-23 Chromium で確かめた）。写すのは取引ナビの evidence.js が頼んだときだけで、ほかのサイトには何もしない
    manifest["host_permissions"] = ["<all_urls>"] + (["https://api.ebay.com/*"] if keys else [])
    shutil.copy(HERE / "extension_aucfan.js", folder / "aucfan.js")
    shutil.copy(HERE / "extension_evidence.js", folder / "evidence.js")
    shutil.copy(HERE / "extension_cpass.js", folder / "cpass.js")
    tpl = (HERE / "extension_background.js").read_text(encoding="utf-8")
    cfg = cfg or ba.load_config(None)
    app, cert = keys or (None, None)
    (folder / "background.js").write_text(
        tpl.replace("__APP_ID__", json.dumps(app)).replace("__CERT_ID__", json.dumps(cert))
        .replace("__FREE_SHIP_USD__", json.dumps(float(cfg["judge"].get("active_free_ship_deduction_usd", 30.0))))
        .replace("__DEDUCTION_USD__", json.dumps(float(cfg["shipping"].get("deduction_usd", 0)))), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "content.js").write_text(js, encoding="utf-8")
    (folder / "icons").mkdir(exist_ok=True)
    for png in (HERE / "icons").glob("icon*.png"):
        shutil.copy(png, folder / "icons" / png.name)
    print(f"Chrome拡張機能: {folder}（相場の更新は ↻ 不要。拡張機能のプログラムを新しくしたときだけ ↻）", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chrome拡張機能（入札鑑定）を作る → extension/")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "extension", help="拡張機能のフォルダ（省略時は extension/）")
    ap.add_argument("--chrome", action="store_true", help="（互換用。指定しなくても Chrome拡張機能を作ります）")
    ap.add_argument("--userscript", type=Path, help="Safari「Userscripts」用の .user.js も作る場合の出力先")
    ap.add_argument("--fx", type=float, help="為替（省略時は自動取得、失敗時は config.toml）")
    args = ap.parse_args(argv)
    ba.import_extension_memo()  # 拡張機能で「直す」した相場（ダウンロード/aurora_price_memo.csv）
    ba.import_extension_trusted()  # 拡張機能で登録した安心ストア（ダウンロード/aurora_trusted_sellers.csv）
    cfg = ba.load_config(None)
    fx = args.fx or (cfg["fx"].get("auto_fetch") and ba.fetch_fx()) or float(cfg["fx"]["usd_jpy"])
    data = build_data(cfg, fx)
    keys = ebay_keys()
    data["cfg"]["browse"] = bool(keys)
    tpl = (HERE / "extension_template.js").read_text(encoding="utf-8")
    js = tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    js = js.replace("__VERSION__", datetime.now().strftime("%Y.%m.%d.%H%M"))
    write_chrome_extension(js, args.out, keys, cfg)
    # 相場データだけの data.json: 拡張機能がページを開くたびに読む（作り直しても chrome://extensions の ↻ は不要）。
    # 書き込み途中を読まないよう、別名で書いてから置き換える
    tmp = args.out / "data.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(args.out / "data.json")
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
