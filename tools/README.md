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

## データを更新したとき
```bash
python3 tools/build_reference_prices.py   # data/ledger.csv（古物台帳）+ data/hanpukai_*.xlsx → data/reference_prices.csv
python3 tools/build_extension.py          # → extension/（chrome://extensions で ↻）
python3 tools/build_knowledge.py          # → knowledge/相場/・knowledge/機種/
python3 -m unittest discover -s tools     # テスト
```

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
