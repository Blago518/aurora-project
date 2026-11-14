#!/usr/bin/env python3
"""eBay 公式 API との連携（自分のアカウントの売上・出品・手数料、出品中の相場）.

スクレイピングではなく eBay Developer の公式 API だけを使う。
キーはこのファイルにもチャットにも書かず、Mac のキーチェーン（なければ data/.ebay_secrets.json）に保存する。

使い方（aurora フォルダで）:
    python3 tools/ebay_api.py setup        # App ID・Cert ID・RuName を入力してキーチェーンに保存（初回）
    python3 tools/ebay_api.py login        # ブラウザで自分の eBay アカウントの利用を許可（初回・約1年半ごと）
    python3 tools/ebay_api.py check        # 接続確認
    python3 tools/ebay_api.py orders       # 自分の売上（直近90日。--days 730 で2年）→ reports/ebay_orders_*.csv（金額は currency 列の通貨）
    python3 tools/ebay_api.py listings     # 自分の出品中の一覧（在庫日数順）→ reports/listings_*.csv
    python3 tools/ebay_api.py fees         # 取引明細 → reports/ebay_finances_*.csv、手数料率を通貨ごとに集計
    python3 tools/ebay_api.py browse "Nikon FM2"   # 出品中の相場（公式 Browse API）

保存しない情報: 購入者の名前・住所・ユーザー名（国だけ保存）。
"""
from __future__ import annotations

import argparse
import base64
import csv
import getpass
import json
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

API = "https://api.ebay.com"
APIZ = "https://apiz.ebay.com"
AUTH = "https://auth.ebay.com/oauth2/authorize"
SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.finances",
]
KEYCHAIN_SERVICE = "aurora-ebay"
SECRET_FILE = DATA / ".ebay_secrets.json"
FIELDS = ["app_id", "cert_id", "ru_name", "refresh_token"]


# ---------------------------------------------------------------------------
# 秘密情報の保存（Mac: キーチェーン / それ以外: data/.ebay_secrets.json 権限600）
# ---------------------------------------------------------------------------

def _is_mac() -> bool:
    return platform.system() == "Darwin"


def secret_get(name: str) -> str | None:
    if _is_mac():
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    if SECRET_FILE.exists():
        return json.loads(SECRET_FILE.read_text()).get(name)
    return None


def secret_set(name: str, value: str) -> None:
    if _is_mac():
        subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", name, "-w", value],
                       check=True, capture_output=True)
        return
    DATA.mkdir(exist_ok=True)
    d = json.loads(SECRET_FILE.read_text()) if SECRET_FILE.exists() else {}
    d[name] = value
    SECRET_FILE.write_text(json.dumps(d))
    SECRET_FILE.chmod(0o600)


def need(name: str) -> str:
    v = secret_get(name)
    if not v:
        hint = "python3 tools/ebay_api.py login" if name == "refresh_token" else "python3 tools/ebay_api.py setup"
        raise SystemExit(f"{name} が未設定です。先に `{hint}` を実行してください。")
    return v


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _basic() -> str:
    raw = f"{need('app_id')}:{need('cert_id')}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _post_form(url: str, data: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"eBay の認証でエラー（HTTP {e.code}）: {e.read().decode(errors='replace')[:300]}")


_token_cache: dict[str, tuple[str, float]] = {}


def app_token() -> str:
    """出品中の検索（Browse API）用のアプリのトークン（約2時間有効）."""
    if "app" in _token_cache and _token_cache["app"][1] > time.time() + 60:
        return _token_cache["app"][0]
    d = _post_form(f"{API}/identity/v1/oauth2/token",
                   {"grant_type": "client_credentials", "scope": SCOPES[0]}, {"Authorization": _basic()})
    _token_cache["app"] = (d["access_token"], time.time() + d.get("expires_in", 7200))
    return d["access_token"]


def user_token() -> str:
    """自分のアカウントのデータ用トークン（refresh_token から約2時間分を発行）."""
    if "user" in _token_cache and _token_cache["user"][1] > time.time() + 60:
        return _token_cache["user"][0]
    d = _post_form(f"{API}/identity/v1/oauth2/token",
                   {"grant_type": "refresh_token", "refresh_token": need("refresh_token"), "scope": " ".join(SCOPES)},
                   {"Authorization": _basic()})
    _token_cache["user"] = (d["access_token"], time.time() + d.get("expires_in", 7200))
    return d["access_token"]


