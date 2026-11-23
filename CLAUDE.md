# aurora ― カメラ輸出事業の作業フォルダ

ヤフオク・頒布会・店舗などで中古カメラ・レンズを仕入れ、eBay で販売する事業のための作業場所。
このプロジェクトの位置付けは**事業全体の管理**（古物台帳・相場データ・仕入判定・eBay API 連携・数字の管理など）。単体のアプリ作りではない。
GitHub 上のリポジトリ名は `aurora-project`（**公開**）。事業の詳細・方針・目標は非公開の `knowledge/` にあり、下で読み込む。

@knowledge/CLAUDE.md

## ルール・手順書・担当者
- 細かいルール: `.claude/rules/`（毎回自動で読み込まれる）
- 手順書: `.claude/skills/`（入札候補の選定・相場調査・相場の登録・仕入れの台帳入力・講座資料の取り込み・データ更新・Chatwork取り込み・月次振り返り・ナレッジページ更新・届いた後の検品・輸出許可通知書）
- 担当者: `.claude/agents/`（入札担当・相場調査担当・出品改善担当・数字管理担当・**オーキド博士＝サトシの相談役・サブマネージャー**（ほかの担当と同じく別のセッション）。ユーザーに依頼・質問・提案・金額・画面を出す前に相談する。範囲は `コミュニケーション.md`）
- 最重要: `data/`・`knowledge/`・`reports/`・`extension/` は**コミットしない**（`.claude/rules/情報管理.md`）。

