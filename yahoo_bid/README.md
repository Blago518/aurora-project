# ヤフオク入札判断ツール

ヤフオクのブックマーク（気になる出品）をまとめて読み込み、eBayの相場と自分の過去の販売実績から
**上限入札額・利益・利益率**を出して、入札するかどうかを ◎ / ○ / × / 要確認 で判定します。

## 出てくる情報（1出品ごと）

| 項目 | 内容 |
|---|---|
| ヤフオク出品リンク | 現在価格・即決価格・入札数・終了日時 |
| eBay販売済みリンク | 最近終了した順。取得できれば販売済み価格の中央値と件数 |
| eBay出品中リンク | 安い順。取得できれば最安値と件数（競合の数） |
| eBay Product Research | Best Offer込みの実成約を確認する用 |
| 想定販売価格（USD） | 手入力 ＞ eBay販売済み ＞ 自分の販売実績 の優先順で決定 |
| 仕入価格 | 現在価格＋国内送料 |
| eBay手数料・国際送料 | `config.toml` の率で計算 |
| 利益・利益率 | 今の価格で落札した場合 |
| **上限入札額** | この金額で落札しても基準（既定: 利益5,000円以上かつ利益率15%以上）を満たす最大額。ヤフオクの入札単位で切り捨て |
| 注意 | タイトルから「ジャンク」「光学難あり」「セット品」などを検出 |

## 使い方（Python 3.11以上・追加インストール不要）

```bash
cd yahoo_bid
# 1) URL一覧で実行（ヤフオクとeBayから自動取得）
python bid_analyzer.py samples/bookmarks.txt --sales my_sales.csv

# 2) 通信せずに計算だけ（CSVに価格を手入力した場合）
python bid_analyzer.py samples/items.csv --sales samples/sales_history.csv --offline

# テスト
python -m unittest test_bid_analyzer
```

`out/` に `bid_report_日時.html`（スマホで見やすいカード表示）、`.md`（Claudeに貼り付けて相談する用）、`.csv` が出ます。

### 入力ファイル

- **URL一覧（.txt）**: ヤフオクのURLを1行に1つ。
- **ブックマークHTML**: ブラウザの「ブックマークをエクスポート」したファイルをそのまま指定。ヤフオクの出品リンクだけを拾います。
- **ウォッチリストを保存したHTML**: ヤフオクのウォッチリスト画面を「名前を付けて保存」したものも可。
- **CSV**: `samples/items.csv` の列。空欄は自動取得、入力した値はそちらが優先されます。
  - `ebay_query`: eBay検索語（自動生成がずれるとき）
  - `ebay_price_usd`: 想定販売価格（自分でProduct Researchを見て決めたとき）
  - `ship_size`: `small` / `medium` / `large`（国際送料の目安を切り替え）
  - `intl_shipping_jpy`: 国際送料を個別指定

### 過去の販売実績（--sales）

- eBay Seller Hub → 注文 → **注文レポートをダウンロード** したCSVをそのまま読めます（`Item Title` / `Sold For` / `Item Number` 列）。
- 自作するなら `samples/sales_history.csv` の形式（`title, ebay_query, sold_price_usd, shipping_charged_usd, purchase_jpy, ebay_url`）。

## 設定（config.toml）

為替（自動取得できなければ `usd_jpy`）、eBay手数料率、広告率、国際送料、判定基準（最低利益額・利益率・想定価格に使う分位）を変更できます。
消費税還付を利益に含める場合は `include_consumption_tax_refund = true`。

## 注意

- ヤフオク・eBayのページ構造が変わると自動取得が失敗することがあります。その場合はレポートの「メモ」に失敗が出るので、CSVに手入力してください。
- アクセスは1件ごとに1.5秒空けています（`--delay`）。大量に連続で実行しないでください。
- eBayの販売済み検索の価格はBest Offerの成約額を反映しません。最終判断はProduct Researchで確認してください。
