---
name: data-update
description: 古物台帳・頒布会リスト・Chatworkの販売報告を取り込んで、相場データ・拡張機能・ナレッジを最新にする手順。「データを更新して」「台帳を取り込んで」と言われたら使う。
---
# データ更新

**自動**: `python3 tools/schedule.py install` 済みなら、毎朝とログイン時に `update_all.py` が自動で動く（結果は `python3 tools/schedule.py status`）。
手動で今すぐなら Mac で `python3 tools/update_all.py`（data/ は Mac にしか無いので、クラウドの Claude からは書けない）。

初回の設定（各1回）: `chatwork_sync.py setup`・`ledger_sync.py setup`・`store_publish.py setup`・`schedule.py install`。

`update_all.py` がやること:
0. **古物台帳**をスプレッドシートから直接（`ledger_sync.py`。未設定ならダウンロードフォルダの CSV）
1. ダウンロードフォルダから取り込む（中身で見分けるので、ファイル名は何でもよい。古いファイルは data/backup/ に残す）
   - **古物台帳**: スプレッドシートの「古物台帳」シートを「ファイル → ダウンロード → CSV」 → `data/ledger.csv`
   - **頒布会**: 商品リストの xlsx（シート「カメラ商品一覧」）をダウンロードフォルダに保存 → `data/hanpukai_YYYY-MM.xlsx`
   - CPaSS・オークレボの明細 → `data/statements/`（import_downloads.py）
   - 拡張機能で［直す］［登録］した相場（`aurora_price_memo.csv`）→ `data/price_memo.csv`
2. **Chatwork**: 販売報告と、添付された頒布会リスト（xlsx）を公式 API で取り込む（`chatwork_sync.py`。メッセージは最新100件まで）
3. 作り直す: `build_reference_prices.py` → `build_extension.py` → `build_store.py` → `build_knowledge.py`

ユーザーに頼むこと（コマンドは毎回そのまま貼れる形で示す）:
- Chrome: `chrome://extensions` で ↻
- iPhone: 自動配信（`store_publish.py`）を設定済みなら不要。未設定なら設定タブで `store_data.json` を選び直す

古い Chatwork の報告（API で読めない100件より前）はスクショで `chatwork-import` スキル。
件数の変化（例: 販売実績 58→65件）を報告し、意思決定ログに記録。