def get_json(url: str, token: str, extra: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                               "Content-Type": "application/json", **(extra or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"eBay API エラー（HTTP {e.code}）: {url.split('?')[0]}\n{e.read().decode(errors='replace')[:400]}")


# ---------------------------------------------------------------------------
# コマンド
# ---------------------------------------------------------------------------

def cmd_setup(_args) -> None:
    print("eBay Developer（developer.ebay.com → Application Keysets → Production）の値を入力してください。")
    print("入力した値は画面に表示されず、" + ("Mac のキーチェーン" if _is_mac() else str(SECRET_FILE)) + " に保存されます。\n")
    for name, label in (("app_id", "App ID (Client ID)"), ("cert_id", "Cert ID (Client Secret)"),
                        ("ru_name", "RuName（User Tokens 画面の “eBay Redirect URL name”）")):
        v = getpass.getpass(f"{label}: ").strip()
        if v:
            secret_set(name, v)
    print("\n保存しました。次に `python3 tools/ebay_api.py login` を実行してください。")


def cmd_login(_args) -> None:
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": need("app_id"), "response_type": "code", "redirect_uri": need("ru_name"),
        "scope": " ".join(SCOPES), "prompt": "login"}, quote_via=urllib.parse.quote)
    print("ブラウザで eBay のログイン画面を開きます。自分の販売アカウントでログインし「同意（Agree）」を押してください。")
    print("同意後に移動したページの URL（アドレスバー）を丸ごとコピーして、下に貼り付けて Enter。\n")
    webbrowser.open(url)
    pasted = getpass.getpass("移動先のURL（表示されません）: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [pasted])[0]
    d = _post_form(f"{API}/identity/v1/oauth2/token",
                   {"grant_type": "authorization_code", "code": code, "redirect_uri": need("ru_name")},
                   {"Authorization": _basic()})
    secret_set("refresh_token", d["refresh_token"])
    days = int(d.get("refresh_token_expires_in", 0)) // 86400
    print(f"\n連携できました（有効期限 約{days}日）。`python3 tools/ebay_api.py check` で確認できます。")


def cmd_check(_args) -> None:
    ok = []
    app_token(); ok.append("アプリのキー（Browse API）")
    user_token(); ok.append("自分のアカウント")
    d = get_json(f"{API}/sell/fulfillment/v1/order?limit=1", user_token())
    ok.append(f"注文の読み取り（全{d.get('total', 0)}件）")
    print("接続OK: " + " / ".join(ok))


def _money(d: dict | None) -> float:
    try:
        return float((d or {}).get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def _cur(d: dict | None) -> str:
    return (d or {}).get("currency", "")


def _orig(d: dict | None) -> str:
    """通貨換算があったときの元の金額（例: "93.43 AUD"）。無ければ空."""
    d = d or {}
    return f"{d['convertedFromValue']} {d.get('convertedFromCurrency', '')}" if d.get("convertedFromValue") else ""


def fetch_orders(days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"filter": f"creationdate:[{since}..]", "limit": 200, "offset": offset})
        d = get_json(f"{API}/sell/fulfillment/v1/order?{q}", user_token())
        for o in d.get("orders", []):
            ship_to = ((o.get("fulfillmentStartInstructions") or [{}])[0].get("shippingStep") or {}).get("shipTo") or {}
            country = (ship_to.get("contactAddress") or {}).get("countryCode", "")
            for li in o.get("lineItems", []):
                ship_d = (li.get("deliveryCost") or {}).get("shippingCost")
                item_d = li.get("lineItemCost")
                total_d = (o.get("pricingSummary") or {}).get("total")
                fee_d = o.get("totalMarketplaceFee")
                rows.append({
                    "date": o.get("creationDate", "")[:10],
                    "order_id": o.get("orderId", ""),
                    "title": li.get("title", ""),
                    "sku": li.get("sku", ""),
                    "quantity": li.get("quantity", 1),
                    # 金額の通貨は currency 列を見る（英国・豪州サイト経由は GBP・AUD のことがある）
                    "currency": _cur(item_d),
                    "item_price": _money(item_d),
                    "shipping": _money(ship_d),
                    "order_total": _money(total_d),
                    "marketplace_fee": _money(fee_d),
                    "fee_currency": _cur(fee_d),
                    "item_price_original": _orig(item_d),
                    "order_total_original": _orig(total_d),
                    "site": li.get("listingMarketplaceId", ""),  # EBAY_US / EBAY_GB / EBAY_AU など
                    "country": country,  # 購入者の名前・住所・IDは保存しない
                    "status": o.get("orderFulfillmentStatus", ""),
                    "payment_status": o.get("orderPaymentStatus", ""),  # PAID / FULLY_REFUNDED / PARTIALLY_REFUNDED など
                    "cancel_state": (o.get("cancelStatus") or {}).get("cancelState", ""),
                    "ebay_url": f"https://www.ebay.com/itm/{li.get('legacyItemId', '')}",
                })
        offset += 200
        if offset >= d.get("total", 0):
            break
    return rows


def cmd_orders(args) -> None:
    rows = fetch_orders(args.days)
    REPORTS.mkdir(exist_ok=True)
    detail = REPORTS / f"ebay_orders_{datetime.now():%Y%m%d}.csv"
    with open(detail, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["date"])
        w.writeheader(); w.writerows(rows)
    # 相場データ（自分の販売実績）として保存。販売価格は送料込み（古物台帳と同じ扱い）
    sales = DATA / "ebay_sales.csv"
    with open(sales, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "title", "ebay_query", "sold_price_usd", "shipping_charged_usd",
                                          "purchase_jpy", "ebay_url", "source"])
        w.writeheader()
        skipped = 0
        for r in rows:
            if r["item_price"] <= 0:
                continue
            if r["currency"] != "USD":  # ドル以外は相場データに混ぜない（明細には残る）
                skipped += 1
                continue
            w.writerow({"date": r["date"], "title": r["title"], "ebay_query": "", "sold_price_usd": r["item_price"],
                        "shipping_charged_usd": r["shipping"], "purchase_jpy": "", "ebay_url": r["ebay_url"],
                        "source": "自分の販売実績(eBay API)"})
    by_cur: dict[str, float] = {}
    for r in rows:
        by_cur[r["currency"] or "?"] = by_cur.get(r["currency"] or "?", 0) + r["item_price"] + r["shipping"]
    print(f"直近{args.days}日の注文 {len(rows)}件・売上（送料込み）: "
          + " / ".join(f"{v:,.2f} {k}" for k, v in sorted(by_cur.items())))
    if skipped:
        print(f"  ドル以外の {skipped}件は相場データに入れていません（明細の currency 列を確認）")
    # 拡張機能の相場は古物台帳（data/ledger.csv → reference_prices.csv）から作る。同じ販売を二重に数えないよう、ここの結果は混ぜない
    print(f"→ {sales}（ドル建ての販売の控え。拡張機能の相場は古物台帳から作るので、ここは混ぜません）\n→ {detail}（明細）")


