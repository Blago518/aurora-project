#!/usr/bin/env python3
"""事業の数字をグラフで見るページ（reports/dashboard.html）を作る.

入力（どれも無ければ、その部分は「データ無し」と表示するだけ）:
    reports/ebay_orders_*.csv  eBay API の注文（python3 tools/ebay_api.py orders --days 730）→ 販売の件数・金額・国・返金の有無。
                               あれば販売はこれで数える（古物台帳の記入が遅れていても eBay の実態どおりになる）
    reports/ebay_finances_*.csv eBay API の取引明細（python3 tools/ebay_api.py fees --days 730）→ 実際の手取り・手数料・広告費・返金
    data/ledger.csv            古物台帳（ledger_sync.py / update_all.py が取り込む）→ 仕入金額・購入日・仕入先（No. で結び付け）。
                               eBay API の注文が無いときは販売もこれで数える
    data/ledger_costs.csv      管理番号ごとの国際送料・関税・発送代行費・販売国（build_ledger_costs.py）
    reports/listings_*.csv     eBay の出品中一覧（python3 tools/ebay_api.py listings）の一番新しいもの
    data/kanri.csv             管理シートの No.・状態・購入日・販売日（ledger_sync.py が取り込む）→ 売れた商品の状態
    data/ebay_report.csv       ebay売上シート（eBay の取引レポート）→ eBay API の取引明細が無いときの実際の手取り
    data/junk.csv              J管理シート → ジャンク品のヤフオク再販の成績
    data/item_types.csv        出品情報シートの商品種別（Lens / Camera Body）→ 種類別のグラフ
    data/monthly_costs.csv     （任意）月ごとの送料・関税・発送代行の実額（列: 月,CPaSS,関税,オークレボ）。
                               発送明細（ledger_costs.csv）が無いときに、推定（一律 $30）と比べるのに使う
    data/sku_fix.csv           昔の注文（SKU_TRUSTED_FROM より前）の SKU の対応表（列: order_id,正しいNo.,メモ）。
                               その頃は eBay の SKU（Custom label）が No. と合っていないので、SKU は使わずこの表で No. を決める。
                               候補は python3 tools/build_dashboard.py --sku-candidates → reports/sku_fix_candidates.csv
    data/targets.toml          目標（任意）。例:
                                   [month]
                                   sales_jpy = 500000
                                   profit_jpy = 100000
    tools/config.toml          手数料・為替（利益は appraise.py と同じ率で計算する）

古物台帳の列は下の COLUMNS の候補名で探す。見つからない列はページの「使った列」に「無し」と出る
（推測した値では埋めない）。列名が違うときは COLUMNS に足す。

使い方（aurora フォルダで）:
    python3 tools/build_dashboard.py           # → reports/dashboard.html（Mac ではブラウザで開く）
    python3 tools/build_dashboard.py --sku-candidates   # 昔の注文の正しい No. の候補 → reports/sku_fix_candidates.csv
    python3 tools/build_dashboard.py --no-open
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
TEMPLATE = HERE / "dashboard_template.html"
sys.path.insert(0, str(HERE))

import appraise  # noqa: E402  状態の読み取り・メーカー名・機種名・設定の読み込みを共通化

# 古物台帳の列の候補名（改行・空白を除いた見出しと照合。完全一致 → 前方一致の順）
COLUMNS = {
    "sold_date": ["販売日"],
    "title": ["品目(商品名)", "品目", "商品名"],
    "price_usd": ["販売金額($)", "販売金額"],
    "cost_jpy": ["仕入合計", "仕入金額"],  # 送料込みの仕入額を優先（Claude更新版は「仕入合計(円)」）
    "buy_date": ["購入日", "仕入日", "仕入れ日", "仕入年月日", "購入年月日", "取引年月日", "入荷日"],
    "supplier": ["アカウント名", "アカウント名(仕入元)", "仕入先", "仕入れ先", "購入先"],
    "country": ["販売国", "発送先国", "購入者の国", "国"],
    "sku": ["管理番号", "管理ID", "SKU", "No."],
}
COLUMN_LABEL = {
    "sold_date": "販売日", "title": "品目（商品名）", "price_usd": "販売金額（$）", "cost_jpy": "仕入金額",
    "buy_date": "購入日", "supplier": "仕入先（アカウント名）", "country": "販売国", "sku": "管理番号",
}
COUNTRY_JA = {
    "US": "アメリカ", "GB": "イギリス", "UK": "イギリス", "AU": "オーストラリア", "CA": "カナダ",
    "DE": "ドイツ", "FR": "フランス", "IT": "イタリア", "ES": "スペイン", "NL": "オランダ",
    "TW": "台湾", "HK": "香港", "SG": "シンガポール", "KR": "韓国", "CN": "中国", "CH": "スイス",
    "SE": "スウェーデン", "AT": "オーストリア", "BE": "ベルギー", "IE": "アイルランド", "NZ": "ニュージーランド",
    "IL": "イスラエル", "MX": "メキシコ", "BR": "ブラジル", "PL": "ポーランド", "DK": "デンマーク",
    "NO": "ノルウェー", "FI": "フィンランド", "PT": "ポルトガル", "TH": "タイ", "MY": "マレーシア",
}
# この日より前の eBay の注文は SKU（Custom label）が No. と合っていない（ユーザー確認 2026-11-15。ebay売上シートの注意書きと同じ）
SKU_TRUSTED_FROM = date(2026, 8, 18)
MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
KIND_JA = {"lens": "レンズ", "camera body": "カメラ（ボディ）"}
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
# 状態の並び（良い順）。Exc+2 以下はまとめる
COND_ORDER = ["未使用", "TOP MINT", "MINT", "Almost MINT", "Near MINT", "Exc+5", "Exc+4", "Exc+3", "Exc+2以下",
              "ジャンク", "その他", "表記なし"]
# 管理シートの状態欄（「MINT/点カビ」の / より前）→ 状態. 上から順に見る（空白・記号を除いた小文字で照合）
SHEET_COND_RULES = [
    (r"^(unused|未使用|新品)", "未使用"),
    (r"^(top|t)mint", "TOP MINT"),
    (r"^(almost|a)mint", "Almost MINT"),
    (r"^(near|n)mint|^nm", "Near MINT"),
    (r"^mint", "MINT"),
    (r"^(e|ex|exc|excellent)\+?(\d)", "Exc+$2"),
    (r"^(e|ex|exc|excellent)(\+*)$", "Exc$2"),
    (r"junk|ジャンク|parts", "ジャンク"),
]
OPTICAL_RE = re.compile(r"カビ|かび|曇|くもり|クモリ|バルサム|fungus|haze", re.I)


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def _num(v) -> float | None:
    s = re.sub(r"[,$¥￥円\s]", "", unicodedata.normalize("NFKC", str(v or "")))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_date(v, default_year: int | None = None) -> date | None:
    s = unicodedata.normalize("NFKC", str(v or "")).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", s):  # スプレッドシートの日付シリアル値
        n = float(s)
        if 30000 < n < 80000:
            return date(1899, 12, 30) + timedelta(days=int(n))
        return None
    m = re.match(r"(\d{4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})", s)
    e = re.match(r"(\d{1,2})[-\s]([A-Za-z]{3})[a-z]*[-\s,]+(\d{2,4})$", s)  # eBay のレポート: 17-Aug-26
    if e and e.group(2).lower() in MONTHS_EN:
        y = int(e.group(3)) + (2000 if len(e.group(3)) == 2 else 0)
        mo, d = MONTHS_EN.index(e.group(2).lower()) + 1, int(e.group(1))
    elif m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})\s*[/月]\s*(\d{1,2})", s)
        if not m or not default_year:
            return None
        y, (mo, d) = default_year, map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def find_columns(headers: list[str]) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for key, cands in COLUMNS.items():
        hit = next((h for c in cands for h in headers if h == c), None)
        hit = hit or next((h for c in cands for h in headers if h.startswith(c) and "販売日" not in h), None)
        found[key] = hit
    return found


def read_ledger(path: Path) -> tuple[list[dict], dict[str, str | None]]:
    """古物台帳 → 行のリスト（title, sold, price_usd, cost_jpy, buy, supplier, country, sku）と使った列."""
    if not path.exists():
        return [], {k: None for k in COLUMNS}
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    start = next((i for i, ln in enumerate(lines) if "販売日" in ln), 0)
    reader = csv.DictReader(lines[start:])
    nk = lambda h: unicodedata.normalize("NFKC", h or "").replace("\n", "").strip()  # noqa: E731  全角カッコもそろえる
    headers = [nk(h) for h in (reader.fieldnames or [])]
    cols = find_columns(headers)
    rows = []
    for raw in reader:
        r = {nk(k): (v or "").strip() for k, v in raw.items() if not isinstance(v, list)}
        g = lambda key: r.get(cols[key] or "", "")  # noqa: E731
        title = g("title")
        if not title:
            continue
        sold = parse_date(g("sold_date"))
        price = _num(g("price_usd"))
        buy = parse_date(g("buy_date"), default_year=sold.year if sold else None)
        rows.append({
            "title": title, "sold": sold if sold and price else None, "price_usd": price or 0.0,
            "cost_jpy": _num(g("cost_jpy")), "buy": buy, "supplier": g("supplier"),
            "country": g("country").upper() if len(g("country")) == 2 else g("country"), "sku": g("sku"),
        })
    return rows, cols


def norm_sku(v: str) -> str:
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", str(v or ""))).upper()


def read_costs(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
        if r.get("管理番号"):
            out[norm_sku(r["管理番号"])] = {
                "ship": _num(r.get("cpass_shipping_jpy")), "duty": _num(r.get("duty_jpy")) or 0.0,
                "aucrevo": _num(r.get("aucrevo_jpy")) or 0.0, "country": (r.get("country") or "").strip().upper(),
            }
    return out


def latest_listings(reports: Path) -> tuple[list[dict], str]:
    files = sorted(reports.glob("listings_*.csv")) if reports.exists() else []
    if not files:
        return [], ""
    rows = list(csv.DictReader(files[-1].read_text(encoding="utf-8-sig").splitlines()))
    m = re.search(r"(\d{4})(\d{2})(\d{2})", files[-1].stem)
    return rows, (f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else files[-1].name)


def read_targets(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib
        d = tomllib.loads(path.read_text(encoding="utf-8"))
    except ImportError:
        d = appraise._parse_simple_toml(path.read_text(encoding="utf-8"))
    return d.get("month", {}) or {}


# ---------------------------------------------------------------------------
# 分類
# ---------------------------------------------------------------------------

def condition_group(title: str) -> str:
    c = appraise.condition_of({"title": title})
    if not c:
        return "表記なし"
    m = re.fullmatch(r"Exc(?:\+(\d)|(\++))?", c)
    if m:
        n = int(m.group(1)) if m.group(1) else len(m.group(2) or "")
        return f"Exc+{n}" if n >= 3 else "Exc+2以下"
    return c if c in COND_ORDER else "表記なし"


def sheet_condition(raw: str) -> str:
    """管理シートの状態欄 → 状態のまとまり（COND_ORDER の名前）."""
    t = re.sub(r"[\s　.\-]", "", unicodedata.normalize("NFKC", (raw or "").split("/")[0])).lower()
    if not t:
        return "表記なし"
    for pat, out in SHEET_COND_RULES:
        m = re.search(pat, t)
        if m:
            if "$2" in out:
                g = m.group(2)
                n = int(g) if g.isdigit() else len(g)
                return f"Exc+{n}" if n >= 3 else "Exc+2以下"
            return out
    return "その他"


def read_kanri(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()):
        no = norm_sku(r.get("No.", ""))
        if no:
            out[no] = {"raw": (r.get("状態") or "").strip(), "cond": sheet_condition(r.get("状態", "")),
                       "sold": parse_date(r.get("販売日")), "optical": bool(OPTICAL_RE.search(r.get("状態") or "")),
                       "cost": _num(r.get("仕入(円)")), "buy": parse_date(r.get("購入日")),
                       "title": (r.get("商品名") or "").strip(), "supplier": (r.get("アカウント名(仕入元)") or "").strip()}
    return out


def _csv_rows(path: Path) -> list[dict]:
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines())) if path.exists() else []


def read_ebay_sales(path: Path) -> dict:
    """ebay売上（eBay の取引レポート）→ 管理番号（Custom label）ごとの実際の手取り（USD）.

    Order・Other fee（広告費など）・Refund・Claim・Adjustment を合計する。Custom label が空の行は同じ注文番号の Order の
    ラベルに付ける。Hold（一時保留）・Payout（入金）は売上ではないので数えない。"""
    rows = _csv_rows(path)
    if not rows:
        return {}
    valid = lambda v: (v or "").strip() not in ("", "--")  # noqa: E731
    order_label = {r.get("Order number"): norm_sku(r["Custom label"]) for r in rows
                   if r.get("Type") == "Order" and valid(r.get("Custom label")) and valid(r.get("Order number"))}
    per: dict[str, dict] = defaultdict(lambda: {"net": 0.0, "gross": 0.0, "orders": 0, "promo": 0.0, "refund": 0.0})
    unassigned: dict[str, float] = defaultdict(float)
    refunds, skipped_cur = [], 0
    for r in rows:
        typ = (r.get("Type") or "").strip()
        if typ in ("Hold", "Payout", "Transfer", "") or not valid(r.get("Net amount")):
            continue
        if (r.get("Payout currency") or "USD").strip() not in ("USD", "--", ""):
            skipped_cur += 1
            continue
        amt = _num(r["Net amount"]) or 0.0
        label = norm_sku(r["Custom label"]) if valid(r.get("Custom label")) else order_label.get(r.get("Order number"))
        day = parse_date(r.get("Transaction creation date"))
        if typ == "Refund":
            refunds.append({"date": day.strftime("%Y/%m/%d") if day else "", "label": label or "", "usd": amt})
        if not label:
            unassigned[typ] += amt
            continue
        p = per[label]
        p["net"] += amt
        if typ == "Order":
            p["orders"] += 1
            p["gross"] += _num(r.get("Gross transaction amount")) or 0.0
        elif typ == "Refund":
            p["refund"] += amt
        elif "promoted" in (r.get("Description") or "").lower():
            p["promo"] += amt
    return {"per": dict(per), "unassigned": dict(unassigned), "refunds": refunds, "skipped_currency": skipped_cur,
            "first": min((d for d in (parse_date(r.get("Transaction creation date")) for r in rows) if d), default=None)}


def _latest(reports: Path, prefix: str) -> tuple[list[dict], str]:
    files = sorted(reports.glob(f"{prefix}_*.csv")) if reports.exists() else []
    if not files:
        return [], ""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", files[-1].stem)
    return _csv_rows(files[-1]), (f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else files[-1].name)


def read_api_finances(rows: list[dict]) -> dict:
    """eBay API の取引明細（ebay_api.py fees）→ 注文ごとの実際の手取り（USD）.

    SALE（手数料を引いた入金）・REFUND・広告費などの NON_SALE_CHARGE を注文番号で合計する。
    注文に結び付かないもの（ストアの月額など）は unassigned。PAYOUT（銀行への入金）・TRANSFER は数えない。"""
    per: dict[str, dict] = defaultdict(lambda: {"net": 0.0, "refund": 0.0, "promo": 0.0, "fee": 0.0})
    unassigned: dict[str, float] = defaultdict(float)
    refunds, skipped = [], 0
    for r in rows:
        typ = (r.get("type") or "").strip()
        if typ in ("PAYOUT", "TRANSFER", "") or (r.get("status") or "") in ("FAILED",):
            continue
        if (r.get("amount_currency") or "USD") != "USD":
            skipped += 1
            continue
        amt = _num(r.get("amount")) or 0.0
        amt = amt if r.get("booking") == "CREDIT" else -amt
        oid = (r.get("order_id") or "").strip()
        if not oid:
            m = re.search(r"ORDER_ID:(\S+)", r.get("references") or "")
            oid = m.group(1) if m else ""
        if typ == "REFUND":
            refunds.append({"date": (r.get("date") or "").replace("-", "/"), "order_id": oid, "usd": amt})
        if not oid:
            unassigned[f"{typ} {r.get('fee_type') or ''}".strip()] += amt
            continue
        p = per[oid]
        p["net"] += amt
        if typ == "REFUND":
            p["refund"] += amt
        elif "AD_FEE" in (r.get("fee_type") or "") or "AD_FEE" in typ:
            p["promo"] += amt
        if typ == "SALE":
            p["fee"] += _num(r.get("total_fee")) or 0.0
    return {"per": dict(per), "unassigned": dict(unassigned), "refunds": refunds, "skipped_currency": skipped}


def read_sku_fix(path: Path) -> dict[str, str]:
    """注文番号 → 正しい No.（data/sku_fix.csv）."""
    return {r["order_id"].strip(): r["正しいNo."].strip() for r in _csv_rows(path)
            if (r.get("order_id") or "").strip() and (r.get("正しいNo.") or "").strip()}


def _title_tokens(title: str) -> set[str]:
    return {t.lower() for t in appraise.title_to_query(title or "", 20).split() if len(t) > 1 or t.isdigit()}


def sku_candidates(orders: list[dict], ledger: list[dict], kanri_full: dict, fix: dict[str, str],
                   top: int = 3) -> list[dict]:
    """SKU が信用できない昔の注文ごとに、商品名が似ている台帳・管理シートの No. を候補として出す（購入日が販売日以前のものだけ）."""
    items: dict[str, dict] = {}
    for k, v in kanri_full.items():
        if v.get("title"):
            items[k] = {"no": k, "title": v["title"], "buy": v.get("buy"), "cost": v.get("cost")}
    for r in ledger:
        if r["sku"]:
            items[norm_sku(r["sku"])] = {"no": r["sku"], "title": r["title"], "buy": r["buy"], "cost": r["cost_jpy"]}
    toks = {k: _title_tokens(v["title"]) for k, v in items.items()}
    out = []
    for o in orders:
        day = parse_date(o.get("date"))
        if not day or day >= SKU_TRUSTED_FROM or o.get("order_id", "") in fix:
            continue
        ot = _title_tokens(o.get("title", ""))
        scored = []
        for k, it in items.items():
            if it["buy"] and it["buy"] > day:
                continue  # 売れた後に買ったものは違う
            common = ot & toks[k]
            if not common:
                continue
            scored.append((len(common) / len(ot | toks[k]), it))
        scored.sort(key=lambda x: -x[0])
        row = {"order_id": o.get("order_id", ""), "注文日": day.strftime("%Y/%m/%d"), "eBayのSKU": o.get("sku", ""),
               "eBayの商品名": o.get("title", ""), "金額($)": o.get("item_price", "")}
        for i in range(top):
            s, it = scored[i] if i < len(scored) else (0, None)
            row[f"候補{i + 1}_No."] = it["no"] if it else ""
            row[f"候補{i + 1}_品目"] = it["title"] if it else ""
            row[f"候補{i + 1}_一致度"] = f"{s * 100:.0f}%" if it else ""
        out.append(row)
    return out


def report_to_api(rows: list[dict]) -> tuple[list[dict], dict]:
    """ebay売上シート（eBay の取引レポート）→ eBay API の注文・取引明細と同じ形. eBay API が使えないときの代わり."""
    valid = lambda v: (v or "").strip() not in ("", "--")  # noqa: E731
    refund_gross: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("Type") == "Refund" and valid(r.get("Order number")):
            refund_gross[r["Order number"]] += _num(r.get("Gross transaction amount")) or 0.0
    orders, fin_rows = [], []
    for r in rows:
        typ = (r.get("Type") or "").strip()
        day = parse_date(r.get("Transaction creation date"))
        oid = r.get("Order number", "") if valid(r.get("Order number")) else ""
        if typ == "Order" and day:
            gross = _num(r.get("Gross transaction amount")) or 0.0
            cur = (r.get("Transaction currency") or "USD").strip() or "USD"
            rate = _num(r.get("Exchange rate")) if valid(r.get("Exchange rate")) else None
            if cur not in ("USD", "--", "") and rate:  # 豪ドル・ポンド建ての注文は、レポートの換算レートでドルに直す
                refund_gross[oid] *= rate
                gross, cur = round(gross * rate, 2), "USD"
            orders.append({"date": day.isoformat(), "order_id": oid, "title": "",
                           "sku": r["Custom label"] if valid(r.get("Custom label")) else "",
                           "currency": "USD" if cur in ("--", "") else cur, "item_price": gross, "shipping": 0,
                           "country": "", "cancel_state": "",
                           "payment_status": "FULLY_REFUNDED" if gross and refund_gross.get(oid, 0) <= -gross * 0.99 else "PAID"})
        if typ in ("Hold", "Payout", "Transfer", "") or not valid(r.get("Net amount")):
            continue
        amt = _num(r["Net amount"]) or 0.0
        fin_rows.append({"date": day.isoformat() if day else "", "type": {"Order": "SALE", "Refund": "REFUND"}.get(typ, typ),
                         "booking": "CREDIT" if amt >= 0 else "DEBIT", "order_id": oid, "amount": abs(amt),
                         "amount_currency": (r.get("Payout currency") or "USD").strip() or "USD", "total_fee": "",
                         "fee_type": "AD_FEE" if "promoted" in (r.get("Description") or "").lower() else (r.get("Description") or ""),
                         "references": "", "status": ""})
    return orders, read_api_finances(fin_rows)


def sales_from_api(orders: list[dict], fin: dict, ledger: list[dict], kanri_full: dict, fx: float,
                   fix: dict[str, str] | None = None) -> dict:
    """eBay API の注文 → 販売の行（古物台帳の行と同じ形）. 仕入金額・購入日・仕入先は No.（SKU）で台帳・管理シートから.

    SKU_TRUSTED_FROM より前の注文は SKU を使わず、data/sku_fix.csv の対応表で No. を決める（無ければ仕入額不明）."""
    fix = fix or {}
    by_sku = {norm_sku(r["sku"]): r for r in ledger if r["sku"]}
    per = (fin or {}).get("per", {})
    sales, cancelled, non_usd, unfixed = [], [], 0, []
    for o in orders:
        day = parse_date(o.get("date"))
        if not day:
            continue
        oid = o.get("order_id", "")
        if oid in fix:
            sku = fix[oid]
        elif day < SKU_TRUSTED_FROM:
            sku = ""  # 昔の注文の SKU は信用しない
        else:
            sku = o.get("sku", "")
        key = norm_sku(sku)
        f = per.get(o.get("order_id", ""))
        net = f["net"] if f else None
        if day < SKU_TRUSTED_FROM and oid not in fix and not ((o.get("payment_status") or "") == "FULLY_REFUNDED"):
            unfixed.append({"date": day.strftime("%Y/%m/%d"), "order_id": oid, "sku": o.get("sku", ""),
                            "title": o.get("title", "")})
        if (o.get("payment_status") or "") == "FULLY_REFUNDED" or (o.get("cancel_state") or "").upper().startswith("CANCEL"):
            cancelled.append({"date": day.strftime("%Y/%m/%d"), "label": o.get("sku", ""), "title": o.get("title", ""),
                              "why": "返金" if o.get("payment_status") == "FULLY_REFUNDED" else "キャンセル"})
            continue
        price = (_num(o.get("item_price")) or 0.0) + (_num(o.get("shipping")) or 0.0)
        if (o.get("currency") or "USD") != "USD":
            non_usd += 1
            price = round(price * {"GBP": 1.27, "AUD": 0.66, "EUR": 1.08, "CAD": 0.73}.get(o.get("currency"), 1.0), 2)
        led = by_sku.get(key, {}) if key else {}
        k = kanri_full.get(key, {}) if key else {}
        cost = led.get("cost_jpy") if led.get("cost_jpy") is not None else k.get("cost")
        sales.append({
            "title": led.get("title") or k.get("title") or o.get("title", "") or sku
                     or (f"（{SKU_TRUSTED_FROM - timedelta(days=1):%-m/%-d}以前・No.不明）" if day < SKU_TRUSTED_FROM else "（品目名なし）"),
            "sold": day, "price_usd": price, "cost_jpy": cost,
            "buy": led.get("buy") or k.get("buy"), "supplier": led.get("supplier") or k.get("supplier", ""),
            "country": (o.get("country") or "").upper(), "sku": sku, "order_id": oid,
            "net_usd": net, "api": {"gross": price, "net": net or 0.0, "refund": f["refund"] if f else 0.0,
                                    "promo": f["promo"] if f else 0.0, "orders": 1} if f else None,
            "in_ledger_sold": bool(led.get("sold")), "in_ledger": bool(led), "old_unfixed": bool(day < SKU_TRUSTED_FROM and oid not in fix),
        })
    return {"sales": sales, "cancelled": cancelled, "non_usd": non_usd, "unfixed": unfixed}


def read_item_types(path: Path) -> dict[str, str]:
    out = {}
    for r in _csv_rows(path):
        t = KIND_JA.get((r.get("商品種別") or "").strip().lower())
        if r.get("管理番号") and t:
            out[norm_sku(r["管理番号"])] = t
    return out


def read_junk(path: Path) -> list[dict]:
    out = []
    for r in _csv_rows(path):
        if not (r.get("No.") or "").strip():
            continue
        out.append({"cost": _num(r.get("仕入(円)")) or 0.0, "sale": _num(r.get("実販売(円)")),
                    "diff": _num(r.get("売上−仕入(円)")), "buy": parse_date(r.get("購入日")),
                    "sold": parse_date(r.get("販売日")), "where": (r.get("保管") or "").strip()})
    return out


def read_monthly_costs(path: Path) -> dict[str, float]:
    """月 → 送料・関税・発送代行の実額の合計（円）."""
    out = {}
    for r in _csv_rows(path):
        m = parse_date((r.get("月") or "") + "/1")
        if m:
            out[f"{m.year}-{m.month:02d}"] = sum(_num(r.get(k)) or 0.0 for k in ("CPaSS", "関税", "オークレボ"))
    return out


# グラフ用のメーカー名（製品名・別表記 → メーカー）。入札判断の照合（appraise.py の BRANDS_EN）には影響させない
BRAND_ALIASES = [
    (r"rolleiflex|rolleicord|rollei", "Rollei"), (r"leotax", "Leotax"), (r"topcon|topcor", "Topcon"),
    (r"yongnuo", "Yongnuo"), (r"hasselblad", "Hasselblad"), (r"mamiya|sekor", "Mamiya"), (r"bronica|zenza", "Bronica"),
    (r"fujica|fujinon|fujifilm|\bfuji\b", "Fujifilm"), (r"voigtl[aä]nder", "Voigtlander"),
    (r"zeiss|planar|sonnar|distagon|tessar|biogon", "Zeiss"), (r"konica|hexanon|hexar", "Konica"),
    (r"minolta|rokkor", "Minolta"), (r"takumar|asahi", "Pentax"), (r"zuiko", "Olympus"), (r"nikkor", "Nikon"),
    (r"serenar", "Canon"), (r"chinon", "Chinon"), (r"cosina", "Cosina"), (r"vivitar", "Vivitar"), (r"tamron", "Tamron"),
    (r"tokina", "Tokina"), (r"samyang|rokinon", "Samyang"), (r"kodak", "Kodak"), (r"polaroid", "Polaroid"),
    (r"praktica|pentacon", "Praktica"), (r"zenit|helios|jupiter|kiev|lomo", "ロシア製（Zenit など）"),
    (r"petri", "Petri"), (r"miranda", "Miranda"), (r"exakta|ihagee", "Exakta"), (r"plaubel", "Plaubel"),
    (r"linhof", "Linhof"), (r"horseman", "Horseman"), (r"ricoh", "Ricoh"), (r"kyocera|yashica", "Yashica"),
]


def brand_of(title: str) -> str:
    """品目名 → メーカー. 入札判断と同じ読み取り → グラフ用の表 → 品目名の最初の英単語 の順。空のときだけ「（品目名なし）」."""
    if (title or "").startswith("（"):  # （8/17以前・No.不明）など、品目名が分からない販売はそのまま
        return title
    toks = appraise.title_to_query(title, 20).split()
    known = {b.lower() for b in appraise.BRANDS_EN.values()} | {"zeiss", "carl"}
    if toks and toks[0].lower() in known:
        return "Zeiss" if toks[0].lower() in ("carl", "zeiss") else toks[0]
    t = unicodedata.normalize("NFKC", title or "").lower()
    for pat, name in BRAND_ALIASES:
        if re.search(pat, t):
            return name
    m = re.search(r"[A-Za-z][A-Za-z\-]+", unicodedata.normalize("NFKC", title or ""))
    if m:
        return m.group(0).capitalize()
    return (title or "").split()[0] if (title or "").strip() else "（品目名なし）"


def kind_of(title: str) -> str:
    t = unicodedata.normalize("NFKC", title)
    flags = appraise.detect_flags(t)
    if any(f.startswith("付属品") for f in flags):
        return "付属品"
    if re.search(r"\d\s*mm|レンズ|\blens\b", t, re.I) and not re.search(r"ボディ|\bbody\b|カメラ|\bcamera\b", t, re.I):
        return "レンズ"
    if re.search(r"\d\s*mm|レンズ|\blens\b", t, re.I):
        return "カメラ＋レンズ"
    return "カメラ（ボディ）"


def price_band(usd: float) -> str:
    for hi, label in [(100, "〜$99"), (200, "$100〜"), (300, "$200〜"), (500, "$300〜"), (1000, "$500〜")]:
        if usd < hi:
            return label
    return "$1000〜"


PRICE_BANDS = ["〜$99", "$100〜", "$200〜", "$300〜", "$500〜", "$1000〜"]
MARGIN_BINS = [(-1e9, 0, "赤字"), (0, 5, "0〜5%"), (5, 10, "5〜10%"), (10, 15, "10〜15%"),
               (15, 20, "15〜20%"), (20, 30, "20〜30%"), (30, 1e9, "30%以上")]
DAY_BINS = [(0, 7, "〜7日"), (8, 14, "〜14日"), (15, 30, "〜30日"), (31, 60, "〜60日"),
            (61, 90, "〜90日"), (91, 180, "〜180日"), (181, 10**6, "181日〜")]


def bin_label(v: float, bins) -> str:
    for lo, hi, label in bins:
        if lo <= v <= hi if isinstance(lo, int) else lo <= v < hi:
            return label
    return bins[-1][2]


# ---------------------------------------------------------------------------
# 利益（appraise.calculate と同じ手数料率。送料は明細があれば実額、無ければ config の一律差し引き）
# ---------------------------------------------------------------------------

def listed_deduction(cfg: dict) -> float:
    """出品中・販売済みの商品の送料の見積もり（USD）. 入札判断の deduction_usd とは別（出品に乗せていた送料の頃の値）."""
    s = cfg["shipping"]
    return float(s.get("listed_deduction_usd", s.get("deduction_usd", 30)))


def profit_parts(price_usd: float, cost_jpy: float | None, cfg: dict, fx: float, actual: dict | None,
                 net_usd: float | None = None) -> dict:
    """1件の利益の内訳（円）. net_usd（eBay の実際の手取り）があれば手数料・広告費・返金はその実額、無ければ config の率で推定."""
    e = cfg["ebay"]
    fee_pct = e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]
    fees_usd = price_usd * fee_pct / 100 + e["per_order_fee_usd"] if net_usd is None else price_usd - net_usd
    loss = (price_usd - fees_usd) * fx * cfg["fx"]["conversion_loss_pct"] / 100
    ship_actual = actual and actual.get("ship")
    ship = ship_actual if ship_actual else listed_deduction(cfg) * fx
    duty = (actual or {}).get("duty", 0.0)
    aucrevo = (actual or {}).get("aucrevo", 0.0)
    sale = price_usd * fx
    cost = cost_jpy or 0.0
    profit = sale - fees_usd * fx - loss - ship - duty - aucrevo - cost
    return {"sale": sale, "fees": fees_usd * fx, "loss": loss, "ship": ship, "duty": duty, "aucrevo": aucrevo,
            "cost": cost, "profit": profit, "ship_is_actual": bool(ship_actual), "has_cost": cost_jpy is not None,
            "fee_is_actual": net_usd is not None}


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def _months(first: date, last: date) -> list[str]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _group(rows: list[dict], key, top: int | None = None) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    out = [{"label": k, "count": len(v), "profit": round(sum(x["p"]["profit"] for x in v)),
            "sales": round(sum(x["p"]["sale"] for x in v)),
            "avg_profit": round(sum(x["p"]["profit"] for x in v) / len(v)),
            "margin": round(sum(x["p"]["profit"] for x in v) / max(sum(x["p"]["sale"] for x in v), 1) * 100, 1)}
           for k, v in g.items()]
    out.sort(key=lambda d: (-d["count"], -d["profit"]))
    if top and len(out) > top:
        rest = out[top:]
        n, p, s = sum(d["count"] for d in rest), sum(d["profit"] for d in rest), sum(d["sales"] for d in rest)
        out = out[:top] + [{"label": f"その他（{len(rest)}）", "count": n, "profit": p, "sales": s,
                            "avg_profit": round(p / n), "margin": round(p / max(s, 1) * 100, 1)}]
    return out


def build(ledger: list[dict], cols: dict, costs: dict, listings: list[dict], listings_date: str,
          targets: dict, cfg: dict, fx: float, today: date, kanri: dict | None = None,
          ebay: dict | None = None, item_types: dict | None = None, junk: list | None = None,
          monthly_costs: dict | None = None, api: dict | None = None) -> dict:
    j = cfg["judge"]
    ebay = ebay or {}
    per = ebay.get("per", {})
    if api and api.get("sales"):  # eBay API の注文で販売を数える
        sold = api["sales"]
        sold_keys = {norm_sku(r["sku"]) for r in sold if r["sku"]}
        stock = [r for r in ledger if not r["sold"] and norm_sku(r["sku"]) not in sold_keys]
        source = "api"
    else:
        sold = [r for r in ledger if r["sold"]]
        stock = [r for r in ledger if not r["sold"]]
        source = "ledger"
    for r in sold:
        key = norm_sku(r["sku"]) if r["sku"] else ""
        actual = costs.get(key) if key else None
        r["matched"] = actual is not None
        if source == "api":
            e, net = r.get("api"), r.get("net_usd")
        else:
            e = per.get(key) if key else None
            net = e["net"] if e and e["orders"] else None
        r["p"] = profit_parts(r["price_usd"], r["cost_jpy"], cfg, fx, actual, net)
        r["p_est"] = profit_parts(r["price_usd"], r["cost_jpy"], cfg, fx, actual) if net is not None else r["p"]
        r["ebay"] = e if net is not None else None
        r["margin"] = r["p"]["profit"] / r["p"]["sale"] * 100 if r["p"]["sale"] else 0.0
        r["country_code"] = (r["country"] or (actual or {}).get("country") or "").strip()
    d: dict = {"generated": datetime.now().strftime("%Y/%m/%d %H:%M"), "fx": fx,
               "min_profit": j["min_profit_jpy"], "min_margin": j["min_margin_pct"],
               "columns": [{"key": k, "label": COLUMN_LABEL[k], "found": v} for k, v in cols.items()],
               "has_ledger": bool(ledger), "has_costs": bool(costs), "source": source,
               "source_name": (api or {}).get("name", "eBay API の注文"),
               "api_date": (api or {}).get("date", ""), "fin_date": (api or {}).get("fin_date", ""),
               "cancelled": (api or {}).get("cancelled") or [], "non_usd": (api or {}).get("non_usd", 0),
               "unfixed": (api or {}).get("unfixed") or [], "sku_trusted_from": SKU_TRUSTED_FROM.strftime("%Y/%m/%d"),
               "deduction_usd": listed_deduction(cfg)}

    # 概要
    tp = sum(r["p"]["profit"] for r in sold)
    ts = sum(r["p"]["sale"] for r in sold)
    d["kpi"] = {"count": len(sold), "sales": round(ts), "profit": round(tp),
                "margin": round(tp / ts * 100, 1) if ts else None,
                "no_cost": sum(1 for r in sold if not r["p"]["has_cost"]),
                "ship_actual": sum(1 for r in sold if r["p"]["ship_is_actual"]),
                "matched": sum(1 for r in sold if r["matched"]), "costs_total": len(costs),
                "fee_actual": sum(1 for r in sold if r["p"]["fee_is_actual"]),
                "stock": len(stock), "stock_cost": round(sum(r["cost_jpy"] or 0 for r in stock)),
                "first": min((r["sold"] for r in sold), default=None), "last": max((r["sold"] for r in sold), default=None)}
    for k in ("first", "last"):
        d["kpi"][k] = d["kpi"][k].strftime("%Y/%m/%d") if d["kpi"][k] else ""

    # 月別
    if sold:
        months = _months(min(r["sold"] for r in sold), max(max(r["sold"] for r in sold), today))
        bym = {m: {"month": m, "count": 0, "sales": 0.0, "profit": 0.0} for m in months}
        for r in sold:
            b = bym[f"{r['sold'].year}-{r['sold'].month:02d}"]
            b["count"] += 1
            b["sales"] += r["p"]["sale"]
            b["profit"] += r["p"]["profit"]
        d["monthly"] = [{**b, "sales": round(b["sales"]), "profit": round(b["profit"]),
                         "margin": round(b["profit"] / b["sales"] * 100, 1) if b["sales"] else None}
                        for b in bym.values()]
    else:
        d["monthly"] = []

    # 今月の累計（日ごと）: 今月・先月・目標ペース
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    ndays = calendar.monthrange(today.year, today.month)[1]

    def cum(y: int, m: int, upto: int, field: str) -> list[float | None]:
        daily = [0.0] * 32
        for r in sold:
            if r["sold"].year == y and r["sold"].month == m:
                daily[r["sold"].day] += r["p"][field]
        out, acc = [], 0.0
        for day in range(1, ndays + 1):
            acc += daily[day] if day < 32 else 0
            out.append(round(acc) if day <= upto else None)
        return out

    lm_days = calendar.monthrange(last_month_end.year, last_month_end.month)[1]
    d["cumulative"] = {
        "month": f"{today.year}/{today.month}", "days": ndays, "today": today.day,
        "sales": cum(today.year, today.month, today.day, "sale"),
        "profit": cum(today.year, today.month, today.day, "profit"),
        "last_sales": cum(last_month_end.year, last_month_end.month, lm_days, "sale"),
        "last_profit": cum(last_month_end.year, last_month_end.month, lm_days, "profit"),
        "target_sales": targets.get("sales_jpy"), "target_profit": targets.get("profit_jpy"),
    }

    # 分類別
    d["brand"] = _group(sold, lambda r: brand_of(r["title"]), top=10)
    item_types = item_types or {}
    d["kind"] = _group(sold, lambda r: item_types.get(norm_sku(r["sku"])) or kind_of(r["title"]))
    d["kind_from_sheet"] = sum(1 for r in sold if item_types.get(norm_sku(r["sku"])))
    d["supplier"] = _group(sold, lambda r: r["supplier"] or "（空欄）", top=10) if cols.get("supplier") else None
    d["country"] = (_group(sold, lambda r: COUNTRY_JA.get(r["country_code"], r["country_code"] or "（不明）"), top=8)
                    if cols.get("country") or costs or source == "api" else None)
    d["weekday"] = [{"label": w, "count": sum(1 for r in sold if r["sold"].weekday() == i)}
                    for i, w in enumerate(WEEKDAYS)]
    pb = {b["label"]: b for b in _group(sold, lambda r: price_band(r["price_usd"]))}
    d["price_band"] = [pb.get(b, {"label": b, "count": 0, "profit": 0, "sales": 0, "avg_profit": 0, "margin": None})
                       for b in PRICE_BANDS]

    # 利益率の分布（赤字・基準未満・基準以上）
    def mclass(r) -> str:
        if r["p"]["profit"] < 0:
            return "loss"
        return "ok" if r["p"]["profit"] >= j["min_profit_jpy"] and r["margin"] >= j["min_margin_pct"] else "low"
    c = Counter(mclass(r) for r in sold)
    d["margin_class"] = {"loss": c["loss"], "low": c["low"], "ok": c["ok"]}
    mb = Counter(bin_label(r["margin"], MARGIN_BINS) for r in sold)
    d["margin_bins"] = [{"label": lab, "count": mb[lab]} for _, _, lab in MARGIN_BINS]

    # 機種ランキング
    models = _group(sold, lambda r: appraise.model_key(r["title"])[0] or r["title"][:30])
    d["top_profit"] = sorted(models, key=lambda m: -m["profit"])[:10]
    d["top_count"] = sorted(models, key=lambda m: (-m["count"], -m["profit"]))[:10]
    d["worst"] = [{"title": r["title"], "sold": r["sold"].strftime("%Y/%m/%d"), "price_usd": r["price_usd"],
                   "profit": round(r["p"]["profit"]), "margin": round(r["margin"], 1)}
                  for r in sorted(sold, key=lambda r: r["p"]["profit"])[:10] if r["p"]["profit"] < j["min_profit_jpy"]]

    # 販売までの日数
    if cols.get("buy_date"):
        days = [(r["sold"] - r["buy"]).days for r in sold if r["buy"] and r["sold"] >= r["buy"]]
        dc = Counter(bin_label(x, DAY_BINS) for x in days)
        d["days_to_sell"] = {"n": len(days), "median": sorted(days)[len(days) // 2] if days else None,
                             "bins": [{"label": lab, "count": dc[lab]} for _, _, lab in DAY_BINS]}
        sd = [(today - r["buy"]).days for r in stock if r["buy"]]
        sc = Counter(bin_label(x, DAY_BINS) for x in sd)
        d["stock_days"] = {"n": len(sd), "bins": [{"label": lab, "count": sc[lab]} for _, _, lab in DAY_BINS]}
    else:
        d["days_to_sell"] = d["stock_days"] = None

    # 費用の内訳（売上の行き先）
    keys = [("cost", "仕入"), ("fees", "eBay手数料・広告・返金"), ("ship", "国際送料"), ("duty", "関税等"),
            ("aucrevo", "発送代行"), ("loss", "円転ロス"), ("profit", "利益")]
    d["breakdown"] = [{"key": k, "label": lab, "value": round(sum(r["p"][k] for r in sold))} for k, lab in keys]

    # 売れた商品の状態（管理シートの状態欄。台帳の No. で結び付け、利益も出す）
    kanri = kanri or {}
    d["has_kanri"] = bool(kanri)
    if kanri:
        for r in sold:
            k = kanri.get(norm_sku(r["sku"])) if r["sku"] else None
            r["cond"] = k["cond"] if k else None
            r["optical"] = k["optical"] if k else None
        joined = [r for r in sold if r["cond"]]
        by = {g["label"]: g for g in _group(joined, lambda r: r["cond"])}
        d["sold_conditions"] = [by[k] for k in COND_ORDER if k in by]
        d["sold_cond_matched"] = len(joined)
        stock_c = Counter(k["cond"] for k in kanri.values() if not k["sold"])
        d["stock_conditions"] = [{"label": k, "count": stock_c[k]} for k in COND_ORDER if stock_c.get(k)]
        opt = [r for r in joined if r["optical"]]
        clean = [r for r in joined if not r["optical"]]
        avg = lambda rs: round(sum(x["p"]["profit"] for x in rs) / len(rs)) if rs else None  # noqa: E731
        mg = lambda rs: round(sum(x["p"]["profit"] for x in rs) / max(sum(x["p"]["sale"] for x in rs), 1) * 100, 1) if rs else None  # noqa: E731
        d["optical"] = {"n": len(opt), "avg": avg(opt), "margin": mg(opt), "clean_n": len(clean),
                        "clean_avg": avg(clean), "clean_margin": mg(clean)}
    else:
        d["sold_conditions"] = d["stock_conditions"] = d["optical"] = None

    # 実際の手取り（ebay売上）と推定の比較・返金・注文に結び付かない費用
    act = [r for r in sold if r["ebay"]]
    d["has_ebay"] = bool(per) or bool(act)
    if act:
        gross = sum(r["ebay"]["gross"] for r in act)
        net = sum(r["ebay"]["net"] - r["ebay"]["refund"] for r in act)  # 手数料率は返金を除いて見る
        e = cfg["ebay"]
        d["actual"] = {
            "n": len(act), "since": ebay["first"].strftime("%Y/%m/%d") if ebay.get("first") else "",
            "source": ((api or {}).get("name", "eBay API") + " の手取り") if source == "api" else "ebay売上シート",
            "profit": round(sum(r["p"]["profit"] for r in act)), "profit_est": round(sum(r["p_est"]["profit"] for r in act)),
            "fee_rate": round((1 - net / gross) * 100, 1) if gross else None,
            "fee_rate_cfg": round(e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"], 2),
            "promo_usd": round(-sum(r["ebay"]["promo"] for r in act), 2),
            "refund_usd": round(-sum(r["ebay"]["refund"] for r in act), 2),
        }
    else:
        d["actual"] = None
    fin = (api or {}).get("fin") or {}
    if fin.get("per"):
        ebay = {**ebay, "refunds": [{"date": x["date"], "label": x["order_id"], "usd": x["usd"]} for x in fin["refunds"]],
                "unassigned": fin["unassigned"]}
    if per or fin.get("per"):
        rf = defaultdict(lambda: {"count": 0, "usd": 0.0})
        for x in ebay.get("refunds", []):
            if x["date"] and x["usd"] < 0:
                m = x["date"][:7].replace("/", "-")
                rf[m]["count"] += 1
                rf[m]["usd"] += -x["usd"]
        d["refunds"] = [{"month": m, "count": v["count"], "jpy": round(v["usd"] * fx)} for m, v in sorted(rf.items())]
        d["refund_list"] = [x for x in ebay.get("refunds", []) if x["usd"] < 0]
        d["unassigned"] = [{"label": k, "jpy": round(v * fx)} for k, v in ebay.get("unassigned", {}).items() if round(v * fx)]
    else:
        d["refunds"] = d["refund_list"] = d["unassigned"] = None

    # eBay で売れたのに古物台帳に販売の記録が無い No.（台帳の記入漏れの確認用）
    ledger_keys = {norm_sku(r["sku"]) for r in ledger if r["sku"]}
    if source == "api":
        d["ebay_not_in_ledger"] = sorted(
            ({"label": r["sku"] or "（SKU なし）", "usd": round(r["price_usd"], 2), "in_ledger": r["in_ledger"]}
             for r in sold if not r["in_ledger_sold"] and not r.get("old_unfixed")), key=lambda x: x["label"])
    else:
        ledger_sold = {norm_sku(r["sku"]) for r in sold if r["sku"]}
        d["ebay_not_in_ledger"] = sorted(
            ({"label": k, "usd": round(v["gross"], 2), "in_ledger": k in ledger_keys}
             for k, v in per.items() if v["orders"] and v["net"] > 0 and k not in ledger_sold),
            key=lambda x: x["label"])

    # 送料・関税・発送代行: 月ごとの推定と実額（data/monthly_costs.csv があれば）
    if monthly_costs:
        est = defaultdict(float)
        for r in sold:
            est[f"{r['sold'].year}-{r['sold'].month:02d}"] += r["p"]["ship"] + r["p"]["duty"] + r["p"]["aucrevo"]
        prof = {m["month"]: m["profit"] for m in d["monthly"]}
        d["ship_check"] = [{"month": m, "est": round(est[m]), "actual": round(a), "profit": prof.get(m, 0),
                            "adjusted": round(prof.get(m, 0) + est[m] - a)}
                           for m, a in sorted(monthly_costs.items()) if a or est[m]]
    else:
        d["ship_check"] = None

    # ジャンク（ヤフオク再販。J管理シート）
    if junk:
        js = [j for j in junk if j["sold"] and j["sale"] is not None]
        bym = defaultdict(lambda: {"count": 0, "sale": 0.0, "diff": 0.0})
        for j in js:
            b = bym[f"{j['sold'].year}-{j['sold'].month:02d}"]
            b["count"] += 1
            b["sale"] += j["sale"]
            b["diff"] += j["diff"] if j["diff"] is not None else j["sale"] - j["cost"]
        d["junk"] = {"sold": len(js), "sale": round(sum(j["sale"] for j in js)),
                     "diff": round(sum(j["diff"] if j["diff"] is not None else j["sale"] - j["cost"] for j in js)),
                     "stock": sum(1 for j in junk if not j["sold"]), "stock_cost": round(sum(j["cost"] for j in junk if not j["sold"])),
                     "monthly": [{"month": m, "count": v["count"], "sale": round(v["sale"]), "diff": round(v["diff"])}
                                 for m, v in sorted(bym.items())]}
    else:
        d["junk"] = None

    # 出品中（eBay）
    d["listings_date"] = listings_date
    if listings:
        uniq: dict[str, dict] = {}
        for r in listings:  # eBay Mag で UK・豪州に複製した出品は1件として数える
            uniq.setdefault((r.get("sku") or "").strip() or r.get("title", ""), r)
        cg: dict[str, list[dict]] = defaultdict(list)
        for r in uniq.values():
            cg[condition_group(r.get("title", ""))].append(r)
        d["conditions"] = [{"label": k, "count": len(cg[k]),
                            "avg_watch": round(sum(_num(x.get("watchers")) or 0 for x in cg[k]) / len(cg[k]), 1),
                            "avg_days": round(sum(_num(x.get("days_listed")) or 0 for x in cg[k]) / len(cg[k]))}
                           for k in COND_ORDER if cg.get(k)]
        d["listing_points"] = [{"days": _num(r.get("days_listed")) or 0, "watch": _num(r.get("watchers")) or 0,
                                "title": r.get("title", ""), "price": r.get("price", ""), "cur": r.get("currency", ""),
                                "url": r.get("ebay_url", "")} for r in uniq.values()]
        d["sites"] = [{"label": k or "不明", "count": v} for k, v in Counter(r.get("site", "") for r in listings).most_common()]
        d["listings_total"] = len(listings)
        d["listings_unique"] = len(uniq)
    else:
        d["conditions"] = d["listing_points"] = d["sites"] = None
    return d


def render(data: dict, template: Path = TEMPLATE) -> str:
    html = template.read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return html.replace("/*__DATA__*/null", blob)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="事業の数字をグラフにする → reports/dashboard.html")
    ap.add_argument("--ledger", type=Path, default=DATA / "ledger.csv")
    ap.add_argument("--costs", type=Path, default=DATA / "ledger_costs.csv")
    ap.add_argument("--reports", type=Path, default=REPORTS)
    ap.add_argument("--targets", type=Path, default=DATA / "targets.toml")
    ap.add_argument("--kanri", type=Path, default=DATA / "kanri.csv")
    ap.add_argument("--ebay-sales", type=Path, default=DATA / "ebay_report.csv")
    ap.add_argument("--item-types", type=Path, default=DATA / "item_types.csv")
    ap.add_argument("--junk", type=Path, default=DATA / "junk.csv")
    ap.add_argument("--monthly-costs", type=Path, default=DATA / "monthly_costs.csv")
    ap.add_argument("--sku-fix", type=Path, default=DATA / "sku_fix.csv")
    ap.add_argument("--sku-candidates", action="store_true", help="昔の注文の正しい No. の候補 → reports/sku_fix_candidates.csv")
    ap.add_argument("-o", "--out", type=Path, default=REPORTS / "dashboard.html")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    cfg = appraise.load_config(None)
    fx = float(cfg["fx"]["usd_jpy"])
    if cfg["fx"].get("auto_fetch"):  # 鑑定と同じく、取れれば今日の為替で円にする
        try:
            fx = appraise.fetch_fx() or fx
        except Exception:
            pass
    ledger, cols = read_ledger(args.ledger)
    listings, ldate = latest_listings(args.reports)
    orders, odate = _latest(args.reports, "ebay_orders")
    fin_rows, fdate = _latest(args.reports, "ebay_finances")
    kanri = read_kanri(args.kanri)
    fix = read_sku_fix(args.sku_fix)
    if args.sku_candidates:
        if not orders:
            print("eBay API の注文がありません。先に python3 tools/ebay_api.py orders --days 730 を実行してください。")
            return 1
        rows = sku_candidates(orders, ledger, kanri, fix)
        out = args.reports / "sku_fix_candidates.csv"
        args.reports.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["order_id"])
            w.writeheader()
            w.writerows(rows)
        print(f"{SKU_TRUSTED_FROM:%Y/%m/%d} より前で対応表に無い注文 {len(rows)}件の候補 → {out}")
        print("確認できたら data/sku_fix.csv（列: order_id,正しいNo.,メモ）に書く。Claude に「候補を確認したい」と言えば一緒に作ります")
        return 0
    api = None
    report_rows = _csv_rows(args.ebay_sales)
    if orders:
        fin = read_api_finances(fin_rows) if fin_rows else {}
        api = {**sales_from_api(orders, fin, ledger, kanri, fx, fix), "date": odate, "fin_date": fdate, "fin": fin,
               "name": "eBay API の注文"}
    elif report_rows:  # eBay API が使えないとき: ebay売上シートの注文で数える
        r_orders, fin = report_to_api(report_rows)
        if r_orders:
            last = max(o["date"] for o in r_orders).replace("-", "/")
            api = {**sales_from_api(r_orders, fin, ledger, kanri, fx, fix), "date": f"〜{last}", "fin_date": f"〜{last}",
                   "fin": fin, "name": "ebay売上シート（eBay の取引レポート）の注文"}
    data = build(ledger, cols, read_costs(args.costs), listings, ldate, read_targets(args.targets), cfg, fx, date.today(),
                 kanri, read_ebay_sales(args.ebay_sales), read_item_types(args.item_types),
                 read_junk(args.junk), read_monthly_costs(args.monthly_costs), api)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data), encoding="utf-8")
    # 同じ中身を JSON でも置く（ledger_sync.upload_dashboard が台帳へ送り、Artifacts のグラフが開くたびに読む）
    (args.out.parent / "dashboard_data.json").write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    missing = [COLUMN_LABEL[k] for k, v in cols.items() if not v and not (k == "country" and (data["has_costs"] or data["source"] == "api"))]
    print(f"ダッシュボード: 販売 {data['kpi']['count']}件・在庫 {data['kpi']['stock']}件・出品中 "
          f"{data.get('listings_unique') or 0}件 → {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    if ledger and missing:
        print(f"  古物台帳に見つからない列: {'・'.join(missing)}（そのグラフは出ません。列名を Claude に伝えると対応します）")
    if not data["has_kanri"] or not data["has_ebay"]:
        print("  管理・ebay売上・J管理・出品情報シートが未取り込みです（python3 tools/ledger_sync.py script でスクリプトを貼り替え）")
    print(f"  販売の数え方: {api['name'] + '（' + api['date'] + '）' if api else '古物台帳（eBay の注文データが無いため）'}")
    if api and api["unfixed"]:
        print(f"  {SKU_TRUSTED_FROM:%Y/%m/%d} より前で No. が分からない注文 {len(api['unfixed'])}件（仕入額不明）"
              " → python3 tools/build_dashboard.py --sku-candidates で候補を出して data/sku_fix.csv に")
    if data["actual"]:
        print(f"  実際の手取りで計算: {data['actual']['n']} / {data['kpi']['count']}件（{data['actual']['source']}）")
    if not listings:
        print("  出品中の状態グラフ: eBay の出品一覧がありません（python3 tools/ebay_api.py listings で作れます）")
    if sys.platform == "darwin" and not args.no_open:
        subprocess.call(["open", str(args.out)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
