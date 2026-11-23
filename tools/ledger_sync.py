#!/usr/bin/env python3
"""古物台帳を 元の古物台帳 → Claude更新版 → Mac（data/ledger.csv）と毎日取り込む.

Apps Script（下の APPS_SCRIPT）は script.google.com の独立したプロジェクトに置く（どちらのスプレッドシートの中のスクリプトにも触らない）。
合言葉付きの専用 URL を Mac が読むと、まず 元の古物台帳（読むだけ）→ Claude更新版 へ写す:
    古物台帳: Claude更新版に無い No. の行を足し、ある行は空欄だけ埋める（入っている値・数式は上書きしない。行は消さない）
    管理・ebay売上・J管理・出品情報: Claude更新版の「写し_シート名」に、Mac が使う列だけを毎回写す
    意思決定ログ: Claude更新版の「意思決定ログ」シート（Claude がクラウドから書く）を、knowledge/意思決定ログ.md に追記する
    検索URL: 「検索URL」シート（Claude がクラウドから書く）の URL を data/search_urls.txt に足す。「状態」列が「削除」の URL は消す
    これから増やすシートは、名前を「共有_〜」（Claude → Mac）・「Mac_〜」（Mac → Claude）にすればスクリプトの貼り替えが要らない
そのあと Claude更新版の古物台帳を CSV で返す。?sheet=… を付けると写しのシートを返す。購入者の名前・住所の列（Buyer…・Ship to…）は
スクリプトの側で取り除いてから返す。ほかのシートからは EXTRA_SHEETS の列だけを data/ に保存する（グラフ用）:
    管理      → data/kanri.csv        No.・状態・購入日・販売日・仕入・商品名・仕入元（状態と、古物台帳に無い商品の仕入額）
    ebay売上  → data/ebay_report.csv  eBay の取引レポートの日付・種類・注文番号・手取り・手数料・Custom label
                                      （eBay API の取引明細が無いときの代わり。data/ebay_sales.csv は ebay_api.py orders が使う）
    J管理     → data/junk.csv         ジャンク品のヤフオク再販の仕入・実販売・日付
    出品情報  → data/item_types.csv   管理番号・商品種別
Mac はその URL を読むだけ。URL と合言葉はキーチェーンに保存し、GitHub やチャットには書かない。
スプレッドシートの ID もこのファイルには書かない（setup で聞いてキーチェーンに保存し、スクリプトに埋め込む）。

使い方（aurora フォルダで）:
    python3 tools/ledger_sync.py setup   # 初回: スクリプトをクリップボードにコピー → 貼って公開 → URL を入力
    python3 tools/ledger_sync.py         # 取り込む（update_all.py が毎回実行する）
    python3 tools/ledger_sync.py script  # スクリプトを新しくしたとき: コピー → 貼り替えて「新バージョン」でデプロイ（URL は同じ）
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
OUT = DATA / "ledger.csv"
# シート名 → (保存先, 保存する列). 列の1つ目と2つ目がある行を見出しとみなす。これ以外の列（アカウント名・URL など）は保存しない
EXTRA_SHEETS = {
    "管理": ("kanri.csv", ["No.", "状態", "購入日", "販売日", "仕入(円)", "商品名", "アカウント名(仕入元)"]),
    "ebay売上": ("ebay_report.csv", ["Custom label", "Transaction creation date", "Type", "Order number", "Net amount",
                                  "Payout currency", "Quantity", "Item subtotal", "Shipping and handling",
                                  "Final Value Fee - fixed", "Final Value Fee - variable", "International fee",
                                  "Gross transaction amount", "Transaction currency", "Exchange rate", "Description"]),
    "J管理": ("junk.csv", ["No.", "仕入(円)", "実販売(円)", "売上−仕入(円)", "購入日", "販売日", "保管", "ヤフオク出品"]),
    "出品情報": ("item_types.csv", ["管理番号", "商品種別"]),
}
BACKUP = DATA / "backup"
# Claude更新版の「意思決定ログ」シート（Claude がクラウドから書く。列: 日付・決めたこと）→ knowledge/意思決定ログ.md の末尾に追記
DECISION_SHEET = "意思決定ログ"
DECISION_MD = ROOT / "knowledge" / "意思決定ログ.md"
# 入札の記録（data/bid_log.csv）→ Claude更新版の「入札結果」シート（ゼニガメが負け方を学ぶ）。金額は自分の入札上限と公開の最終価格だけ
BID_SHEET = "入札結果"
# Claude更新版の「検索URL」シート（Claude がクラウドから書く。列: URL・メモ）→ data/search_urls.txt に無い URL を足す（毎朝の入札リスト用）
SEARCH_SHEET = "検索URL"
PAST_BIDS_SHEET = "共有_過去の入札"  # 以前の「入札分析」ファイルの入札履歴（自分の入札額・最終落札価格）。Claude が値だけ写した
PAST_BIDS = DATA / "bid_history.csv"
RESEARCH_SHEET = "共有_リサーチ相場"  # 以前の入札分析ファイル「リサーチ貼付」の品名・eBay 売値$・講師価格・状態（Claude が値だけ写した）→ data/research_prices.csv
RESEARCH = DATA / "research_prices.csv"
MEMO_SHEET = "共有_相場メモ"  # チャットで決めた相場（Claude が書く: 機種・相場$（送料別）・状態・日付）→ data/price_memo.csv
KNOWLEDGE_SHEET = "共有_ナレッジ"  # 講座資料などの要点（Claude が書く: ファイル名・本文）→ knowledge/講座/〈ファイル名〉.md
UNPRICED_SHEET = "Mac_未登録機種"
MESSAGES_SHEET = "Mac_メッセージ"  # eBay の受信メッセージの見出しと返信済みか（ebay_api.py messages --upload。毎時）  # 毎朝の入札リストで相場が無かった機種（夜にクラウドが下調べする）
BID_COLUMNS = ["auction_id", "title", "max_bid", "final_price", "result", "end", "usd", "profit_at_max", "profit_final",
               "trusted", "tentative", "store_tax", "ship", "bid_at"]
SERVICE = "aurora-ledger"
SHEET = "古物台帳"

sys.path.insert(0, str(HERE))
import keychain  # noqa: E402

APPS_SCRIPT = r"""// aurora: 元の古物台帳 → Claude更新版 へ毎日写し、Claude更新版を CSV で返す（Mac の tools/ledger_sync.py が読む）
// ・元の古物台帳は読むだけ（書き込み・削除は一切しない）
// ・Claude更新版の「古物台帳」: 無い No. の行を下に足す／ある行は空欄だけ埋める（入っている値・数式は上書きしない・行は消さない）
// ・ほかのシートは Claude更新版に「写し_シート名」を作り、Mac が使う列だけを毎回写す（Claude更新版のほかのシートには触らない）
// ・合言葉が一致したときだけ動く。購入者の名前・住所の列（Buyer…・Ship to…）は返さない
const KEY = '__KEY__';
// 店舗アプリ用の鍵（2026-11-20 ユーザー決定）: この鍵では「共有_店舗」に行を足すことしかできない（読む・消す・ほかのシートは不可）
const STORE_KEY = '__STORE_KEY__';
const STORE_SHEET = '共有_店舗';
const STORE_HEAD = ['日時', '種類', '商品名', '値札（税込・円）', '判定', '上限（税込・円）', '想定$（送料別）', '店', 'メモ', 'アプリの版'];
const BOOK = '__BOOK__';  // Claude更新版（写し先・Mac が読む）
const SRC = '__SRC__';    // 元の古物台帳（読むだけ）
const LEDGER = '古物台帳';  // 元の古物台帳（読むだけ）
const DST = BOOK === SRC ? '古物台帳_詳細' : LEDGER;  // 1冊運用（2026-11-17〜）は同じ台帳の「古物台帳_詳細」へ写す
const EXTRA = __EXTRA__;  // シート名 → Mac が使う列
const PAIRS = [  // Claude更新版の列（見出しの先頭）← 元の古物台帳の列（見出しの先頭）
  ['管理番号', 'No.'], ['購入日', '購入日'], ['品目', '品目'], ['数量', '数量'], ['仕入元', 'アカウント名'],
  ['仕入合計', '仕入金額'], ['仕入URL', 'ヤフオクURL'], ['販売日', '販売日'], ['販売金額', '販売金額'], ['備考', '備考']];
