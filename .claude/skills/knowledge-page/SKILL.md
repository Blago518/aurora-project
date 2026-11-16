---
name: knowledge-page
description: Mac の knowledge/ をまとめた knowledge_share.md から、Artifacts の読むだけのナレッジページ「aurora ナレッジ」（ノート＋ニューロン）を作り直す手順。knowledge_share.md が送られてきたとき、「ナレッジ更新」「ナレッジページを更新して」と言われたら使う。
---
# ナレッジページの更新

ページは Artifacts の「aurora ナレッジ」（非公開）。**新しく作らず、同じリンクを更新する。**

## ユーザーにしてもらうこと（足りなければ、この形で頼む）
Mac のターミナルで1回実行し、デスクトップにできた `knowledge_share.md` をチャットにドラッグしてもらう。
```bash
cd ~/aurora/knowledge && find . -name '*.md' -not -path './相場/*' -not -path './機種/*' -not -path './仕入先/*' -not -path './データ/*' -not -path './.obsidian/*' -not -name 'CLAUDE.md' | sort | while read f; do echo "=== $f"; cat "$f"; done > ~/Desktop/knowledge_share.md && open -R ~/Desktop/knowledge_share.md
```
ターミナルの表示をコピーすると中身が上書きされるので、コピペではなくファイルを添付してもらう。

## 手順
1. 添付の `knowledge_share.md` を全部読む。
2. **載せてはいけないものを消した写し**をスクラッチパッドに作る（元の文の意味は変えない）。
   - 金額の実績（売上・利益・発送費・仕入値・請求額）、目標の金額、件数の実績、在庫日数の実例
   - 仕入先・取引先の名前、人の名前、商品の管理番号、注文番号、スプレッドシートID
   - 事業プロフィールの中身（副業歴など）
   - 方針の数値（利益2,500円・10%・手数料率・$30 など）とツールの使い方は残してよい
   - 新しいフォルダのノートが入っていたら、数字が多いものは載せる前にユーザーに確認する
3. ページを作る:
   ```bash
   python3 tools/build_knowledge_page.py <写し.md> -o <スクラッチパッド>/aurora-knowledge.html
   ```
4. Artifact の `action: "list"` で「aurora ナレッジ」の URL を探す → `action: "read"` で読む → その `url` を付けて publish（description は前と同じ趣旨で、icon は付けない）。見つからないときだけユーザーに URL を聞く。
5. 報告（表で短く）: 増えた・変わったノート、消した情報の種類、ニューロンのノート数・リンク数、リンク。

## 注意
- このファイルと `tools/` は公開リポジトリに入る。ページの URL や事業の数字をここに書かない。
- ページの見た目を変えたら `tools/knowledge_page_template.html` も直してコミットする（次の更新で戻らないように）。
