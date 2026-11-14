# aurora ― カメラ輸出事業の作業フォルダ

ヤフオク・頒布会・店舗などで中古カメラ・レンズを仕入れ、eBay で販売する事業のための作業場所。
このプロジェクトの位置付けは**事業全体の管理**（古物台帳・相場データ・仕入判定・eBay API 連携・数字の管理など）。単体のアプリ作りではない。
GitHub 上のリポジトリ名は `aurora-project`（**公開**）。事業の詳細・方針・目標は非公開の `knowledge/` にあり、下で読み込む。

@knowledge/CLAUDE.md

## ルール・手順書・担当者
- 細かいルール: `.claude/rules/`（毎回自動で読み込まれる）
- 手順書: `.claude/skills/`（入札候補の選定・相場調査・データ更新・Chatwork取り込み・月次振り返り・ナレッジページ更新）
- 担当者: `.claude/agents/`（入札担当・相場調査担当・出品改善担当・数字管理担当）
- 最重要: `data/`・`knowledge/`・`reports/`・`extension/` は**コミットしない**（`.claude/rules/情報管理.md`）。

## フォルダ構成
| 場所 | 中身 | 公開 |
|---|---|---|
| `tools/` | プログラム（下の表） | する |
| `data/` | 販売実績・頒布会リスト・Chatwork実績・相場メモ・検索URL | **しない** |
| `knowledge/` | 事業ナレッジ（Obsidian の保管庫） | **しない** |
| `extension/` | 生成した Chrome拡張機能（相場データ入り） | **しない** |
| `reports/` | 鑑定レポート（HTML/CSV/MD） | **しない** |
| `.claude/` | ルール・手順書・担当者 | する（事業の数字は書かない） |
| `store/` | 店舗仕入れ用スマホアプリ（GitHub Pages で公開。iPhone のホーム画面から使うので移動しない。データは入れない） | する |
| `ebay-auth.html`・`privacy.html` | eBay API の許可後の戻り先とプライバシーポリシー（GitHub Pages で公開。eBay に URL を登録済みなので移動・削除しない） | する |

## プログラム（tools/）
| ファイル | 役割 |
|---|---|
| `appraise.py` | 鑑定の本体。検索URL→出品一覧→相場照合→利益・上限入札額→レポート |
| `extension_template.js` | Chrome拡張機能の中身（判定は appraise.py と同一） |
| `build_extension.py` | data/ と config.toml を埋め込んで `extension/` を作る |
| `build_reference_prices.py` | 古物台帳（data/ledger.csv）と頒布会リスト（data/hanpukai_*.xlsx）→ `data/reference_prices.csv` |
| `build_store.py` | 店舗アプリ用: `store/core.js`（拡張機能の判定のコピー）と `data/store_data.json`（相場＋おすすめ機種。iCloud Drive にもコピー）を作る |
| `build_knowledge.py` | data/ から `knowledge/相場/`・`knowledge/機種/` を自動生成 |
| `build_knowledge_page.py`・`knowledge_page_template.html` | knowledge_share.md → Artifacts の読むだけのナレッジページ（ニューロン付き） |
| `make_icons.py` | 拡張機能のアイコン（tools/icons/） |
| `config.toml` | 手数料・送料・為替・判定基準・ジャンク評価率 |
| `test_appraise.py`・`test_store.py` | テスト（Python と拡張機能・店舗アプリの判定一致も確認） |

## よく使うコマンド（aurora フォルダで実行）
```bash
python3 tools/appraise.py                    # data/search_urls.txt の検索URLを鑑定 → reports/
python3 tools/build_reference_prices.py      # 古物台帳・頒布会 → data/reference_prices.csv
python3 tools/build_extension.py             # 拡張機能を作り直す → chrome://extensions で ↻
python3 tools/build_store.py                 # 店舗アプリのデータ → iCloud Drive › aurora › store_data.json
python3 tools/build_knowledge.py             # 相場ノート・機種ノートを更新
python3 -m unittest discover -s tools        # テスト
```

## Mac での場所
- 作業フォルダ: `~/aurora`
- Chrome拡張機能: `~/aurora/extension`
- Obsidian の保管庫: `~/aurora/knowledge`

## 環境メモ
- クラウド環境からは eBay がアクセスを拒否する（403）。eBay の相場はリンクを出すか、ユーザーのMacで取得する。
- ヤフオクの検索結果ページは `__NEXT_DATA__`（JSON）に1ページ100件分の価格・入札数・送料・終了時刻が入っている。
