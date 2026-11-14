---
name: price-research
description: 相場が未登録の機種の eBay 相場を調べて相場メモ（data/price_memo.csv）に登録する手順。「相場を調べて」「未登録の機種を埋めたい」と言われたら使う。
---
# 相場調査と相場メモ登録

1. 未登録の機種を件数の多い順に並べる（`reports/unpriced_models_*.csv`、または拡張機能の「相場が未登録の機種」）。
2. 各機種の eBay 販売済み（価格+送料の高い順）を確認する。
   - **eBay のページを自動で読み取らない（スクレイピングは eBay の規約違反）。**
   - sold リンク・Product Research を渡し、ユーザーに数字を確認してもらう（または画面のスクショを読む）。
   - 出品中の価格は公式 Browse API で取れる（拡張機能の「eBay出品中」列、または `python3 tools/ebay_api.py browse "機種名"`）。
     出品中は売れた価格より高めなので、案は中央値×`active_price_factor`（config.toml、0.7）。**案は sold で確認してから登録**する。
3. 想定販売価格は**中央値より少し下**（保守的に）。ジャンク・難ありの成約は除いて考える。
4. `data/price_memo.csv` に追記: `title,sold_price_usd,source,memo`（source は「相場メモ」）。
5. 反映: `python3 tools/build_extension.py` と `python3 tools/build_knowledge.py`。
6. `knowledge/意思決定ログ.md` に「◯機種の相場を登録」と記録。
