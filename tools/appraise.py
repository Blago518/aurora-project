#!/usr/bin/env python3
"""ヤフオク入札判断ツール.

ヤフオクのブックマーク（URL一覧・ブラウザのブックマークHTML・ウォッチリストを保存したHTML・CSV）を読み込み、
eBayの販売済み／出品中の相場と過去の販売実績から、想定販売価格・利益・上限入札額を計算してレポートを出力する。

使い方:
    python appraise.py samples/bookmarks.txt --sales samples/sales_history.csv
    python appraise.py bookmarks.html --offline          # 通信せず手入力値と販売実績だけで計算
    python3 tools/appraise.py items.csv --fx 152          # 為替を指定

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

HERE = Path(__file__).resolve().parent      # tools/
ROOT = HERE.parent                           # aurora/
DATA = ROOT / "data"                         # 非公開データ
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
    store_tax: bool = False  # ストア出品: 入札は税抜、支払いは税込（×1.1）
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
    history_matches: list[dict] = field(default_factory=list)
    expected_price_usd: float | None = None
    price_source: str = ""
    calc: dict = field(default_factory=dict)
    sold_hit: bool = False  # 売れ筋（根拠の販売実績で利益が出ていた機種）
    set_parts: list = field(default_factory=list)  # まとめ売りの中身 [(中身, 相場$送料込み)]（2点以上のときだけ）
    seller_id: str = ""      # 出品者（検索結果の seller.userId。ストアのページ /seller/<これ>）
    trusted: bool = False    # 安心ストア（フォロー中）→ 安心枠の基準で判定
    desc_hits: list = field(default_factory=list)  # 説明文に書かれていた難（カビ・クモリなど）
    desc_checked: bool = False  # 説明文を読んだか（check_descriptions）
    tentative: dict = field(default_factory=dict)   # 仮◎○（相場が無いが eBay 出品中の価格なら利益が出る。tentative.py）


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
            store_tax=x.get("taxFlag") == 1,
            memo="ストア" if (x.get("seller") or {}).get("isStore") else "",
            seller_id=str((x.get("seller") or {}).get("userId") or ""),
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
            if d.get("taxinPrice") and d.get("price") and _int_or_none(d["taxinPrice"]) != _int_or_none(d["price"]):
                out["store_tax"] = True
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
        if k == "store_tax":
            item.store_tax = bool(v)
            continue
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
# 同じ機種でも「型」で相場が違うもの。タイトルの書き方をそろえてから（VARIANT_NORMALIZE）、
# 型が違う実績とは照合しない。strict=True は「片方にだけ型の表記がある」場合も別物とみなす（保守的）。
# neg=True は eBay の検索語に、ほかの型を除外する語（-MMG など）も入れる。ebay は eBay 検索で使う表記。
VARIANT_NORMALIZE = [
    (r"\bAE\s*[-/]?\s*(?:West\s*)?(?:G\b|Germany\b)", " AEG "),
    (r"\bAE\s*[-/]?\s*(?:J\b|Japan\b)", " AEJ "),
    (r"\bMM\s*[-/]?\s*(?:West\s*)?(?:G\b|Germany\b)", " MMG "),
    (r"\bMM\s*[-/]?\s*(?:J\b|Japan\b)", " MMJ "),
    (r"\bAi\s*改|\bAi[-\s]?converted\b|\bAi'?d\b", " AiKai "),
    (r"\bAi\s*-?\s*S\b", " Ai-S "),
    (r"(?<![A-Za-z])S\s*\.\s*S\s*\.\s*C\b\.?", " SSC "),
    (r"(?<![A-Za-z.])S\s*\.\s*C\b\.?", " SC "),
    (r"\bNew\s*FD\b|\bnFD\b|\bFDn\b|ニュー\s*FD", " NewFD "),
    (r"アイレベル|\beye\s*-?\s*level\b", " EyeLevel "),   # Nikon F・F2 のファインダー（相場が違う）
    (r"フォトミック|\bphotomic\b", " Photomic "),
]
VARIANT_GROUPS = [
    {"name": "Contax/Yashica（ツァイス C/Y）", "ctx": ["contax", "yashica"], "strict": True, "neg": True,
     "vars": {"mmg": "MMG", "mmj": "MMJ", "aeg": "AEG", "aej": "AEJ"}},
    {"name": "Hasselblad", "ctx": ["hasselblad"], "strict": True, "neg": True,
     "vars": {"cfe": "CFE", "cfi": "CFi", "cf": "CF", "cb": "CB", "fe": "FE"}},
    {"name": "Nikon（非Ai/Ai/Ai-S/Ai改）", "ctx": ["nikon", "nikkor"], "strict": False, "neg": False,
     "vars": {"nonai": "Non-Ai", "ai": "Ai", "ai-s": "(Ai-S,AIS)", "aikai": "Ai"}},
    {"name": "Canon FD（S.S.C./S.C./New FD）", "ctx": ["fd", "newfd"], "strict": False, "neg": False,
     "vars": {"ssc": "(SSC,S.S.C.)", "sc": "(S.C.,SC)", "newfd": '("New FD",nFD)'}},
    {"name": "Minolta（MC/MD）", "ctx": ["minolta", "rokkor"], "strict": False, "neg": True,
     "vars": {"mc": "MC", "md": "MD"}},
    # 「F2 アイレベル」に「F2 Photomic A」の相場が当たっていた（2026-11-16）。両方に表記があって違うときだけ別物
    {"name": "Nikon F/F2（アイレベル/フォトミック）", "ctx": ["nikon"], "strict": False, "neg": False,
     "vars": {"eyelevel": '("Eye Level",Eyelevel)', "photomic": "Photomic"}},
]


FLAG_RULES = [
    ("ジャンク", r"ジャンク|部品取り|現状品|動作未確認|動作不良|不具合|故障|for\s*parts"),
    ("光学難あり", r"カビ|クモリ|曇り|バルサム|ヤケ|傷あり|キズあり|拭き傷"),
    ("動作品", r"動作確認済|動作品|動作良好|完動"),
    ("美品", r"超美品|極上|美品|新品同様|未使用"),
    ("セット品", r"セット|まとめ|おまとめ|大量|\d+\s*点"),
    ("アダプター等", r"アダプタ|adapter|コンバーター|converter|接写リング|中間リング|エクステンション|テレプラス|"
                   r"teleplus|テレコンバーター|テレコン|ムター|mutar|エクステンダー|extender|塗料|ペイント|マウント変換|→|"
                   r"^(?!.*\d\s*mm).*(?:adapt-?all|アダプトール)"),  # タムロンの交換マウント。焦点距離があればレンズ本体なので除く
    ("付属品のみ?", r"(?:キャップ|ストラップ|ケース|フード|フィルター|背面|裏蓋|データバック|ファインダー|使用説明書|説明書)"
                   r"(?![\s　]?[^\s　]{0,14}(?:付|あり|有|セット))|カタログ|説明書のみ|\bback\b|"
                   # 「Sonnar T*180mmF2.8用」= そのレンズ用の付属品（「ニコン用」のようなマウントの表記は mm が無いので当たらない）
                   r"\d+\s*mm\s*(?:F\s*/?\s*\d+(?:\.\d+)?)?\s*用|"
                   r"ポーチ|月号|雑誌|ムック|書籍|写真集|カメラスタイル|\bP-?FILTER\b|magazine|マグニファイ|アイピース|ルーペ|アイカップ|接眼|magnifier|eyepiece|"
                   # 三脚まわりの部品（例: 「Wimberley AP-452 … 400mm F2.8G / 600mm F4G 用プレート」に 600mm F4 の相場が当たっていた）
                   r"ウィンバリー|wimberley|\bkirk\b|カーク|really\s*right\s*stuff|sunwayfoto|アルカ\s*スイス|arca[\s\-]*swiss|"
                   r"レンズフット|レンズプレート|クイックリリース|lens\s*(?:plate|foot)|replacement\s*foot|"
                   # 箱だけ（「【元箱のみ】… レンズ用元箱」が入札候補に出ていた）。「元箱付き」は除かない
                   r"(?:元|外|空|化粧)?箱のみ|空箱|用(?:の)?(?:元|外|化粧)箱|\bbox\s*only\b|\bempty\s*box\b|"
                   # カメラ・レンズの形をした雑貨（「レンズ型タンブラー」に 24-70mm の相場が当たっていた）
                   r"タンブラー|マグカップ|ステンレスマグ|\bmug\b|tumbler|キーホルダー|ミニチュア|フィギュア|ガチャ|ぬいぐるみ|"
                   r"(?:レンズ|カメラ)型|ノベルティ|ステッカー|バッジ|ピンバッジ"),
]


# ---- 状態（コンディション）. 規則はここだけに書き、拡張機能・店舗アプリへは build_extension.py で渡す
# 過去データ（eBay の商品名・頒布会のコンディション欄・手で直した相場の状態）→ 状態の名前。$1 は ( ) の中身
COND_RULES = [
    (r"\b(?:almost|nearly|near)\s*unused\b", "TOP MINT"),
    (r"\b(?:top|t)[\s.\-]*mint\b", "TOP MINT"),
    (r"\balmost[\s.\-]*mint\b", "Almost MINT"),
    (r"\b(?:near|n)[\s.\-]*mint\b|\bnmint\b", "Near MINT"),
    (r"\b(?:unused|brand\s*new|new\s*in\s*box)\b|未使用|新品", "Unused"),
    (r"\bmint\b", "MINT"),
    (r"\bexc(?:ellent)?\s*\+\s*([1-9])\b", "Exc+$1"),
    (r"\bexc(?:ellent)?\s*(\+{1,9})", "Exc$1"),
    (r"\bexc(?:ellent)?\b", "Exc"),
    (r"\b(?:for\s*parts|parts\s*only|as[\s\-]?is|not\s*working|junk)\b|ジャンク|部品取り", "ジャンク"),
]
# 状態のランク（大きいほど良い）。ジャンクや分からない状態（頒布会の A・AB など）はランク無し＝補正しない
# 2026-11-15 ユーザー指定: Unused > TOP MINT > MINT > Almost MINT > Near MINT > Exc+5 の順に1ランク（$10）ずつ
COND_RANK = {"Unused": 11, "未使用": 11, "TOP MINT": 10, "MINT": 9, "Almost MINT": 8, "Near MINT": 7,
             "Exc+5": 6, "Exc+++++": 6, "Exc+4": 5, "Exc++++": 5, "Exc+3": 4, "Exc+++": 4,
             "Exc+2": 3, "Exc++": 3, "Exc+1": 2, "Exc+": 2, "Exc": 1}
# ヤフオクの商品名（日本語の表記だけ使う。英語の MINT・Exc+++ は出品者の自己申告なので使わない）→ ランク。上から順に見る
LISTING_COND_RULES = [
    (r"未使用に近", 10), (r"未使用|新品未開封", 11), (r"新品同様", 10), (r"極上|超美品", 9), (r"美品", 7),
    (r"良品|動作品|動作確認済|動作良好|完動|実用品|並品|難あり", 6),
]
LISTING_DEFAULT_RANK = 7  # 表記なし＝Near MINT として計算（ユーザーの指定 2026-11-15）


def _cond_label(m: re.Match, out: str) -> str:
    return out.replace("$1", m.group(1)) if "$1" in out else out


def condition_of(row: dict) -> str:
    """過去データの状態. 頒布会リストのコンディション欄・相場メモの状態（source の括弧内）→ 商品名 の順."""
    src = unicodedata.normalize("NFKC", str(row.get("source") or ""))
    h = re.match(r"^(頒布会リスト|相場メモ)\((.*?)\)?$", src)
    for t in [h.group(2) if h else "", unicodedata.normalize("NFKC", str(row.get("title") or ""))]:
        for pat, out in COND_RULES:
            m = re.search(pat, t, re.I)
            if m:
                return _cond_label(m, out)
    if h and h.group(1) == "相場メモ":
        return h.group(2).strip()
    w = h and re.match(r"^[^\s)]+", h.group(2))
    return w.group(0) if w else ""


def listing_rank(title: str) -> int:
    t = unicodedata.normalize("NFKC", title or "")
    for pat, rank in LISTING_COND_RULES:
        if re.search(pat, t):
            return rank
    return LISTING_DEFAULT_RANK


def condition_adjusted(matches: list[dict], title: str, flags: list[str], step: float) -> list[float]:
    """過去データの価格を、この出品の状態に合わせて 1ランク step %ずつ補正する（ジャンクの出品は補正しない）.
    2026-11-18 ユーザー決定（おまかせ）: 1ランク $10 の固定額 → 価格に対する割合（実績から推定。estimate_condition_step_pct）."""
    lr = None if "ジャンク" in flags or not step else listing_rank(title)
    out = []
    for m in matches:
        rr = COND_RANK.get(condition_of(m))
        p = m["_price"]
        out.append(round(max(p * (1 + step / 100) ** (lr - rr), 0), 2) if lr is not None and rr is not None else p)
    return out


def estimate_condition_step_pct(rows: list[dict], fallback: float, lo: float = 3.0, hi: float = 15.0,
                                min_pairs: int = 5) -> tuple[float, int]:
    """実際に売れた記録（自分・Chatwork の販売実績）から、状態1ランクで価格が何%変わるかを推定する.
    同じ機種（照合の語が同じ）で状態の違う2件ずつを比べ、1ランクあたりの比の中央値。足りなければ fallback.
    戻り値: (%, 比べた組の数)."""
    import math
    groups: dict[frozenset, list[tuple[int, float]]] = {}
    for r in rows:
        src = r.get("source") or "自分の販売実績"
        if "販売実績" not in src or not r.get("_price"):
            continue
        rank = COND_RANK.get(condition_of(r))
        toks = r.get("_tokens")
        if rank is None or not toks or len(toks) < 2:
            continue
        groups.setdefault(frozenset(toks), []).append((rank, float(r["_price"])))
    logs = []
    for vals in groups.values():
        for i in range(len(vals)):
            for k in range(i + 1, len(vals)):
                (ra, pa), (rb, pb) = vals[i], vals[k]
                if ra != rb and pa > 0 and pb > 0:
                    logs.append(math.log(pa / pb) / (ra - rb))
    if len(logs) < min_pairs:
        return fallback, len(logs)
    pct = (math.exp(statistics.median(logs)) - 1) * 100
    return round(min(max(pct, lo), hi), 1), len(logs)


def condition_step_pct(cfg: dict, rows: list[dict]) -> float:
    """設定（[judge] condition_step_pct。condition_step_auto=true なら実績から推定）."""
    j = cfg["judge"]
    base = float(j.get("condition_step_pct", 0) or 0)
    if not base or not j.get("condition_step_auto", True):
        return base
    return estimate_condition_step_pct(rows, base)[0]


def detect_flags(title: str) -> list[str]:
    return [name for name, pat in FLAG_RULES if re.search(pat, title, re.I)]


# ---- 出品者の管理番号（Tj036・Ft644461・NA77401・IT120751・【L256】・OL4709 など）を検索語から外す
# 型番（M645・D850・GW690・SB800・SX-70 など）と形が似ているので、形だけでは決めない:
#   1. 相場データ（売れた実績の eBay タイトル・鑑定表）に出てくる語は型番 → 絶対に外さない
#   2. 型番にはまず無い形（Tj036 のような大文字＋小文字＋数字、英字1〜3文字＋数字5桁以上）→ 外す
#   3. 型番にもある形（【L256】のような括弧の中だけの英数字、OL4709 のような英字2〜3文字＋数字4桁）
#      → メーカー名より前にあるときだけ外す（管理番号はタイトルの頭、型番はメーカー名の後ろに書かれる）
# 拡張機能の isStockCode と同じ規則（tools/test_appraise.py で一致を確認）
STOCK_CODE_STRONG = (r"[A-Z][a-z]\d{3,}", r"[A-Za-z]{1,3}-?\d{5,}")
STOCK_CODE_WEAK = (r"[A-Za-z]{2,3}\d{4}",)
BRACKET_CODE_RE = r"[【\[〔<]\s*([A-Za-z]{0,3}-?\d{2,7}[A-Za-z]?)\s*[】\]〕>]"
KNOWN_MODEL_TOKENS: set[str] = set()


def set_known_models(titles) -> None:
    """相場データのタイトルに出てくる英数字の語（型番）を覚える。ここにある語は管理番号として外さない."""
    KNOWN_MODEL_TOKENS.clear()
    for t in titles:
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", unicodedata.normalize("NFKC", str(t or ""))):
            low = tok.lower().replace("-", "")
            if re.search(r"\d", low) and re.search(r"[a-z]", low):
                KNOWN_MODEL_TOKENS.add(low)


def is_stock_code(tok: str, before_brand: bool, bracketed: bool) -> bool:
    if tok.lower().replace("-", "") in KNOWN_MODEL_TOKENS:
        return False
    if any(re.fullmatch(p, tok) for p in STOCK_CODE_STRONG):
        return True
    return before_brand and (bracketed or any(re.fullmatch(p, tok) for p in STOCK_CODE_WEAK))


def title_to_query(title: str, max_tokens: int = 7) -> str:
    t = unicodedata.normalize("NFKC", title)
    t = re.sub(r"/[A-Z]{1,2}\d{3}\s*$", " ", t)  # ストアの管理番号（…/ジャンク/A077）
    t = re.sub(BRACKET_CODE_RE, lambda m: " \x01" + m.group(1) + " ", t)  # 【L256】→ 管理番号の候補の印（\x01）を付ける
    t = re.sub(r"\d+円[~〜～]?", " ", t)  # 「1円～」
    t = re.sub(r"\b[wW]\s*/\s*", " ", t)  # w/case（with）
    t = re.sub(r"(\d)\s*(mm|㎜)\s*([fF]\s*/?\s*\d)", r"\1mm \3", t)  # 75-150mmF4 → 75-150mm F4
    t = re.sub(r"[①-⑳]", " ", t)  # まとめ⑤ 等の丸数字
    t = re.sub(r"非\s*-?\s*A[iI](?![-sS])", " nonAi ", t)  # 非Ai（Ai/Ai-Sとは相場が違う）
    for pat, rep in VARIANT_NORMALIZE:  # 型の表記をそろえる（AE Japan → AEJ、S.S.C. → SSC など）
        t = re.sub(pat, rep, t, flags=re.I)
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
            t = t.replace(ja, " \x02 ")  # \x02 = メーカー名があった位置（管理番号の判定に使う）
    for ja, en in WORDS_JA:
        t = t.replace(ja, f" {en} ")
    tokens: list[str] = []
    found = list(re.finditer(r"[A-Za-z0-9αµ][A-Za-z0-9αµ.\-+]*", t))
    brand_at = [t.find("\x02")] if "\x02" in t else []
    brand_at += [m.start() for m in found if m.group().lower().strip(".-") in BRANDS_EN][:1]
    # メーカー名が無いタイトルは「先頭の語」だけを頭とみなす（Polaroid 【SX-70】 の SX-70 を外さない）
    first_brand = min(brand_at) if brand_at else (found[0].end() if found else 0)
    for m in found:
        tok = m.group()
        low = tok.lower().strip(".-")
        if not low or low in NOISE_TOKENS:
            continue
        # 1桁の数字は検索語では落とすが、照合用（max_tokens>7）では残す（EOS 5 / Nikon 1 等）
        if re.fullmatch(r"\d+", low) and ((len(low) < 2 and max_tokens <= 7) or len(low) >= 4):
            continue  # 「1円」の1や、シリアル番号っぽい長い数字
        if re.fullmatch(r"no\.?\d+", low):
            continue
        if is_stock_code(tok, m.start() < first_brand, m.start() > 0 and t[m.start() - 1] == "\x01"):
            continue  # 出品者の管理番号は eBay の検索語に入れない
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


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return math.nan
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


# ---------------------------------------------------------------------------
# 過去の販売実績
# ---------------------------------------------------------------------------


def _tokens(s: str) -> set[str]:
    q = title_to_query(s, max_tokens=20)
    q = unicodedata.normalize("NFKC", q).lower()
    q = re.sub(r"(\d)\s*mm(?![a-z])", r"\1mm", q)  # 「50 mm」→50mm（「F1.4 MMJ」はつなげない）
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
        if str(row.get("source") or "").startswith("Chatwork"):
            row["_price"] = chatwork_gross(row["_price"])
        row["_tokens"] = _tokens(row.get("ebay_query") or row.get("title") or "")
        rows.append(row)
    return rows


def chatwork_gross(price: float, cfg: dict | None = None) -> float:
    """Chatwork（スクールの販売報告）の販売価格は、だいたい送料 $30 込み（スクールの方針）. 商品代＝価格 − $30 とし、
    うちが売るときの送料（入札判断の一律額 $40）を乗せた額を、送料込みの売値として扱う（2026-11-18 ユーザー決定）.
    例: 報告 $169 → 商品代 $139（送料別）→ 送料込み $179."""
    sh = (cfg or _base_cfg())["shipping"]
    return round(price - float(sh.get("chatwork_included_usd", 30.0)) + float(sh["deduction_usd"]), 2)


_BASE_CFG: dict | None = None


def _base_cfg() -> dict:
    global _BASE_CFG
    if _BASE_CFG is None:
        _BASE_CFG = load_config(None)
    return _BASE_CFG


ACCESSORY_RE = re.compile(r"strap|(?<!w/)\bcase\b|grip|charger|battery|hood only|\bcap\b|catalog", re.I)
THIRD_PARTY = {"tamron", "sigma", "tokina", "7artisans", "ttartisan", "brightin", "kenko", "pixco", "k&f", "meike", "viltrox", "laowa",
               "samyang", "yongnuo", "kipon", "fotodiox", "neewer", "commlite", "sg-image", "pergear",
               "kamlan", "zonlai", "fujian", "rokinon", "vello"}
COLOR_WORDS = {"black", "silver", "chrome", "white", "red", "blue", "green", "brown"}
GENERATIONS = {"ii", "iii", "iv", "mkii", "mkiii", "mk2", "mk3", "mark"}
PREMIUM_VARIANTS = {"gold", "titanium", "titan", "limited", "限定", "whiteface", "af"}

def variants_conflict(qt: set, rt: set) -> bool:
    """型が違う（または strict のグループで片方にだけ型がある）なら True."""
    for g in VARIANT_GROUPS:
        if not (set(g["ctx"]) & (qt | rt)):
            continue
        qv, rv = qt & set(g["vars"]), rt & set(g["vars"])
        if (qv != rv) if g["strict"] else (qv and rv and qv != rv):
            return True
    return False


def ebay_search_query(query: str, title: str = "") -> str:
    """eBay 検索語に型を入れる（入っていなければ足す）。neg のグループはほかの型を除外する語も足す."""
    toks = _tokens(f"{query} {title}")
    words = query.split()
    for g in VARIANT_GROUPS:
        if not (set(g["ctx"]) & toks):
            continue
        found = [v for v in g["vars"] if v in toks]
        if len(found) != 1:
            continue
        v = found[0]
        disp = g["vars"][v]
        words = [disp if w.lower() == v else w for w in words]
        if disp not in words:
            words.append(disp)
        if g["neg"]:
            words += ["-" + d for k, d in g["vars"].items() if k != v and d != disp]
    return " ".join(dict.fromkeys(words))


SOURCE_LABELS = ["自分の販売実績", "Chatwork販売実績", "頒布会リスト", "鑑定表"]


def is_sold_record(row: dict) -> bool:
    """販売履歴として扱う記録: 自分の販売実績・Chatwork 販売実績・頒布会リスト（2026-11-18 ユーザー決定: 頒布会も売れたものとみなす）.
    相場メモ（手で入れた相場）・鑑定表（推定）は販売履歴ではない."""
    src = row.get("source") or "自分の販売実績"
    return src.startswith("頒布会") or ("販売実績" in src and not src.startswith("相場メモ"))


# 根拠の強さ「確認不要」になる source_tier の上限（0=自分 1=Chatwork 2=頒布会。2026-11-18 から頒布会も売れた記録として確認不要）
SURE_TIER = 2


def sold_hit(row: dict, cfg: dict, fx: float) -> bool:
    """売れ筋: 販売履歴のある機種（自分の古物台帳・Chatwork の他の人の事例・頒布会。頒布会も売れたものとみなす。
    2026-11-18 ユーザー決定）. 仕入値が分かる記録は、その時の仕入値から見て入札基準以上の利益が出ていたものだけ.
    利益 = (売値 − eBay手数料) × 為替 × (1 − 円転ロス) − 送料（入札判断と同じ一律額）− 梱包 − 仕入値"""
    if not is_sold_record(row):
        return False
    price, cost = row.get("_price"), _float_or_none(row.get("purchase_jpy"))
    if not price:
        return False
    if not cost:
        return True  # 仕入値が分からない販売履歴（頒布会・Chatwork の一部）は、売れた機種として扱う
    e, j = cfg["ebay"], cfg["judge"]
    fee_pct = e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]
    fees = price * fee_pct / 100 + e["per_order_fee_usd"]
    net = (price - fees) * fx * (1 - cfg["fx"]["conversion_loss_pct"] / 100)
    profit = net - cfg["shipping"]["deduction_usd"] * fx - int(cfg["shipping"]["packing_jpy"]) - cost
    return profit >= j["min_profit_jpy"] and profit / (price * fx) * 100 >= j["min_margin_pct"]


# 売れ筋の機種を名指しで探す検索（2026-11-18 ユーザー決定）: 販売実績で利益が出ていた機種ごとに、ヤフオクの検索URLを毎回自動で作る。
# 「メーカー（英語か日本語のどちらか）」＋「型の語（28mm・T2・D7100 など）」で探し、照合は今までどおり相場データで行う
def _hit_profit(row: dict, cfg: dict, fx: float) -> float:
    e = cfg["ebay"]
    price, cost = float(row["_price"]), _float_or_none(row.get("purchase_jpy"))
    if not cost:  # 仕入値が分からない販売履歴は、売値の1割を利益の目安にして並べる
        return price * fx * 0.1
    fee_pct = e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]
    net = (price - price * fee_pct / 100 - e["per_order_fee_usd"]) * fx * (1 - cfg["fx"]["conversion_loss_pct"] / 100)
    return net - cfg["shipping"]["deduction_usd"] * fx - int(cfg["shipping"]["packing_jpy"]) - cost


def _brand_of(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "")
    for ja, en in BRANDS_JA:
        if ja in t:
            return en
    for w in re.findall(r"[A-Za-z]+", t):
        lw = w.lower()
        if lw in BRANDS_EN and BRANDS_EN[lw] not in ("OM", "Carl"):
            return BRANDS_EN[lw]
        if lw in LENS_LINES and LENS_LINES[lw] != "Zeiss":
            return LENS_LINES[lw]
    return ""


def _model_word(title: str) -> str:
    """型の語: 焦点距離（28mm・80-200mm）か型番（T2・D7100・A-1・XA2）。F値・数字だけの語は使わない."""
    for tok in title_to_query(title, max_tokens=12).split():
        tl = tok.lower().strip(".,")
        if re.fullmatch(r"\d+(?:-\d+)?mm", tl):
            return tl
        # F値（f/2・f1.4・f2.8）は型の語にしない。ただし「F3」「F2」のような小数点も / も無い語はボディの型番として使う
        if re.search(r"\d", tl) and re.search(r"[a-z]", tl) and not re.fullmatch(r"f(?:/\d+(?:\.\d+)?|\d+\.\d+)", tl) \
                and not re.fullmatch(r"\d+(?:\.\d+)?(?:mp|x)", tl):
            return tok
    return ""


def lost_models(bid_rows: list[dict], days: int = 60) -> list[tuple[str, str]]:
    """競り負けた入札（入札の記録の「負け」「落札失敗」）の機種 [(メーカー, 型の語)]. 新しい順・重複なし（2026-11-18 ユーザー決定）."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=days)).isoformat()
    out: list[tuple[str, str]] = []
    for r in sorted(bid_rows, key=lambda r: r.get("end") or r.get("bid_at") or "", reverse=True):
        if (r.get("result") or "") not in ("負け", "落札失敗") or (r.get("end") or r.get("bid_at") or "9")[:10] < since:
            continue
        k = (_brand_of(r.get("title", "")), _model_word(r.get("title", "")))
        if all(k) and k not in out:
            out.append(k)
    return out


