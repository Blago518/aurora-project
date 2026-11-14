---
name: data-update
description: 古物台帳・頒布会リスト・Chatworkの販売報告を取り込んで、相場データ・拡張機能・ナレッジを最新にする手順。「データを更新して」「台帳を取り込んで」と言われたら使う。
---
# データ更新

1. **古物台帳**: 古物台帳のスプレッドシートの「古物台帳」シートを読む（Google Sheets コネクタ、または CSV）。`data/ledger.csv` に保存。
2. **頒布会**: 新しい商品リスト（xlsx）を `data/hanpu_YYYY_MM.xlsx` として保存。
3. 相場データを作り直す:
   ```bash
   python3 tools/build_reference_prices.py --ledger data/ledger.csv --hanpu data/hanpukai_*.xlsx -o data/reference_prices.csv
   ```
4. **Chatwork**: `chatwork-import` スキルで `data/chatwork_sales.csv` に追記。
5. 反映: `python3 tools/build_extension.py` → `python3 tools/build_store.py`（店舗アプリ。iPhone の設定タブで読み込み直す）→ `python3 tools/build_knowledge.py` → `python3 -m unittest discover -s tools`。
6. 件数の変化（例: 販売実績 58→65件）を報告し、意思決定ログに記録。
