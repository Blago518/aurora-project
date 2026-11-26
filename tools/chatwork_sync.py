#!/usr/bin/env python3
"""Chatwork の販売報告（日報チャットの「販売通知」）を公式 API で読み、data/chatwork_sales.csv に追記する.

スクレイピングではなく Chatwork の公式 API（https://api.chatwork.com/v2）だけを使う。
API トークンはこのファイルにもチャットにも書かず、Mac のキーチェーンに保存する（keychain.py）。
頒布会の商品リスト（xlsx。シート「カメラ商品一覧」）が専用のルームに添付される場合は、それも data/hanpukai_YYYY-MM.xlsx に取り込む
（setup でそのルームを指定したときだけ。Excel で受け取るなら、ダウンロードフォルダに置けば update_all.py が取り込む）。
API で読めるのは、そのルームの最新100件のメッセージまで。こまめに実行すれば取りこぼさない（update_all.py が毎回実行する）。
100件より前の報告はスクショを Claude に送って取り込む（.claude/skills/chatwork-import）。

使い方（aurora フォルダで）:
    python3 tools/chatwork_sync.py setup     # API トークンとルーム（URL）を保存（初回）
    python3 tools/chatwork_sync.py           # 新しい販売報告を取り込む
    python3 tools/chatwork_sync.py --dry-run # 取り込む内容を表示するだけ

販売通知の書式（1件分）:
    [00104] [Top MINT+++w/Box] Nikon AF-S Teleconverter TC-14E II 1.4x Extender Converter JP
    [販売価格] $169.00 (eBay)
    https://www.ebay.com/itm/377577821275
    [仕入価格] 11000円
    [仕入先] キタムラ
    [利益] 10525円(概算)
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
OUT = DATA / "chatwork_sales.csv"
FIELDS = ["date", "title", "ebay_query", "sold_price_usd", "shipping_charged_usd", "purchase_jpy", "ebay_url", "source"]
API = "https://api.chatwork.com/v2"
SERVICE = "aurora-chatwork"
FILES_STATE = DATA / "chatwork_files.json"  # 取り込み済みの添付ファイル（同じファイルを2回ダウンロードしない）
BACKUP = DATA / "backup"

sys.path.insert(0, str(HERE))
import appraise as ba  # noqa: E402
import keychain  # noqa: E402


def secret_get(name: str) -> str | None:
    return keychain.get(SERVICE, name)


def secret_set(name: str, value: str) -> None:
    keychain.set(SERVICE, name, value)


# ---------------------------------------------------------------------------
# 販売通知の読み取り
# ---------------------------------------------------------------------------

# Chatwork の装飾タグ（[info][title]…）。販売通知の [00104] [Top MINT] [販売価格] などは残す
CW_TAGS = re.compile(r"\[/?(?:info|title|code|hr|qt|qtmeta[^\]]*|To:\d+|toall|rp[^\]]*|piconname:\d+|picon:\d+|"
                     r"preview[^\]]*|download[^\]]*|task[^\]]*)\]", re.I)
ITEM_START = re.compile(r"\[\d{3,8}\]")  # [00104] 管理番号で1件が始まる（行の途中でもよい）


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_sales(text: str, date: str) -> list[dict]:
    """販売通知の本文から販売を取り出す。読めない項目がある報告は飛ばす（推測しない）."""
    text = re.sub(r"\[title\].*?\[/title\]", "\n", text.replace("\r", ""), flags=re.S)  # 見出し（販売通知）は商品名ではない
    text = CW_TAGS.sub("", text)
    starts = [m.start() for m in ITEM_START.finditer(text)]
    if not starts and "[販売価格]" in text:
        # 管理番号 [00104] の無い報告も1件として読む（前は黙って飛ばしていた。2026-11-26 オーナー「追記0件とは？」）。
        # 商品名は [販売価格] のすぐ上の行
        head = [x for x in text.split("[販売価格]")[0].splitlines() if x.strip()]
        if head:
            text = "[00000] " + head[-1] + "\n[販売価格]" + text.split("[販売価格]", 1)[1]
            starts = [0]
    rows = []
    for a, b in zip(starts, starts[1:] + [len(text)]):
        block = text[a:b]
        head = block.split("[販売価格]")[0]
        title = re.sub(r"^\s*\[\d{3,8}\]\s*", "", " ".join(head.split()))  # 管理番号は除く。状態表記 [MINT] は残す（状態の表示に使う）
        price = re.search(r"\[販売価格\]\s*\$\s*([\d,]+(?:\.\d+)?)", block)
        url = re.search(r"https?://(?:www\.)?ebay\.[a-z.]+/itm/(?:[^\s/]+/)?(\d+)", block)
        cost = re.search(r"\[仕入価格\]\s*([\d,]+)\s*円", block)
        shop = re.search(r"\[仕入先\]\s*(.+)", block)
        profit = re.search(r"\[利益\]\s*(-?[\d,]+)", block)
        if not (title and price):
            continue
        clean = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", title)  # 照合用は状態表記を除く
        tag = (shop.group(1).strip() if shop else "")
        if profit and (_num(profit.group(1)) or 0) < 0:
            tag += "・赤字"
        rows.append({
            "date": date, "title": title, "ebay_query": ba.title_to_query(clean),
            "sold_price_usd": f"{_num(price.group(1)):g}", "shipping_charged_usd": 0,
            "purchase_jpy": int(_num(cost.group(1))) if cost else "",
            "ebay_url": f"https://www.ebay.com/itm/{url.group(1)}" if url else "",
            "source": f"Chatwork販売実績({tag})" if tag else "Chatwork販売実績",
        })
    return rows


def read_existing(path: Path = OUT) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return FIELDS, []
    rd = csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines())
    return list(rd.fieldnames or FIELDS), list(rd)


def _key(r: dict) -> str:
    u = re.search(r"/itm/(?:[^\s/]+/)?(\d+)", r.get("ebay_url") or "")
    return "itm:" + u.group(1) if u else f"{r.get('date')}|{r.get('title')}|{r.get('sold_price_usd')}"


def append_new(rows: list[dict], path: Path = OUT, dry_run: bool = False) -> list[dict]:
    """eBay の商品番号（無ければ 日付・商品名・価格）で重複を除いて追記. 追記した行を返す."""
    fields, old = read_existing(path)
    seen = {_key(r) for r in old}
    new = []
    for r in rows:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            new.append(r)
    if new and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = fields + [f for f in FIELDS if f not in fields]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(old + new)
    return new


# ---------------------------------------------------------------------------
# Chatwork API
# ---------------------------------------------------------------------------


def _get(token: str, path: str):
    req = urllib.request.Request(f"{API}{path}", headers={"X-ChatWorkToken": token})
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read()
    return json.loads(body) if body else []


def fetch_messages(token: str, room: str) -> list[dict]:
    """ルームの最新メッセージ（最大100件）. force=1 で既読に関係なく取る."""
    return _get(token, f"/rooms/{room}/messages?force=1")


def sheet_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
        return re.findall(r'<sheet[^>]*\bname="([^"]+)"', xml)
    except (zipfile.BadZipFile, KeyError, OSError):
        return []


def hanpukai_name(filename: str, upload_time: int) -> str:
    m = re.search(r"(20\d\d)[^\d]?(0[1-9]|1[0-2])", filename)
    ym = f"{m.group(1)}-{m.group(2)}" if m else datetime.fromtimestamp(upload_time).strftime("%Y-%m")
    return f"hanpukai_{ym}.xlsx"


def sync_files(token: str, room: str, days: int = 120, dry_run: bool = False) -> int:
    """ルームに添付された頒布会の商品リスト（xlsx・シート「カメラ商品一覧」）を data/hanpukai_YYYY-MM.xlsx に取り込む."""
    done = json.loads(FILES_STATE.read_text(encoding="utf-8")) if FILES_STATE.exists() else {}
    limit = datetime.now().timestamp() - days * 86400
    files = [f for f in _get(token, f"/rooms/{room}/files")
             if str(f.get("filename", "")).lower().endswith(".xlsx") and f.get("upload_time", 0) >= limit
             and str(f.get("file_id")) not in done]
    n = 0
    for f in sorted(files, key=lambda f: f.get("upload_time", 0)):  # 古い順 → 同じ月なら新しい方が残る
        fid = str(f["file_id"])
        if dry_run:
            print(f"  添付: {f['filename']}（取り込み候補）", file=sys.stderr)
            continue
        url = _get(token, f"/rooms/{room}/files/{fid}?create_download_url=1").get("download_url")
        if not url:
            continue
        tmp = DATA / f".dl_{fid}.xlsx"
        DATA.mkdir(exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as res, open(tmp, "wb") as out:
            shutil.copyfileobj(res, out)
        if "カメラ商品一覧" in sheet_names(tmp):
            dest = DATA / hanpukai_name(f["filename"], f.get("upload_time", 0))
            if dest.exists():
                BACKUP.mkdir(exist_ok=True)
                shutil.move(str(dest), str(BACKUP / f"{dest.stem}_{datetime.now():%Y%m%d-%H%M%S}.xlsx"))
            shutil.move(str(tmp), str(dest))
            print(f"  頒布会リスト: {f['filename']} → data/{dest.name}", file=sys.stderr)
            n += 1
        else:
            tmp.unlink()
        done[fid] = f["filename"]
    if not dry_run:
        FILES_STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    return n


def sync(dry_run: bool = False) -> int:
    """新しく追記した件数を返す。トークン未設定なら -1."""
    token, room = secret_get("token"), secret_get("room")
    if not token or not room:
        print("Chatwork: 未設定のため飛ばしました（python3 tools/chatwork_sync.py setup）", file=sys.stderr)
        return -1
    try:
        msgs = fetch_messages(token, room)
    except urllib.error.HTTPError as e:
        hint = {401: "トークンが正しくないか期限切れ", 403: "このルームを読む権限がない", 404: "ルームが見つからない"}.get(e.code, "")
        print(f"Chatwork: 読み込みに失敗（{e.code} {hint}）。setup をやり直してください", file=sys.stderr)
        return 0
    except urllib.error.URLError as e:
        print(f"Chatwork: 接続できませんでした（{e.reason}）", file=sys.stderr)
        return 0
    rows, unread = [], []
    for m in msgs:
        body = m.get("body", "")
        if "[販売価格]" not in body:
            continue
        date = datetime.fromtimestamp(m.get("send_time", 0)).strftime("%Y-%m-%d")
        got = parse_sales(body, date)
        rows += got
        if not got:
            unread.append(f"{date} " + " ".join(CW_TAGS.sub("", body).split())[:50])
    new = append_new(rows, dry_run=dry_run)
    for r in new:
        print(f"  + {r['date']} ${r['sold_price_usd']} {r['title'][:60]}（{r['source']}）", file=sys.stderr)
    for u in unread:  # 販売報告らしいのに読めなかった物は黙って飛ばさない
        print(f"  ? 読めなかった報告: {u}", file=sys.stderr)
    times = [m.get("send_time", 0) for m in msgs if m.get("send_time")]
    since = datetime.fromtimestamp(min(times)).strftime("%m/%d %H:%M") if times else "-"
    total = len(read_existing()[1])
    # 言い方（2026-11-26 オーナー「追記0件とは？」）: 0件は「見た範囲の報告は全部もう入っている」こと
    print(f"Chatwork: 日報チャットの最新 {len(msgs)}件（{since} 以降。Chatwork の API で読めるのはこの分だけ）に販売報告 {len(rows)}件。"
          + (f"うち新しい {len(new)}件を{'追記予定' if dry_run else '追記'}" if new else "全部もう取り込み済みで、新しい報告はありません")
          + (f"・読めなかった報告 {len(unread)}件" if unread else "")
          + f"（{OUT.relative_to(ROOT)} は全 {total}件）", file=sys.stderr)
    # 頒布会の商品リスト（添付の xlsx）。専用のルームを設定したときだけ（日報チャットの添付は見ない）
    froom = secret_get("files_room")
    if not froom or froom == room:
        return len(new)
    try:
        got = sync_files(token, froom, dry_run=dry_run)
        if got:
            print(f"Chatwork: 頒布会リスト {got}件を取り込み", file=sys.stderr)
    except (urllib.error.URLError, OSError) as e:
        print(f"Chatwork: 添付ファイルを読めませんでした（{e}）", file=sys.stderr)
    return len(new)


def cmd_setup() -> None:
    print("Chatwork の API トークンを入力してください（Chatwork の右上メニュー → サービス連携 → API トークン）。")
    print("入力した文字は表示されません。キーチェーンに保存し、チャットやファイルには書きません。")
    token = getpass.getpass("API トークン: ").strip()
    url = input("日報チャット（販売報告）を開いたときのブラウザの URL（例 https://www.chatwork.com/#!rid123456789）: ").strip()
    m = re.search(r"rid(\d+)", url) or re.fullmatch(r"(\d+)", url)
    if not token or not m:
        print("トークンかルームの URL が読み取れませんでした。もう一度実行してください。")
        sys.exit(1)
    furl = input("頒布会の商品リスト（xlsx）が添付されるルームの URL（Chatwork で受け取っていなければ何も入れずに Enter）: ").strip()
    fm = re.search(r"rid(\d+)", furl) or re.fullmatch(r"(\d+)", furl)
    secret_set("token", token)
    secret_set("room", m.group(1))
    secret_set("files_room", fm.group(1) if fm else "")  # 空＝添付は見ない（頒布会の Excel はダウンロードフォルダに置けば update_all.py が取り込む）
    print("保存しました。試しに読み込みます…")
    sync(dry_run=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chatwork の販売報告を data/chatwork_sales.csv に取り込む")
    ap.add_argument("cmd", nargs="?", choices=["sync", "setup"], default="sync")
    ap.add_argument("--dry-run", action="store_true", help="追記せずに表示だけ")
    args = ap.parse_args(argv)
    if args.cmd == "setup":
        cmd_setup()
    else:
        sync(args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