def _model_url(brand: str, word: str) -> str:
    ja = [j for j, en in BRANDS_JA if en == brand]
    q = urllib.parse.urlencode({"va": word, "vo": " ".join([brand] + ja), "auccat": "23636", "aucminprice": "1000",
                                "s1": "end", "o1": "a", "n": "100"})
    return f"https://auctions.yahoo.co.jp/search/search?{q}"


# まとめ売り（2026-11-18 ユーザー決定）: 「Nikon F3 + 50mm f1.4 + 28mm f2.8」のようなセット出品を、中身ごとに相場に当てて合計する。
# 区切り（＋ + 、 , ／ / ＆ & 「と」）で分け、型の語（焦点距離・型番）がある部分だけを中身とみなす。メーカーが無い部分は商品名の最初のメーカーを付ける。
# 相場が見つかった中身だけを足し（見つからない中身は $0 ＝保守的）、2点以上見つかったときだけ判定する。
# 中身は別々に売る前提なので、2点目からは送料（一律）・1注文の手数料・梱包をそれぞれ引く。拡張機能の setParts と同じ
SET_SPLIT_RE = re.compile(r"\s*(?:[＋+、,／/＆&]|\sと\s)\s*")


def set_part_titles(title: str) -> list[str]:
    t = unicodedata.normalize("NFKC", title or "")
    b0 = _brand_of(t)
    out = []
    for seg in SET_SPLIT_RE.split(t):
        seg = seg.strip()
        if not seg or not _model_word(seg):
            continue
        if {"付属品のみ?", "アダプター等"} & set(detect_flags(seg)):
            continue
        if b0 and not _brand_of(seg):
            seg = f"{b0} {seg}"
        out.append(seg)
    return out if len(out) >= 2 else []


