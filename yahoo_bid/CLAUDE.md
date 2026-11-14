# ヤフオク→eBay カメラ輸出の入札判断ツール

このフォルダは、ヤフオク等で中古カメラ・レンズを仕入れて eBay で販売する事業のための作業場所。
事業の詳細・方針・目標は非公開のナレッジ `knowledge/` にあり、下で読み込む。

@knowledge/CLAUDE.md

## 作業のルール
細かいルールは `.claude/rules/`（毎回自動で読み込まれる）、手順書は `.claude/skills/`、担当者は `.claude/agents/`。
- 最重要: リポジトリは**公開**。`data/`・`knowledge/`・`out/` はコミットしない（`.claude/rules/情報管理.md`）。
- 判定ロジックは Python と拡張機能の2か所。必ず両方直してテストする（`.claude/rules/判定ロジック.md`）。
- 学んだこと・決めたことは `knowledge/` に追記する。

## Mac での置き場所
すべて `~/aurora/` にある（このフォルダ＝`~/aurora/yahoo_bid`）。
- Chrome拡張機能: `~/aurora/yahoo_bid/out/chrome_extension`
- Obsidian の保管庫: `~/aurora/yahoo_bid/knowledge`

## ファイル構成
| ファイル | 役割 |
|---|---|
| `bid_analyzer.py` | 本体。ヤフオク検索URL→出品一覧→相場照合→利益・上限入札額→HTML/CSV/MDレポート |
| `userscript_template.js` | Chrome拡張機能／Safariユーザースクリプトの中身（判定は bid_analyzer.py と同一） |
| `build_userscript.py` | data/ と config.toml を埋め込んで拡張機能を生成（`--chrome`） |
| `build_sales.py` | 古物台帳CSV・頒布会xlsx → `data/sales.csv` |
| `build_knowledge.py` | data/ から `knowledge/相場/`・`knowledge/機種/` のノートを自動生成 |
| `config.toml` | 手数料・送料・為替・判定基準・ジャンク評価率 |
| `data/` | 非公開データ（sales.csv, chatwork_sales.csv, price_memo.csv, 鑑定表, 頒布会xlsx） |
| `knowledge/` | 非公開ナレッジ（Obsidian の vault としても開ける） |

## よく使うコマンド
```bash
python3 bid_analyzer.py 検索URL.txt            # 検索URLをまとめて鑑定 → out/
python3 build_userscript.py --chrome            # 拡張機能を作り直す → chrome://extensions で ↻
python3 build_sales.py --ledger data/古物台帳.csv --hanpu data/頒布会_*.xlsx -o data/sales.csv
python3 build_knowledge.py                      # 相場ノート・機種ノートを更新
python3 -m unittest test_bid_analyzer           # テスト（JSとPythonの判定一致も確認）
```

## 環境メモ
- クラウド環境からは eBay がアクセスを拒否する（403）。eBay の相場はリンクを出すか、ユーザーのMacで取得する。
- ヤフオクの検索結果ページは `__NEXT_DATA__`（JSON）に1ページ100件分の価格・入札数・送料・終了時刻が入っている。
