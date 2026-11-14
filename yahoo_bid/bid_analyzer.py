#!/usr/bin/env python3
"""ヤフオク入札判断ツール.

ヤフオクのブックマーク（URL一覧・ブラウザのブックマークHTML・ウォッチリストを保存したHTML・CSV）を読み込み、
eBayの販売済み／出品中の相場と過去の販売実績から、想定販売価格・利益・上限入札額を計算してレポートを出力する。

使い方:
    python bid_analyzer.py samples/bookmarks.txt --sales samples/sales_history.csv
    python bid_analyzer.py bookmarks.html --offline          # 通信せず手入力値と販売実績だけで計算
    python bid_analyzer.py items.csv --fx 152 --out out/     # 為替を指定

標準ライブラリのみで動作する（Python 3.9 以上。Macの標準 python3 でOK）。
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
AUCTION_ID_RE = re.compile(r"auctions\.yahoo\.co\.jp/(?:jp/)?auction/([a-z]?\d{6,12})", re.I)

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def _parse_simple_toml(text: str) -> dict:
    """tomllib が無い Python 3.9/3.10 用。config.toml 程度の単純な書式だけ読む."""
    data: dict = {}
    section = data
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line[1:-1].strip(), {})
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if val in ("true", "false"):
            v = val == "true"
        elif val.startswith('"') and val.endswith('"'):
            v = val[1:-1]
        else:
            v = float(val) if any(c in val for c in ".eE") else int(val)
        section[key.strip()] = v
    return data


def load_config(path: Path | None) -> dict:
    path = path or HERE / "config.toml"
    try:
        import tomllib
    except ModuleNotFoundError:
        return _parse_simple_toml(path.read_text(encoding="utf-8"))
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class Item:
    url: str
    auction_id: str = ""
    title: str = ""
    current_price_jpy: int | None = None
    buy_now_jpy: int | None = None
    bids: int | None = None
    end_time: str = ""
    domestic_shipping_jpy: int | None = None
    image: str = ""
    watch: int | None = None
    prefilled: bool = False  # 検索結果ページから価格等を取得済み（個別ページを開かない）
    # 手入力の上書き
    ebay_query: str = ""
    ebay_price_usd: float | None = None
    ship_size: str = ""
    intl_shipping_jpy: int | None = None
    memo: str = ""
    # 分析結果
    fetch_note: str = ""
    flags: list[str] = field(default_factory=list)
    sold_prices_usd: list[float] = field(default_factory=list)
    active_prices_usd: list[float] = field(default_factory=list)
    history_matches: list[dict] = field(default_factory=list)
    expected_price_usd: float | None = None
    price_source: str = ""
    calc: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 入力の読み込み
# ---------------------------------------------------------------------------


def normalize_yahoo_url(text: str) -> tuple[str, str] | None:
    m = AUCTION_ID_RE.search(text)
    if not m:
        return None
    aid = m.group(1)
    return f"https://auctions.yahoo.co.jp/jp/auction/{aid}", aid


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("円", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _float_or_none(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_items(path: Path) -> list[Item]:
    """URL一覧(txt)・ブックマークHTML・ウォッチリストHTML・CSVを読み込む."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    items: list[Item] = []
    seen: set[str] = set()

    if path.suffix.lower() == ".csv":
        for row in csv.DictReader(text.splitlines()):
            norm = normalize_yahoo_url(row.get("yahoo_url") or row.get("url") or "")
            if not norm or norm[1] in seen:
                continue
            seen.add(norm[1])
            items.append(
                Item(
                    url=norm[0],
                    auction_id=norm[1],
                    title=(row.get("title") or "").strip(),
                    current_price_jpy=_int_or_none(row.get("current_price_jpy")),
                    buy_now_jpy=_int_or_none(row.get("buy_now_jpy")),
                    bids=_int_or_none(row.get("bids")),
                    end_time=(row.get("end_time") or "").strip(),
                    domestic_shipping_jpy=_int_or_none(row.get("domestic_shipping_jpy")),
                    ebay_query=(row.get("ebay_query") or "").strip(),
                    ebay_price_usd=_float_or_none(row.get("ebay_price_usd")),
                    ship_size=(row.get("ship_size") or "").strip().lower(),
                    intl_shipping_jpy=_int_or_none(row.get("intl_shipping_jpy")),
                    memo=(row.get("memo") or "").strip(),
                )
            )
        return items

    # txt / html: URLを全部拾う。HTMLのブックマークなら <a> のテキストをタイトル候補にする
    anchor_titles: dict[str, str] = {}
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        norm = normalize_yahoo_url(html.unescape(m.group(1)))
        if norm:
            t = re.sub(r"<[^>]+>", "", m.group(2))
            t = html.unescape(t).strip()
            if t and norm[1] not in anchor_titles:
                anchor_titles[norm[1]] = re.sub(r"\s*-\s*Yahoo!オークション.*$", "", t)
    for m in AUCTION_ID_RE.finditer(text):
        aid = m.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        items.append(
            Item(
                url=f"https://auctions.yahoo.co.jp/jp/auction/{aid}",
                auction_id=aid,
                title=anchor_titles.get(aid, ""),
            )
        )
    return items


# ---------------------------------------------------------------------------
# 通信
# ---------------------------------------------------------------------------


def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


def fetch_fx() -> float | None:
    for url, getter in (
        ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["JPY"]),
        ("https://api.frankfurter.app/latest?from=USD&to=JPY", lambda d: d["rates"]["JPY"]),
    ):
        try:
            return float(getter(json.loads(http_get(url, timeout=10))))
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# ヤフオクの検索結果URL（Safariのブックマーク等）→ 出品URLに展開
# ---------------------------------------------------------------------------

YAHOO_LIST_RE = re.compile(r"https?://auctions\.yahoo\.co\.jp/[^\s\"'<>]+", re.I)


def find_search_urls(text: str) -> list[str]:
    """出品ページ以外のヤフオクURL（検索結果・出品者・カテゴリ一覧）を拾う。落札相場は除く."""
    out: list[str] = []
    for m in YAHOO_LIST_RE.finditer(text):
        u = html.unescape(m.group(0)).rstrip(").,")
        if AUCTION_ID_RE.search(u) or "closedsearch" in u:
            continue
        path = urllib.parse.urlparse(u).path
        if path.strip("/") == "" or not re.search(r"search|seller|category|list|user", path):
            continue
        if u not in out:
            out.append(u)
    return out


