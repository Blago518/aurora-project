---
name: chatwork-import
description: Chatworkの販売報告スクショ（[販売価格][仕入価格][仕入先]…の形式）を読み取って data/chatwork_sales.csv に追記する手順。スクショが送られてきたら使う。
---
# Chatwork 販売報告の取り込み

新しい報告は Mac の `python3 tools/update_all.py`（中で `chatwork_sync.py` が公式 API から自動で取り込む）で入る。
スクショで取り込むのは、API で読めない古い報告（ルームの最新100件より前）や、API が未設定のとき。

1. スクショから1件ずつ読み取る: 商品名、販売価格$、eBayのURL、仕入価格、仕入先、日付、利益（赤字かどうか）。
   - **title** は報告どおり（管理番号 [00104] は除き、状態表記 [Top MINT+++] は**残す** → 拡張機能・店舗アプリの「状態」表示に使う）
   - **ebay_query** は状態表記を除いた機種名（照合に使う）
2. 読めない数字は推測せず、ユーザーに確認する。
3. data/ は Mac にしか無いので、Mac で貼るコマンドにして渡す。重複は `chatwork_sync.append_new()` が eBay の商品番号で除く:
   ```bash
   cd ~/aurora && python3 -c "
   import sys; sys.path.insert(0,'tools'); import chatwork_sync as cs
   rows=[{'date':'2026-11-06','title':'[Top MINT+++w/Box] Nikon AF-S Teleconverter TC-14E II 1.4x Extender Converter JP','ebay_query':'Nikon AF-S TC-14E II 1.4x','sold_price_usd':'169','shipping_charged_usd':0,'purchase_jpy':11000,'ebay_url':'https://www.ebay.com/itm/377577821275','source':'Chatwork販売実績(キタムラ)'}]
   print(len(cs.append_new(rows)),'件追記')" && python3 tools/update_all.py
   ```
   source は `Chatwork販売実績(仕入先)`。赤字の報告は `(仕入先・赤字)`。
4. 追加件数と、仕入れの参考になりそうな傾向（利益率が高い仕入先・機種）を短く報告。
