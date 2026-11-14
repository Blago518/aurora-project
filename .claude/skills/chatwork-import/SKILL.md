---
name: chatwork-import
description: Chatworkの販売報告スクショ（[販売価格][仕入価格][仕入先]…の形式）を読み取って data/chatwork_sales.csv に追記する手順。スクショが送られてきたら使う。
---
# Chatwork 販売報告の取り込み

1. スクショから1件ずつ読み取る: 商品名（[MINT]等の状態表記と管理番号は除く）、販売価格$、eBayのURL、仕入価格、仕入先、日付。
2. 既存の `data/chatwork_sales.csv` と eBay URL で重複を確認し、新しいものだけ追記:
   `date,title,ebay_query,sold_price_usd,shipping_charged_usd,purchase_jpy,ebay_url,source`
   source は `Chatwork販売実績(仕入先)`。赤字の報告は `(…・赤字)` と付ける。
3. 読み取れない数字は推測せず、ユーザーに確認する。
4. 反映: `python3 tools/build_extension.py`、`python3 tools/build_knowledge.py`。
5. 追加件数と、仕入れの参考になりそうな傾向（利益率が高い仕入先・機種）を短く報告。
