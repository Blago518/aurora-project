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
5. 反映: `python3 tools/build_extension.py` → `python3 tools/build_knowledge.py` → `python3 -m unittest discover -s tools`。
6. **発送の明細（毎月）**: ユーザーが CPaSS の注文一覧（全期間）・請求明細・関税明細、オークレボの商品一覧・請求書を
   **ダウンロードするだけ**でよい。`python3 tools/import_downloads.py` が ~/Downloads から中身（列名）で見分けて `data/statements/` に移し、
   `build_ledger_costs.py` まで実行して `data/ledger_costs.csv` を作る（同じ月の明細は新しい方で置き換え。関税明細はファイル名の日付＝ダウンロード日時なので、対象月は中身の日付で決める）。
   管理番号ごとの国際送料・発送代行費・関税等（全期間の合計）を、承認を得てから新しい古物台帳の S〜U 列に**上書き**し、L〜M 列（注文番号・国）の空欄を埋める。
   見出しの「※◯〜◯月分を反映」を更新。結び付かない明細は `reports/ledger_costs_unmatched.csv` を見せて確認する。
   CPaSS注文一覧には購入者の氏名・住所が入っているので、そのまま保存・転記しない（追跡番号・eBay注文番号・SKU・国だけ使う）。
7. 件数の変化（例: 販売実績 58→65件）を報告し、意思決定ログに記録。