def _with_page(url: str, start: int, per_page: int = 100) -> str:
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    q["b"], q["n"] = str(start), str(per_page)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def parse_listing_page(page: str) -> tuple[list[Item], int | None]:
    """検索結果・出品者ページの __NEXT_DATA__ から出品一覧を読む（1ページ最大100件）."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return [], None
    data = json.loads(m.group(1))
    listing = None
    for d in _walk(data):
        if isinstance(d.get("items"), list) and d["items"] and isinstance(d["items"][0], dict) \
                and "auctionId" in d["items"][0]:
            listing = d
            break
    if listing is None:
        return [], None
    total = None
    t = re.search(r'"totalResultsAvailable"\s*:\s*(\d+)', m.group(1))
    if t:
        total = int(t.group(1))
    items = []
    for x in listing["items"]:
        if x.get("isFleamarketItem"):
            continue
        ship = x.get("currentPriceShippingFee")
        if x.get("isFreeShipping"):
            ship = 0
        items.append(Item(
            url=f"https://auctions.yahoo.co.jp/jp/auction/{x['auctionId']}",
            auction_id=x["auctionId"],
            title=x.get("title") or "",
            current_price_jpy=_int_or_none(x.get("price")),
            buy_now_jpy=_int_or_none(x.get("buyNowPrice")) or None,
            bids=_int_or_none(x.get("bidCount")),
            end_time=x.get("endTime") or "",
            domestic_shipping_jpy=_int_or_none(ship),
            image=x.get("imageUrl") or "",
            watch=_int_or_none(x.get("watchCount")),
            prefilled=True,
            memo="ストア" if (x.get("seller") or {}).get("isStore") else "",
        ))
    return items, total


def fetch_listing(url: str, limit: int, delay: float) -> list[Item]:
    """検索URLの結果を100件ずつページ送りして最大 limit 件取得する."""
    out: list[Item] = []
    start = 1
    while len(out) < limit:
        items, total = parse_listing_page(http_get(_with_page(url, start)))
        if not items:
            break
        out += items
        start += 100
        if total is not None and start > total:
            break
        time.sleep(delay)
    return out[:limit]


# ---------------------------------------------------------------------------
# ヤフオクのページ解析
# ---------------------------------------------------------------------------


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def parse_yahoo_page(page: str) -> dict:
    """ヤフオクの商品ページHTMLから情報を抜き出す（ページ構造の変更に備えて複数の方法を試す）."""
    out: dict = {}
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', page)
    if m:
        out["title"] = re.sub(r"\s*-\s*Yahoo!オークション.*$", "", html.unescape(m.group(1))).strip()
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', page)
    if m:
        out["image"] = html.unescape(m.group(1))

    blobs: list = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            blobs.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    m = re.search(r"var\s+pageData\s*=\s*(\{.*?\});", page, re.S)
    if m:
        try:
            blobs.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass

    for blob in blobs:
        for d in _walk(blob):
            has_id = any(k in d for k in ("auctionId", "productID", "aID"))
            if not has_id or not any(k in d for k in ("price", "taxinPrice", "currentPrice")):
                continue
            price = _pick(d, "taxinPrice", "currentPrice", "price")
            out.setdefault("current_price_jpy", _int_or_none(price))
            bin_ = _pick(d, "taxinBidorbuy", "bidorbuy", "buyNowPrice", "winPrice")
            if bin_ and _int_or_none(bin_):
                out.setdefault("buy_now_jpy", _int_or_none(bin_))
            bids = _pick(d, "bids", "bidCount")
            if bids is not None:
                out.setdefault("bids", _int_or_none(bids))
            end = _pick(d, "endTime", "endDate", "endtime")
            if end:
                out.setdefault("end_time", str(end))
            title = _pick(d, "title", "productName")
            # og:title は長いと「...」で切れるため、JSON側の完全なタイトルを優先
            if title and len(str(title)) >= len(out.get("title", "").rstrip(".… ")):
                out["title"] = str(title)
            if out.get("current_price_jpy"):
                break

    if "current_price_jpy" not in out:  # 最後の手段: 表示テキストから
        m = re.search(r"現在[^0-9]{0,40}?([\d,]+)\s*円", re.sub(r"<[^>]+>", " ", page))
        if m:
            out["current_price_jpy"] = _int_or_none(m.group(1))
    if "bids" not in out:
        m = re.search(r'"bids"\s*:\s*"?(\d+)', page)
        if m:
            out["bids"] = int(m.group(1))
    m = re.search(r"送料[^0-9無]{0,30}?([\d,]{3,})\s*円", re.sub(r"<[^>]+>", " ", page))
    if m:
        out["domestic_shipping_jpy"] = _int_or_none(m.group(1))
    elif re.search(r"送料無料", page):
        out["domestic_shipping_jpy"] = 0
    return out


def enrich_from_yahoo(item: Item) -> None:
    try:
        info = parse_yahoo_page(http_get(item.url))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        item.fetch_note += f"ヤフオク取得失敗({e.__class__.__name__}) "
        return
    for k, v in info.items():
        if v in (None, ""):
            continue
        if getattr(item, k) in (None, ""):  # 手入力値を優先
            setattr(item, k, v)


# ---------------------------------------------------------------------------
# 日本語タイトル → eBay検索語
# ---------------------------------------------------------------------------

BRANDS_JA = [
    ("富士フイルム", "Fujifilm"), ("富士フィルム", "Fujifilm"), ("フジフイルム", "Fujifilm"),
    ("フジフィルム", "Fujifilm"), ("ニコン", "Nikon"), ("キヤノン", "Canon"), ("キャノン", "Canon"),
    ("ソニー", "Sony"), ("オリンパス", "Olympus"), ("ペンタックス", "Pentax"), ("リコー", "Ricoh"),
    ("パナソニック", "Panasonic"), ("ライカ", "Leica"), ("ミノルタ", "Minolta"), ("コニカ", "Konica"),
    ("コンタックス", "Contax"), ("ハッセルブラッド", "Hasselblad"), ("ローライ", "Rollei"),
    ("マミヤ", "Mamiya"), ("シグマ", "Sigma"), ("タムロン", "Tamron"), ("トキナー", "Tokina"),
    ("ヤシカ", "Yashica"), ("カシオ", "Casio"), ("フォクトレンダー", "Voigtlander"),
    ("ブロニカ", "Bronica"), ("ツァイス", "Zeiss"), ("コダック", "Kodak"), ("京セラ", "Kyocera"),
]
# 検索語に残す日本語（色など）
WORDS_JA = [("ブラック", "Black"), ("シルバー", "Silver"), ("クローム", "Chrome"), ("チタン", "Titanium")]
BRANDS_EN = {b.lower(): b for _, b in BRANDS_JA} | {
    "fuji": "Fujifilm", "om": "OM", "lumix": "Panasonic", "carl": "Carl",
}
LENS_MAKERS = {"tamron", "sigma", "tokina", "kenko", "samyang", "vivitar", "soligor"}
LENS_LINES = {"zuiko": "Olympus", "nikkor": "Nikon", "takumar": "Pentax", "rokkor": "Minolta",
              "sekor": "Mamiya", "serenar": "Canon", "hexanon": "Konica", "planar": "Zeiss",
              "sonnar": "Zeiss", "distagon": "Zeiss", "summicron": "Leica", "summilux": "Leica", "elmarit": "Leica"}
NOISE_TOKENS = {
    "1", "the", "and", "with", "from", "japan", "mint", "exc", "exc+", "exc++", "exc+++", "near",
    "n", "a", "b", "c", "ab", "aa", "aaa", "s", "sale", "free", "shipping", "used", "ok",
}
FLAG_RULES = [
    ("ジャンク", r"ジャンク|部品取り|現状品|動作未確認|動作不良|不具合|故障|for\s*parts"),
    ("光学難あり", r"カビ|クモリ|曇り|バルサム|ヤケ|傷あり|キズあり|拭き傷"),
    ("動作品", r"動作確認済|動作品|動作良好|完動"),
    ("美品", r"超美品|極上|美品|新品同様|未使用"),
    ("セット品", r"セット|まとめ|おまとめ|大量|\d+\s*点"),
    ("アダプター等", r"アダプタ|adapter|コンバーター|converter|接写リング|中間リング|エクステンション|テレプラス|"
                   r"teleplus|テレコンバーター|塗料|ペイント|マウント変換|→"),
    ("付属品のみ?", r"(?:キャップ|ストラップ|ケース|フード|フィルター|背面|裏蓋|データバック|ファインダー)"
                   r"(?![\s　]?[^\s　]{0,14}(?:付|あり|有|セット))|カタログ|取扱説明書|説明書のみ|\bback\b"),
]


def detect_flags(title: str) -> list[str]:
    return [name for name, pat in FLAG_RULES if re.search(pat, title, re.I)]


def title_to_query(title: str, max_tokens: int = 7) -> str:
    t = unicodedata.normalize("NFKC", title)
    t = re.sub(r"/[A-Z]{1,2}\d{3}\s*$", " ", t)  # ストアの管理番号（…/ジャンク/A077）
    t = re.sub(r"\d+円[~〜～]?", " ", t)  # 「1円～」
    t = re.sub(r"\b[wW]\s*/\s*", " ", t)  # w/case（with）
    t = re.sub(r"(\d)\s*(mm|㎜)\s*([fF]\s*/?\s*\d)", r"\1mm \3", t)  # 75-150mmF4 → 75-150mm F4
    t = re.sub(r"[①-⑳]", " ", t)  # まとめ⑤ 等の丸数字
    t = re.sub(r"非\s*-?\s*A[iI](?![-sS])", " nonAi ", t)  # 非Ai（Ai/Ai-Sとは相場が違う）
    t = re.sub(r"[#＃No.]{1,3}\s*[A-Za-z]{0,3}-?\d{3,}[A-Za-z]?\b", " ", t)  # #v4081 等の管理番号
    t = re.sub(r"(\d)\s*[・･]\s*(\d)", r"\1.\2", t)        # f2・8 → f2.8
    t = re.sub(r"\b[fF]\s*/\s*(\d)", r"F\1", t)              # f/2.8 → F2.8
    t = re.sub(r"\b1\s*:\s*(\d(?:\.\d)?)\b", r"F\1", t)      # 1:1.8 → F1.8
    t = re.sub(r"\b(EF-S|EF|RF|FD|XF|FE)(\d)", r"\1 \2", t, flags=re.I)  # EF135mm → EF 135mm
    t = re.sub(r"\b([fF]\d(?:\.\d)?(?:-\d(?:\.\d)?)?)([LGD])\b", r"\1 \2", t)  # F2L → F2 L
    t = re.sub(r"[【】\[\]（）()「」『』★☆◆◇■□●○◎※!！?？/|｜,、。:：~〜♪#＃]+", " ", t)
    brand = ""
    all_brands: list[str] = []
    for ja, en in BRANDS_JA:
        if ja in t:
            brand = brand or en
            all_brands.append(en)
            t = t.replace(ja, " ")
    for ja, en in WORDS_JA:
        t = t.replace(ja, f" {en} ")
    tokens: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9αµ][A-Za-z0-9αµ.\-+]*", t):
        low = tok.lower().strip(".-")
        if not low or low in NOISE_TOKENS:
            continue
        # 1桁の数字は検索語では落とすが、照合用（max_tokens>7）では残す（EOS 5 / Nikon 1 等）
        if re.fullmatch(r"\d+", low) and ((len(low) < 2 and max_tokens <= 7) or len(low) >= 4):
            continue  # 「1円」の1や、シリアル番号っぽい長い数字
        if re.fullmatch(r"no\.?\d+", low):
            continue
        line = re.sub(r"^[a-z]\.", "", low)  # g.zuiko → zuiko
        if line in LENS_LINES:  # レンズ銘からメーカーを補う（語は残す）
            brand = brand or LENS_LINES[line]
        if low in BRANDS_EN:
            brand = brand or BRANDS_EN[low]
            all_brands.append(BRANDS_EN[low])
            if BRANDS_EN[low] != "Carl":
                continue
        if not any(x.lower() == low for x in tokens):
            tokens.append(tok)
    makers = [b for b in all_brands if b.lower() in LENS_MAKERS]
    if makers:  # 「タムロン … ニコン用」はタムロン製
        brand = makers[0]
    if max_tokens <= 7:  # eBay検索語では一般的な表記に
        tokens = ["Non-Ai" if x == "nonAi" else x for x in tokens]
    words = ([brand] if brand else []) + tokens
    if max_tokens > 7:  # 照合用: タイトル中の全メーカー名を残す（「タムロン ニコン用」等）
        words += [b for b in dict.fromkeys(all_brands) if b != brand]
    return " ".join(words[:max_tokens] if max_tokens <= 7 else words)


# ---------------------------------------------------------------------------
# eBay
# ---------------------------------------------------------------------------


def ebay_links(query: str) -> dict:
    q = urllib.parse.quote_plus
    return {
        # 販売済み: 価格+送料の高い順
        "sold": f"https://www.ebay.com/sch/i.html?_nkw={q(query)}&LH_Sold=1&LH_Complete=1&_sop=16&_ipg=120",
        # 出品中: 価格+送料の高い順
        "active": f"https://www.ebay.com/sch/i.html?_nkw={q(query)}&_sop=16&_ipg=120",
        # Product Research（Terapeak）: Best Offer込みの実成約を確認
        "research": "https://www.ebay.com/sh/research?"
        + urllib.parse.urlencode(
            {"marketplace": "EBAY-US", "keywords": query, "dayRange": "90", "tabName": "SOLD"},
            quote_via=urllib.parse.quote,
        ),
    }


def parse_ebay_prices(page: str, fx: float) -> list[float]:
    """eBay検索結果HTMLから価格（USD）を抜き出す。円表示なら為替で換算."""
    prices: list[float] = []
    for m in re.finditer(
        r'class="[^"]*s-(?:item|card)__price[^"]*"[^>]*>(.*?)</(?:span|div)>', page, re.S
    ):
        txt = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        usd = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", txt)
        jpy = re.search(r"(?:JPY|¥|円)\s*([\d,]+)|([\d,]+)\s*円", txt)
        if usd:
            prices.append(float(usd.group(1).replace(",", "")))
        elif jpy:
            prices.append(float((jpy.group(1) or jpy.group(2)).replace(",", "")) / fx)
    # 先頭の「Shop on eBay」ダミー行（$20.00固定）を除外
    if prices and "Shop on eBay" in page[:200000] and prices[0] == 20.0:
        prices = prices[1:]
    return [p for p in prices if p > 0]


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return math.nan
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def trim_outliers(values: list[float]) -> list[float]:
    """中央値から大きく外れた価格（付属品だけ・セット品など）を除く."""
    if len(values) < 5:
        return values
    med = statistics.median(values)
    return [v for v in values if med / 3 <= v <= med * 3]


# ---------------------------------------------------------------------------
# 過去の販売実績
# ---------------------------------------------------------------------------


def _tokens(s: str) -> set[str]:
    q = title_to_query(s, max_tokens=20)
    q = unicodedata.normalize("NFKC", q).lower()
    q = re.sub(r"(\d)\s*mm", r"\1mm", q)
    q = re.sub(r"\bf\s*/\s*(\d)", r"f\1", q)  # f/1.4 → f1.4
    q = re.sub(r"[()\[\]+&]", " ", q)
    out = set()
    for t in re.split(r"[\s/,]+", q):
        t = t.strip(".-")
        if not t or t in NOISE_TOKENS or t in ("body", "lens", "本体"):
            continue
        if re.fullmatch(r"\d{1,2}\.\d", t):  # 1.4 → f1.4（F値の表記ゆれ）
            t = "f" + t
        if t in ("ais", "ai-s"):
            t = "ai-s"
        out.add(t)
    return out


# eBay Seller Hub の「注文レポート」CSVの列名にも対応する
SALES_ALIASES = {
    "title": ("title", "Item Title", "Title"),
    "sold_price_usd": ("sold_price_usd", "Sold For", "Sold for", "Item Subtotal", "Total Price"),
    "shipping_charged_usd": ("shipping_charged_usd", "Shipping And Handling", "Shipping and handling"),
    "date": ("date", "Sale Date", "Paid On Date"),
}


def read_sales(path: Path | None) -> list[dict]:
    if not path:
        return []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    # レポート先頭の説明行を飛ばして見出し行を探す
    start = next(
        (i for i, ln in enumerate(lines) if "sold_price_usd" in ln or "Item Title" in ln), 0
    )
    rows = []
    for raw in csv.DictReader(lines[start:]):
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        for key, names in SALES_ALIASES.items():
            if not row.get(key):
                row[key] = next((row[n] for n in names if row.get(n)), "")
        if not row.get("ebay_url") and row.get("Item Number"):
            row["ebay_url"] = f"https://www.ebay.com/itm/{row['Item Number']}"
        price = _float_or_none(row.get("sold_price_usd"))
        if price is None:
            continue
        row["_price"] = price + (_float_or_none(row.get("shipping_charged_usd")) or 0)
        row["_tokens"] = _tokens(row.get("ebay_query") or row.get("title") or "")
        rows.append(row)
    return rows


ACCESSORY_RE = re.compile(r"strap|(?<!w/)\bcase\b|grip|charger|battery|hood only|\bcap\b|catalog", re.I)
THIRD_PARTY = {"tamron", "sigma", "tokina", "7artisans", "ttartisan", "brightin", "kenko", "pixco", "k&f", "meike", "viltrox", "laowa",
               "samyang", "yongnuo", "kipon", "fotodiox", "neewer", "commlite", "sg-image", "pergear",
               "kamlan", "zonlai", "fujian", "rokinon", "vello"}
COLOR_WORDS = {"black", "silver", "chrome", "white", "red", "blue", "green", "brown"}
GENERATIONS = {"ii", "iii", "iv", "mkii", "mkiii", "mk2", "mk3", "mark"}
PREMIUM_VARIANTS = {"gold", "titanium", "titan", "limited", "限定", "whiteface", "af"}


SOURCE_LABELS = ["自分の販売実績", "Chatwork販売実績", "頒布会リスト", "鑑定表"]


def source_tier(row: dict) -> int:
    src = row.get("source") or "自分の販売実績"
    if src.startswith("Chatwork"):
        return 1
    if src.startswith(("頒布会", "eBay")):
        return 2
    if src.startswith("鑑定表"):
        return 3
    return 0


def match_sales(query: str, sales: list[dict], threshold: float = 0.6) -> list[dict]:
    qt = _tokens(query)
    if not qt:
        return []
    brands = {b.lower() for b in BRANDS_EN.values()}
    scored = []
    for row in sales:
        rt = row["_tokens"]
        # 社外レンズ（タイトルに「オリンパス用」等とあっても別物）
        if (qt & THIRD_PARTY) - rt:
            continue
        # 実績側のメーカーがタイトルに無ければ別物（社外品・他社用アダプター等）
        rb = rt & brands
        if rb and not (rb & qt):
            continue
        # レンズ（mm表記あり）とボディ（なし）を取り違えない
        if any(t.endswith("mm") for t in qt) != any(t.endswith("mm") for t in rt):
            continue
        # 付属品だけの実績（ストラップ・ケース等）は本体の参考にしない
        if ACCESSORY_RE.search(row.get("title", "")) and not ACCESSORY_RE.search(query):
            continue
        # 限定・チタン・ゴールド等は相場が別物なので、片方にだけあれば除外
        if (qt & PREMIUM_VARIANTS) != (rt & PREMIUM_VARIANTS):
            continue
        # 非Ai と Ai/Ai-S は相場が違う
        if ("nonai" in qt and rt & {"ai", "ai-s"}) or ("nonai" in rt and qt & {"ai", "ai-s"}):
            continue
        # II / III などの世代違いは別機種
        if (qt & GENERATIONS) != (rt & GENERATIONS):
            continue
        # 型番・焦点距離・F値など数字を含む語は、実績側のものが全部一致すること
        digits = {t for t in rt if re.search(r"\d", t)}
        if not digits <= qt:
            continue
        if not digits:  # 型番に数字が無い機種（Rolleicord Vb 等）は実績側の語が全部そろうこと
            core = rt - COLOR_WORDS
            if not (core - brands) or not core <= qt:
                continue
        # 実績側の語がどれだけ含まれるか（ヤフオクのタイトルは余計な語が多いので片側で見る）
        score = len(qt & rt) / len(rt)
        if score >= threshold:
            scored.append((score, row))
    # 自分の実績 → Chatwork（他メンバーの実績）→ 頒布会リスト の順に優先
    chosen = []
    for tier in (0, 1, 2):
        chosen = [x for x in scored if source_tier(x[1]) == tier]
        if chosen:
            break
    chosen.sort(key=lambda x: -x[0])
    return [dict(r, _score=round(s, 2)) for s, r in chosen[:10]]


# ---------------------------------------------------------------------------
# 鑑定ブックマークレットの相場表を読む
# ---------------------------------------------------------------------------


def _read_text_any(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        import zipfile
        x = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", x, re.S)
        return html.unescape("\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", q, re.S)) for q in paras))
    return path.read_text(encoding="utf-8-sig", errors="replace")


def read_kantei_table(path: Path) -> list[dict]:
    """ブックマークレット内の相場表 h=[[商品名, 想定$, 根拠, [照合パターン...], 高額フラグ], ...] を読む.

    途中で切れたファイルでも、読めたところまで使う。
    """
    js = urllib.parse.unquote(_read_text_any(path))
    variables = {m.group(1): json.loads(f"\"{m.group(2)}\"")
                 for m in re.finditer(r'[,{;\s]([a-z])="((?:[^"\\]|\\.)*)"', js.split("h=[[", 1)[0])}
    rows = []
    entry_re = re.compile(r'\["((?:[^"\\]|\\.)*)",([\d.e]+),"([^"]*)",\[(.*?)\],(\d)\]')
    elem_re = re.compile(r'"((?:[^"\\]|\\.)*)"|\b([a-z])\b')
    for m in entry_re.finditer("[" + js.split("h=[[", 1)[-1] if "h=[[" in js else ""):
        pats = []
        for e in elem_re.finditer(m.group(4)):
            pats.append(json.loads(f'"{e.group(1)}"') if e.group(1) is not None else variables.get(e.group(2), ""))
        try:
            compiled = [re.compile(x, re.I) for x in pats if x]
        except re.error:
            continue
        rows.append({
            "title": json.loads(f'"{m.group(1)}"'), "_price": float(m.group(2)),
            "source": f"鑑定表({m.group(3)})", "_patterns": compiled,
        })
    return rows


def match_kantei(title: str, table: list[dict]) -> list[dict]:
    """ブックマークレットと同じく、上から順に全パターンが一致した最初の行を返す."""
    t = unicodedata.normalize("NFKC", title).lower()
    qt = _tokens(title)
    for row in table:
        if not all(p.search(t) for p in row["_patterns"]):
            continue
        # 表のパターンが緩い場合の保険: 型番（数字を含む語）とレンズ/ボディの区別を確認
        rt = _tokens(row["title"])
        if (qt & THIRD_PARTY) - rt:  # 「for Nikon」等の社外品
            continue
        if "nonai" in qt and rt & {"ai", "ai-s"}:
            continue
        if any(x.endswith("mm") for x in qt) != any(x.endswith("mm") for x in rt):
            continue
        model = {x for x in rt if re.search(r"\d", x) and not x.endswith("mm") and not x.startswith("f")}
        if model and not model <= qt:
            continue
        return [dict(row, _score=1.0)]
    return []


# ---------------------------------------------------------------------------
# 利益計算
# ---------------------------------------------------------------------------

YAHOO_INCREMENTS = [(1000, 10), (5000, 100), (10000, 250), (50000, 500), (math.inf, 1000)]


def round_down_to_increment(price: float) -> int:
    for limit, step in YAHOO_INCREMENTS:
        if price < limit:
            return int(price // step * step)
    return int(price)


def intl_shipping(item: Item, cfg: dict, fx: float) -> int:
    if item.intl_shipping_jpy is not None:
        return item.intl_shipping_jpy
    s = cfg["shipping"]
    if s.get("deduction_usd", 0) > 0:  # 販売価格から一律ドルで差し引く方式
        return round(s["deduction_usd"] * fx)
    return int(s.get(f"{item.ship_size}_jpy", s["default_jpy"])) if item.ship_size else int(s["default_jpy"])


def calculate(item: Item, cfg: dict, fx: float) -> dict:
    """想定販売価格から手取り・利益・上限入札額を計算する."""
    if item.expected_price_usd is None:
        return {}
    e, y, t, j = cfg["ebay"], cfg["yahoo"], cfg["tax"], cfg["judge"]
    revenue_usd = item.expected_price_usd + e["buyer_shipping_usd"]
    fee_pct = e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]
    fees_usd = revenue_usd * fee_pct / 100 + e["per_order_fee_usd"]
    net_jpy = (revenue_usd - fees_usd) * fx * (1 - cfg["fx"]["conversion_loss_pct"] / 100)
    revenue_jpy = revenue_usd * fx

    dom = item.domestic_shipping_jpy
    dom = int(y["default_domestic_shipping_jpy"]) if dom is None else dom
    ship = intl_shipping(item, cfg, fx)
    pack = int(cfg["shipping"]["packing_jpy"])
    tax_mult = 1 + y["store_tax_pct"] / 100
    refund_ratio = (
        t["consumption_tax_pct"] / (100 + t["consumption_tax_pct"])
        if t["include_consumption_tax_refund"]
        else 0.0
    )
    # 入札額1円あたりの実コスト
    k = tax_mult * (1 - refund_ratio)
    fixed = net_jpy - dom - ship - pack  # 入札額以外を引いた残り

    def profit_at(bid: float) -> float:
        return fixed - bid * k

    # 上限入札額: 利益額と利益率の両方の基準を満たす最大値
    by_profit = (fixed - j["min_profit_jpy"]) / k
    by_margin = (fixed - j["min_margin_pct"] / 100 * revenue_jpy) / k
    max_bid = max(0, round_down_to_increment(min(by_profit, by_margin)))
    breakeven = max(0, round_down_to_increment(fixed / k))

    price_now = item.current_price_jpy
    out = {
        "fx": fx,
        "net_sale_usd": round(item.expected_price_usd - cfg["shipping"].get("deduction_usd", 0), 2),
        "revenue_jpy": round(revenue_jpy),
        "fees_jpy": round(fees_usd * fx),
        "net_jpy": round(net_jpy),
        "domestic_shipping_jpy": dom,
        "intl_shipping_jpy": ship,
        "packing_jpy": pack,
        "max_bid_jpy": max_bid,
        "breakeven_bid_jpy": breakeven,
    }
    if price_now is not None:
        purchase = round(price_now * tax_mult + dom)
        p = profit_at(price_now)
        out.update(
            purchase_jpy=purchase,
            profit_now_jpy=round(p),
            margin_now_pct=round(p / revenue_jpy * 100, 1) if revenue_jpy else None,
            headroom_jpy=max_bid - price_now,
        )
    if item.buy_now_jpy:
        out["profit_buy_now_jpy"] = round(profit_at(item.buy_now_jpy))
    return out


def judge(item: Item) -> tuple[str, str]:
    c = item.calc
    if not c:
        return "要確認", "eBay相場が取れていません（ebay_price_usd を手入力してください）"
    if "ジャンク" in item.flags:
        prefix = "ジャンク品: 動作・状態リスクあり。"
    else:
        prefix = ""
    now = item.current_price_jpy
    if {"付属品のみ?", "アダプター等", "セット品"} & set(item.flags):
        return "要確認", prefix + "付属品だけの出品の可能性。本体か商品ページで確認してください"
    if c["max_bid_jpy"] <= 0:
        return "×", prefix + "基準を満たす入札額がありません"
    now = item.current_price_jpy
    if now is None:
        return "要確認", prefix + f"現在価格不明。上限 {c['max_bid_jpy']:,}円 まで"
    if now <= c["max_bid_jpy"] * 0.8:
        return "◎", prefix + f"上限 {c['max_bid_jpy']:,}円 まで余裕あり"
    if now <= c["max_bid_jpy"]:
        return "○", prefix + f"上限 {c['max_bid_jpy']:,}円 に近い。終了間際の競り上がりに注意"
    return "×", prefix + f"現在価格が上限 {c['max_bid_jpy']:,}円 を超過"


# ---------------------------------------------------------------------------
# 分析の流れ
# ---------------------------------------------------------------------------


def assign_price(item: Item, cfg: dict, fx: float) -> None:
    """想定販売価格: 手入力 > eBay販売済み（個別取得） > 実績・相場データ の順に決めて利益を計算."""
    q_level = cfg["judge"]["sold_price_quantile"]
    if item.ebay_price_usd is not None:
        item.expected_price_usd, item.price_source = item.ebay_price_usd, "手入力"
    elif len(item.sold_prices_usd) >= 3:
        item.expected_price_usd = round(quantile(item.sold_prices_usd, q_level), 2)
        item.price_source = f"eBay販売済み{len(item.sold_prices_usd)}件の{int(q_level*100)}%点"
    elif item.history_matches:
        ps = [m["_price"] for m in item.history_matches]
        item.expected_price_usd = round(statistics.median(ps), 2)
        src = str(item.history_matches[0].get("source", ""))
        label = SOURCE_LABELS[source_tier(item.history_matches[0])]
        if src.startswith(("相場メモ", "鑑定表", "eBay")):
            label = src
        item.price_source = f"{label} {len(ps)}件の中央値" if len(ps) > 1 else label
    if item.expected_price_usd is not None and "ジャンク" in item.flags and item.ebay_price_usd is None:
        factor = cfg["judge"].get("junk_price_factor", 1.0)
        if factor < 1:
            item.expected_price_usd = round(item.expected_price_usd * factor, 2)
            item.price_source += f" ×ジャンク{int(factor * 100)}%"
    item.calc = calculate(item, cfg, fx)


def analyze(items: list[Item], sales: list[dict], cfg: dict, fx: float, offline: bool,
            fetch_ebay: bool, delay: float, kantei: list[dict] | None = None) -> None:
    for i, item in enumerate(items, 1):
        if not item.prefilled:
            print(f"[{i}/{len(items)}] {item.auction_id} {item.title[:40]}", file=sys.stderr)
        if not offline and not item.prefilled:
            enrich_from_yahoo(item)
            time.sleep(delay)
        item.flags = detect_flags(item.title)
        if not item.ebay_query:
            item.ebay_query = title_to_query(item.title) if item.title else ""
        if not item.ebay_query:
            item.fetch_note += "検索語を作れません（title か ebay_query を入力） "
            continue

        # 照合はタイトル全体（検索語は7語に絞っているため）
        item.history_matches = match_sales(f"{item.ebay_query} {item.title}", sales)
        if not item.history_matches and kantei:
            item.history_matches = match_kantei(item.title, kantei)

        # 個別URLを少数渡したときだけ、出品ごとにeBayを見に行く（検索URLの大量取得では機種単位で行う）
        if not offline and fetch_ebay and not item.prefilled:
            links = ebay_links(item.ebay_query)
            for kind, attr in (("sold", "sold_prices_usd"), ("active", "active_prices_usd")):
                try:
                    setattr(item, attr, trim_outliers(parse_ebay_prices(http_get(links[kind]), fx)))
                except (urllib.error.URLError, TimeoutError, OSError) as e:
                    item.fetch_note += f"eBay{kind}取得失敗({e.__class__.__name__}) "
                time.sleep(delay)
        assign_price(item, cfg, fx)


# ---------------------------------------------------------------------------
# 相場未登録の機種を eBay 販売済みから自動で補う（キャッシュ付き）
# ---------------------------------------------------------------------------

CACHE_FIELDS = ["date", "title", "sold_price_usd", "source", "count", "ebay_url"]


def auto_price_unpriced(items: list[Item], cfg: dict, fx: float, top_n: int, delay: float,
                        cache_path: Path, max_age_days: int = 7) -> int:
    """相場が無い機種の上位 top_n 件について eBay 販売済み検索を1回ずつ見て相場を作る."""
    cache: dict[str, dict] = {}
    if cache_path.exists():
        with open(cache_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                cache[row["title"].lower()] = row
    today = datetime.now()
    new_rows: list[dict] = []
    fetched = 0
    for m in unpriced_models(items)[:top_n]:
        label = m["title"]
        row = cache.get(label.lower())
        fresh = row and (today - datetime.strptime(row["date"], "%Y-%m-%d")).days <= max_age_days
        if not fresh:
            url = ebay_links(label)["sold"] + "&_sacat=625"
            try:
                prices = trim_outliers(parse_ebay_prices(http_get(url), fx))
            except urllib.error.HTTPError as e:
                print(f"eBayに接続できません（HTTP {e.code}）。自動の相場取得を中止します。", file=sys.stderr)
                break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"eBay取得失敗({e.__class__.__name__}): {label}", file=sys.stderr)
                continue
            fetched += 1
            time.sleep(delay)
            q = cfg["judge"]["sold_price_quantile"]
            row = {"date": today.strftime("%Y-%m-%d"), "title": label,
                   "sold_price_usd": round(quantile(prices, q), 2) if len(prices) >= 5 else "",
                   "source": f"eBay販売済み自動({len(prices)}件)", "count": len(prices), "ebay_url": url}
            cache[label.lower()] = row
        if row.get("sold_price_usd"):
            new_rows.append(row)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
        w.writeheader()
        w.writerows(cache.values())
    # 取れた相場で、未判定の出品をもう一度照合
    rows = []
    for r in new_rows:
        r = dict(r)
        r["_price"] = float(r["sold_price_usd"])
        r["_tokens"] = _tokens(r["title"])
        rows.append(r)
    added = 0
    for it in items:
        if it.expected_price_usd is None and it.ebay_query:
            it.history_matches = match_sales(f"{it.ebay_query} {it.title}", rows)
            if it.history_matches:
                assign_price(it, cfg, fx)
                added += 1
    print(f"eBay自動相場: {fetched}機種を取得、{len(new_rows)}機種で相場あり → {added}件を追加判定", file=sys.stderr)
    return added


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


def _yen(v) -> str:
    return "-" if v is None else f"{v:,}円"


def _usd(v) -> str:
    return "-" if v is None else f"${v:,.2f}"


def rows_for_output(items: list[Item]) -> list[dict]:
    rows = []
    for it in items:
        mark, reason = judge(it)
        c = it.calc
        links = ebay_links(it.ebay_query) if it.ebay_query else {}
        rows.append(
            {
                "判定": mark,
                "理由": reason,
                "タイトル": it.title,
                "ヤフオクURL": it.url,
                "現在価格": it.current_price_jpy,
                "即決価格": it.buy_now_jpy,
                "入札数": it.bids,
                "終了": it.end_time,
                "eBay検索語": it.ebay_query,
                "eBay販売済みURL": links.get("sold", ""),
                "eBay出品中URL": links.get("active", ""),
                "eBayProductResearchURL": links.get("research", ""),
                "eBay販売済み中央値USD": round(statistics.median(it.sold_prices_usd), 2) if it.sold_prices_usd else None,
                "eBay販売済み件数": len(it.sold_prices_usd) or None,
                "eBay出品中最安USD": min(it.active_prices_usd) if it.active_prices_usd else None,
                "eBay出品中件数": len(it.active_prices_usd) or None,
                "過去実績件数": len(it.history_matches) or None,
                "過去実績URL": " ".join(m.get("ebay_url", "") for m in it.history_matches if m.get("ebay_url")),
                "過去実績の内訳": " / ".join(
                    f"{m.get('title', '')} ${m['_price']:g}"
                    + (f" 仕入{int(float(m['purchase_jpy'])):,}円" if str(m.get('purchase_jpy') or '').replace('.', '').isdigit() else "")
                    + f" [{m.get('source', '自分の販売実績')}]"
                    for m in it.history_matches[:5]),
                "想定販売価格USD": it.expected_price_usd,
                "価格の根拠": it.price_source,
                "Net販売価格USD": c.get("net_sale_usd"),
                "仕入価格(現在+国内送料)": c.get("purchase_jpy"),
                "eBay手数料": c.get("fees_jpy"),
                "国際送料": c.get("intl_shipping_jpy"),
                "手取り(円)": c.get("net_jpy"),
                "利益(現在価格)": c.get("profit_now_jpy"),
                "利益率%": c.get("margin_now_pct"),
                "上限入札額": c.get("max_bid_jpy"),
                "損益分岐入札額": c.get("breakeven_bid_jpy"),
                "上限までの余裕": c.get("headroom_jpy"),
                "注意": " / ".join(it.flags),
                "メモ": (it.memo + " " + it.fetch_note).strip(),
            }
        )
    order = {"◎": 0, "○": 1, "要確認": 2, "×": 3}
    # ◎○は終了が近い順（入札の段取り順）、それ以外は余裕の大きい順
    rows.sort(key=lambda r: (order[r["判定"]],
                             r["終了"] if r["判定"] in ("◎", "○") else "",
                             -(r["上限までの余裕"] or -10**9)))
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_markdown(rows: list[dict], path: Path, cfg: dict, fx: float) -> None:
    j = cfg["judge"]
    lines = [
        f"# ヤフオク入札判断レポート（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        f"- 為替: 1 USD = {fx:.2f} 円（円転ロス {cfg['fx']['conversion_loss_pct']}%）",
        f"- 基準: 利益 {j['min_profit_jpy']:,}円以上 かつ 利益率 {j['min_margin_pct']}%以上",
        f"- eBay手数料: FVF {cfg['ebay']['final_value_fee_pct']}% + 海外 {cfg['ebay']['international_fee_pct']}%"
        f" + 広告 {cfg['ebay']['ad_rate_pct']}% + ${cfg['ebay']['per_order_fee_usd']}",
        "",
    ]
    for r in rows:
        lines += [
            f"## {r['判定']} {r['タイトル'] or r['ヤフオクURL']}",
            "",
            f"- 判定理由: {r['理由']}",
            f"- ヤフオク: {r['ヤフオクURL']}",
            f"- 現在 {_yen(r['現在価格'])} / 即決 {_yen(r['即決価格'])} / 入札 {r['入札数'] if r['入札数'] is not None else '-'} / 終了 {r['終了'] or '-'}",
            f"- eBay検索語: `{r['eBay検索語']}`",
            f"- eBay販売済み: {r['eBay販売済みURL']}",
            f"- eBay出品中: {r['eBay出品中URL']}",
            f"- eBay Product Research: {r['eBayProductResearchURL']}",
            f"- eBay相場: 販売済み中央値 {_usd(r['eBay販売済み中央値USD'])}（{r['eBay販売済み件数'] or 0}件） / 出品中最安 {_usd(r['eBay出品中最安USD'])}（{r['eBay出品中件数'] or 0}件）",
            f"- 過去の実績: {r['過去実績件数'] or 0}件 {r['過去実績の内訳']}",
            f"- 想定販売価格: {_usd(r['想定販売価格USD'])}（{r['価格の根拠'] or '根拠なし'}） → 送料差引後Net {_usd(r['Net販売価格USD'])}",
            f"- 仕入価格(現在+国内送料): {_yen(r['仕入価格(現在+国内送料)'])} / eBay手数料 {_yen(r['eBay手数料'])} / 国際送料 {_yen(r['国際送料'])}",
            f"- 利益(現在価格で落札した場合): {_yen(r['利益(現在価格)'])}（{r['利益率%'] if r['利益率%'] is not None else '-'}%）",
            f"- **上限入札額: {_yen(r['上限入札額'])}**（損益分岐 {_yen(r['損益分岐入札額'])}）",
        ]
        if r["注意"]:
            lines.append(f"- 注意: {r['注意']}")
        if r["メモ"]:
            lines.append(f"- メモ: {r['メモ']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def model_key(title: str) -> tuple[str, str]:
    """相場未登録の出品を機種ごとにまとめるためのキー（表示用の検索語, 比較用キー）."""
    toks = title_to_query(title, 20).split()
    brand = toks[0] if toks and toks[0].lower() in {b.lower() for b in BRANDS_EN.values()} else ""
    rest = toks[1:] if brand else toks
    key_toks = [t for t in rest if re.search(r"\d", t)][:3] or rest[:2]
    label = " ".join(([brand] if brand else []) + key_toks)
    norm = []
    for t in label.lower().split():
        t = re.sub(r"^f/?", "", t) if re.fullmatch(r"f/?\d+(\.\d)?(-\d+(\.\d)?)?", t) else t
        norm.append("f" + t if re.fullmatch(r"\d+(\.\d)?(-\d+(\.\d)?)?", t) and "." in t else t)
    return label, " ".join(norm)


def unpriced_models(items: list[Item]) -> list[dict]:
    groups: dict[str, dict] = {}
    for it in items:
        if it.expected_price_usd is not None or not it.title:
            continue
        if {"付属品のみ?", "アダプター等", "セット品"} & set(it.flags):
            continue
        label, key = model_key(it.title)
        if not label or len(label.split()) < 2:
            continue
        g = groups.setdefault(key, {"label": label, "count": 0, "examples": [], "prices": []})
        g["count"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(f"{it.title[:50]}（{it.current_price_jpy or '-'}円）")
        if it.current_price_jpy:
            g["prices"].append(it.current_price_jpy)
    out = []
    for g in sorted(groups.values(), key=lambda g: -g["count"]):
        links = ebay_links(g["label"])
        out.append({
            "title": g["label"], "sold_price_usd": "", "source": "相場メモ",
            "件数": g["count"], "ヤフオク現在価格の中央値": int(statistics.median(g["prices"])) if g["prices"] else "",
            "出品例": " / ".join(g["examples"]),
            "eBay販売済みURL": links["sold"], "ProductResearchURL": links["research"],
        })
    return out


def write_unpriced_csv(models: list[dict], path: Path) -> None:
    fields = ["title", "sold_price_usd", "source", "件数", "ヤフオク現在価格の中央値", "出品例",
              "eBay販売済みURL", "ProductResearchURL"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(models)


def write_html(rows: list[dict], path: Path, cfg: dict, fx: float,
               unpriced: list[dict] | None = None) -> None:
    e = html.escape
    j = cfg["judge"]
    cards = []
    shown = [r for r in rows if r["判定"] in ("◎", "○") or (r["判定"] == "要確認" and r["想定販売価格USD"])]
    hidden_bad = [r for r in rows if r["判定"] == "×"]
    for r in shown:
        cls = {"◎": "good", "○": "ok", "×": "bad"}.get(r["判定"], "unk")
        def link(u, label):
            return f'<a href="{e(u)}" target="_blank" rel="noopener">{label}</a>' if u else ""
        hist = " ".join(
            link(u, f"実績{i+1}") for i, u in enumerate(r["過去実績URL"].split()) if u
        )
        cards.append(f"""
