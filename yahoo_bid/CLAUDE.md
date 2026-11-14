# ヤフオク→eBay カメラ輸出の入札判断ツール

このフォルダは、ヤフオク等で中古カメラ・レンズを仕入れて eBay で販売する事業のための作業場所。
事業の詳細・方針・目標は非公開のナレッジ `knowledge/` にあり、下で読み込む。

@knowledge/CLAUDE.md

## 作業のルール（必ず守る）
- **日本語で、専門用語は噛み砕いて説明する。** ユーザーは副業で始めて1年未満。コードは主に Python。
- **言われなくても改善する。** 使いやすさ・効率の改善点に気づいたら、その場で実施するか提案する。
  例: リンクは文字ではなく関連する数字や名前に付ける、長いものは短く、頻繁な操作は1クリックに。
- **利益計算は保守的に。** 相場は弱気に見積もり、赤字仕入れを防ぐことを優先する。
  根拠の弱い相場（推定・頒布会の出品価格・ジャンク）はそう明示する。
- **個人情報・取引情報を公開しない。** GitHub リポジトリ（aurora-research）は**公開**。
  `data/`（販売実績・仕入値）、`knowledge/`（事業ナレッジ）、`out/`（レポート）は .gitignore 済みで、絶対にコミットしない。
  コミット前に氏名・住所・口座・仕入値・販売実績が含まれていないか確認する。
- 判定ロジックは **Python（bid_analyzer.py）と拡張機能（userscript_template.js）の2か所**にある。
  片方を変えたら必ずもう片方も同じに直し、`python3 -m unittest test_bid_analyzer` の一致テストを通す。
- 学んだこと・決めたことは `knowledge/` の該当ノートと `knowledge/意思決定ログ.md` に追記する（書き方は knowledge/CLAUDE.md）。

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
