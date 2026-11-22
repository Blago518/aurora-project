---
name: export-permit
description: DHL・FedEx から輸出許可通知書（輸出許可書）の写しをもらう手順と、FedEx の依頼用 Excel の自動入力。「輸出許可証」「輸出許可通知書」「DHL・FedEx の許可書」と言われたら使う。
---
# 輸出許可通知書を DHL・FedEx からもらう

2026-11-23 オーナー依頼「DHLとfedexから輸出許可証をもらわないといけない」「fedexのexcelはあなたが自動入力して作成するように」。
発送は CPaSS 経由（オークレボから）。

確かめた所（公式サイト。2026-11-23 は公式ページを直接開けず、公式サイトの検索結果の文で確かめた）と、確かめていない所を分けて書く。
確かめていない所は、実際に依頼した後に分かったことで書き直す。

## DHL（公式サイトから申請するだけ）
1. DHL の「輸出入関連書類送付リクエスト受付フォーム」から依頼する（運送状・インボイス・輸出入許可書をここで頼める）。公式の案内: https://www.dhl.com/discover/ja-jp/contact-us
2. 請求書が出た荷物は、DHL MyBill からダウンロードもできる（同じページ）。
3. 分からないとき: jpcqhandling@dhl.com・03-6732-4980（平日 9:00〜17:30）（同じページ）。
- 確かめていない: フォームの URL と入力する項目、料金、届くまでの日数。CPaSS 経由の荷物は、輸出者の名義と依頼に使う番号（DHL の運送状番号か CPaSS の番号か）。

## FedEx（公式サイトで申請 → Excel をメールで送る）
1. FedEx の通関サービスセンターで「輸出許可通知書のリクエスト」を出す: https://www.fedex.com/ja-jp/customs-tools/clearance/customs-services.html
   付帯通関サービス料がかかる場合がある（公式）。依頼前にフォームの注意事項を読む（公式 FAQ）。
2. 件数が多いときは、受付の確認メールに付いた Excel に運送状番号を入れて、FedEx の指定のアドレスに送る。
   - 確かめていない: 何件から Excel か、送り先のアドレス（ブログでは fscjp@fedex.com）、料金、日数。受付メールに書かれた宛先を使う。
3. **Excel はサトシ（プログラム）が埋める**:
   ```bash
   python3 tools/export_permit.py ~/Downloads/<FedEx から届いた Excel>.xlsx
   ```
   - data/statements/ の CPaSS 注文一覧から FedEx の追跡番号を集め、Excel の「運送状番号」の列に入れて `reports/fedex_export_permit_<日付>.xlsx` に保存（元の Excel は変えない）。
   - CPaSS 注文一覧に FedEx の番号の列が見つからないときは、列名を表示して止まる → その表示をサトシに送る。
   - 追跡番号の一覧が別にあれば `--list 追跡番号.txt`、期間を絞るなら `--since 2026-11-01`。
   - Excel の書式は FedEx から受け取るまで確かめていない。見出しが見つからなければ止まる（推測で書かない）。
   - Mac で初めて使うときは `pip3 install openpyxl` が要る。

## 保存
- 届いた輸出許可通知書は、税務の証拠として保存する（期間・扱いは税理士に確かめる）。