def set_value(parts: list[tuple[str, float]], cfg: dict, fx: float) -> float:
    """中身の相場（送料込み）の合計から、2点目以降の送料・1注文の手数料・梱包を引いた額（送料込みの売値として calculate に渡す）."""
    extra = cfg["shipping"]["deduction_usd"] + cfg["ebay"]["per_order_fee_usd"] + int(cfg["shipping"]["packing_jpy"]) / fx
    return round(sum(pr for _, pr in parts) - (len(parts) - 1) * extra, 2)


def hit_search_urls(sales: list[dict], cfg: dict, fx: float, limit: int = 30,
                    lost: list[tuple[str, str]] | None = None, lost_limit: int = 20) -> list[tuple[str, str]]:
    """売れ筋（sold_hit）の機種ごとの検索URL. 利益の大きい順に limit 本. 戻り値: [(URL, 機種の説明)]."""
    best: dict[tuple[str, str], float] = {}
    for r in sales:
        if not sold_hit(r, cfg, fx):
            continue
        brand, word = _brand_of(r.get("title", "")), _model_word(r.get("title", ""))
        if not brand or not word:
            continue
        k = (brand, word)
        best[k] = max(best.get(k, -1e9), _hit_profit(r, cfg, fx))
    keys = [k for k, _ in sorted(best.items(), key=lambda kv: -kv[1])[:limit]]
    out = [(_model_url(b, w), f"{b} {w}") for b, w in keys]
    for b, w in (lost or [])[:lost_limit]:  # 競り負けた機種（同じ機種はまた出品される）
        if (b, w) not in keys:
            keys.append((b, w))
            out.append((_model_url(b, w), f"{b} {w}（負けた機種）"))
    return out


