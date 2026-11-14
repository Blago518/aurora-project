---
name: bid-selection
description: ヤフオクの検索URLから入札候補（◎○）を選び、上限入札額と eBay の sold/now リンクを一覧にする手順。「入札候補を出して」「この検索URLを鑑定して」と言われたら使う。
---
# 入札候補の選定

1. ユーザーから検索URL（ヤフオクの検索結果・出品者ページ）を受け取る。複数可。
2. `yahoo_bid/検索URL.txt` に1行1本で保存する。
3. 実行:
   ```bash
   cd yahoo_bid && python3 bid_analyzer.py 検索URL.txt
   ```
4. `out/bid_report_*.csv` から ◎○ を**終了が近い順**に取り出し、表にする:
   | 終了 | 商品（ヤフオクリンク） | 現在 | 上限入札額 | eBay相場（$→soldリンク） | 根拠 |
5. 根拠が「頒布会リスト」「鑑定表(推定)」「ジャンク」のものは、入札前に sold リンクで確認するよう注意書きを付ける。
6. 相場が無い機種が多ければ、`out/unpriced_models_*.csv` の上位を示し、`price-research` スキルを提案する。

注意: クラウド環境からは eBay に接続できない（403）。eBay の数字はリンクで示す。