const OWN = ['意思決定ログ', '入札結果', '検索URL'];  // Claude更新版のこのシートと「共有_」で始まるシートは、そのまま返す
const PUT = ['入札結果'];  // Mac から書き込んでよいシート（Claude更新版の中だけ。中身を毎回まるごと置き換える）。「Mac_」で始まるシートも可
const own = (p) => OWN.includes(p) || p.startsWith('共有_');
const put = (p) => PUT.includes(p) || p.startsWith('Mac_');  // Claude更新版にあるシートをそのまま返す（Claude が書く。Mac が knowledge/ に追記する）
const DROP = /^(Buyer|Ship to)/;
const nh = (s) => String(s).normalize('NFKC').replace(/\s+/g, '');
const colOf = (head, p) => head.findIndex((h) => h === p || h.startsWith(p));
const empty = (v) => v === '' || v === null || v === undefined;

function syncLedger(dstBook, srcBook) {
  const s = srcBook.getSheetByName(LEDGER), d = dstBook.getSheetByName(DST);
  if (!s || !d) return '古物台帳シートが見つかりません';
  const sv = s.getDataRange().getValues(), dr = d.getDataRange(), dv = dr.getValues(), df = dr.getFormulas();
  const sh = sv[0].map(nh), dh = dv[0].map(nh);
  const pairs = PAIRS.map(([a, b]) => [colOf(dh, a), colOf(sh, b)]).filter(([a, b]) => a >= 0 && b >= 0);
  const dKey = colOf(dh, '管理番号'), sKey = colOf(sh, 'No.');
  if (dKey < 0 || sKey < 0) return 'No. の列が見つかりません';
  const supp = colOf(dh, '仕入先'), sUrl = colOf(sh, 'ヤフオクURL');
  const at = {};
  dv.forEach((r, i) => { const k = String(r[dKey]).trim(); if (i > 0 && k && !(k in at)) at[k] = i; });
  let filled = 0;
  const add = [];
  for (let i = 1; i < sv.length; i++) {
    const r = sv[i], k = String(r[sKey]).trim();
    if (!k) continue;
    if (k in at) {
      if (at[k] < 0) continue;  // 今回足した行
      for (const [a, b] of pairs) {
        if (empty(dv[at[k]][a]) && !df[at[k]][a] && !empty(r[b])) {  // 空欄（数式なし）だけ埋める
          d.getRange(at[k] + 1, a + 1).setValue(r[b]);
          filled++;
        }
      }
    } else {
      const row = new Array(dv[0].length).fill('');
      for (const [a, b] of pairs) row[a] = r[b];
      if (supp >= 0 && sUrl >= 0 && /auctions\.yahoo/.test(String(r[sUrl]))) row[supp] = 'ヤフオク';
      add.push(row);
      at[k] = -1;
    }
  }
  if (add.length) d.getRange(d.getLastRow() + 1, 1, add.length, add[0].length).setValues(add);
  return `古物台帳: ${add.length}行を追加・空欄${filled}か所を埋めた`;
}

// 新しいシートは右端に作る（insertSheet だけだと開いているシートの隣＝左側に入って邪魔になる）
function addAtEnd(book, name) { return book.insertSheet(name, book.getSheets().length); }

function copyExtra(dstBook, srcBook) {
  const out = [];
  for (const name of Object.keys(EXTRA)) {
    const s = srcBook.getSheetByName(name);
    if (!s) { out.push(`${name}: 元に無い`); continue; }
    const want = EXTRA[name], rows = s.getDataRange().getValues();
    const hi = rows.slice(0, 10).findIndex((r) => { const h = r.map(nh); return h.includes(nh(want[0])) && h.includes(nh(want[1])); });
    if (hi < 0) { out.push(`${name}: 見出しが見つからない`); continue; }
    const head = rows[hi].map(nh), idx = want.map((c) => head.indexOf(nh(c)));
    const body = rows.slice(hi + 1).map((r) => idx.map((j) => (j >= 0 ? r[j] : ''))).filter((r) => r.some((v) => !empty(v)));
    const table = [want].concat(body);
    const t = dstBook.getSheetByName('写し_' + name) || addAtEnd(dstBook, '写し_' + name);
    t.clearContents();
    t.getRange(1, 1, table.length, want.length).setValues(table);
    out.push(`${name}: ${body.length}行`);
  }
  return '写し ' + out.join('・');
}

function syncAll() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const dst = SpreadsheetApp.openById(BOOK), src = SpreadsheetApp.openById(SRC);
    return syncLedger(dst, src) + ' ／ ' + copyExtra(dst, src);
  } catch (err) {
    return '写せませんでした: ' + err;
  } finally {
    lock.releaseLock();
  }
}