# 入札候補の並び（2026-11-18 ユーザー決定）: 売値（送料込み）が $99 以下の候補は下に回す（実績では1件あたりの利益が小さい）
LOW_PRICE_USD = 99.0


def bid_priority(price_usd: float | None, hit: bool) -> int:
    """小さいほど上: 0=売れ筋で$99超 1=$99超 2=売れ筋で$99以下 3=$99以下. 同じ順位の中は終了が近い順."""
    return (2 if price_usd is not None and price_usd <= LOW_PRICE_USD else 0) + (0 if hit else 1)


def source_tier(row: dict) -> int:
    src = row.get("source") or "自分の販売実績"
    if src.startswith("Chatwork"):
        return 1
    if src.startswith("頒布会"):
        return 2
    if src.startswith("鑑定表"):
        return 3
    return 0


def _lensy(tokens: set[str]) -> bool:
    """焦点距離（50mm・5cm）があればレンズ."""
    return any(t.endswith("mm") or re.fullmatch(r"\d+(?:\.\d+)?cm", t) for t in tokens)


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
        # レンズ（mm・cm表記あり）とボディ（なし）を取り違えない（「NIKKOR-H 5cm f2」に「Nikon F2」が当たっていた）
        if _lensy(qt) != _lensy(rt):
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
        # 型違い（Contax の MMJ と AEG など）は相場が違う
        if variants_conflict(qt, rt):
            continue
        # 実績側の英字の語（メーカー・レンズ銘など）が1つも無ければ別物
        # 例: 「YASHICA 35mm 1:2.8」に「ALPA 35mm 2.8」が数字だけで当たっていた
        if not (qt & {t for t in rt if not re.search(r"\d", t) and t not in COLOR_WORDS}):
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
        if _lensy(qt) != _lensy(rt):
            continue
        model = {x for x in rt if re.search(r"\d", x) and not x.endswith("mm") and not x.startswith("f")}
        if model and not model <= qt:
            continue
        # 焦点距離・F値は表の側の値がタイトルに全部あること（「55mm F3.5」に「18mm f/3.5」が当たっていた）
        if not {x for x in rt if re.fullmatch(r"[\d.\-]+mm|f[\d.\-]+", x)} <= qt:
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
    # ストア出品は入札額が税抜。入札1円あたり (1+税率) 円を払う。個人出品は税なし
    tax_mult = 1 + y["store_tax_pct"] / 100 if item.store_tax else 1.0
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
    # 安心ストア（フォロー中）の基準（config.toml の trusted_*。2026-11-16 から全体と同じ値）
    min_profit = j.get("trusted_min_profit_jpy", j["min_profit_jpy"]) if item.trusted else j["min_profit_jpy"]
    min_margin = j.get("trusted_min_margin_pct", j["min_margin_pct"]) if item.trusted else j["min_margin_pct"]
    by_profit = (fixed - min_profit) / k
    by_margin = (fixed - min_margin / 100 * revenue_jpy) / k
    max_bid = max(0, round_down_to_increment(min(by_profit, by_margin)))
    breakeven = max(0, round_down_to_increment(fixed / k))

    price_now = item.current_price_jpy  # ヤフオクの表示価格（ストアは税込）
    bid_now = price_now / tax_mult if price_now is not None else None  # 入札額の基準（ストアは税抜）
    out = {
        "tax_excl_bid": item.store_tax,
        "bid_now": bid_now,
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
        purchase = round(price_now + dom)
        p = profit_at(bid_now)
        out.update(
            purchase_jpy=purchase,
            profit_now_jpy=round(p),
            margin_now_pct=round(p / revenue_jpy * 100, 1) if revenue_jpy else None,
            headroom_jpy=round(max_bid - bid_now),
        )
    if item.buy_now_jpy:
        out["profit_buy_now_jpy"] = round(profit_at(item.buy_now_jpy / tax_mult))
    return out


# 光学難（2026-11-18 ユーザー決定・おまかせ）: カビ・クモリ・バルサムなどがはっきり書かれた出品は × で見送る（実績は平均赤字）。
# 「カビ・クモリなし」のような打ち消しは desc_hits の規則で除く。傷・ヤケなどその他の難は今までどおり要確認
OPTICAL_RE = re.compile(r"(カビ|かび|黴|クモリ|くもり|曇り|白濁|バルサム|分離)")
OTHER_DEFECT_RE = re.compile(r"ヤケ|傷あり|キズあり|拭き傷")
_OPT_NEG_RE = re.compile(r"(なし|無し|ナシ|ありません|有りません|ございません|見られません|見当たりません|確認できません|(?<!少)ない|無い)")


def optical_title_words(text: str) -> list[str]:
    """商品名などの短い文から、打ち消されていない光学難の語（「カビ・クモリなし」「クモリ：無」は除く）. 拡張機能の opticalWords と同じ."""
    out = []
    for m in OPTICAL_RE.finditer(text or ""):
        w, rest = m.group(1), text[m.start():m.start() + 60]
        kv = re.match(r"^\s*[：:]\s*([^\s　、。|｜]+)", rest[len(w):])
        if kv:
            if re.match(r"^(なし|無し|ナシ|無|ない|-|ー|×)", kv.group(1)):
                continue
        else:
            cut = re.search(r"[。\n】|｜]", rest)
            if _OPT_NEG_RE.search(rest[:cut.start()] if cut else rest):
                continue
        if w not in out:
            out.append(w)
    return out


def optical_skip_words(title: str, hits: list[dict] | None = None) -> list[str]:
    """商品名と説明文（desc_hits の結果）にある、打ち消されていない光学難の語."""
    out = optical_title_words(title)
    for h in hits or []:
        if OPTICAL_RE.fullmatch(h["word"]) and h["word"] not in out:
            out.append(h["word"])
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
    if "アダプター等" in item.flags or (not item.set_parts and {"付属品のみ?", "セット品"} & set(item.flags)):
        return "要確認", prefix + "付属品だけの出品の可能性。本体か商品ページで確認してください"
    if item.set_parts:
        prefix += f"まとめ売り{len(item.set_parts)}点。"
    opt = optical_skip_words(item.title, item.desc_hits)
    if opt:  # 光学難は見送り（修理しない方針。実績は平均赤字）
        return "×", prefix + "光学難（" + "・".join(opt) + "）は見送り"
    if item.desc_hits:  # 説明文に傷・ヤケ等（拡張機能の説明文チェックと同じ規則）
        return "要確認", prefix + "説明文に " + "・".join(h["word"] for h in item.desc_hits) + "（方針では避ける）"
    if "光学難あり" in item.flags and OTHER_DEFECT_RE.search(item.title or ""):  # 傷あり・ヤケ等。「カビ・クモリなし」は除く
        return "要確認", prefix + "難あり（方針では避ける）。写真で程度を確認してください"
    if c["max_bid_jpy"] <= 0:
        return "×", prefix + "基準を満たす入札額がありません"
    now = c.get("bid_now")
    lim = f"{c['max_bid_jpy']:,}円" + (
        f"（このまま入札。ストアなので支払いは税込{round(c['max_bid_jpy'] * 1.1):,}円）" if c.get("tax_excl_bid") else "")
    if now is None:
        return "要確認", prefix + f"現在価格不明。上限 {lim} まで"
    if now <= c["max_bid_jpy"] * 0.8:
        return "◎", prefix + f"上限 {lim} まで余裕あり"
    if now <= c["max_bid_jpy"]:
        return "○", prefix + f"上限 {lim} に近い。終了間際の競り上がりに注意"
    return "×", prefix + f"現在価格が上限 {lim} を超過"


# ---------------------------------------------------------------------------
# 分析の流れ
# ---------------------------------------------------------------------------


def assign_price(item: Item, cfg: dict, fx: float) -> None:
    """想定販売価格: 手入力 > 実績・相場データ の順に決めて利益を計算.

    eBay のページを自動で読みに行くこと（スクレイピング）は eBay の規約違反なので行わない。
    """
    if item.ebay_price_usd is not None:
        item.expected_price_usd, item.price_source = item.ebay_price_usd, "手入力"
    elif item.history_matches:
        step = float(cfg["judge"].get("_condition_step_pct", cfg["judge"].get("condition_step_pct", 0)) or 0)
        ps = condition_adjusted(item.history_matches, item.title, item.flags, step)
        item.expected_price_usd = round(statistics.median(ps), 2)
        src = str(item.history_matches[0].get("source", ""))
        label = SOURCE_LABELS[source_tier(item.history_matches[0])]
        if src.startswith(("相場メモ", "鑑定表")):
            label = src
        item.price_source = f"{label} {len(ps)}件の中央値" if len(ps) > 1 else label
    if item.expected_price_usd is not None and "ジャンク" in item.flags and item.ebay_price_usd is None:
        factor = cfg["judge"].get("junk_price_factor", 1.0)
        if factor < 1:
            item.expected_price_usd = round(item.expected_price_usd * factor, 2)
            item.price_source += f" ×ジャンク{int(factor * 100)}%"
    item.calc = calculate(item, cfg, fx)
    item.sold_hit = bool(item.history_matches) and item.ebay_price_usd is None and sold_hit(item.history_matches[0], cfg, fx)


MEMO_DOWNLOAD = "aurora_price_memo.csv"  # 拡張機能が「直す」「登録」のたびに ダウンロード/ に上書き保存する
MEMO_FIELDS = ["title", "sold_price_usd", "source", "memo"]
MEMO_HIST_FIELDS = ["title", "sold_price_usd", "cond", "date"]  # 相場メモの履歴（上書きする前の値も残す。usd は送料込み）


def _memo_cond(src: str) -> str:
    return src[src.index("(") + 1:src.rindex(")")] if "(" in src and src.endswith(")") else ""


def add_memo_history(entries: list[dict], dest: Path | None = None) -> int:
    """相場メモの履歴 data/price_memo_history.csv に、まだ無い（機種・金額・日付）だけ足す. 足した件数."""
    dest = dest or DATA / "price_memo_history.csv"
    old = list(csv.DictReader(dest.read_text(encoding="utf-8-sig").splitlines())) if dest.exists() else []
    have = {(r["title"], f"{_float_or_none(r['sold_price_usd']) or 0:g}", r.get("date", "")) for r in old}
    add = []
    for e in entries:
        v = _float_or_none(e.get("sold_price_usd"))
        k = ((e.get("title") or "").strip(), f"{v or 0:g}", (e.get("date") or "").strip())
        if not k[0] or not v or k in have:
            continue
        have.add(k)
        add.append({"title": k[0], "sold_price_usd": k[1], "cond": e.get("cond", ""), "date": k[2]})
    if add:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=MEMO_HIST_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(old + add)
    return len(add)


def stale_memos(memo_path: Path | None = None, hist_path: Path | None = None, days: int = 90,
                limit: int = 10) -> list[dict]:
    """見直したい相場メモ（最後に直した日が days 日より前、または日付が分からないもの）. 古い順に limit 件.
    毎朝 Mac が「Mac_未登録機種」に足し、夜の下調べで新しい相場の候補を出す（2026-11-18 ユーザー決定）."""
    from datetime import date, timedelta
    memo_path = memo_path or DATA / "price_memo.csv"
    if not memo_path.exists():
        return []
    hist = load_memo_history(hist_path)
    cut = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for r in csv.DictReader(memo_path.read_text(encoding="utf-8-sig").splitlines()):
        t = (r.get("title") or "").strip()
        v = _float_or_none(r.get("sold_price_usd"))
        if not t or v is None:
            continue
        dates = [h[1] for h in hist.get(t, []) if h[1]]
        last = max(dates) if dates else ""
        if not last or last[:10] < cut:
            out.append({"title": t, "usd": v, "date": last})
    return sorted(out, key=lambda r: r["date"])[:limit]


def load_memo_history(path: Path | None = None) -> dict[str, list]:
    """{機種: [[usd, 日付, 状態], ...]}（日付順。日付不明が先）. 拡張機能の「相場の動き」に使う."""
    path = path or DATA / "price_memo_history.csv"
    out: dict[str, list] = {}
    if path.exists():
        for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
            v = _float_or_none(r.get("sold_price_usd"))
            if r.get("title") and v:
                out.setdefault(r["title"], []).append([v, r.get("date", ""), r.get("cond", "")])
    for v in out.values():
        v.sort(key=lambda e: e[1])
    return out


def import_extension_memo(downloads: Path | None = None, dest: Path | None = None) -> tuple[int, int]:
    """拡張機能が保存した相場メモ（ダウンロード/aurora_price_memo.csv）を data/price_memo.csv に取り込む.

    同じ機種（title）の相場メモは金額を新しい値に更新し、無ければ追加する。相場メモ以外の行はそのまま残す。
    戻り値: (追加した件数, 更新した件数)。ファイルが無ければ (0, 0)。
    """
    downloads = downloads or Path.home() / "Downloads"
    dest = dest or DATA / "price_memo.csv"
    hist_path = dest.parent / "price_memo_history.csv"
    hfiles = sorted(downloads.glob("aurora_price_memo_history*.csv"), key=lambda p: p.stat().st_mtime) if downloads.exists() else []
    if hfiles:  # 拡張機能が残した相場の履歴（上書きする前の値も入っている）
        add_memo_history(list(csv.DictReader(hfiles[-1].read_text(encoding="utf-8-sig").splitlines())), hist_path)
    files = [p for p in sorted(downloads.glob("aurora_price_memo*.csv"), key=lambda p: p.stat().st_mtime)
             if not p.name.startswith("aurora_price_memo_history")] if downloads.exists() else []
    if not files:
        return 0, 0
    new = {}  # 機種名 → (金額, source)。source は 相場メモ か 相場メモ(状態)
    for r in csv.DictReader(files[-1].read_text(encoding="utf-8-sig").splitlines()):
        t, v = (r.get("title") or "").strip(), _float_or_none(r.get("sold_price_usd"))
        src = (r.get("source") or "").strip()
        if t and v:
            new[t] = (v, src if src.startswith("相場メモ") else "相場メモ")
    added, updated = merge_memo(new, dest, "拡張機能で直した")
    if added or updated:
        print(f"相場メモを取り込み: 追加 {added}件・更新 {updated}件（{files[-1].name} → {dest.name}）", file=sys.stderr)
    return added, updated


def merge_memo(new: dict, dest: Path | None = None, how: str = "拡張機能で直した") -> tuple[int, int]:
    """{機種: (送料込み$, source)} を data/price_memo.csv に入れる（同じ機種の相場メモは上書き。前の値は履歴に残す）."""
    dest = dest or DATA / "price_memo.csv"
    hist_path = dest.parent / "price_memo_history.csv"
    new = dict(new)
    rows, fields = [], list(MEMO_FIELDS)
    if dest.exists():
        rd = csv.DictReader(dest.read_text(encoding="utf-8-sig").splitlines())
        fields = list(rd.fieldnames or MEMO_FIELDS) + [f for f in MEMO_FIELDS if f not in (rd.fieldnames or [])]
        rows = list(rd)
    added = updated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for r in rows:
        t = (r.get("title") or "").strip()
        if t in new and is_memo(r):
            v, src = new[t]
            if _float_or_none(r.get("sold_price_usd")) != v or (r.get("source") or "") != src:
                # 上書きする前の値と新しい値を履歴に残す（前の日付は memo 欄の「拡張機能で直した 日付」から）
                od = re.search(r"20\d\d-\d\d-\d\d", r.get("memo") or "")
                add_memo_history([{"title": t, "sold_price_usd": r.get("sold_price_usd"), "cond": _memo_cond(r.get("source") or ""),
                                   "date": od.group(0) if od else ""},
                                  {"title": t, "sold_price_usd": v, "cond": _memo_cond(src), "date": today}], hist_path)
                r["sold_price_usd"], r["source"], r["memo"] = f"{v:g}", src, f"{how} {today}"
                updated += 1
            del new[t]
    for t, (v, src) in new.items():
        rows.append({"title": t, "sold_price_usd": f"{v:g}", "source": src, "memo": f"{how} {today}"})
        add_memo_history([{"title": t, "sold_price_usd": v, "cond": _memo_cond(src), "date": today}], hist_path)
        added += 1
    if added or updated:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return added, updated


# ---- 説明文チェック（extension_template.js の descHits と同じ規則）
DEFECT_RE = re.compile(r"(カビ|かび|黴|クモリ|くもり|曇り|白濁|バルサム|分離|コーティング(?:剥がれ|はがれ|ハガレ|剥離|劣化|ムラ|傷|キズ)|剥離|ヤケ|黄変|"
                       r"拭き傷|拭き跡|絞り粘り|絞りに粘り|油染み|油浮き|油滲み|オイル染み|オイル漏れ|動作未確認|不動|故障|写りに影響)")
PHOTO_ONLY_RE = re.compile(r"光学[^\n]{0,15}\n?(?:[^\n]{0,60}\n?){0,3}?[^\n]{0,40}写真[^\n]{0,10}確認")
NEG_RE = re.compile(r"(なし|無し|ナシ|ありません|有りません|ございません|見られません|見当たりません|確認できません|(?<!少)ない|無い)")


def desc_hits(text: str, cond: str = "") -> list[dict]:
    hits: list[dict] = []
    for m in DEFECT_RE.finditer(text):
        i, w = m.start(), m.group(1)
        if i > 0 and text[i - 1] == "「":
            continue  # 「動作未確認品」は返品対象外 など、規約の定型文
        around = text[max(0, i - 15):i + 25]
        if w == "故障" and re.search(r"初期不良|自然故障|返品|保証|修理|場合", around):
            continue
        s_start = max(text.rfind("。", 0, i + 1), text.rfind("\n", 0, i + 1)) + 1
        s_end = re.search(r"[。\n]", text[i:])
        sentence = text[s_start:(i + s_end.start()) if s_end else i + 80]
        if re.search(r"返品|返金|対象外|保証|免責", text[max(0, i - 200):i]) and \
                len(re.findall(r"カビ|かび|くもり|クモリ|曇り|サビ|錆|キズ|傷|磨耗|摩耗|劣化|ゴミ|チリ|ホコリ", sentence)) >= 3:
            continue
        rest = text[i:i + 60]
        kv = re.match(r"^\s*[：:]\s*([^\s　、。|｜]+)", rest[len(w):])
        if kv:
            if re.match(r"^(なし|無し|ナシ|無|ない|-|ー|×)", kv.group(1)):
                continue
        else:
            cut = re.search(r"[。\n】|｜]", rest)
            if NEG_RE.search(rest[:cut.start()] if cut else rest):
                continue
        if any(h["word"] == w for h in hits):
            continue
        hits.append({"word": w, "snip": " ".join(text[max(0, i - 18):i + 32].split())})
    ph = PHOTO_ONLY_RE.search(text)
    if ph:
        hits.append({"word": "光学は写真で確認", "snip": " ".join(ph.group(0).split())[:60]})
    if cond and "状態が悪い" in cond:
        hits.append({"word": "状態が悪い", "snip": "商品の状態: " + cond})
    return hits


def read_description(page: str) -> tuple[str, str]:
    """出品ページの説明文（テキスト）と商品の状態."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return "", ""
    j = json.loads(m.group(1))
    props = j.get("props") or {}
    st = (props.get("pageProps") or {}).get("initialState") or props.get("initialState") or {}
    item = (((st.get("item") or {}).get("detail") or {}).get("item")) or {}
    raw = str(item.get("descriptionHtml") or item.get("description") or "")
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|div|li|tr|h\d)>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", raw).replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text, str(item.get("conditionName") or "")


CONFIG_DEFAULT_SHIP = 1000  # 表示用。計算は config.toml の default_domestic_shipping_jpy（main で上書き）
# eBay の$は送料を除いた商品代（net・送料別）で表示する（2026-11-15 ユーザー指定）。計算は送料込みのまま
CONFIG_SHIP_USD = 40.0  # 表示用。config.toml の [shipping] deduction_usd（main で上書き）


def _net(v) -> float | None:
    return None if v is None else round(float(v) - CONFIG_SHIP_USD, 2)


def item_shipping(page: str) -> int | None:
    """出品ページの送料（extension_template.js の shipFee と同じ）: 出品者負担なら0、配送方法の送料（一律）の一番安いもの。分からなければ None."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    props = json.loads(m.group(1)).get("props") or {}
    st = (props.get("pageProps") or {}).get("initialState") or props.get("initialState") or {}
    item = (((st.get("item") or {}).get("detail") or {}).get("item")) or {}
    if item.get("chargeForShipping") == "seller":
        return 0
    fees = [x.get("shippingFee") for x in ((item.get("shipping") or {}).get("methods") or []) if isinstance(x, dict)]
    fees = [int(v) for v in fees if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0]
    return min(fees) if fees else None


def check_descriptions(items: list[Item], delay: float, cfg: dict | None = None, fx: float | None = None) -> int:
    """◎○の出品だけ出品ページを読み、説明文にカビ・クモリ等があれば要確認にする. 検索結果に送料が無ければ、
    出品ページの送料で計算し直す（無ければ既定の額のまま）. 読んだ件数を返す."""
    n = 0
    for it in items:
        if not it.auction_id or judge(it)[0] not in ("◎", "○"):
            continue
        try:
            page = http_get(it.url)
            text, cond = read_description(page)
        except Exception as e:  # 読めなくても判定は続ける
            it.fetch_note += f"説明文を読めませんでした（{e}） "
            continue
        if it.domestic_shipping_jpy is None:
            ship = item_shipping(page)
            if ship is not None:
                it.domestic_shipping_jpy = ship
                it.fetch_note += "送料は出品ページ "
                if cfg and it.expected_price_usd is not None:
                    it.calc = calculate(it, cfg, fx)
        it.desc_hits = desc_hits(text, cond)
        it.desc_checked = True
        n += 1
        time.sleep(delay)
    return n


# ---- 安心ストア（フォロー中）: data/trusted_sellers.csv（user_id,name）。拡張機能がストアのページで自動登録し、
#      ダウンロード/aurora_trusted_sellers.csv に保存 → import_extension_trusted() で取り込む
TRUSTED_DOWNLOAD = "aurora_trusted_sellers.csv"


def load_trusted(path: Path | None = None) -> dict[str, str]:
    path = path or DATA / "trusted_sellers.csv"
    if not path.exists():
        return {}
    return {r["user_id"].strip(): (r.get("name") or "").strip()
            for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()) if (r.get("user_id") or "").strip()}


