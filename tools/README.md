# tools ― 仕入れ鑑定ツール

全体の説明は一つ上の `CLAUDE.md`。ここはプログラムの使い方。コマンドはすべて `~/aurora` で実行する。

## 毎日の使い方
1. Chrome でヤフオクの検索結果を開く → 右上の「入札鑑定」パネルで ◎○ を確認（拡張機能）
2. eBay相場の金額（$）をクリック → eBay販売済み（高い順）、横の now → 出品中（高い順）
3. 上限入札額まで入札

まとめてレポートにしたいとき:
```bash
python3 tools/appraise.py          # data/search_urls.txt の検索URLを全部鑑定 → reports/
open reports
```

## 拡張機能で相場を直したとき
各行の［直す］（送料込み$を入れる。$40引いて計算）や［登録］を押すと、その相場メモが `ダウンロード/aurora_price_memo.csv` に自動保存される。
`build_store.py`・`build_extension.py`・`appraise.py` を実行すると `data/price_memo.csv` に自動で取り込まれ（同じ機種は金額を更新）、スマホにも反映される。直した相場は同じ機種の過去データより優先される。

## 自動化（初回だけ設定。以後は何もしなくてよい）
| 何を | 初回の設定 | 以後 |
|---|---|---|
| Chatwork の販売報告 | `python3 tools/chatwork_sync.py setup`（API トークンと日報チャットの URL） | 自動で取り込み |
| 頒布会リスト（Excel） | なし | ダウンロードフォルダに保存すれば、次の自動更新で取り込み（ファイル名は何でもよい） |
| 古物台帳（スプレッドシート） | `python3 tools/ledger_sync.py setup`（スクリプトを貼って公開） | 自動で取り込み |
| スマホのデータ | `python3 tools/store_publish.py setup`（GitHub のトークン）→ 設定コードを iPhone のアプリに貼る | アプリを開くと最新 |
| 全部の更新 | `python3 tools/schedule.py install` | 毎朝 7:03 と Mac のログイン時に自動。終わると通知 |

拡張機能は、更新後に `chrome://extensions` の ↻ を押すと新しい相場になる。

## データを更新したとき（コマンド1つ）
```bash
python3 tools/update_all.py
```
- 古物台帳: スプレッドシートの「古物台帳」シートを「ファイル → ダウンロード → CSV」でダウンロードフォルダへ
- 頒布会: 商品リストの xlsx をダウンロードフォルダへ
- Chatwork: 日報チャットの販売報告を公式 API で自動取り込み（初回だけ `python3 tools/chatwork_sync.py setup`）
- 拡張機能で直した相場・CPaSS/オークレボの明細も自動で取り込み、拡張機能・スマホ用データ・ナレッジまで作り直す

## 個別に作り直すとき
```bash
python3 tools/build_reference_prices.py   # data/ledger.csv（古物台帳）+ data/hanpukai_*.xlsx → data/reference_prices.csv
python3 tools/build_extension.py          # → extension/（chrome://extensions で ↻）
python3 tools/build_store.py              # → 店舗アプリのデータ（iCloud Drive › aurora › store_data.json）
python3 tools/build_knowledge.py          # → knowledge/相場/・knowledge/機種/
python3 tools/build_dashboard.py          # → reports/dashboard.html（数字のグラフ）
python3 -m unittest discover -s tools     # テスト
```

## 店舗リサーチ（iPhone アプリ）
`store/`（GitHub Pages: https://blago518.github.io/aurora-project/store/ ）。お店で使う。
- 調べる: 商品名（または📷で値札を撮影）→ 判定・「この値段以下なら買い（税込）」・eBay 販売済み／出品中（高い順）／Product Research
- おすすめ: メーカー＋カメラ/レンズ → 過去の平均利益×販売回数の上位20機種
- 判定は拡張機能と同じ（`store/core.js` は `extension_template.js` の `@core-start`〜`@core-end` のコピー。直接編集しない）
- 店頭は国内送料0円・値札は税込の支払額として計算
- 値札読み取りは Claude API（Haiku 5.5）。APIキーと相場データは iPhone の中だけに保存

初回: Safari で上のURL → 共有 → ホーム画面に追加 → 設定タブで store_data.json と APIキーを入れる。
データ更新: `python3 tools/build_store.py` → アプリの設定タブでファイルを選び直す。

## Chrome拡張機能を入れる（初回）
1. `chrome://extensions` → 右上「デベロッパー モード」をオン
2. 「パッケージ化されていない拡張機能を読み込む」→ `~/aurora/extension` を選ぶ

## 主なオプション（appraise.py）
| オプション | 意味 |
|---|---|
| `--max-per-search 500` | 検索URL1本あたりの件数上限 |
| `--offline` | 通信しない（手入力CSVで計算） |
| `--fx 155` | 為替を指定 |

## データの形式（data/）
| ファイル | 中身 |
|---|---|
| `search_urls.txt` | ヤフオクの検索結果URL（1行1本） |
| `ledger.csv` | 古物台帳（スプレッドシートの「古物台帳」シートをCSVで保存） |
| `hanpukai_YYYY-MM.xlsx` | 頒布会の商品リスト |
| `chatwork_sales.csv` | Chatworkの販売報告（date,title,ebay_query,sold_price_usd,shipping_charged_usd,purchase_jpy,ebay_url,source） |
| `price_memo.csv` | 自分で調べた相場（title,sold_price_usd,source,memo） |
| `kantei_bookmarklet.docx` | 旧ブックマークレットの相場表 |
| `reference_prices.csv` | 自動生成（古物台帳＋頒布会） |

設定（手数料・送料・判定基準・ジャンク評価率）は `tools/config.toml`。
