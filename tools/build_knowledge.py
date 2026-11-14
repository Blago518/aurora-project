#!/usr/bin/env python3
"""data/ の相場データから、ナレッジ（knowledge/）の自動生成ノートを作る.

- knowledge/相場/相場インデックス.md : メーカー別ノートへの入口
- knowledge/相場/<メーカー>.md       : そのメーカーの全相場（出典つき）
- knowledge/機種/<機種>.md           : 自分・Chatworkで売れた機種ごとに、関連する全相場をまとめたノート

手書きのノートは上書きしない（相場/ と 機種/ だけを作り直す）。Obsidian の [[リンク]] 形式で書く。
"""
from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import appraise as ba

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KNOW = ROOT / "knowledge"
SOURCES = ("reference_prices.csv", "chatwork_sales.csv", "price_memo.csv", "ebay_cache.csv")


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', " ", name).strip()[:80] or "その他"


def brand_of(title: str) -> str:
    toks = ba.title_to_query(title, 20).split()
    brands = {b.lower() for b in ba.BRANDS_EN.values()}
    b = toks[0] if toks and toks[0].lower() in brands else "その他"
    return "Zeiss" if b == "Carl" else b


def main() -> int:
    refs = []
    for name in SOURCES:
        p = ROOT / "data" / name
        if p.exists():
            refs += ba.read_sales(p)
    if not refs:
        print("data/ に相場データがありません", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    label = lambda r: ba.SOURCE_LABELS[ba.source_tier(r)] if not str(r.get("source", "")).startswith(("相場メモ", "eBay")) \
        else str(r["source"])

    # 機種ノート（自分・Chatworkの実績がある機種）
    models: dict[str, dict] = {}
    for r in refs:
        if ba.source_tier(r) > 1:
            continue
        lab, key = ba.model_key(r["title"])
        if brand_of(r["title"]) == "その他" or len(lab.split()) < 3 and not re.search(r"\d", lab):
            lab = ba.title_to_query(r["title"])  # メーカー不明は機種名をそのまま使う
        models.setdefault(key, {"label": lab, "title": r["title"]})
    for d in ("相場", "機種"):
        (KNOW / d).mkdir(parents=True, exist_ok=True)
        for old in (KNOW / d).glob("*.md"):
            old.unlink()  # 自動生成フォルダだけ作り直す
    model_note_of: dict[str, str] = {}
    for key, m in models.items():
        # 優先度の低いデータ（頒布会・鑑定表など）も見たいので、1件ずつ照合して全部集める
        allm = [r for r in refs if ba.match_sales(m["title"], [r])]
        note = safe(m["label"])
        model_note_of[key] = note
        brand = brand_of(m["title"])
        prices = [r["_price"] for r in allm]
        links = ba.ebay_links(m["label"])
        rows = "\n".join(
            f"| {r.get('date', '')} | {r.get('title', '')} | ${r['_price']:g} | "
            f"{(str(int(float(r['purchase_jpy']))) + '円') if str(r.get('purchase_jpy') or '').replace('.', '').isdigit() else '-'} | {label(r)} |"
            for r in sorted(allm, key=lambda r: (ba.source_tier(r), str(r.get("date", ""))))
        )
        (KNOW / "機種" / f"{note}.md").write_text(
            f"# {m['label']}\n\n"
            f"> 自動生成（{stamp}）。手書きのメモは [[機種メモ/{note}]] に。\n\n"
            f"- メーカー: [[相場/{safe(brand)}]]\n"
            f"- 相場の中央値: **${statistics.median(prices):g}**（{len(prices)}件）\n"
            f"- eBay: [販売済み（高い順）]({links['sold']}) ・ [出品中]({links['active']}) ・ [Product Research]({links['research']})\n\n"
            f"| 日付 | タイトル | 価格(送料込) | 仕入 | 出典 |\n|---|---|---|---|---|\n{rows}\n\n"
            f"関連: [[データ/データソース]] [[方針/利益計算]]\n",
            encoding="utf-8")

    # メーカー別ノート
    by_brand: dict[str, list] = defaultdict(list)
    for r in refs:
        by_brand[brand_of(r["title"])].append(r)
    for brand, rs in sorted(by_brand.items()):
        lines = []
        for r in sorted(rs, key=lambda r: (ba.source_tier(r), r.get("title", ""))):
            _, key = ba.model_key(r["title"])
            t = f"[[機種/{model_note_of[key]}\\|{r['title']}]]" if key in model_note_of else r.get("title", "")
            lines.append(f"| {t} | ${r['_price']:g} | {label(r)} |")
        (KNOW / "相場" / f"{safe(brand)}.md").write_text(
            f"# {brand} の相場\n\n> 自動生成（{stamp}）。{len(rs)}件。出典の優先度は [[データ/データソース]]。\n\n"
            "| 機種 | 価格(送料込) | 出典 |\n|---|---|---|\n" + "\n".join(lines) + "\n", encoding="utf-8")
    idx = "\n".join(f"- [[相場/{safe(b)}]]（{len(rs)}件）" for b, rs in sorted(by_brand.items(), key=lambda x: -len(x[1])))
    (KNOW / "相場" / "相場インデックス.md").write_text(
        f"# 相場インデックス\n\n> 自動生成（{stamp}）。相場データ {len(refs)}件、機種ノート {len(models)}件。\n\n{idx}\n\n"
        "関連: [[00_ホーム]] [[データ/データソース]]\n", encoding="utf-8")
    print(f"相場ノート {len(by_brand)}件・機種ノート {len(models)}件を作成: {KNOW}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
