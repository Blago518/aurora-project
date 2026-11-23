---
name: price-research
description: 相場が未登録の機種の eBay 相場を調べて相場メモ（data/price_memo.csv）に登録する手順。「相場を調べて」「未登録の機種を埋めたい」と言われたら使う。
---
# 相場調査と相場メモ登録

1. 未登録の機種を件数の多い順に並べる（`reports/unpriced_models_*.csv`、または拡張機能の「相場が未登録の機種」）。
2. 各機種の eBay 販売済み（価格+送料の高い順）を確認する。
   - **eBay のページを自動で読み取らない（スクレイピングは eBay の規約違反）。**
   - sold リンク・Product Research を渡し、ユーザーに数字を確認してもらう（または画面のスクショを読む）。
3. 想定販売価格は、実際に売れた額の真ん中（中央値）。低めに見積もらない（2026-11-21 オーナー「高く見積もれば安心ではない」と同じ考え。数字は実額）。根拠の強さ（件数・期間・状態）を書く。ジャンク・難ありの成約は除いて考える。
4. `data/price_memo.csv` に追記: `title,sold_price_usd,source,memo`（source は「相場メモ」）。
5. 反映: `python3 tools/build_extension.py` と `python3 tools/build_knowledge.py`。
6. `knowledge/意思決定ログ.md` に「◯機種の相場を登録」と記録。
