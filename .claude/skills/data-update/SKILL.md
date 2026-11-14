---
name: data-update
description: 古物台帳・頒布会リスト・Chatworkの販売報告を取り込んで、相場データ・拡張機能・ナレッジを最新にする手順。「データを更新して」「台帳を取り込んで」と言われたら使う。
---
# データ更新

1. **古物台帳**: 古物台帳のスプレッドシートの「古物台帳」シートを読む（Google Sheets コネクタ、または CSV）。`yahoo_bid/data/kobutsu_daicho.csv` に保存。
2. **頒布会**: 新しい商品リスト（xlsx）を `yahoo_bid/data/hanpu_YYYY_MM.xlsx` として保存。
3. 相場データを作り直す:
   ```bash
   cd yahoo_bid && python3 build_sales.py --ledger data/kobutsu_daicho.csv --hanpu data/hanpu_*.xlsx -o data/sales.csv
   ```
4. **Chatwork**: `chatwork-import` スキルで `data/chatwork_sales.csv` に追記。
5. 反映: `python3 build_userscript.py --chrome` → `python3 build_knowledge.py` → `python3 -m unittest test_bid_analyzer`。
6. 件数の変化（例: 販売実績 58→65件）を報告し、意思決定ログに記録。