def cmd_listings(_args) -> None:
    rows, page = [], 1
    while True:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ActiveList><Include>true</Include><Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination></ActiveList>
  <DetailLevel>ReturnAll</DetailLevel>
</GetMyeBaySellingRequest>"""
        req = urllib.request.Request(f"{API}/ws/api.dll", data=body.encode(), headers={
            "X-EBAY-API-CALL-NAME": "GetMyeBaySelling", "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1193", "X-EBAY-API-IAF-TOKEN": user_token(),
            "Content-Type": "text/xml"})
        with urllib.request.urlopen(req, timeout=60) as r:
            root = ET.fromstring(r.read())
        ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
        if root.findtext("e:Ack", namespaces=ns) not in ("Success", "Warning"):
            raise SystemExit("eBay API エラー: " + (root.findtext(".//e:LongMessage", namespaces=ns) or "不明"))
        al = root.find("e:ActiveList", ns)
        now = datetime.now(timezone.utc)
        for it in (al.findall(".//e:Item", ns) if al is not None else []):
            start = it.findtext("e:ListingDetails/e:StartTime", default="", namespaces=ns)
            days = (now - datetime.fromisoformat(start.replace("Z", "+00:00"))).days if start else ""
            price_el = it.find("e:SellingStatus/e:CurrentPrice", ns)
            if price_el is None:
                price_el = it.find("e:BuyItNowPrice", ns)
            rows.append({
                "days_listed": days,
                "title": it.findtext("e:Title", default="", namespaces=ns),
                # Site が返らないことがあるので、通貨から補う（eBay Mag で英国・豪州に複製した出品）
                "site": it.findtext("e:Site", default="", namespaces=ns)
                or {"USD": "US", "GBP": "UK", "AUD": "Australia", "EUR": "EU"}.get(
                    price_el.get("currencyID", "") if price_el is not None else "", ""),
                "currency": price_el.get("currencyID", "") if price_el is not None else "",
                "price": (price_el.text or "") if price_el is not None else "",
                "watchers": it.findtext("e:WatchCount", default="0", namespaces=ns),
                "quantity": it.findtext("e:QuantityAvailable", default="", namespaces=ns),
                "sku": it.findtext("e:SKU", default="", namespaces=ns),
                "ebay_url": f"https://www.ebay.com/itm/{it.findtext('e:ItemID', default='', namespaces=ns)}",
            })
        pages = int((al.findtext("e:PaginationResult/e:TotalNumberOfPages", default="1", namespaces=ns)
                     if al is not None else "1") or 1)
        if page >= pages:
            break
        page += 1
    rows.sort(key=lambda r: -(r["days_listed"] or 0))
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"listings_{datetime.now():%Y%m%d}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["days_listed"])
        w.writeheader(); w.writerows(rows)
    old = [r for r in rows if isinstance(r["days_listed"], int) and r["days_listed"] >= 60]
    print(f"出品中 {len(rows)}件（60日以上: {len(old)}件）→ {out}")
    for r in rows[:10]:
        print(f"  {r['days_listed']:>4}日  {r['price']:>8} {r['currency']:<3} {r['site'][:9]:<9}  ウォッチ{r['watchers']:>3}  {r['title'][:60]}")


def _refs(t: dict) -> str:
    return " ".join(f"{r.get('referenceType', '')}:{r.get('referenceId', '')}" for r in t.get("references", []) or [])


def cmd_fees(args) -> None:
    """取引明細（Finances API）を1行ずつ保存し、手数料率を通貨ごとに集計する.

    SALE の amount は入金通貨（USD）、手数料の対象額・手数料は出品サイトの通貨（GBP・AUD のことがある）。
    通貨が混ざった合計は意味がないので、率は通貨ごとに出す。
    """
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    # Finances API は1回に約90日分までしか返さないので、60日ずつに分けて取る
    windows = []
    while start < end:
        w_end = min(start + timedelta(days=60), end)
        windows.append((start, w_end))
        start = w_end
    rows, seen = [], set()
    for w_start, w_end in windows:
        offset = 0
        while True:
            q = urllib.parse.urlencode({"filter": f"transactionDate:[{w_start.strftime(fmt)}..{w_end.strftime(fmt)}]",
                                        "limit": 200, "offset": offset})
            d = get_json(f"{APIZ}/sell/finances/v1/transaction?{q}", user_token())
            for t in d.get("transactions", []):
                if t.get("transactionId") in seen:  # 期間の境目で重複しないように
                    continue
                seen.add(t.get("transactionId"))
                intl = sum(_money(f.get("amount")) for li in t.get("orderLineItems", []) or []
                           for f in li.get("marketplaceFees", []) or [] if "INTERNATIONAL" in f.get("feeType", ""))
                rows.append({
                    "date": (t.get("transactionDate") or "")[:10],
                    "type": t.get("transactionType", ""),
                    "booking": t.get("bookingEntry", ""),  # CREDIT=入金 / DEBIT=引き落とし
                    "order_id": t.get("orderId", ""),
                    "amount": _money(t.get("amount")),
                    "amount_currency": _cur(t.get("amount")),
                    "amount_original": _orig(t.get("amount")),
                    "fee_basis": _money(t.get("totalFeeBasisAmount")),
                    "fee_basis_currency": _cur(t.get("totalFeeBasisAmount")),
                    "total_fee": _money(t.get("totalFeeAmount")),
                    "fee_currency": _cur(t.get("totalFeeAmount")),
                    "intl_fee": round(intl, 2),
                    "fee_type": t.get("feeType", ""),
                    "memo": t.get("transactionMemo", ""),
                    "references": _refs(t),
                    "status": t.get("transactionStatus", ""),
                })
            offset += 200
            if offset >= d.get("total", 0):
                break
    rows.sort(key=lambda r: r["date"], reverse=True)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"ebay_finances_{datetime.now():%Y%m%d}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["date"])
        w.writeheader(); w.writerows(rows)
    sales = [r for r in rows if r["type"] == "SALE"]
    if not sales:
        print(f"直近{args.days}日の売上取引がありません。→ {out}")
        return
    print(f"直近{args.days}日の取引 {len(rows)}行（うち売上 {len(sales)}件）→ {out}")
    for cur in sorted({r["fee_basis_currency"] or r["amount_currency"] for r in sales}):
        grp = [r for r in sales if (r["fee_basis_currency"] or r["amount_currency"]) == cur]
        basis = sum(r["fee_basis"] for r in grp) or 1
        fee, intl = sum(r["total_fee"] for r in grp), sum(r["intl_fee"] for r in grp)
        print(f"  {cur}: {len(grp)}件 対象額 {basis:,.2f}  手数料 {fee / basis * 100:.2f}%"
              f"（うち海外決済 {intl / basis * 100:.2f}%）")
    other: dict[str, float] = {}
    for r in rows:
        if r["type"] != "SALE":
            k = f"{r['type']} {r['fee_type']}".strip() + f" [{r['amount_currency']}]"
            other[k] = other.get(k, 0) + (r["amount"] if r["booking"] != "CREDIT" else -r["amount"])
    for k, v in sorted(other.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v:,.2f}")
    print("広告費（AD_FEE）は上の一覧。率は Claude が注文ごとに計算します。")


def browse_search(query: str, limit: int = 50) -> list[dict]:
    """出品中の商品を公式 Browse API で検索（米国サイト、価格+送料の安い順）."""
    q = urllib.parse.urlencode({"q": query, "limit": limit, "sort": "price",
                                "filter": "buyingOptions:{FIXED_PRICE|AUCTION}"})
    d = get_json(f"{API}/buy/browse/v1/item_summary/search?{q}", app_token(),
                 {"X-EBAY-C-MARKETPLACE-ID": "EBAY_US"})
    out = []
    for it in d.get("itemSummaries", []):
        ship = min((_money(s.get("shippingCost")) for s in it.get("shippingOptions", []) if s.get("shippingCost")),
                   default=0.0)
        out.append({"title": it.get("title", ""), "price_usd": _money(it.get("price")), "shipping_usd": ship,
                    "total_usd": _money(it.get("price")) + ship, "condition": it.get("condition", ""),
                    "country": (it.get("itemLocation") or {}).get("country", ""), "url": it.get("itemWebUrl", "")})
    return out


def cmd_browse(args) -> None:
    items = browse_search(args.query)
    if not items:
        print("出品中の商品が見つかりませんでした。")
        return
    totals = [i["total_usd"] for i in items]
    jp = [i for i in items if i["country"] == "JP"]
    print(f"「{args.query}」出品中 {len(items)}件（うち日本から {len(jp)}件）"
          f" 最安 ${min(totals):,.2f} ・ 中央値 ${statistics.median(totals):,.2f}（送料込み）")
    for i in items[:15]:
        print(f"  ${i['total_usd']:>8.2f}  {i['country']:>2}  {i['condition'][:14]:<14} {i['title'][:60]}")
    print("※ 売れた価格（sold）は公式APIでは取れません。sold ボタン・Product Research で確認してください。")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="eBay 公式 API 連携（自分のアカウント・出品中の相場）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="App ID・Cert ID・RuName を保存（初回）").set_defaults(fn=cmd_setup)
    sub.add_parser("login", help="自分の eBay アカウントの利用を許可（初回・約1年半ごと）").set_defaults(fn=cmd_login)
    sub.add_parser("check", help="接続確認").set_defaults(fn=cmd_check)
    p = sub.add_parser("orders", help="自分の売上を取り込む → data/ebay_sales.csv"); p.add_argument("--days", type=int, default=90)
    p.set_defaults(fn=cmd_orders)
    sub.add_parser("listings", help="自分の出品中の一覧（在庫日数順）").set_defaults(fn=cmd_listings)
    p = sub.add_parser("fees", help="実際の手数料率を集計"); p.add_argument("--days", type=int, default=90)
    p.set_defaults(fn=cmd_fees)
    p = sub.add_parser("browse", help="出品中の相場（公式 Browse API）"); p.add_argument("query")
    p.set_defaults(fn=cmd_browse)
    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