<article class="card {cls}">
  <div class="top"><span class="mark">{e(r['判定'])}</span><h2>{e(r['タイトル'] or r['ヤフオクURL'])}</h2></div>
  <p class="reason">{e(r['理由'])}{(' ・ ⚠ ' + e(r['注意'])) if r['注意'] else ''}</p>
  <div class="grid">
    <div><label>現在価格</label><b>{_yen(r['現在価格']) or '-'}</b><small>入札{r['入札数'] if r['入札数'] is not None else '-'}・即決{_yen(r['即決価格']) or '-'}</small></div>
    <div class="hl"><label>上限入札額</label><b>{_yen(r['上限入札額']) or '-'}</b><small>損益分岐 {_yen(r['損益分岐入札額']) or '-'}</small></div>
    <div><label>想定販売価格</label><b>{_usd(r['想定販売価格USD']) or '-'}</b><small>{e(r['価格の根拠'] or '根拠なし')} ・ Net {_usd(r['Net販売価格USD'])}</small></div>
    <div><label>利益（現在価格）</label><b>{_yen(r['利益(現在価格)']) or '-'}</b><small>利益率 {r['利益率%'] if r['利益率%'] is not None else '-'}%</small></div>
    <div><label>仕入価格</label><b>{_yen(r['仕入価格(現在+国内送料)']) or '-'}</b><small>現在価格＋国内送料</small></div>
    <div><label>eBay手数料／国際送料</label><b>{_yen(r['eBay手数料']) or '-'}</b><small>送料 {_yen(r['国際送料']) or '-'}</small></div>
    <div><label>eBay販売済み</label><b>{_usd(r['eBay販売済み中央値USD']) or '-'}</b><small>{r['eBay販売済み件数'] or 0}件（中央値）</small></div>
    <div><label>eBay出品中</label><b>{_usd(r['eBay出品中最安USD']) or '-'}</b><small>{r['eBay出品中件数'] or 0}件（最安）</small></div>
  </div>
  <p class="links">{link(r['ヤフオクURL'], 'ヤフオク出品')}{link(r['eBay販売済みURL'], 'eBay販売済み')}{link(r['eBay出品中URL'], 'eBay出品中')}{link(r['eBayProductResearchURL'], 'Product Research')}{hist}</p>
  {('<p class="meta">参考実績: ' + e(r['過去実績の内訳']) + '</p>') if r['過去実績の内訳'] else ''}
  <p class="meta">検索語: <code>{e(r['eBay検索語'])}</code> ・ 終了: {e(r['終了'] or '-')}{(' ・ ' + e(r['メモ'])) if r['メモ'] else ''}</p>
