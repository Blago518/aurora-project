#!/usr/bin/env python3
"""事業の数字をグラフで見るページ（reports/dashboard.html）を作る.

入力（どれも無ければ、その部分は「データ無し」と表示するだけ）:
    data/ledger.csv            古物台帳（ledger_sync.py / update_all.py が取り込む）
    data/ledger_costs.csv      管理番号ごとの国際送料・関税・発送代行費・販売国（build_ledger_costs.py）
    reports/listings_*.csv     eBay の出品中一覧（python3 tools/ebay_api.py listings）の一番新しいもの
    data/targets.toml          目標（任意）。例:
                                   [month]
                                   sales_jpy = 500000
                                   profit_jpy = 100000
    tools/config.toml          手数料・為替（利益は appraise.py と同じ率で計算する）

古物台帳の列は下の COLUMNS の候補名で探す。見つからない列はページの「使った列」に「無し」と出る
（推測した値では埋めない）。列名が違うときは COLUMNS に足す。

使い方（aurora フォルダで）:
    python3 tools/build_dashboard.py           # → reports/dashboard.html（Mac ではブラウザで開く）
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
    "cost_jpy": ["仕入金額"],
    "buy_date": ["購入日", "仕入日", "仕入れ日", "仕入年月日", "購入年月日", "取引年月日", "入荷日"],
    "supplier": ["アカウント名", "アカウント名(仕入元)", "仕入先", "仕入れ先", "購入先"],
    "country": ["販売国", "発送先国", "国"],
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
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
# 状態の並び（良い順）。Exc+2 以下はまとめる
COND_ORDER = ["未使用", "TOP MINT", "MINT", "Near MINT", "Exc+5", "Exc+4", "Exc+3", "Exc+2以下", "ジャンク", "表記なし"]


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
    if m:
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
    headers = [(h or "").replace("\n", "").strip() for h in (reader.fieldnames or [])]
    cols = find_columns(headers)
    rows = []
    for raw in reader:
        r = {(k or "").replace("\n", "").strip(): (v or "").strip() for k, v in raw.items() if not isinstance(v, list)}
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


def brand_of(title: str) -> str:
    toks = appraise.title_to_query(title, 20).split()
    known = {b.lower() for b in appraise.BRANDS_EN.values()} | {"zeiss", "carl"}
    return toks[0] if toks and toks[0].lower() in known else "不明"


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

def profit_parts(price_usd: float, cost_jpy: float | None, cfg: dict, fx: float, actual: dict | None) -> dict:
    e = cfg["ebay"]
    fee_pct = e["final_value_fee_pct"] + e["international_fee_pct"] + e["ad_rate_pct"]
    fees_usd = price_usd * fee_pct / 100 + e["per_order_fee_usd"]
    loss = (price_usd - fees_usd) * fx * cfg["fx"]["conversion_loss_pct"] / 100
    ship_actual = actual and actual.get("ship")
    ship = ship_actual if ship_actual else cfg["shipping"].get("deduction_usd", 30) * fx
    duty = (actual or {}).get("duty", 0.0)
    aucrevo = (actual or {}).get("aucrevo", 0.0)
    sale = price_usd * fx
    cost = cost_jpy or 0.0
    profit = sale - fees_usd * fx - loss - ship - duty - aucrevo - cost
    return {"sale": sale, "fees": fees_usd * fx, "loss": loss, "ship": ship, "duty": duty, "aucrevo": aucrevo,
            "cost": cost, "profit": profit, "ship_is_actual": bool(ship_actual), "has_cost": cost_jpy is not None}


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
          targets: dict, cfg: dict, fx: float, today: date) -> dict:
    j = cfg["judge"]
    sold = [r for r in ledger if r["sold"]]
    stock = [r for r in ledger if not r["sold"]]
    for r in sold:
        actual = costs.get(norm_sku(r["sku"])) if r["sku"] else None
        r["matched"] = actual is not None
        r["p"] = profit_parts(r["price_usd"], r["cost_jpy"], cfg, fx, actual)
        r["margin"] = r["p"]["profit"] / r["p"]["sale"] * 100 if r["p"]["sale"] else 0.0
        r["country_code"] = (r["country"] or (actual or {}).get("country") or "").strip()
    d: dict = {"generated": datetime.now().strftime("%Y/%m/%d %H:%M"), "fx": fx,
               "min_profit": j["min_profit_jpy"], "min_margin": j["min_margin_pct"],
               "columns": [{"key": k, "label": COLUMN_LABEL[k], "found": v} for k, v in cols.items()],
               "has_ledger": bool(ledger), "has_costs": bool(costs),
               "deduction_usd": cfg["shipping"].get("deduction_usd", 30)}

    # 概要
    tp = sum(r["p"]["profit"] for r in sold)
    ts = sum(r["p"]["sale"] for r in sold)
    d["kpi"] = {"count": len(sold), "sales": round(ts), "profit": round(tp),
                "margin": round(tp / ts * 100, 1) if ts else None,
                "no_cost": sum(1 for r in sold if not r["p"]["has_cost"]),
                "ship_actual": sum(1 for r in sold if r["p"]["ship_is_actual"]),
                "matched": sum(1 for r in sold if r["matched"]), "costs_total": len(costs),
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
    d["kind"] = _group(sold, lambda r: kind_of(r["title"]))
    d["supplier"] = _group(sold, lambda r: r["supplier"] or "（空欄）", top=10) if cols.get("supplier") else None
    d["country"] = (_group(sold, lambda r: COUNTRY_JA.get(r["country_code"], r["country_code"] or "（不明）"), top=8)
                    if cols.get("country") or costs else None)
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
    keys = [("cost", "仕入"), ("fees", "eBay手数料"), ("ship", "国際送料"), ("duty", "関税等"),
            ("aucrevo", "発送代行"), ("loss", "円転ロス"), ("profit", "利益")]
    d["breakdown"] = [{"key": k, "label": lab, "value": round(sum(r["p"][k] for r in sold))} for k, lab in keys]

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
    ap.add_argument("-o", "--out", type=Path, default=REPORTS / "dashboard.html")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    cfg = appraise.load_config(None)
    fx = float(cfg["fx"]["usd_jpy"])
    ledger, cols = read_ledger(args.ledger)
    listings, ldate = latest_listings(args.reports)
    data = build(ledger, cols, read_costs(args.costs), listings, ldate, read_targets(args.targets), cfg, fx, date.today())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data), encoding="utf-8")
    missing = [COLUMN_LABEL[k] for k, v in cols.items() if not v and not (k == "country" and data["has_costs"])]
    print(f"ダッシュボード: 販売 {data['kpi']['count']}件・在庫 {data['kpi']['stock']}件・出品中 "
          f"{data.get('listings_unique') or 0}件 → {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    if ledger and missing:
        print(f"  古物台帳に見つからない列: {'・'.join(missing)}（そのグラフは出ません。列名を Claude に伝えると対応します）")
    if not listings:
        print("  出品中の状態グラフ: eBay の出品一覧がありません（python3 tools/ebay_api.py listings で作れます）")
    if sys.platform == "darwin" and not args.no_open:
        subprocess.call(["open", str(args.out)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