## 担当のセッションの探し方（新しい会話から）
新しい会話で始めても、担当は同じセッションのまま。`list_sessions`（タグ `aurora-tantou`、archived でないもの）でタイトルから探し、`send_message` で話す:
相談役（オーキド博士）・リサーチ担当（ゼニガメ）・出品担当（ピカチュウ）・入庫担当（フシギダネ）・カスタマー担当（ゴース）・マーケティング担当（ヒトカゲ）・経理担当（イーブイ）・毎朝の報告（サトシ）。
定期作業はそれぞれのセッションに結び付いているので、新しい会話を作っても止まらない。
オーナーと話すサトシのセッションはタイトル「サトシ」（古い物は「サトシ（〜日付・引き継ぎ済み）」）。「サトシを続ける」と言われたら `.claude/rules/セッション引き継ぎ.md` の順に引き継ぐ。

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
| `appraise.py` | 鑑定の本体。検索URL→出品一覧→相場照合→利益・上限入札額→レポート。一番上に「今日の予約リスト（目標8本）」（◎○・確認不要・終了が今日〜明日を 勝ちやすさ×利益 の順。即決で上限以下は別枠）→ `reports/reserve_list_*.csv` → 古物台帳「Mac_予約リスト」 |
| `robots.py`・`yahoo_robots.txt` | ヤフオクの robots.txt で読む前に URL を検査し、Disallow の引数（n=・s1=・o1=・select= など）を外す（7日ごとに robots.txt を取り直して data/ に保存。読めなければ同梱の写し）。appraise.py・win_score.py・拡張機能の［続けて鑑定］が使う |
| `win_score.py` | **勝ちやすさ**: 入札リストの ◎○・売れ筋・負けた機種について、ヤフオクの終わった出品（`/closedsearch/closedsearch?p=`、Allow の形）を読み、今の判定ロジックの上限入札額以下で終わった割合・月の件数を `data/win_score.csv` に（1回30機種・1機種2ページ・3秒間隔・同じ機種は7日ごと）。1割未満の機種は入札リスト・拡張機能の下に回す（判定・上限額は変えない） |
| `extension_template.js` | Chrome拡張機能の中身（判定は appraise.py と同一） |
| `extension_evidence.js` | **ヤフオクの仕入れ証跡**: Chrome で取引ナビを開くと、その画面を PNG で ダウンロード/ヤフオク仕入証跡/ に自動保存（名前は 終了日_出品者_金額_オークションID。同じIDは1回だけ）。`update_all.py` が毎朝 デスクトップ › 税理 › 8.〜 › ヤフオク に移す |
| `build_extension.py` | data/ と config.toml を埋め込んで `extension/` を作る |
| `build_reference_prices.py` | 古物台帳（data/ledger.csv）と頒布会リスト（data/hanpukai_*.xlsx）→ `data/reference_prices.csv`。台帳に豪ドル・ポンドのまま記入された販売金額は、eBay の取引レポート（data/ebay_report.csv）の換算レートでドルに直す |
| `update_all.py` | **全部まとめて最新にする**: ダウンロードフォルダの古物台帳CSV・頒布会xlsx・明細の取り込み → Chatwork 同期 → 相場データ・拡張機能・スマホ用データ・ナレッジを作り直す |
| `ledger_sync.py` | 古物台帳をスプレッドシートから直接 `data/ledger.csv` に、管理シートの No.・状態・購入日・販売日を `data/kanri.csv` に取り込む。古物台帳の「意思決定ログ」シート（Claude がクラウドから書く）を `knowledge/意思決定ログ.md` に追記し、入札の記録（data/bid_log.csv）を「入札結果」シートに送り、以前の入札分析シートの入札履歴（「共有_過去の入札」）を data/bid_history.csv に、「共有_相場メモ」（チャットで決めた相場）を data/price_memo.csv に、「共有_ナレッジ」を knowledge/講座/ に取り込み、毎朝の入札リストで相場が無かった機種を「Mac_未登録機種」に送る（夜にクラウドが下調べ。店舗アプリで調べた物（自動）と［購入］は店舗用の鍵で「共有_店舗」に足される。Apps Script の合言葉付き URL。初回 `setup`、スクリプト更新時 `script`、1冊運用への切り替え `single`。1冊運用では元の台帳の「古物台帳」→「古物台帳_詳細」と「写し_〜」を同じ台帳に作る） |
| `store_publish.py` | スマホ用データを暗号化してシークレット Gist に配信 → iPhone のアプリが開くたびに自動更新（初回 `setup`） |
| `schedule.py` | `update_all.py` を毎朝＋ログイン時に自動実行（Mac の launchd。`install`・`status`・`uninstall`）。`install-messages` で eBay の受信メッセージの見出し（返信済みか）を1時間ごとに古物台帳「Mac_メッセージ」へ（`ebay_api.py messages --upload`。Claude は使わない） |
| `keychain.py` | トークン・合言葉を Mac のキーチェーンに保存する共通部品 |
| `chatwork_sync.py` | Chatwork の販売報告（日報チャット）を公式 API で `data/chatwork_sales.csv` に、添付の頒布会リスト（xlsx）を `data/hanpukai_*.xlsx` に取り込む（初回 `setup`） |
| `build_store.py` | 店舗アプリ用: `store/core.js`（拡張機能の判定のコピー）と `data/store_data.json`（相場＋おすすめ機種。iCloud Drive にもコピー）を作る |
| `build_knowledge.py` | data/ から `knowledge/相場/`・`knowledge/機種/` を自動生成 |
| `build_knowledge_page.py`・`knowledge_page_template.html` | knowledge_share.md → Artifacts の読むだけのナレッジページ（ニューロン付き） |
| `build_dashboard.py`・`dashboard_template.html` | eBay API の注文・取引明細・出品中一覧＋古物台帳・管理シート・発送明細 → 数字のグラフ `reports/dashboard.html`（月別売上・利益率・状態の割合など。update_all.py が毎回作り、中身を古物台帳の「Mac_グラフ」シートに送る → Artifacts の「aurora 数字のグラフ」が開くたびに読む）。2026/8/17 以前の注文は SKU が No. と合っていないので `data/sku_fix.csv` の対応表で直す（候補は `--sku-candidates`） |
| `yahoo_results.py` | **落札相場**: 毎朝の入札リスト・拡張機能（ダウンロード/aurora_seen.csv）で ◎○・仮◎○・確認してから に出た出品を見張り、終了後にヤフオクの出品ページで最終価格・入札数を `data/yahoo_results.csv` に記録（流れた出品も。1回 300件・1.5秒間隔）→ 拡張機能に機種ごとの落札額の中央値を表示・古物台帳「Mac_落札相場」へ。判定・上限額には使わない |
| `export_permit.py` | 輸出許可通知書: FedEx の依頼用 Excel に、まだもらっていない運送状番号を自動で入れる → `reports/fedex_export_permit_*.xlsx`。`--dhl` で DHL のフォームに貼る番号の一覧（手順は `.claude/skills/export-permit`） |
| `bid_results.py` | 入札の結果: ［入札した］の記録の落札率・負けた差。落札かどうかは古物台帳にそのオークションIDがあるかで決める（無ければ終了から10日は「確認待ち」→「負け」） |
| `ebay_listing_template.html` | eBayStock の説明文テンプレートのデザイン案（濃いネイビー×ゴールド。2026-11-22）。**まだ使っていない**（オーナー「まだ実装しないが、いつか変更するかもしれないので覚えておいて」）。今 eBayStock で使っているのは `ebay_listing_template_current.html`。差し込み・分岐は同じ。英語の文は、上の店名の行と下の「Shipped from Japan」を足した以外は同じ |
| `make_icons.py` | 拡張機能のアイコン（tools/icons/） |
| `config.toml` | 手数料・送料・為替・判定基準・ジャンク評価率 |
| `test_*.py` | テスト（Python と拡張機能・店舗アプリの判定一致も確認） |