function csvOf(rows) {
  const head = rows.slice(0, 10).find((r) => r.some((v) => DROP.test(v)));
  if (head) {
    const keep = head.map((v, i) => (DROP.test(v) ? -1 : i)).filter((i) => i >= 0);
    rows = rows.map((r) => keep.map((i) => r[i]));
  }
  return rows.map((r) => r.map((v) => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\n');
}

function doGet(e) {
  if (!e || !e.parameter || e.parameter.key !== KEY) return ContentService.createTextOutput('forbidden');
  const p = e.parameter.sheet || '';
  const props = PropertiesService.getScriptProperties();
  if (p === '_status') return ContentService.createTextOutput(props.getProperty('last') || '');
  if (!p) props.setProperty('last', new Date().toISOString() + ' ' + syncAll());  // 古物台帳を読むときに、先に元から写す
  const name = !p ? DST : (p in EXTRA ? '写し_' + p : (own(p) ? p : null));
  const sh = name && SpreadsheetApp.openById(BOOK).getSheetByName(name);
  if (!sh) return ContentService.createTextOutput('NO_SHEET:' + p);
  return ContentService.createTextOutput(csvOf(sh.getDataRange().getDisplayValues())).setMimeType(ContentService.MimeType.CSV);
}

// 店舗アプリ → 共有_店舗 に足す（1回50行まで・1マス200字まで。= + - @ で始まる値は文字として入れる＝数式にしない）
function storeAdd(e) {
  let body;
  try { body = JSON.parse((e.postData && e.postData.contents) || '{}'); } catch (err) { return ContentService.createTextOutput('BAD_JSON'); }
  const rows = (Array.isArray(body.rows) ? body.rows : []).slice(0, 50).filter(Array.isArray)
    .map((r) => STORE_HEAD.map((_, i) => { const v = String(r[i] == null ? '' : r[i]).slice(0, 200); return /^[=+\-@]/.test(v) ? "'" + v : v; }));
  if (!rows.length) return ContentService.createTextOutput('EMPTY');
  const lock = LockService.getScriptLock(); lock.waitLock(20000);
  try {
    const book = SpreadsheetApp.openById(BOOK);
    let sh = book.getSheetByName(STORE_SHEET);
    if (!sh) { sh = addAtEnd(book, STORE_SHEET); sh.appendRow(STORE_HEAD); }
    rows.forEach((r) => sh.appendRow(r));
  } finally { lock.releaseLock(); }
  return ContentService.createTextOutput('OK:' + rows.length);
}

function doPost(e) {
  if (e && e.parameter && STORE_KEY.length >= 16 && e.parameter.store === STORE_KEY) return storeAdd(e);
  if (!e || !e.parameter || e.parameter.key !== KEY) return ContentService.createTextOutput('forbidden');
  const p = e.parameter.sheet || '';
  if (!put(p)) return ContentService.createTextOutput('NO_SHEET:' + p);
  const rows = Utilities.parseCsv(e.postData.contents || '');
  if (!rows.length) return ContentService.createTextOutput('EMPTY');
  const book = SpreadsheetApp.openById(BOOK);
  const sh = book.getSheetByName(p) || addAtEnd(book, p);
  const w = Math.max(...rows.map((r) => r.length));
  const table = rows.map((r) => r.concat(new Array(w - r.length).fill('')));
  sh.clearContents();
  sh.getRange(1, 1, table.length, w).setValues(table);
  return ContentService.createTextOutput('OK:' + (table.length - 1));
}
"""


def script_text(key: str, book: str, src: str, store_key: str = "") -> str:
    """book＝Claude更新版（写し先・Mac が読む）、src＝元の古物台帳（読むだけ）. store_key＝店舗アプリ用の鍵（共有_店舗に足すだけ）."""
    import json
    extra = json.dumps({k: cols for k, (_, cols) in EXTRA_SHEETS.items()}, ensure_ascii=False)
    return (APPS_SCRIPT.replace("__STORE_KEY__", store_key).replace("__KEY__", key).replace("__BOOK__", book).replace("__SRC__", src)
            .replace("__EXTRA__", extra))


def book_id(text: str) -> str:
    """スプレッドシートの URL（…/spreadsheets/d/<ID>/edit…）から ID を取り出す. ID だけでもよい."""
    import re
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text) or re.fullmatch(r"\s*([A-Za-z0-9_-]{20,})\s*", text)
    return m.group(1) if m else ""


GUIDE = """
 1. https://script.google.com/home を開く →［新しいプロジェクト］
    （古物台帳のスプレッドシートの「拡張機能 → Apps Script」は使いません。今あるスクリプトには一切触りません）
 2. 左上の「無題のプロジェクト」を「aurora 古物台帳」に変える
 3. 新しいプロジェクトに最初から入っている3行（function myFunction() {…}）だけを消して、貼り付け（⌘V）→ 保存（⌘S）
 4. 右上「デプロイ」→「新しいデプロイ」→ 歯車 →「ウェブアプリ」
      次のユーザーとして実行: 自分　／　アクセスできるユーザー: 全員
    →「デプロイ」（初回は「アクセスを承認」→ 自分のアカウント → 詳細 → 安全ではないページに移動 → 許可）
 5. 最後に出る「ウェブアプリ」の URL（https://script.google.com/macros/s/…/exec）をコピー
"""


def fetch(url: str, key: str, sheet: str | None = None) -> str:
    sep = "&" if "?" in url else "?"
    q = f"key={urllib.parse.quote(key)}" + (f"&sheet={urllib.parse.quote(sheet)}" if sheet else "")
    with urllib.request.urlopen(f"{url}{sep}{q}", timeout=60) as res:  # Google は転送（302）してから返す
        return res.read().decode("utf-8-sig")


def explain(text: str) -> str:
    """古物台帳が返らなかったときの理由（Google のエラー画面の文章を短く添える）."""
    import re
    t = text.strip()
    if t == "forbidden":
        return "合言葉が違います（python3 tools/ledger_sync.py setup をやり直す）"
    if t.startswith("NO_SHEET"):
        return "Claude更新版に「古物台帳」シートが見つかりません（setup の①の URL を確認）"
    msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", t))).strip()[:300]
    if re.search(r"Sign in|ログイン|accounts\.google", t):
        hint = "デプロイの「アクセスできるユーザー」を「全員」にして、新バージョンでデプロイし直してください"
    elif re.search(r"[Aa]uthori[sz]ation|承認|権限", t):
        hint = "Apps Script の画面で関数 doGet を選んで［実行］→ 許可してから、もう一度試してください"
    else:
        hint = "下の表示を Claude に送ってください"
    return f"古物台帳ではないものが返りました。{hint}\n  （返ってきた内容: {msg or '空'}）"


def sync(dry_run: bool = False) -> bool | None:
    """取り込んだら True、変化なし False、未設定・失敗は None."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    if not url or not key:
        print("古物台帳: 未設定のため飛ばしました（python3 tools/ledger_sync.py setup）", file=sys.stderr)
        return None
    try:
        text = fetch(url, key)
    except (urllib.error.URLError, OSError) as e:
        print(f"古物台帳: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if "販売日" not in text[:5000]:
        print(f"古物台帳: {explain(text)}", file=sys.stderr)
        return None
    try:
        st = fetch(url, key, "_status").strip()
        if st[:2] == "20" and st[4:5] == "-":  # 新しいスクリプトだけが返す（「日時 結果」）
            print(f"Claude更新版への写し: {st}", file=sys.stderr)
    except (urllib.error.URLError, OSError):
        pass
    sync_extra(url, key, dry_run)
    sync_decisions(url, key, dry_run)
    sync_search_urls(url, key, dry_run)
    sync_past_bids(url, key, dry_run)
    sync_research(url, key, dry_run)
    sync_sheet_memo(url, key, dry_run)
    sync_candidates(url, key, dry_run)   # ゼニガメの下調べ → 店舗アプリの仮の相場
    sync_phone_bids(url, key, dry_run)   # スマホの入札タブで予約した物 → 入札の結果の集計
    sync_trivia(url, key, dry_run)       # 機種の豆知識 → 店舗アプリ
    sync_store_memo(url, key, dry_run)   # 店舗で［登録］した相場。同じ機種はチャットの相場メモと日付の新しい方
    sync_knowledge(url, key, dry_run)
    old = OUT.read_text(encoding="utf-8-sig") if OUT.exists() else None
    if old == text:
        print("古物台帳: 変化なし", file=sys.stderr)
        return False
    n = max(0, len(text.strip().splitlines()) - 1)
    if dry_run:
        print(f"古物台帳: {n}行を取り込み予定", file=sys.stderr)
        return True
    DATA.mkdir(exist_ok=True)
    if OUT.exists():
        BACKUP.mkdir(exist_ok=True)
        shutil.copy(OUT, BACKUP / f"ledger_{datetime.now():%Y%m%d-%H%M%S}.csv")
    OUT.write_text(text, encoding="utf-8")
    print(f"古物台帳: {n}行を取り込み → data/ledger.csv", file=sys.stderr)
    return True


def pick_columns(text: str, columns: list[str]) -> list[dict] | None:
    """シートの CSV → columns の列だけの行. 見出し（columns の1つ目と2つ目がある行）が無ければ None."""
    rows = list(csv.reader(io.StringIO(text)))
    norm = lambda r: [h.replace("\n", "").strip() for h in r]  # noqa: E731
    i = next((i for i, r in enumerate(rows[:10]) if columns[0] in norm(r) and columns[1] in norm(r)), None)
    if i is None:
        return None
    head = norm(rows[i])
    idx = {c: head.index(c) for c in columns if c in head}
    out = []
    for r in rows[i + 1:]:
        d = {c: (r[j].strip() if j < len(r) else "") for c, j in idx.items()}
        if any(d.values()):
            out.append(d)
    return out


def sync_extra(url: str, key: str, dry_run: bool = False) -> dict[str, int | None]:
    """古物台帳以外のシートの必要な列 → data/*.csv. 古いスクリプト（そのシートを返さない）なら None."""
    result: dict[str, int | None] = {}
    for sheet, (fname, columns) in EXTRA_SHEETS.items():
        try:
            text = fetch(url, key, sheet)
            if text.startswith("NO_SHEET:"):  # 取り込み元のスプレッドシートにそのシートが無い → 前のデータを残して飛ばす
                print(f"{sheet}シート: 取り込み元に無いので飛ばしました（前のデータはそのまま）", file=sys.stderr)
                result[sheet] = None
                continue
            rows = pick_columns(text, columns)
        except (urllib.error.URLError, OSError) as e:
            print(f"{sheet}シート: 読み込めませんでした（{e}）", file=sys.stderr)
            result[sheet] = None
            continue
        if rows is None:
            print(f"{sheet}シート: 取り込めませんでした。スプレッドシートの Apps Script が古いためです"
                  "（python3 tools/ledger_sync.py script で貼り替え）", file=sys.stderr)
            result[sheet] = None
            continue
        result[sheet] = len(rows)
        if dry_run:
            print(f"{sheet}シート: {len(rows)}行を取り込み予定", file=sys.stderr)
            continue
        DATA.mkdir(exist_ok=True)
        with open(DATA / fname, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        print(f"{sheet}シート: {len(rows)}行 → data/{fname}", file=sys.stderr)
    return result


def append_decisions(text: str, md: Path = DECISION_MD, dry_run: bool = False) -> int:
    """「意思決定ログ」シートの CSV → まだ md に無い行を「- 日付 決めたこと」で末尾に足す. 足した行数を返す."""
    rows = pick_columns(text, ["日付", "決めたこと"])
    if not rows:
        return 0
    have = md.read_text(encoding="utf-8") if md.exists() else ""
    new = []
    for r in rows:
        what = " ".join(r.get("決めたこと", "").split())
        if what and what not in have and what not in "\n".join(new):
            new.append(f"- {r.get('日付', '').strip()} {what}（Claude が記録）".replace("-  ", "- "))
    if new and not dry_run:
        md.parent.mkdir(parents=True, exist_ok=True)
        with open(md, "a", encoding="utf-8") as f:
            f.write(("" if not have or have.endswith("\n") else "\n") + "\n".join(new) + "\n")
    return len(new)


def sync_decisions(url: str, key: str, dry_run: bool = False) -> int | None:
    """Claude更新版の「意思決定ログ」シート → knowledge/意思決定ログ.md（シートやスクリプトが無ければ何もしない）."""
    try:
        text = fetch(url, key, DECISION_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"意思決定ログ: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "決めたこと" not in text[:2000]:
        return None  # 古いスクリプト、またはシートがまだ無い
    n = append_decisions(text, dry_run=dry_run)
    if n:
        print(f"意思決定ログ: {n}件を{'追記予定' if dry_run else '追記'} → knowledge/意思決定ログ.md", file=sys.stderr)
    return n


def remove_search_urls(urls: list[str], dest: Path) -> int:
    """data/search_urls.txt から urls の行（と、そのすぐ上の「# …（Claude が追加）」の行）を消す. 消した数."""
    if not urls or not dest.exists():
        return 0
    lines = dest.read_text(encoding="utf-8").splitlines()
    drop = {u.strip() for u in urls}
    out, n = [], 0
    for ln in lines:
        if ln.strip() in drop:
            n += 1
            if out and out[-1].startswith("#") and "Claude が追加" in out[-1]:
                out.pop()
            continue
        out.append(ln)
    if n:
        dest.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return n


def append_search_urls(text: str, dest: Path | None = None, dry_run: bool = False) -> int:
    """「検索URL」シートの CSV → data/search_urls.txt に無い URL を「# メモ」付きで末尾に足す. 足した数.
    「状態」列に「削除」「停止」と書いた行の URL は data/search_urls.txt から消す（検索を差し替えるとき用）."""
    dest = dest or DATA / "search_urls.txt"
    rows = pick_columns(text, ["URL", "メモ", "状態"]) or []
    off = [r.get("URL", "").strip() for r in rows if any(w in r.get("状態", "") for w in ("削除", "停止"))]
    if off and not dry_run:
        gone = remove_search_urls(off, dest)
        if gone:
            print(f"検索URL: {gone}件を削除 → data/search_urls.txt", file=sys.stderr)
    rows = [r for r in rows if r.get("URL", "").strip() not in off]
    have = dest.read_text(encoding="utf-8") if dest.exists() else ""
    add = []
    for r in rows:
        u = r.get("URL", "").strip()
        if u.startswith("https://auctions.yahoo.co.jp/") and u not in have and u not in "\n".join(add):
            add.append((f"# {r['メモ'].strip()}（Claude が追加）\n" if r.get("メモ", "").strip() else "") + u)
    if add and not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a", encoding="utf-8") as f:
            f.write(("" if not have or have.endswith("\n") else "\n") + "\n".join(add) + "\n")
    return len(add)


def sync_search_urls(url: str, key: str, dry_run: bool = False) -> int | None:
    """Claude更新版の「検索URL」シート → data/search_urls.txt（シートやスクリプトが無ければ何もしない）."""
    try:
        text = fetch(url, key, SEARCH_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"検索URL: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "URL" not in text[:200]:
        return None
    n = append_search_urls(text, dry_run=dry_run)
    if n:
        print(f"検索URL: {n}件を{'追加予定' if dry_run else '追加'} → data/search_urls.txt", file=sys.stderr)
    return n


def sheet_memo_rows(text: str, ship_usd: float) -> dict:
    """「共有_相場メモ」の CSV → {機種: (送料込み$, source)}. 相場$ は送料別（net）で書いてあるので送料を足す."""
    out = {}
    for r in pick_columns(text, ["機種", "相場$（送料別）", "状態"]) or []:
        t = (r.get("機種") or "").strip()
        try:
            v = float(str(r.get("相場$（送料別）", "")).replace("$", "").replace(",", "").strip())
        except ValueError:
            continue
        if t and v > 0:
            cond = (r.get("状態") or "").strip()
            out[t] = (round(v + ship_usd, 2), f"相場メモ({cond})" if cond else "相場メモ")
    return out


def sync_sheet_memo(url: str, key: str, dry_run: bool = False) -> int | None:
    """古物台帳「共有_相場メモ」→ data/price_memo.csv（同じ機種は上書き。前の値は相場の履歴に残る）."""
    import appraise as ba
    try:
        text = fetch(url, key, MEMO_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"相場メモ（シート）: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "機種" not in text[:300]:
        return None
    new = sheet_memo_rows(text, float(ba.load_config(None)["shipping"].get("deduction_usd", 0)))
    if dry_run or not new:
        return len(new)
    added, updated = ba.merge_memo(new, how="チャットで登録")
    if added or updated:
        print(f"相場メモ（シート）: 追加 {added}件・更新 {updated}件 → data/price_memo.csv", file=sys.stderr)
    return added + updated


CAND_SHEET = "共有_下調べ相場"  # ゼニガメの下調べ（機種・候補$（送料別）・確度・日付・根拠）→ data/candidate_prices.csv → 店舗アプリだけ「仮の相場」に使う（2026-11-21）
CAND_OUT = ROOT / "data" / "candidate_prices.csv"


def sync_candidates(url: str, key: str, dry_run: bool = False) -> int | None:
    """古物台帳「共有_下調べ相場」→ data/candidate_prices.csv（まるごと置き換え）. 店舗アプリで相場なしを減らす仮の相場（確認してから）."""
    try:
        text = fetch(url, key, CAND_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"下調べ相場: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:"):
        return None
    rows = [r for r in (pick_columns(text, ["機種", "候補$（送料別）", "確度", "日付", "根拠"]) or []) if (r.get("機種") or "").strip()]
    if dry_run or not rows:   # 見出しの無いシート・空のシートは書かない
        return len(rows)
    CAND_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CAND_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["機種", "候補$（送料別）", "確度", "日付", "根拠"])
        w.writeheader(); w.writerows(rows)
    print(f"下調べ相場: {len(rows)}件 → data/candidate_prices.csv", file=sys.stderr)
    return len(rows)


TRIVIA_SHEET = "共有_豆知識"  # 機種の豆知識（毎晩クラウドで少しずつ調べる。2026-11-21 オーナー「エピソードがあると機種を覚えやすい」）


def sync_trivia(url: str, key: str, dry_run: bool = False) -> int | None:
    """古物台帳「共有_豆知識」→ data/trivia.csv（まるごと置き換え）. 店舗アプリの機種名の下に1行で出す."""
    try:
        text = fetch(url, key, TRIVIA_SHEET)
    except (urllib.error.URLError, OSError):
        return None
    if text.startswith("NO_SHEET:"):
        return None
    rows = [r for r in (pick_columns(text, ["機種", "豆知識", "出典", "日付", "確認"]) or []) if (r.get("機種") or "").strip() and (r.get("豆知識") or "").strip()]
    if dry_run or not rows:
        return len(rows)
    out = DATA / "trivia.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["機種", "豆知識", "出典", "日付", "確認"], restval="")
        w.writeheader(); w.writerows(rows)
    return len(rows)


def phone_bid_rows(text: str) -> list[dict]:
    """「共有_店舗」の CSV のうち 種類＝入札予約 の行（スマホの入札タブの［予約］）→ 入札の記録（bid_log）と同じ形の行."""
    import re
    out = {}
    for r in pick_columns(text, ["日時", "種類", "商品名", "判定", "上限（税込・円）", "想定$（送料別）", "メモ"]) or []:
        kind = (r.get("種類") or "").strip()
        if kind not in ("入札予約", "入札予約取消"):
            continue
        memo = r.get("メモ") or ""
        m = re.search(r"ID\s*([A-Za-z0-9]+)", memo)
        if not m:
            continue
        if kind == "入札予約取消":   # スマホで「予約した」を外した
            out.pop(m.group(1), None)
            continue
        end = re.search(r"終了\s*([^／]+)", memo)
        out[m.group(1)] = {"auction_id": m.group(1), "title": r.get("商品名", ""), "max_bid": r.get("上限（税込・円）", ""),
                           "pay_max": "", "usd": r.get("想定$（送料別）", ""), "end": (end.group(1).strip() if end else ""),
                           "bid_at": r.get("日時", ""), "tentative": "1" if "／仮" in memo else "0", "via": "phone"}
    return list(out.values())


PHONE_BID_FIELDS = ["auction_id", "title", "max_bid", "pay_max", "usd", "end", "bid_at", "tentative", "via"]


def sync_phone_bids(url: str, key: str, dry_run: bool = False) -> int | None:
    """古物台帳「共有_店舗」の「入札予約」→ data/phone_bids.csv（bid_results.py が入札の記録に足す）."""
    try:
        text = fetch(url, key, "共有_店舗")
    except (urllib.error.URLError, OSError):
        return None
    if text.startswith("NO_SHEET:"):
        return None
    rows = phone_bid_rows(text)
    if dry_run:
        return len(rows)
    # 全部外して0件になったときも書き直す（古い予約が残らないように）
    out = DATA / "phone_bids.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PHONE_BID_FIELDS)
        w.writeheader(); w.writerows(rows)
    return len(rows)


STORE_MEMO_KIND = "相場登録"  # 店舗アプリの Sold 欄の［登録］（2026-11-21 オーナー指示）


def store_memo_rows(text: str, ship_usd: float, newer_than: dict | None = None) -> dict:
    """「共有_店舗」の CSV のうち 種類＝相場登録 の行 → {機種: (送料込み$, source)}. 想定$ は送料別（Sold で見た額）. 同じ機種は後の行が勝つ.
    newer_than＝{機種: 日付}（チャットで決めた「共有_相場メモ」の日付）。それより古い登録は使わない（新しい方の相場を残す）."""
    newer_than = newer_than or {}
    out = {}
    for r in pick_columns(text, ["日時", "種類", "商品名", "想定$（送料別）", "メモ"]) or []:
        if (r.get("種類") or "").strip() != STORE_MEMO_KIND:
            continue
        t = (r.get("商品名") or "").strip()
        if (r.get("日時") or "")[:10] < newer_than.get(t, ""):
            continue
        try:
            v = float(str(r.get("想定$（送料別）", "")).replace("$", "").replace(",", "").strip())
        except ValueError:
            continue
        if t and v > 0:
            cond = (r.get("メモ") or "").strip()
            out[t] = (round(v + ship_usd, 2), f"相場メモ({cond})" if cond else "相場メモ")
    return out


def sheet_memo_dates(text: str) -> dict:
    """「共有_相場メモ」の CSV → {機種: 日付（YYYY-MM-DD）}."""
    out = {}
    for r in pick_columns(text, ["機種", "相場$（送料別）", "日付"]) or []:
        t = (r.get("機種") or "").strip()
        d = (r.get("日付") or "").strip().replace("/", "-")[:10]
        if t and d > out.get(t, ""):
            out[t] = d
    return out


def sync_store_memo(url: str, key: str, dry_run: bool = False) -> int | None:
    """古物台帳「共有_店舗」の［登録］の行 → data/price_memo.csv. 「共有_相場メモ」の後に取り込み、同じ機種はどちらか新しい日付の方を残す."""
    import appraise as ba
    try:
        text = fetch(url, key, "共有_店舗")
        memo = fetch(url, key, MEMO_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"相場メモ（店舗の登録）: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:"):
        return None
    dates = {} if memo.startswith("NO_SHEET:") else sheet_memo_dates(memo)
    new = store_memo_rows(text, float(ba.load_config(None)["shipping"].get("deduction_usd", 0)), dates)
    if dry_run or not new:
        return len(new)
    added, updated = ba.merge_memo(new, how="店舗で登録")
    if added or updated:
        print(f"相場メモ（店舗の登録）: 追加 {added}件・更新 {updated}件 → data/price_memo.csv", file=sys.stderr)
    return added + updated


def sync_knowledge(url: str, key: str, dry_run: bool = False, root: Path | None = None) -> int | None:
    """古物台帳「共有_ナレッジ」（ファイル名・本文）→ knowledge/講座/〈ファイル名〉.md（中身が変わったものだけ書き直す）."""
    import re
    try:
        text = fetch(url, key, KNOWLEDGE_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"ナレッジ: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "ファイル名" not in text[:300]:
        return None
    folder = (root or ROOT / "knowledge") / "講座"
    n = 0
    for r in pick_columns(text, ["ファイル名", "本文"]) or []:
        name = re.sub(r'[\\/:*?"<>|]', "_", (r.get("ファイル名") or "").strip())[:60]
        body = (r.get("本文") or "").replace("\r\n", "\n").strip() + "\n"
        if not name or len(body) < 2:
            continue
        path = folder / f"{name}.md"
        if path.exists() and path.read_text(encoding="utf-8") == body:
            continue
        n += 1
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    if n:
        print(f"ナレッジ: {n}件を{'更新予定' if dry_run else '更新'} → knowledge/講座/", file=sys.stderr)
    return n


def upload_unpriced(reports: Path | None = None, post=None, top: int = 30) -> int | None:
    """一番新しい reports/unpriced_models_*.csv の上位 → 古物台帳「Mac_未登録機種」（夜にクラウドが相場を下調べする）."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    files = sorted((reports or ROOT / "reports").glob("unpriced_models_*.csv"))
    if not url or not key or not files:
        return None
    rows = list(csv.DictReader(files[-1].read_text(encoding="utf-8-sig").splitlines()))[:top]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["機種", "件数", "ヤフオク中央値", "出品例", "sold"])
    for r in rows:
        w.writerow([r.get("title", ""), r.get("件数", ""), r.get("ヤフオク現在価格の中央値", ""), r.get("出品例", "")[:120],
                    r.get("eBay販売済みURL", "")])
    # 古い相場メモ（90日以上直していない・日付不明）も、夜の下調べで見直す（2026-11-18 ユーザー決定）
    import appraise as ba
    ship = float(ba.load_config(None)["shipping"]["deduction_usd"])
    stale = ba.stale_memos()
    for m in stale:
        q = urllib.parse.quote_plus(m["title"])
        w.writerow([m["title"], "古い相場", "", f"今の相場 ${m['usd'] - ship:.0f}（送料別。{m['date'][:10] or '日付不明'}）を見直す",
                    f"https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1&_sop=16&_ipg=120"])
    try:
        if post:
            res = post(buf.getvalue())
        else:
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(
                f"{url}{sep}key={urllib.parse.quote(key)}&sheet={urllib.parse.quote(UNPRICED_SHEET)}",
                data=buf.getvalue().encode("utf-8"), headers={"Content-Type": "text/csv; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r2:
                res = r2.read().decode("utf-8-sig")
    except (urllib.error.URLError, OSError) as e:
        print(f"未登録機種: 送れませんでした（{e}）", file=sys.stderr)
        return None
    if not res.startswith("OK"):
        return None
    print(f"未登録機種: 上位 {len(rows)}件＋古い相場 {len(stale)}件 → 古物台帳「{UNPRICED_SHEET}」（夜に下調べ）", file=sys.stderr)
    return len(rows) + len(stale)


def past_bids_rows(text: str) -> list[dict]:
    """「共有_過去の入札」の CSV → bid_log と同じ形の行（自分の入札額と結果（落札成功／落札失敗）がある行だけ。空欄は使わない）."""
    from datetime import date, timedelta
    num = lambda v: float(str(v).replace(",", "").replace("円", "").strip()) if str(v).replace(",", "").replace("円", "").strip() else None
    out: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(text)):
        aid, res = (r.get("オークションID") or "").strip(), (r.get("落札結果") or "").strip()
        try:
            mb, fp = num(r.get("自分の入札額(円)", "")), num(r.get("最終落札価格(円)", ""))
        except ValueError:
            continue
        if not aid or not mb or mb <= 0 or res not in ("落札成功", "落札失敗"):
            continue
        d = (r.get("入札日") or "").strip()
        if d.replace(".", "").isdigit():  # 表計算の日付の数（1899/12/30 からの日数）
            d = (date(1899, 12, 30) + timedelta(days=int(float(d)))).isoformat()
        row = {"auction_id": aid, "title": (r.get("商品名") or "").strip(), "max_bid": int(mb),
               "final_price": int(fp) if fp else "", "result": "落札" if res == "落札成功" else "負け",
               "bid_at": d.replace("/", "-"), "usd": r.get("eBay想定売値($)", "").strip(), "store_tax": ""}
        if aid not in out or mb > float(out[aid]["max_bid"]):  # 同じ出品は高い方の入札額
            out[aid] = row
    return list(out.values())


def sync_past_bids(url: str, key: str, dry_run: bool = False, dest: Path | None = None) -> int | None:
    """古物台帳「共有_過去の入札」→ data/bid_history.csv（まるごと置き換え）. 落札見込み（bid_results）に使う."""
    try:
        text = fetch(url, key, PAST_BIDS_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"過去の入札: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "オークションID" not in text[:300]:
        return None
    rows = past_bids_rows(text)
    if dry_run or not rows:
        return len(rows)
    dest = dest or PAST_BIDS
    dest.parent.mkdir(parents=True, exist_ok=True)
    cols = ["auction_id", "title", "max_bid", "final_price", "result", "bid_at", "usd", "store_tax"]
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def research_rows(text: str, ship_usd: float, free_ship_usd: float) -> list[dict]:
    """「共有_リサーチ相場」の CSV → reference_prices と同じ形の行. 講師価格があれば講師価格、無ければ売値$.
    売れた額か送料込みかが分からないので保守的に、送料無料の出品とみなして −free_ship_usd を商品代（送料別）とし、
    計算用に一律の送料 ship_usd を足して送料込みにする（2026-11-19 ユーザー決定: 確認してから入札の扱い）."""
    out = []
    for r in pick_columns(text, ["品名", "売値$", "講師価格", "状態", "日付"]) or []:
        title = (r.get("品名") or "").strip()
        v = None
        for k in ("講師価格", "売値$"):
            try:
                v = float(str(r.get(k, "")).replace("$", "").replace(",", "").strip())
                if v > 0:
                    break
            except ValueError:
                v = None
        if not title or not v or v <= free_ship_usd:
            continue
        cond = (r.get("状態") or "").strip()
        out.append({"date": (r.get("日付") or "").strip(), "title": title,
                    "sold_price_usd": round(v - free_ship_usd + ship_usd, 2), "shipping_charged_usd": 0,
                    "source": f"リサーチ({cond})" if cond else "リサーチ"})
    return out


def sync_research(url: str, key: str, dry_run: bool = False, dest: Path | None = None) -> int | None:
    """古物台帳「共有_リサーチ相場」→ data/research_prices.csv（まるごと置き換え）. 相場の根拠「リサーチ（推定）」."""
    import appraise as ba
    try:
        text = fetch(url, key, RESEARCH_SHEET)
    except (urllib.error.URLError, OSError) as e:
        print(f"リサーチ相場: 読み込めませんでした（{e}）", file=sys.stderr)
        return None
    if text.startswith("NO_SHEET:") or "品名" not in text[:300]:
        return None
    cfg = ba.load_config(None)
    rows = research_rows(text, float(cfg["shipping"].get("deduction_usd", 0)),
                         float(cfg["judge"].get("active_free_ship_deduction_usd", 30)))
    if dry_run or not rows:
        return len(rows)
    dest = dest or RESEARCH
    dest.parent.mkdir(parents=True, exist_ok=True)
    cols = ["date", "title", "ebay_query", "sold_price_usd", "shipping_charged_usd", "purchase_jpy", "ebay_url", "source"]
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"リサーチ相場: {len(rows)}件 → data/research_prices.csv", file=sys.stderr)
    return len(rows)


def upload_bids(dry_run: bool = False, log: Path | None = None, post=None) -> int | None:
    """data/bid_log.csv の必要な列 → Claude更新版の「入札結果」シート（まるごと置き換え）. 送った行数. 未設定・古いスクリプトは None."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    log = log or DATA / "bid_log.csv"
    if not url or not key or not log.exists():
        return None
    rows = list(csv.DictReader(log.read_text(encoding="utf-8-sig").splitlines()))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=BID_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    if dry_run:
        print(f"入札結果: {len(rows)}行を送る予定 → Claude更新版「{BID_SHEET}」", file=sys.stderr)
        return len(rows)
    try:
        if post:
            res = post(buf.getvalue())
        else:
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(
                f"{url}{sep}key={urllib.parse.quote(key)}&sheet={urllib.parse.quote(BID_SHEET)}",
                data=buf.getvalue().encode("utf-8"), headers={"Content-Type": "text/csv; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:  # Google は 302 で結果のページに転送する
                res = r.read().decode("utf-8-sig")
    except (urllib.error.URLError, OSError) as e:
        print(f"入札結果: 送れませんでした（{e}）", file=sys.stderr)
        return None
    if not res.startswith("OK"):
        print("入札結果: 送れませんでした。スプレッドシートの Apps Script が古いためです"
              "（python3 tools/ledger_sync.py script で貼り替え）", file=sys.stderr)
        return None
    print(f"入札結果: {len(rows)}行 → Claude更新版「{BID_SHEET}」", file=sys.stderr)
    return len(rows)


TXN_SHEET = "Mac_取引"  # eBay API の取引明細を「ebay売上」と同じ列の位置で（管理の月次サマリーが毎朝これを読む。2026-11-19）
# 「ebay売上」（Seller Hub の取引レポートを貼ったシート）と同じ列: A 日付・B 種類・K 入金額(USD)・U Custom label・AI 総額・AJ 通貨・AK 換算レート
TXN_COLS = {"A": "Transaction creation date", "B": "Type", "K": "Net amount", "U": "Custom label",
            "AI": "Gross transaction amount", "AJ": "Transaction currency", "AK": "Exchange rate"}
TXN_TYPES = {"SALE": "Order", "REFUND": "Refund", "DISPUTE": "Claim", "NON_SALE_CHARGE": "Other fee",
             "ADJUSTMENT": "Adjustment", "PAYOUT": "Payout", "SHIPPING_LABEL": "Shipping label", "TRANSFER": "Transfer",
             "CREDIT": "Credit"}


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _report_date(iso: str) -> str:
    """2026-11-16 → 「d:16-Nov-26」（取引レポートと同じ書き方。先頭の d: は、シートが日付に変えないため）."""
    d = datetime.strptime(iso[:10], "%Y-%m-%d")
    return f"d:{d.day}-{d.strftime('%b')}-{d.strftime('%y')}"


def transactions_csv(fin_rows: list[dict], order_rows: list[dict]) -> str:
    """eBay API の取引明細（ebay_finances）と注文（ebay_orders）→ 「Mac_取引」に送る CSV.
    1行目は説明、2行目は見出し、3行目から（「ebay売上」と同じく3行目から）。金額は USD（換算レート 1）.
    注文の総額 = 商品代＋送料（出品サイトの通貨のときは、その取引の入金額の換算レートで USD に直す）。返金・クレームは入金額（マイナス）."""
    orders: dict[str, dict] = {}
    for o in order_rows:
        g = orders.setdefault(o.get("order_id", ""), {"sku": o.get("sku", ""), "gross": 0.0, "cur": o.get("currency", "USD")})
        try:
            g["gross"] += float(o.get("item_price") or 0) * float(o.get("quantity") or 1) + float(o.get("shipping") or 0)
        except ValueError:
            pass
    width = _col_index("AK") + 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Mac が eBay API から毎朝書き込みます（手で直さない）。管理の月次サマリーが読みます"] + [""] * (width - 1))
    head = [""] * width
    for col, name in TXN_COLS.items():
        head[_col_index(col)] = name
    w.writerow(head)
    for r in fin_rows:
        t = TXN_TYPES.get(r.get("type", ""), r.get("type", ""))
        try:
            amt = float(r.get("amount") or 0)
        except ValueError:
            continue
        net = amt if r.get("booking") == "CREDIT" else -amt
        rate = 1.0
        orig = (r.get("amount_original") or "").split()
        if orig:
            try:
                rate = amt / float(orig[0]) if float(orig[0]) else 1.0
            except ValueError:
                pass
        o = orders.get(r.get("order_id", ""), {})
        if t == "Order" and o.get("gross"):
            gross = o["gross"] * (1.0 if o.get("cur", "USD") == "USD" else rate)
        else:
            gross = net
        row = [""] * width
        row[_col_index("A")] = _report_date(r.get("date", "")) if r.get("date") else ""
        row[_col_index("B")] = t
        row[_col_index("K")] = round(net, 2)
        row[_col_index("U")] = o.get("sku", "") or "--"
        row[_col_index("AI")] = round(gross, 2)
        row[_col_index("AJ")] = "USD"
        row[_col_index("AK")] = 1
        w.writerow(row)
    return buf.getvalue()


SALES_SHEET = "Mac_販売"  # 管理番号ごとの販売（販売日・注文番号・国・金額・手数料・広告費・返金・入金）。古物台帳_詳細の K〜R の数式がここを引く
SALES_HEAD = ["管理番号", "販売日", "eBay注文番号", "購入者の国", "販売金額（$・送料込）", "eBay手数料（$）", "広告費（$）", "返金（$）", "入金額（$）", "注文日", "メモ"]
SKU_TRUSTED_FROM = "2026-08-18"  # これより前の注文は SKU が No. と合っていない（build_dashboard と同じ）


def _ymd(iso: str) -> str:
    """2026-11-03 → 2026/11/3（古物台帳の書き方）."""
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d.year}/{d.month}/{d.day}"


def sales_csv(fin_rows: list[dict], order_rows: list[dict]) -> str:
    """eBay API の注文と取引明細 → 「Mac_販売」の CSV（2026-11-23 オーナー「古物台帳の eBay データは API で取得して入力できるよな？」）.
    1つの管理番号に1行（全額返金でない新しい注文を優先）。金額は USD（豪ドル・ポンドの注文は、その入金の換算レートで直す）.
    販売金額−手数料−広告費−返金＝入金額。全額返金・キャンセルは販売日を空にする（商品は在庫に戻る。今までの台帳の書き方と同じ）."""
    per: dict[str, dict] = {}
    for r in fin_rows:
        typ = (r.get("type") or "").strip()
        if typ in ("PAYOUT", "TRANSFER", "") or (r.get("status") or "") == "FAILED":
            continue
        oid = (r.get("order_id") or "").strip()
        if not oid:
            m = re.search(r"ORDER_ID:(\S+)", r.get("references") or "")
            oid = m.group(1) if m else ""
        if not oid:
            continue
        try:
            amt = float(r.get("amount") or 0)
        except ValueError:
            continue
        net = amt if r.get("booking") == "CREDIT" else -amt
        p = per.setdefault(oid, {"sale": 0.0, "ad": 0.0, "refund": 0.0, "rate": 1.0, "has_sale": False})
        if typ == "SALE":
            p["sale"] += net
            p["has_sale"] = True
            orig = (r.get("amount_original") or "").split()
            try:
                if orig and float(orig[0]):
                    p["rate"] = amt / float(orig[0])
            except ValueError:
                pass
        elif "AD_FEE" in (r.get("fee_type") or "") or "AD_FEE" in typ:
            p["ad"] -= net
        elif typ in ("REFUND", "DISPUTE", "CREDIT"):
            p["refund"] -= net
        else:
            p["ad"] -= net   # 注文に付いたその他の手数料も、入金から引かれた額として広告費の欄に寄せる
    best: dict[str, dict] = {}
    for o in order_rows:
        sku, oid, day = (o.get("sku") or "").strip(), (o.get("order_id") or "").strip(), (o.get("date") or "")[:10]
        p = per.get(oid)
        if not sku or not oid or day < SKU_TRUSTED_FROM or not p or not p["has_sale"]:
            continue
        try:
            gross = float(o.get("item_price") or 0) * float(o.get("quantity") or 1) + float(o.get("shipping") or 0)
        except ValueError:
            continue
        gross *= 1.0 if (o.get("currency") or "USD") == "USD" else p["rate"]
        refunded = (o.get("payment_status") == "FULLY_REFUNDED" or o.get("cancel_state") == "CANCELED"
                    or (p["refund"] > 0 and p["refund"] >= p["sale"] - 1.0))
        row = {"sku": sku, "day": day, "oid": oid, "country": o.get("country", ""), "gross": gross, "fee": gross - p["sale"],
               "ad": p["ad"], "refund": p["refund"], "net": p["sale"] - p["ad"] - p["refund"], "refunded": refunded}
        cur = best.get(sku)   # 全額返金でない注文を優先。同じなら新しい注文（返金の後に出し直して売れた物は、売れた方）
        if cur is None or (not row["refunded"], row["day"]) > (not cur["refunded"], cur["day"]):
            best[sku] = row
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(SALES_HEAD)
    money = lambda v: "" if abs(v) < 0.005 else round(v, 2)  # noqa: E731
    for r in sorted(best.values(), key=lambda x: x["day"], reverse=True):
        w.writerow([r["sku"], "" if r["refunded"] else _ymd(r["day"]), r["oid"], r["country"], round(r["gross"], 2), round(r["fee"], 2),
                    money(r["ad"]), money(r["refund"]), round(r["net"], 2), _ymd(r["day"]), "全額返金・キャンセル" if r["refunded"] else ""])
    return buf.getvalue()


RETURNS_SHEET = "Mac_返品"  # 返品・キャンセル・返金の一覧（eBay API。購入者の名前・住所・メッセージは入れない。2026-11-19 返品を減らす作業のため）
RETURNS_COLS = ["kind", "sku", "title", "order_date", "date", "verdict", "refund", "refund_currency", "reason", "status"]


def returns_csv(rows: list[dict]) -> str:
    """reports/ebay_returns_*.csv の行 → 「Mac_返品」に送る CSV（決めた列だけ。購入者の情報は元から入っていない）."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(RETURNS_COLS)
    for r in rows:
        w.writerow([r.get(c, "") for c in RETURNS_COLS])
    return buf.getvalue()


RECS_SHEET = "Mac_おすすめ"  # 店舗アプリのおすすめ全部（枠・表示名・元の商品名）。クラウドのサトシが毎朝全部を見て、枠の誤りを直す（2026-11-22）
REC_CAT_JA = {"mf": "MF", "af": "AF", "film": "Film", "digital": "Digital"}


def recs_csv(recs: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["枠", "メーカー", "表示名", "種類", "販売回数", "想定$（送料込み）", "元の商品名（例）"])
    for r in sorted(recs, key=lambda x: (x.get("cat", ""), x.get("brand", ""), x.get("label", ""))):
        w.writerow([REC_CAT_JA.get(r.get("cat", ""), r.get("cat", "")), r.get("brand", ""), r.get("label", ""),
                    "レンズ" if r.get("kind") == "lens" else "カメラ", r.get("sales", ""), r.get("usd", ""),
                    " ／ ".join(r.get("examples") or [])[:300]])
    return buf.getvalue()


def upload_recs(path: Path | None = None, post=None) -> int | None:
    """data/store_data.json のおすすめ全部 → 古物台帳「Mac_おすすめ」（まるごと置き換え）. 送った行数."""
    path = path or DATA / "store_data.json"
    if not path.exists():
        return None
    recs = json.loads(path.read_text(encoding="utf-8")).get("recs") or []
    if not post_sheet(RECS_SHEET, recs_csv(recs), post=post):
        print(f"おすすめ: 台帳「{RECS_SHEET}」に送れませんでした", file=sys.stderr)
        return None
    print(f"おすすめ: 台帳「{RECS_SHEET}」に {len(recs)}行（クラウドで枠の誤りを見る）", file=sys.stderr)
    return len(recs)


def upload_returns(reports: Path | None = None, post=None) -> int | None:
    """reports/ebay_returns_*.csv（最新）→ 古物台帳「Mac_返品」（まるごと置き換え）. 送った行数."""
    reports = reports or ROOT / "reports"
    files = sorted(p for p in reports.glob("ebay_returns_*.csv") if "_raw_" not in p.name)
    if not files:
        return None
    rows = list(csv.DictReader(files[-1].read_text(encoding="utf-8-sig").splitlines()))
    if not post_sheet(RETURNS_SHEET, returns_csv(rows), post=post):
        print(f"返品の一覧: 台帳「{RETURNS_SHEET}」に送れませんでした", file=sys.stderr)
        return None
    print(f"返品の一覧: 台帳「{RETURNS_SHEET}」に {len(rows)}行（返品を減らす分析に使う）", file=sys.stderr)
    return len(rows)


def upload_transactions(reports: Path | None = None, post=None) -> int | None:
    """reports/ebay_finances_*.csv と ebay_orders_*.csv（最新）→ 古物台帳「Mac_取引」（まるごと置き換え）. 送った行数."""
    reports = reports or ROOT / "reports"
    fins = sorted(reports.glob("ebay_finances_*.csv"))
    ords = sorted(reports.glob("ebay_orders_*.csv"))
    if not fins:
        return None
    read = lambda p: list(csv.DictReader(p.read_text(encoding="utf-8-sig").splitlines()))  # noqa: E731
    body = transactions_csv(read(fins[-1]), read(ords[-1]) if ords else [])
    if not post_sheet(TXN_SHEET, body, post=post):
        print(f"eBay の取引: 台帳「{TXN_SHEET}」に送れませんでした", file=sys.stderr)
        return None
    n = body.count("\n") - 2
    print(f"eBay の取引: 台帳「{TXN_SHEET}」に {n}行 → 管理の月次サマリーが最新になります", file=sys.stderr)
    if ords:   # 管理番号ごとの販売 → 古物台帳_詳細の販売日・注文番号・金額が自動で入る
        sales = sales_csv(read(fins[-1]), read(ords[-1]))
        if post_sheet(SALES_SHEET, sales, post=post):
            print(f"eBay の販売: 台帳「{SALES_SHEET}」に {sales.count(chr(10)) - 1}件 → 古物台帳_詳細の販売の欄が最新になります", file=sys.stderr)
        else:
            print(f"eBay の販売: 台帳「{SALES_SHEET}」に送れませんでした", file=sys.stderr)
    return n


DASH_SHEET = "Mac_グラフ"  # 数字のグラフの中身（Artifacts の「aurora 数字のグラフ」が開くたびに読む）
DASH_CHUNK = 30000  # 1マスに入る文字数（上限 5万字）より小さく分ける


def post_sheet(sheet: str, body: str, post=None) -> bool:
    """CSV の中身 → 古物台帳の「Mac_〜」シート（まるごと置き換え）. 送れたら True."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    if not url or not key:
        return False
    try:
        if post:
            res = post(body)
        else:
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(
                f"{url}{sep}key={urllib.parse.quote(key)}&sheet={urllib.parse.quote(sheet)}",
                data=body.encode("utf-8"), headers={"Content-Type": "text/csv; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                res = r.read().decode("utf-8-sig")
    except (urllib.error.URLError, OSError) as e:
        print(f"{sheet}: 送れませんでした（{e}）", file=sys.stderr)
        return False
    return res.startswith("OK")


def dashboard_csv(data_json: str) -> str:
    """グラフの中身（JSON）→ シートに送る CSV. 1行目は見出し、2行目からは「j:」＋最大3万字ずつ（数字や日付に化けないように）."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["part"])
    for i in range(0, len(data_json), DASH_CHUNK):
        w.writerow(["j:" + data_json[i:i + DASH_CHUNK]])
    return buf.getvalue()


def upload_dashboard(path: Path | None = None, post=None) -> int | None:
    """reports/dashboard_data.json → 古物台帳の「Mac_グラフ」シート（まるごと置き換え）. 送った行数. 未設定・失敗は None."""
    url, key = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "key")
    path = path or ROOT / "reports" / "dashboard_data.json"
    if not url or not key or not path.exists():
        return None
    body = dashboard_csv(path.read_text(encoding="utf-8"))
    try:
        if post:
            res = post(body)
        else:
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(
                f"{url}{sep}key={urllib.parse.quote(key)}&sheet={urllib.parse.quote(DASH_SHEET)}",
                data=body.encode("utf-8"), headers={"Content-Type": "text/csv; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                res = r.read().decode("utf-8-sig")
    except (urllib.error.URLError, OSError) as e:
        print(f"数字のグラフ: 台帳に送れませんでした（{e}）", file=sys.stderr)
        return None
    if not res.startswith("OK"):
        print("数字のグラフ: 台帳に送れませんでした（Apps Script が古いため。python3 tools/ledger_sync.py script で貼り替え）",
              file=sys.stderr)
        return None
    n = body.count("\n") - 1
    print(f"数字のグラフ: 台帳「{DASH_SHEET}」に送りました → Artifacts のグラフが次に開いたとき最新になります", file=sys.stderr)
    return n


def cmd_single() -> None:
    """Claude更新版をやめて、元の古物台帳1冊で動かす（2026-11-17〜）. 写し先を元の台帳にしてスクリプトを貼り替える."""
    src = keychain.get(SERVICE, "src") or ""
    if not src:
        print("先に python3 tools/ledger_sync.py setup を実行してください。")
        sys.exit(1)
    keychain.set(SERVICE, "book", src)
    print("写し先を元の古物台帳に切り替えました（古物台帳 → 古物台帳_詳細、写し_〜 も元の台帳に作られます）。")
    cmd_script()


def get_store_key(create: bool = True) -> str:
    """店舗アプリ用の鍵（キーチェーン）. 無ければ作る."""
    k = keychain.get(SERVICE, "store_key") or ""
    if not k and create:
        k = secrets.token_urlsafe(24)
        keychain.set(SERVICE, "store_key", k)
    return k


def store_endpoint() -> dict | None:
    """店舗アプリが「共有_店舗」に行を足す先（build_store.py が暗号化して届ける）. 未設定なら None."""
    url, k = keychain.get(SERVICE, "url"), get_store_key(create=False)
    return {"url": url, "key": k} if url and k else None


def cmd_script() -> None:
    """スクリプトを新しくしたとき: 貼り替えて新バージョンでデプロイ（URL・合言葉はそのまま）."""
    key = keychain.get(SERVICE, "key")
    if not key or not keychain.get(SERVICE, "url"):
        print("まだ設定されていません。先に python3 tools/ledger_sync.py setup を実行してください。")
        sys.exit(1)
    book, src = keychain.get(SERVICE, "book") or "", keychain.get(SERVICE, "src") or ""
    if not (book and src):
        print("先に python3 tools/ledger_sync.py setup を実行してください（Claude更新版と元の古物台帳の URL を聞きます）。")
        sys.exit(1)
    script = script_text(key, book, src, get_store_key())
    copied = keychain.copy_to_clipboard(script)
    head = "新しいスクリプトをクリップボードにコピーしました。" if copied else "下のスクリプトをコピーしてください:\n" + script
    # 開くページはブラウザで自動で開く（2026-11-20 ユーザー指摘「URL を都度貼って」）。2回目からはプロジェクトの画面を直接開く
    editor = keychain.get(SERVICE, "editor") or ""
    page = editor or "https://script.google.com/home"
    try:
        import webbrowser
        webbrowser.open(page)
    except Exception:
        pass
    where = (f"ブラウザで開いたページ（{page}）" if editor
             else f"ブラウザで開いたページ（{page}）で「aurora 古物台帳」のプロジェクトを開く")
    print(f"""
{head}

 1. {where}
 2. aurora のスクリプト（先頭が「// aurora:」の部分）だけを選んで、貼り付けて置き換え（⌘V）→ 保存（⌘S）
    （ほかのスクリプトは消さない）
 3. 右上「デプロイ」→「デプロイを管理」→ 鉛筆マーク（編集）
    → バージョン:「新バージョン」→「デプロイ」（URL は変わりません）
 4. 終わったら Enter
""")
    if not editor:
        u = input("（次回から直接開くため）いま開いているプロジェクトの画面のアドレス欄の URL を貼って Enter（飛ばすなら Enter だけ）: ").strip()
        if u.startswith("https://script.google.com/") and "/projects/" in u:
            keychain.set(SERVICE, "editor", u.split("?")[0])
    else:
        input("Enter を押すと試しに読み込みます…")
    sync(dry_run=True)
    upload_bids()


def cmd_setup() -> None:
    print("\n古物台帳の自動取り込みを設定します（初回だけ・約5分）。")
    def ask(q: str) -> str:
        while True:
            v = book_id(input(q))
            if v:
                return v
            print("   スプレッドシートの URL（https://docs.google.com/spreadsheets/d/…）を貼ってください。")
    book = ask("① Claude更新版のスプレッドシートの URL（ブラウザのアドレス欄）を貼り付けて Enter: ")
    src = ask("② 元の古物台帳のスプレッドシートの URL を貼り付けて Enter（読むだけ。変更しません）: ")
    if src == book:
        print("①と②が同じスプレッドシートです。もう一度 setup を実行してください。")
        sys.exit(1)
    keychain.set(SERVICE, "book", book)  # キーチェーンだけに保存（リポジトリには書かない）
    keychain.set(SERVICE, "src", src)
    key = keychain.get(SERVICE, "key") or secrets.token_urlsafe(24)
    keychain.set(SERVICE, "key", key)
    script = script_text(key, book, src, get_store_key())
    copied = keychain.copy_to_clipboard(script)
    head = "③ スクリプトをクリップボードにコピーしました。" if copied else "③ 下のスクリプトをコピーしてください:\n" + script
    print(f"\n{head}\n{GUIDE}")
    url = input("④ その URL（script.google.com で始まる）を貼り付けて Enter: ").strip()
    if not url.startswith("https://script.google.com/"):
        print("URL が違うようです。スプレッドシートの URL ではなく、デプロイ後に出る「ウェブアプリ」の URL"
              "（https://script.google.com/macros/s/…/exec）です。もう一度 setup を実行してください。")
        sys.exit(1)
    keychain.set(SERVICE, "url", url)
    print("保存しました。試しに読み込みます…")
    sync(dry_run=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="古物台帳を data/ledger.csv に取り込む")
    ap.add_argument("cmd", nargs="?", choices=["sync", "setup", "script", "single"], default="sync")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "setup":
        cmd_setup()
    elif args.cmd == "script":
        cmd_script()
    elif args.cmd == "single":
        cmd_single()
    else:
        sync(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