</article>""")
    counts = {k: sum(r["判定"] == k for r in rows) for k in ("◎", "○", "要確認", "×")}
    doc = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ヤフオク入札判断</title>
<style>
:root{{--bg:#f3f6fa;--card:#fff;--ink:#192d42;--sub:#617288;--line:#e5eaf0;--good:#207d60;--ok:#1b6387;--bad:#aa4a38;--unk:#8a6d1f;--hl:#eaf5f0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1720;--card:#172331;--ink:#e3ebf3;--sub:#93a5b8;--line:#26384a;--hl:#16362c}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Helvetica Neue",sans-serif}}
.wrap{{max-width:980px;margin:auto;padding:16px}}h1{{font-size:20px;margin:8px 0}}
.summary{{color:var(--sub);font-size:13px;line-height:1.7}}
.card{{background:var(--card);border-radius:14px;padding:14px 16px;margin:12px 0;border-left:6px solid var(--unk)}}
.card.good{{border-color:var(--good)}}.card.ok{{border-color:var(--ok)}}.card.bad{{border-color:var(--bad);opacity:.8}}
.top{{display:flex;gap:10px;align-items:baseline}}.mark{{font-size:22px;font-weight:800}}h2{{font-size:15px;margin:0;line-height:1.4}}
.reason{{font-size:13px;color:var(--sub);margin:6px 0 10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}}
.grid div{{border:1px solid var(--line);border-radius:10px;padding:8px 10px}}.grid .hl{{background:var(--hl)}}
label{{display:block;font-size:11px;color:var(--sub)}}b{{display:block;font-size:17px;margin:2px 0}}small{{font-size:11px;color:var(--sub)}}
.links{{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px}}.links a{{font-size:13px;padding:7px 11px;border-radius:9px;background:var(--hl);color:inherit;text-decoration:none;font-weight:700}}
.meta{{font-size:11px;color:var(--sub);overflow-wrap:anywhere}}code{{font-size:11px}}
h2{{font-size:16px;margin:22px 0 6px}}details{{margin:12px 0}}.mini{{font-size:12px;line-height:1.7}}.mini a{{color:inherit}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px;background:var(--card);border-radius:12px;overflow:hidden}}
.tbl td,.tbl th{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
.tbl a{{color:inherit;font-weight:700;white-space:nowrap}}
</style></head><body><div class="wrap">
<h1>ヤフオク入札判断レポート</h1>
<p class="summary">{datetime.now():%Y-%m-%d %H:%M} 作成 ・ {len(rows)}件（◎{counts['◎']} ○{counts['○']} 要確認{counts['要確認']} ×{counts['×']}）<br>
為替 1USD={fx:.2f}円（円転ロス{cfg['fx']['conversion_loss_pct']}%） ・ 基準: 利益{j['min_profit_jpy']:,}円以上かつ利益率{j['min_margin_pct']}%以上 ・
eBay手数料 FVF{cfg['ebay']['final_value_fee_pct']}%+海外{cfg['ebay']['international_fee_pct']}%+広告{cfg['ebay']['ad_rate_pct']}%+${cfg['ebay']['per_order_fee_usd']}<br>
上限入札額＝この金額で落札しても基準の利益が残る最大額（ヤフオクの入札単位で切り捨て）。eBayの販売済み価格はBest Offer成約額を含まないため、Product Researchで確認してください。</p>
<h2>入札候補（◎○は終了が近い順）</h2>
{''.join(cards) or '<p class="summary">基準を満たす出品はありませんでした。</p>'}
<details><summary>× 見送り {len(hidden_bad)}件</summary><ul class="mini">{''.join(
    f'<li><a href="{e(r["ヤフオクURL"])}" target="_blank" rel="noopener">{e(r["タイトル"][:60])}</a> 現在{_yen(r["現在価格"])} / 上限{_yen(r["上限入札額"])}</li>'
    for r in hidden_bad)}</ul></details>
<h2>相場が未登録の機種（件数順）</h2>
<p class="summary">ここに出る機種は相場データが無いため判定できていません。件数の多い機種から eBay の販売済みを見て、
<code>unpriced_models_*.csv</code> の <code>sold_price_usd</code> 欄に想定販売価格（送料込み$）を入れ、<code>data/price_memo.csv</code> に追記すると次回から判定されます。</p>
<table class="tbl"><tr><th>機種（検索語）</th><th>件数</th><th>ヤフオク現在値</th><th>eBay</th></tr>{''.join(
    f'<tr><td>{e(m["title"])}<br><small>{e(m["出品例"][:90])}</small></td><td>{m["件数"]}</td>'
    f'<td>{_yen(m["ヤフオク現在価格の中央値"] or None)}</td>'
    f'<td><a href="{e(m["eBay販売済みURL"])}" target="_blank" rel="noopener">販売済み</a> '
    f'<a href="{e(m["ProductResearchURL"])}" target="_blank" rel="noopener">PR</a></td></tr>'
    for m in (unpriced or [])[:60])}</table>
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ヤフオク入札判断ツール")
    ap.add_argument("input", type=Path, help="ヤフオクの出品URL・検索結果URLの一覧(.txt) / ブックマークHTML / CSV")
    ap.add_argument("--sales", type=Path, nargs="*", default=None,
                    help="過去の販売実績CSV（複数可）。省略時は data/ の sales.csv, chatwork_sales.csv, price_memo.csv")
    ap.add_argument("--kantei", type=Path, help="鑑定ブックマークレットのテキスト（.txt/.js/.docx）。相場表を参考に使う")
    ap.add_argument("--config", type=Path, help="設定ファイル（既定: config.toml）")
    ap.add_argument("--fx", type=float, help="為替 1USD=何円（指定すると自動取得しない）")
    ap.add_argument("--offline", action="store_true", help="通信しない（手入力値と販売実績のみで計算）")
    ap.add_argument("--no-ebay-fetch", action="store_true", help="eBay相場の自動取得をしない（リンクだけ作る）")
    ap.add_argument("--ebay-auto", type=int, default=40,
                    help="相場未登録の機種のうち件数の多い上位N機種を eBay 販売済みから自動で相場化（0で無効、7日キャッシュ）")
    ap.add_argument("--delay", type=float, default=1.5, help="通信の間隔（秒）")
    ap.add_argument("--max-per-search", type=int, default=2000, help="検索URL1つあたりに調べる出品数（上から。100件ごとに1回アクセス）")
    ap.add_argument("--out", type=Path, default=Path("out"), help="出力フォルダ")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    fx = args.fx
    if fx is None and cfg["fx"].get("auto_fetch") and not args.offline:
        fx = fetch_fx()
        if fx:
            print(f"為替を取得: 1USD = {fx:.2f}円", file=sys.stderr)
    if fx is None:
        fx = float(cfg["fx"]["usd_jpy"])
        print(f"為替は設定値を使用: 1USD = {fx:.2f}円", file=sys.stderr)

    items = read_items(args.input)
    if not args.offline:
        text = args.input.read_text(encoding="utf-8-sig", errors="replace")
        known = {it.auction_id for it in items}
        for su in find_search_urls(text):
            try:
                got = fetch_listing(su, args.max_per_search, args.delay)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                print(f"検索URLを開けません({e.__class__.__name__}): {su}", file=sys.stderr)
                continue
            new = 0
            for it in got:
                if it.auction_id not in known:
                    known.add(it.auction_id)
                    items.append(it)
                    new += 1
            print(f"検索URLから {new}件 抽出: {su[:80]}", file=sys.stderr)
            time.sleep(args.delay)
    if not items:
        print("ヤフオクのURLが見つかりません。", file=sys.stderr)
        return 1
    if args.sales is None:
        args.sales = [p for p in (HERE / "data" / n for n in ("sales.csv", "chatwork_sales.csv", "price_memo.csv"))
                      if p.exists()]
    if args.kantei is None:
        args.kantei = next(iter(sorted((HERE / "data").glob("kantei_bookmarklet.*"))), None)
    sales = [row for p in args.sales for row in read_sales(p)]
    print(f"販売実績・相場: {len(sales)}件（{', '.join(p.name for p in args.sales) or 'なし'}）", file=sys.stderr)
    kantei = read_kantei_table(args.kantei) if args.kantei else []
    if args.kantei:
        print(f"鑑定表: {len(kantei)}件の相場を読み込み", file=sys.stderr)
    analyze(items, sales, cfg, fx, args.offline, not args.no_ebay_fetch, args.delay, kantei)

    if not args.offline and not args.no_ebay_fetch and args.ebay_auto > 0:
        auto_price_unpriced(items, cfg, fx, args.ebay_auto, args.delay, HERE / "data" / "ebay_sold_cache.csv")

    rows = rows_for_output(items)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    unpriced = unpriced_models(items)
    write_csv(rows, args.out / f"bid_report_{stamp}.csv")
    write_markdown([r for r in rows if r["判定"] != "要確認" or r["想定販売価格USD"]],
                   args.out / f"bid_report_{stamp}.md", cfg, fx)
    write_html(rows, args.out / f"bid_report_{stamp}.html", cfg, fx, unpriced)
    write_unpriced_csv(unpriced, args.out / f"unpriced_models_{stamp}.csv")

    priced = sum(1 for it in items if it.expected_price_usd is not None)
    print(f"\n{len(items)}件中 相場あり {priced}件 / 相場未登録の機種 {len(unpriced)}種類", file=sys.stderr)
    print(f"\n{'判定':<4} {'現在価格':>9} {'上限入札':>9} {'利益':>9}  タイトル")
    for r in rows:
        if r["判定"] not in ("◎", "○") and len(rows) > 60:
            continue
        print(
            f"{r['判定']:<4} {_yen(r['現在価格']):>9} {_yen(r['上限入札額']):>9} "
            f"{_yen(r['利益(現在価格)']):>9}  {(r['タイトル'] or r['ヤフオクURL'])[:40]}"
        )
    print(f"\n出力: {args.out}/bid_report_{stamp}.(html|md|csv) / unpriced_models_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
