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
| 想定販売価格（USD） | 手入力 ＞ eBay販売済み ＞ 自分の販売実績 ＞ 頒布会リスト の優先順で決定。送料$30を引いた Net 販売価格も表示 |
| 仕入価格 | 現在価格＋国内送料 |
| eBay手数料・国際送料 | `config.toml` の率で計算 |
| 利益・利益率 | 今の価格で落札した場合 |
| **上限入札額** | この金額で落札しても基準（既定: 利益5,000円以上かつ利益率15%以上）を満たす最大額。ヤフオクの入札単位で切り捨て |
| 注意 | タイトルから「ジャンク」「光学難あり」「セット品」などを検出 |

## ナレッジ（knowledge/ ・ 非公開）

事業の方針・相場・仕入先・意思決定を Markdown ノートで蓄積し、`[[リンク]]` でつないでいます。
無料アプリ [Obsidian](https://obsidian.md) で `knowledge/` フォルダを「保管庫（vault）として開く」と、リンクやグラフで見られます。
Claude Code はこのフォルダの `CLAUDE.md` を毎回読み、方針に沿って作業・追記します。

```bash
python3 build_knowledge.py   # data/ から 相場/・機種/ のノートを自動生成（手書きノートは上書きしない）
```

## Chromeで自動鑑定（自作の拡張機能）― いちばん安全

第三者のアプリを使わず、このフォルダで作った拡張機能だけを Chrome に入れます。
ヤフオクのページ（auctions.yahoo.co.jp の検索・出品者・カテゴリ）でだけ動き、外部への通信はしません。

```bash
python3 build_userscript.py --chrome   # → out/chrome_extension/
```

1. Chrome で `chrome://extensions` を開き、右上の「デベロッパー モード」をオン
2. 「パッケージ化されていない拡張機能を読み込む」→ `out/chrome_extension` フォルダを選ぶ
3. ヤフオクの検索結果を開くと右上に鑑定パネルが出る
4. データを更新したら同じコマンドで作り直し、拡張機能の「更新（↻）」を押す

## Safariで自動鑑定（ユーザースクリプト）

ブックマークレットの代わりに、Safari拡張「Userscripts」（無料・App Store）で動くスクリプトを使います。
長さの制限が無く、ヤフオクの検索結果・出品者ページを開くだけで右上に鑑定パネルが出ます（クリック不要）。

### 初回だけ
1. App Store で「Userscripts」（開発: Justin Wasack）をインストールし、アプリを開いて保存フォルダを決める（例: 書類/Userscripts）
2. Safari → 設定 → 機能拡張 → Userscripts をオン。auctions.yahoo.co.jp を「常に許可」
3. スクリプトを作ってそのフォルダに置く

```bash
python3 build_userscript.py -o ~/Documents/Userscripts/yahoo_kantei.user.js
```

### 使い方
- 検索結果を開くとそのページ（100件）を自動で鑑定。「全ページ鑑定」で全件（1,500件で約30秒）
- 入札候補は終了が近い順。各出品の横にも「◎ 上限6,500円」のバッジが付く
- 「相場が未登録の機種」に想定$を入れて「登録」→ その場で再鑑定（このSafariに保存）
- 「登録した相場メモをコピー」で書き出し、`data/price_memo.csv` に貼るか Claude に送ると全端末のデータに取り込める
- データ（販売実績・相場メモ等）や手数料設定を変えたら、上のコマンドで作り直して上書き
- iPhone の Safari でも同じアプリで使えます

## いちばん速い使い方（検索URLをまとめて鑑定）

1. `検索URL.txt` に、Safariで普段使っているヤフオクの検索結果URL（キーワード検索・ストアのカテゴリ絞り込み等）を1行に1つ貼る
2. 実行する（販売実績・Chatwork・頒布会・鑑定表は `data/` から自動で読み込みます）

```bash
python3 bid_analyzer.py 検索URL.txt
```

- 検索結果は100件ずつまとめて読み込むので、1,500件でも1分ほどで終わります（出品ページを1件ずつ開きません）。
- レポートの「入札候補」は ◎○ を**終了が近い順**に並べています。上限入札額まで入札してください。
- 「相場が未登録の機種」は件数の多い順です。上位から eBay の販売済みを見て想定販売価格（送料込み$）を `unpriced_models_*.csv` の `sold_price_usd` に入れ、その行を `data/price_memo.csv` に追記すると、次回からその機種が判定されます。
- 相場が無い機種は、上位40機種まで eBay の販売済み検索を自動で見に行き相場を作ります（`--ebay-auto`、7日間キャッシュ）。eBay がアクセスを拒否した場合は自動で中止します。
- 鑑定ブックマークレットの相場表は `data/kantei_bookmarklet.*`（.txt / .docx）に置くと参考に使います。

## 使い方（Python 3.9以上・追加インストール不要。Macでは `python3` コマンドを使う）

```bash
cd yahoo_bid
# 1) URL一覧で実行（ヤフオクとeBayから自動取得）
python3 bid_analyzer.py samples/bookmarks.txt --sales my_sales.csv

# 2) 通信せずに計算だけ（CSVに価格を手入力した場合）
python3 bid_analyzer.py samples/items.csv --sales samples/sales_history.csv --offline

# テスト
python3 -m unittest test_bid_analyzer
```

`out/` に `bid_report_日時.html`（スマホで見やすいカード表示）、`.md`（Claudeに貼り付けて相談する用）、`.csv` が出ます。

### 入力ファイル

- **ヤフオクの検索結果URL（おすすめ）**: Safariのブックマークに保存している検索条件のURLをそのまま1行に1つ貼ります。
  検索結果の上から30件（`--max-per-search` で変更）を自動で取り出して判定します。落札相場（closedsearch）のURLは無視します。

- **URL一覧（.txt）**: ヤフオクのURLを1行に1つ。
- **ブックマークHTML**: ブラウザの「ブックマークをエクスポート」したファイルをそのまま指定。ヤフオクの出品リンクだけを拾います。
- **ウォッチリストを保存したHTML**: ヤフオクのウォッチリスト画面を「名前を付けて保存」したものも可。
- **CSV**: `samples/items.csv` の列。空欄は自動取得、入力した値はそちらが優先されます。
  - `ebay_query`: eBay検索語（自動生成がずれるとき）
  - `ebay_price_usd`: 想定販売価格（自分でProduct Researchを見て決めたとき）
  - `ship_size`: `small` / `medium` / `large`（国際送料の目安を切り替え）
  - `intl_shipping_jpy`: 国際送料を個別指定

### 過去の販売実績をまとめる（build_sales.py）

古物台帳と頒布会の商品リストから、照合用の `sales.csv` を作ります。

1. Googleスプレッドシート「古物台帳_推奨価格自動更新」の **古物台帳** シートを開き、ファイル → ダウンロード → CSV で保存
2. 頒布会の商品リスト（xlsx）と一緒に `data/` フォルダに置いて実行

```bash
python3 build_sales.py --ledger data/古物台帳.csv --hanpu data/頒布会_2026_09.xlsx data/頒布会_2026_10.xlsx -o data/sales.csv
python3 bid_analyzer.py 検索URL.txt --sales data/sales.csv data/chatwork_sales.csv
```

- 古物台帳: 販売日が入っている行だけを「自分の販売実績」として使います（販売金額$は送料込み）。
- 頒布会リスト: 販売金額($)を参考相場として使います。
- Chatworkの販売報告: `data/chatwork_sales.csv` に同じ形式で追記します（source列は `Chatwork販売実績`）。
- 優先順位: 自分の販売実績 → Chatwork販売実績 → 頒布会リスト。
- 照合ルール: 型番・焦点距離・F値など数字を含む語が一致すること。付属品だけの実績や、チタン・ゴールド・限定・AF/MFの違いは別物として除外。
- `data/` フォルダは個人データなので Git には含めません。

### 過去の販売実績（--sales）

- eBay Seller Hub → 注文 → **注文レポートをダウンロード** したCSVをそのまま読めます（`Item Title` / `Sold For` / `Item Number` 列）。
- 自作するなら `samples/sales_history.csv` の形式（`title, ebay_query, sold_price_usd, shipping_charged_usd, purchase_jpy, ebay_url`）。

## 設定（config.toml）

為替（自動取得できなければ `usd_jpy`）、eBay手数料率、広告率、国際送料、判定基準（最低利益額（既定3,000円）・利益率（既定10%）・想定価格に使う分位）を変更できます。
消費税還付を利益に含める場合は `include_consumption_tax_refund = true`。

## 注意

- ヤフオク・eBayのページ構造が変わると自動取得が失敗することがあります。その場合はレポートの「メモ」に失敗が出るので、CSVに手入力してください。
- アクセスは1件ごとに1.5秒空けています（`--delay`）。大量に連続で実行しないでください。
- eBayの販売済み検索の価格はBest Offerの成約額を反映しません。最終判断はProduct Researchで確認してください。
