---
name: price-research
description: 相場が未登録の機種の eBay 相場を調べて相場メモ（data/price_memo.csv）に登録する手順。「相場を調べて」「未登録の機種を埋めたい」と言われたら使う。
---
# 相場調査と相場メモ登録

1. 未登録の機種を件数の多い順に並べる（`out/unpriced_models_*.csv`、または拡張機能の「相場が未登録の機種」）。
2. 各機種の eBay 販売済み（価格+送料の高い順）を確認する。
   - ユーザーの Mac なら `python3 bid_analyzer.py 検索URL.txt --ebay-auto 40` で自動取得を試す。
   - 取れない場合は sold リンクを渡し、ユーザーに数字を確認してもらう。
3. 想定販売価格は**中央値より少し下**（保守的に）。ジャンク・難ありの成約は除いて考える。
4. `yahoo_bid/data/price_memo.csv` に追記: `title,sold_price_usd,source,memo`（source は「相場メモ」）。
5. 反映: `python3 build_userscript.py --chrome` と `python3 build_knowledge.py`。
6. `yahoo_bid/knowledge/意思決定ログ.md` に「◯機種の相場を登録」と記録。