def import_extension_trusted(downloads: Path | None = None, dest: Path | None = None) -> bool:
    """拡張機能が保存した安心ストアの一覧（全件）で data/trusted_sellers.csv を置き換える. 変わったら True."""
    downloads = downloads or Path.home() / "Downloads"
    dest = dest or DATA / "trusted_sellers.csv"
    files = sorted(downloads.glob("aurora_trusted_sellers*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        return False
    rows = [r for r in csv.DictReader(files[-1].read_text(encoding="utf-8-sig").splitlines())
            if (r.get("user_id") or "").strip() and str(r.get("on", "1")).strip() != "0"]
    new = {r["user_id"].strip(): (r.get("name") or "").strip() for r in rows}
    if new == load_trusted(dest):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name"])
        w.writerows(sorted(new.items(), key=lambda kv: kv[1]))
    print(f"安心ストアを取り込み: {len(new)}件（{files[-1].name} → {dest.name}）", file=sys.stderr)
    return True


def is_memo(row: dict) -> bool:
    """相場メモ（手で直した相場）. 過去データより優先する（extension_template.js の isMemo と同じ）."""
    return str(row.get("source") or "").startswith("相場メモ")


def match_prefer_memo(query: str, memo: list[dict], others: list[dict]) -> list[dict]:
    return match_sales(query, memo) or match_sales(query, others)


def analyze(items: list[Item], sales: list[dict], cfg: dict, fx: float, offline: bool,
            delay: float, kantei: list[dict] | None = None) -> None:
    memo = [s for s in sales if is_memo(s)]
    others = [s for s in sales if not is_memo(s)]
    set_known_models([s.get("title") for s in sales] + [k.get("title") for k in (kantei or [])])
    cfg["judge"]["_condition_step_pct"] = condition_step_pct(cfg, others)  # 状態1ランクの補正（%）を実績から
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

        if "セット品" in item.flags:  # まとめ売りは中身ごとに相場を当てて合計する
            parts = []
            for seg in set_part_titles(item.title):
                ms = match_prefer_memo(f"{title_to_query(seg)} {seg}", memo, others)
                if ms:
                    parts.append((seg, round(statistics.median(m["_price"] for m in ms), 2)))
            if len(parts) >= 2:
                item.set_parts = parts
                item.flags = [f for f in item.flags if f != "付属品のみ?"]  # 中身を当てたので付属品だけの出品ではない
                item.expected_price_usd = set_value(parts, cfg, fx)
                item.price_source = f"まとめ売り {len(parts)}点の合計（" + " ＋ ".join(f"{seg} ${pr:g}" for seg, pr in parts) + "）"
                item.calc = calculate(item, cfg, fx)
                continue

        # 照合はタイトル全体（検索語は7語に絞っているため）
        item.history_matches = match_prefer_memo(f"{item.ebay_query} {item.title}", memo, others)
        if not item.history_matches and kantei:
            item.history_matches = match_kantei(item.title, kantei)

        assign_price(item, cfg, fx)


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
        links = ebay_links(ebay_search_query(it.ebay_query, it.title)) if it.ebay_query else {}
        rows.append(
            {
                "判定": mark,
                "枠": "安心" if it.trusted else "チャレンジ",
                # 確認不要＝根拠が 自分の販売実績・Chatwork実績・自分で登録した相場（または手入力）。頒布会リスト・鑑定表（推定）は確認してから
                "根拠の強さ": "確認不要" if (it.ebay_price_usd is not None or (it.history_matches and source_tier(it.history_matches[0]) <= SURE_TIER))
                else "確認してから" if it.expected_price_usd is not None else "",
                "国内送料": it.domestic_shipping_jpy,
                "国内送料の根拠": ("出品ページ" if "送料は出品ページ" in it.fetch_note else "検索結果")
                if it.domestic_shipping_jpy is not None else f"不明→{_yen(int(CONFIG_DEFAULT_SHIP))}で計算",
                "仮判定": it.tentative.get("mark", ""),
                "仮の上限入札額": it.tentative.get("max_bid"),
                "仮の想定USD": it.tentative.get("usd"),
                "仮の利益(現在価格)": it.tentative.get("profit"),
                "仮の根拠": (f"{it.tentative['label']} ・ eBay出品中（送料別）最安${_net(it.tentative['min']):.0f}・中央値${_net(it.tentative['med']):.0f}"
                          f"（{it.tentative['n']}件）から案 ${_net(it.tentative['sg']):g}") if it.tentative else "",
                "仮のsoldURL": (it.tentative.get("links") or {}).get("sold", ""),
                "仮の出品中URL": (it.tentative.get("links") or {}).get("active", ""),
                "理由": reason,
                "タイトル": it.title,
                "ヤフオクURL": it.url,
                "現在価格": it.current_price_jpy,
                "即決価格": it.buy_now_jpy,
                "入札数": it.bids,
                "終了": it.end_time,
                "eBay検索語": ebay_search_query(it.ebay_query, it.title) if it.ebay_query else "",
                "eBay販売済みURL": links.get("sold", ""),
                "eBay出品中URL": links.get("active", ""),
                "eBayProductResearchURL": links.get("research", ""),
                "過去実績件数": len(it.history_matches) or None,
                "過去実績URL": " ".join(m.get("ebay_url", "") for m in it.history_matches if m.get("ebay_url")),
                "過去実績の内訳": " / ".join(
                    f"{m.get('title', '')} ${_net(m['_price']):g}（送料別）"
                    + (f" 仕入{int(float(m['purchase_jpy'])):,}円" if str(m.get('purchase_jpy') or '').replace('.', '').isdigit() else "")
                    + f" [{m.get('source', '自分の販売実績')}]"
                    for m in it.history_matches[:5]),
                "想定販売価格USD": it.expected_price_usd,
                "売れ筋": "売れ筋" if it.sold_hit else "",
                "価格の根拠": it.price_source,
                "Net販売価格USD": c.get("net_sale_usd"),
                "仕入価格(現在+国内送料)": c.get("purchase_jpy"),
                "eBay手数料": c.get("fees_jpy"),
                "国際送料": c.get("intl_shipping_jpy"),
                "手取り(円)": c.get("net_jpy"),
                "利益(現在価格)": c.get("profit_now_jpy"),
                "利益率%": c.get("margin_now_pct"),
                "上限入札額": c.get("max_bid_jpy"),
                "入札額の税": (f"ストア: このまま入札。支払いは税込{round(c['max_bid_jpy'] * 1.1):,}円" if c.get("tax_excl_bid")
                              else "個人: このまま入札（税なし）") if c else "",
                "損益分岐入札額": c.get("breakeven_bid_jpy"),
                "上限までの余裕": c.get("headroom_jpy"),
                "注意": " / ".join(it.flags),
                "説明文": ("説明文に " + "・".join(h["word"] for h in it.desc_hits)) if it.desc_hits
                else "説明文に光学難・故障の記載なし" if it.desc_checked else "",
                "メモ": (it.memo + " " + it.fetch_note).strip(),
            }
        )
    order = {"◎": 0, "○": 1, "要確認": 2, "×": 3}
    # ◎○は 売れ筋・$99超 を上に（bid_priority）、同じ順位の中は終了が近い順（入札の段取り順）。それ以外は余裕の大きい順
    rows.sort(key=lambda r: (order[r["判定"]],
                             bid_priority(r["想定販売価格USD"], bool(r["売れ筋"])) if r["判定"] in ("◎", "○") else 0,
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
            f"- 過去の実績: {r['過去実績件数'] or 0}件 {r['過去実績の内訳']}",
            f"- 想定販売価格: {_usd(r['想定販売価格USD'])}（{r['価格の根拠'] or '根拠なし'}） → 送料差引後Net {_usd(r['Net販売価格USD'])}",
            f"- 仕入価格(現在+国内送料): {_yen(r['仕入価格(現在+国内送料)'])} / eBay手数料 {_yen(r['eBay手数料'])} / 国際送料 {_yen(r['国際送料'])}",
            f"- 利益(現在価格で落札した場合): {_yen(r['利益(現在価格)'])}（{r['利益率%'] if r['利益率%'] is not None else '-'}%）",
            f"- **上限入札額: {_yen(r['上限入札額'])}**（{r['入札額の税']}）（損益分岐 {_yen(r['損益分岐入札額'])}）",
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
    # 世代（II・III）と限定などの型も機種名に残す（拡張機能の modelKey と同じ考え方）
    gen = lambda t: t.lower() in GENERATIONS or t.lower() in PREMIUM_VARIANTS  # noqa: E731
    digits = [t for t in rest if re.search(r"\d", t)][:3]
    key_toks = ([t for t in rest if t in digits or gen(t)] if digits else []) or rest[:2]
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
    cards: dict[str, list[str]] = {"安心": [], "チャレンジ": [], "要確認": [], "確認": []}
    # 付属品だけ（カタログ・フード・説明書など）・アダプター等は仕入れの対象外 → 出さない（件数だけ）
    is_acc = lambda r: any(f in (r.get("注意") or "") for f in ("付属品のみ?", "アダプター等"))  # noqa: E731
    acc_n = sum(1 for r in rows if is_acc(r))
    rows_all, rows = rows, [r for r in rows if not is_acc(r)]
    shown = [r for r in rows if r["判定"] in ("◎", "○") or (r["判定"] == "要確認" and r["想定販売価格USD"])]
    hidden_bad = [r for r in rows if r["判定"] == "×"]
    tent = sorted((r for r in rows if r.get("仮判定")), key=lambda r: -(r.get("仮の利益(現在価格)") or 0))
    for r in shown:
        cls = {"◎": "good", "○": "ok", "×": "bad"}.get(r["判定"], "unk")
        def link(u, label):
            return f'<a href="{e(u)}" target="_blank" rel="noopener">{label}</a>' if u else ""
        hist = " ".join(
            link(u, f"実績{i+1}") for i, u in enumerate(r["過去実績URL"].split()) if u
        )
        box = (cards["要確認"] if r["判定"] == "要確認" else cards["確認"] if r.get("根拠の強さ") == "確認してから"
               else cards[r.get("枠") or "チャレンジ"])
        box.append(f"""
<article class="card {cls}">
  <div class="top"><span class="mark">{e(r['判定'])}</span><h2>{'★ ' if r.get('枠') == '安心' else ''}{e(r['タイトル'] or r['ヤフオクURL'])}</h2></div>
  <p class="reason">{e(r['理由'])}{(' ・ ⚠ ' + e(r['注意'])) if r['注意'] else ''}</p>
  <div class="grid">
    <div><label>現在価格</label><b>{_yen(r['現在価格']) or '-'}</b><small>入札{r['入札数'] if r['入札数'] is not None else '-'}・即決{_yen(r['即決価格']) or '-'}</small></div>
    <div class="hl"><label>入札する上限額（このまま入札）</label><b>{_yen(r['上限入札額']) or '-'}</b><small>{e(r["入札額の税"])} ・ 損益分岐 {_yen(r['損益分岐入札額']) or '-'}</small></div>
    <div><label>eBay相場（送料別）</label><b>{_usd(r['Net販売価格USD']) or '-'}</b><small>{e(r['価格の根拠'] or '根拠なし')} ・ 送料込み {_usd(r['想定販売価格USD'])}</small></div>
    <div><label>利益（現在価格）</label><b>{_yen(r['利益(現在価格)']) or '-'}</b><small>利益率 {r['利益率%'] if r['利益率%'] is not None else '-'}%</small></div>
    <div><label>仕入価格</label><b>{_yen(r['仕入価格(現在+国内送料)']) or '-'}</b><small>現在価格＋国内送料（{e(r["国内送料の根拠"])}）</small></div>
    <div><label>eBay手数料／国際送料</label><b>{_yen(r['eBay手数料']) or '-'}</b><small>送料 {_yen(r['国際送料']) or '-'}</small></div>
  </div>
  <p class="links">{link(r['ヤフオクURL'], 'ヤフオク出品')}{link(r['eBay販売済みURL'], 'eBay販売済み')}{link(r['eBay出品中URL'], 'eBay出品中')}{link(r['eBayProductResearchURL'], 'Product Research')}{hist}</p>
  {('<p class="meta">参考実績: ' + e(r['過去実績の内訳']) + '</p>') if r['過去実績の内訳'] else ''}
  <p class="meta">検索語: <code>{e(r['eBay検索語'])}</code> ・ 終了: {e(r['終了'] or '-')}{(' ・ ' + e(r['メモ'])) if r['メモ'] else ''}</p>
</article>""")
    counts = {k: sum(r["判定"] == k for r in rows) for k in ("◎", "○", "要確認", "×")}
    counts["付属品など"] = acc_n
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
<p class="summary">{datetime.now():%Y-%m-%d %H:%M} 作成 ・ {len(rows_all)}件（◎{counts['◎']} ○{counts['○']} 要確認{counts['要確認']} ×{counts['×']} ・ 付属品など{counts['付属品など']}件は除外）<br>
為替 1USD={fx:.2f}円（円転ロス{cfg['fx']['conversion_loss_pct']}%） ・ 基準: 利益{j['min_profit_jpy']:,}円以上かつ利益率{j['min_margin_pct']}%以上 ・
eBay手数料 FVF{cfg['ebay']['final_value_fee_pct']}%+海外{cfg['ebay']['international_fee_pct']}%+広告{cfg['ebay']['ad_rate_pct']}%+${cfg['ebay']['per_order_fee_usd']}<br>
上限入札額＝この金額で落札しても基準の利益が残る最大額（ヤフオクの入札単位で切り捨て）。eBayの相場は自動取得しません（規約順守）。soldリンク・Product Researchで確認してください。</p>
<h2>★ 安心枠（フォロー中のストア）{len(cards['安心'])}件</h2>
<p class="summary">基準: 利益{j.get('trusted_min_profit_jpy', j['min_profit_jpy']):,}円以上かつ利益率{j.get('trusted_min_margin_pct', j['min_margin_pct'])}%以上（2026-11-16 から全体と同じ基準）。終了が近い順。</p>
{''.join(cards['安心']) or '<p class="summary">ありません（ストアのページを開くと、フォロー中なら拡張機能が自動で安心ストアに登録します）。</p>'}
<h2>チャレンジ枠（その他の出品者）{len(cards['チャレンジ'])}件</h2>
{''.join(cards['チャレンジ']) or '<p class="summary">基準を満たす出品はありませんでした。</p>'}
<h2>確認してから入札: 根拠が頒布会リスト・鑑定表（推定）の ◎○ {len(cards['確認'])}件</h2>
<p class="summary">売れた価格ではない根拠です。sold を見て、売れた商品代が相場以上なら拡張機能の［直す$］で登録 → 入札候補に上がります。</p>
{''.join(cards['確認']) or '<p class="summary">ありません。</p>'}
<h2>確認してから入札: 仮◎○（相場は未登録・eBay出品中の価格で仮に計算）{len(tent)}件</h2>
<p class="summary">sold を見て、売れた商品代（送料別）が根拠の「案」以上なら入札してよい候補。ヤフオクの検索ページで拡張機能の［登録］を押すと ◎○ に上がり、同じ機種の今後の出品にも効きます。利益の大きい順。</p>
<table class="tbl"><tr><th>判定</th><th>商品</th><th>現在</th><th>仮の上限（このまま入札）</th><th>仮の利益</th><th>根拠</th><th>eBay</th></tr>{''.join(
    f'<tr><td>仮{e(r["仮判定"])}</td><td>{"★ " if r.get("枠") == "安心" else ""}<a href="{e(r["ヤフオクURL"])}" target="_blank" rel="noopener">{e(r["タイトル"][:60])}</a><br><small>終了 {e(r["終了"] or "-")}</small></td>'
    f'<td>{_yen(r["現在価格"])}</td><td><b>{_yen(r["仮の上限入札額"])}</b></td><td>{_yen(r["仮の利益(現在価格)"])}</td>'
    f'<td><small>{e(r["仮の根拠"])}</small></td><td><a href="{e(r["仮のsoldURL"])}" target="_blank" rel="noopener">sold</a> '
    f'<a href="{e(r["仮の出品中URL"])}" target="_blank" rel="noopener">now</a></td></tr>'
    for r in tent)}</table>
<details><summary>要確認（説明文の難・付属品の可能性など）{len(cards['要確認'])}件</summary>{''.join(cards['要確認'])}</details>
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
    ap.add_argument("input", type=Path, nargs="?", default=DATA / "search_urls.txt",
                    help="ヤフオクの検索結果URL・出品URLの一覧（省略時は data/search_urls.txt）。ブックマークHTML / CSV も可")
    ap.add_argument("--sales", type=Path, nargs="*", default=None,
                    help="過去の販売実績CSV（複数可）。省略時は data/ の reference_prices.csv, chatwork_sales.csv, price_memo.csv")
    ap.add_argument("--kantei", type=Path, help="鑑定ブックマークレットのテキスト（.txt/.js/.docx）。相場表を参考に使う")
    ap.add_argument("--config", type=Path, help="設定ファイル（既定: config.toml）")
    ap.add_argument("--fx", type=float, help="為替 1USD=何円（指定すると自動取得しない）")
    ap.add_argument("--offline", action="store_true", help="通信しない（手入力値と販売実績のみで計算）")
    ap.add_argument("--delay", type=float, default=1.5, help="通信の間隔（秒）")
    ap.add_argument("--hit-searches", type=int, default=0,
                    help="売れ筋（販売実績で利益が出ていた機種）を名指しで探す検索を、利益の大きい順に何本足すか（1本100件。0で足さない）")
    ap.add_argument("--max-per-search", type=int, default=2000, help="検索URL1つあたりに調べる出品数（上から。100件ごとに1回アクセス）")
    ap.add_argument("--out", type=Path, default=ROOT / "reports", help="出力フォルダ（省略時は reports/）")
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
    if args.sales is None:
        import_extension_memo()
        args.sales = [p for p in (DATA / n for n in ("reference_prices.csv", "chatwork_sales.csv", "price_memo.csv"))
                      if p.exists()]
    sales = [row for p in args.sales for row in read_sales(p)]
    if not args.offline:
        text = args.input.read_text(encoding="utf-8-sig", errors="replace")
        known = {it.auction_id for it in items}
        searches = [(su, args.max_per_search) for su in find_search_urls(text)]
        if args.hit_searches:
            bid_rows = []
            for bp in (DATA / "bid_log.csv", DATA / "bid_history.csv"):
                if bp.exists():
                    bid_rows += list(csv.DictReader(bp.read_text(encoding="utf-8-sig").splitlines()))
            hits = hit_search_urls(sales, cfg, fx, args.hit_searches, lost_models(bid_rows))
            print(f"売れ筋・負けた機種の検索: {len(hits)}本（{', '.join(lbl for _, lbl in hits[:8])}{' …' if len(hits) > 8 else ''}）",
                  file=sys.stderr)
            searches += [(u, 100) for u, _ in hits]  # 1本100件（終了が近い順の1ページ）
        for su, lim in searches:
            try:
                got = fetch_listing(su, lim, args.delay)
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
    if args.kantei is None:
        args.kantei = next(iter(sorted(DATA.glob("kantei_bookmarklet.*"))), None)
    print(f"販売実績・相場: {len(sales)}件（{', '.join(p.name for p in args.sales) or 'なし'}）", file=sys.stderr)
    kantei = read_kantei_table(args.kantei) if args.kantei else []
    if args.kantei:
        print(f"鑑定表: {len(kantei)}件の相場を読み込み", file=sys.stderr)
    import_extension_trusted()
    trusted = load_trusted()
    for it in items:
        it.trusted = bool(it.seller_id) and it.seller_id in trusted
    if trusted:
        print(f"安心ストア: {len(trusted)}件（出品 {sum(it.trusted for it in items)}件が安心枠）", file=sys.stderr)
    analyze(items, sales, cfg, fx, args.offline, args.delay, kantei)
    if not args.offline:
        global CONFIG_DEFAULT_SHIP
        CONFIG_DEFAULT_SHIP = cfg["yahoo"]["default_domestic_shipping_jpy"]
        global CONFIG_SHIP_USD
        CONFIG_SHIP_USD = float(cfg["shipping"].get("deduction_usd", 0))
        n = check_descriptions(items, args.delay, cfg, fx)
        print(f"説明文チェック: ◎○ {n}件を確認（カビ・クモリ等があれば要確認に）", file=sys.stderr)
        import tentative  # 相場が無い出品を eBay 出品中の価格（公式 Browse API）で仮に判定
        t = tentative.add_tentative(items, cfg, fx)
        print(f"仮◎○: {t}件（sold を見て、拡張機能で［登録］すると ◎○ に）", file=sys.stderr)


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