## よく使うコマンド（aurora フォルダで実行）
```bash
python3 tools/update_all.py                  # ★全部まとめて最新に（schedule.py install 済みなら毎朝自動）
python3 tools/appraise.py                    # data/search_urls.txt の検索URLを鑑定 → reports/
python3 tools/build_reference_prices.py      # 古物台帳・頒布会 → data/reference_prices.csv
python3 tools/build_extension.py             # 拡張機能を作り直す → chrome://extensions で ↻
python3 tools/build_store.py                 # 店舗アプリのデータ → iCloud Drive › aurora › store_data.json
python3 tools/build_knowledge.py             # 相場ノート・機種ノートを更新
python3 tools/build_dashboard.py             # 数字のグラフ → reports/dashboard.html を開く
python3 tools/win_score.py --max-models 5    # 勝ちやすさ（ふだんは update_all.py が毎朝）
python3 -m unittest discover -s tools        # テスト
```

## Mac での場所
- 作業フォルダ: `~/aurora`
- Chrome拡張機能: `~/aurora/extension`
- Obsidian の保管庫: `~/aurora/knowledge`

## 環境メモ
- **Mac の python3 は 3.9**（2026-11-20 に f-string の書き方で止まった）。tomllib が無い（`appraise._parse_simple_toml` で読む）、f-string の `{}` の中で外と同じ引用符を使えない、`X | None` の型は `from __future__ import annotations` が要る。直したら `uv python install 3.9` の Python 3.9 でもテストを通す（`$(uv python find 3.9) -m unittest discover -s tools`）。
- クラウド環境からは eBay がアクセスを拒否する（403）。eBay の相場はリンクを出すか、ユーザーのMacで取得する。
- ヤフオクの検索結果ページは `__NEXT_DATA__`（JSON）に1ページ分（既定50件）の価格・入札数・送料・終了時刻が入っている。
- ヤフオクは robots.txt の Allow の形だけ読む（2026-11-19 ユーザー決定）。`/search/`・`/seller/` の n=・s1=・o1= などは付けない（`tools/robots.py`）。ページ送り b= は可。`/closedsearch/closedsearch?p=` は Allow。
