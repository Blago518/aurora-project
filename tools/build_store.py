#!/usr/bin/env python3
"""店舗仕入れアプリ（store/）のデータを作る.

- store/core.js      … 拡張機能（extension_template.js）の照合・利益計算をそのままコピー（公開してよい。データは入らない）
- data/store_data.json … 相場データ＋おすすめ機種（非公開。iPhone に1回読み込ませる）

iCloud Drive がある Mac では、store_data.json を iCloud Drive の「aurora」フォルダにもコピーする
（iPhone の「ファイル」アプリ → iCloud Drive → aurora から選べる）。

使い方:
    python3 tools/build_store.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import appraise as ba
import build_extension as bx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORE_JS = ROOT / "store" / "core.js"
ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "aurora"
CORE_START, CORE_END = "  // @core-start", "  // @core-end"
REC_SOURCES = ("reference_prices.csv", "chatwork_sales.csv")  # おすすめは販売履歴（自分・Chatwork・頒布会。相場メモは除く）


# ---------------------------------------------------------------------------
# core.js（拡張機能と同じ判定ロジック）
# ---------------------------------------------------------------------------


def core_js() -> str:
    tpl = (HERE / "extension_template.js").read_text(encoding="utf-8")
    a, b = tpl.index(CORE_START), tpl.index(CORE_END)
    body = tpl[tpl.index("\n", a) + 1:b]
    names = ("titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, "
             "modelKey, ebay, median, roundDown, matchPreferMemo, isMemo, conditionOf, conditionAdjusted, conditionNote, "
             "setPartTitles, setValue, KIT_RE, sweetSpot, siblingExcludes, modelQuery, cleanQuery, ebaySearchQuery, queryForTitle")
    return (
        "// 自動生成ファイル: tools/build_store.py が tools/extension_template.js の @core 部分をコピーしたもの。\n"
        "// 直接編集しない（拡張機能と判定を一致させるため）。データは入っていない。\n"
        "function makeCore(DATA) {\n"
        '  "use strict";\n'
        "  const C = DATA.consts;\n"
        f"{body}"
        f"  return {{ {names} }};\n"
        "}\n"
        'if (typeof module !== "undefined" && module.exports) module.exports = { makeCore };\n'
    )


# ---------------------------------------------------------------------------
# おすすめ機種（販売実績を機種ごとに集計）
# ---------------------------------------------------------------------------

BRAND_NAMES = {b.lower(): b for b in ba.BRANDS_EN.values()}
# おすすめのメーカー（2026-11-20 ユーザー指摘「カールツァイスがない」）: Carl・Zeiss は「Carl Zeiss」に。
# 判定（appraise.py）には使わない、おすすめの並べ方だけの読み替え
BRAND_NAMES.update({"carl": "Carl Zeiss", "zeiss": "Carl Zeiss", "leitz": "Leica", "asahi": "Pentax", "zenza": "Bronica",
                    "fujica": "Fujifilm", "nippon": "Nikon", "topcon": "Topcon", "kowa": "Kowa", "schneider": "Schneider",
                    "angenieux": "Angenieux", "meyer": "Meyer-Optik", "pentacon": "Pentacon", "linhof": "Linhof",
                    "plaubel": "Plaubel", "graflex": "Graflex", "petri": "Petri", "miranda": "Miranda", "cosina": "Cosina",
                    "horseman": "Horseman", "polaroid": "Polaroid", "konica": "Konica", "kodak": "Kodak", "panasonic": "Panasonic"})
BRAND_SKIP = {"carl", "zeiss", "asahi", "zenza", "nippon", "kogaku", "optik", "ikon"}  # メーカー名の続き（機種名には入れない）


CAMERA_WORDS = {"slr", "camera", "body", "rangefinder", "tlr", "film", "compact", "point"}
# レンズの機種を分ける語（マウント・世代）。例: Canon FD 50mm F1.4 と Canon EF 50mm F1.4 は別機種
LENS_LINE_WORDS = {"fd", "newfd", "new", "ef", "ef-s", "rf", "ai", "ai-s", "nonai", "af", "af-s", "af-d", "smc",
                   "takumar", "m42", "md", "mc", "xf", "fe", "l", "macro", "planar", "sonnar", "summicron",
                   "summilux", "elmarit", "noctilux",
                   # Carl Zeiss の系統（2026-11-20）
                   "distagon", "tessar", "biogon", "makro-planar", "vario-sonnar", "jena", "biotar", "flektogon", "pancolar",
                   "otus", "batis", "loxia", "milvus", "zm", "zf", "ze", "zf.2", "hexanon", "rokkor", "zuiko", "xenar", "symmar"}
CAMERA_LINE_WORDS = ba.GENERATIONS | {"program", "pro", "super", "classic", "dx", "quartz", "date"}


# おすすめの4分類（2026-11-22 オーナー「MF、AF、Film、digital の4つのボタン。MFレンズ、AFレンズ、フィルムカメラ、デジタルカメラ」）。表示だけ
AF_STRONG_RE = re.compile(r"(?<![\d.])\d(?:\.\d)?-\d{1,2}(?:\.\d)?\s?mm\b|\bAF[-\s]?[SPID]?\b|オートフォーカス|autofocus|\bUSM\b|\bSTM\b|\bHSM\b|\bSDM\b|\bSSM\b|\bSAM\b|"
                          r"\bEF-?S?\s?\d|\bRF\s?\d|\bFE\s?\d|\bXF\s?\d|\bXC\s?\d|(?<![A-Za-z])M\.\s?ZUIKO|ZUIKO\s+DIGITAL|NIKKOR\s*Z\b|"
                          r"\bD\s?FA\b|\bDA\s?\*?\s?\d|\bFA\s?\*?\s?\d|Maxxum|Dynax|\bDC\b|\bDG\b|\bVR\b|\bVC\b|\bDi\b|\bLUMIX\s*G\b|"
                          r"Contax\s*(?:G\d?|645)\b|Nikon\s+Z\s?\d|Nikon\s*1\s+Nikkor|AT-X|(?:Planar|Biogon|Sonnar|Hologon)\b.*\b(?:for\s+)?G\s*$|\bHCD?\s?\d|\bSony\b|\bSEL\s?\d", re.I)
MF_RE = re.compile(r"\bMF\b|manual\s*focus|マニュアル|\bnon[-\s]?Ai\b|\bAi[-\s]?S?\b|\bNew\s?FD\b|\bnFD\b|\bFD\b|\bFL\b|Takumar|\bM42\b|"
                   r"\bMD\b|\bMC\b|Rokkor|\bOM\b|G\.?\s?Zuiko|\bL39\b|\bLTM\b|Leica\s*M\b|\bC/?Y\b|\bAE[JG]\b|\bMM[JG]\b|"
                   r"Hasselblad|Mamiya|Bronica|Pentax\s*67|\b6x7\b|\bZ[FE]\.?2?\b|Loxia|Milvus|Voigtl|Helios|Jupiter|Industar", re.I)
DIGITAL_CAM_RE = re.compile(r"digital|dslr|mirrorless|\d+(?:\.\d+)?\s?MP\b|megapixel|万画素|デジタル|デジカメ|ミラーレス|コンデジ|"
                            r"coolpix|powershot|cyber[-\s]?shot|\bDSC-|Nikon\s*1\b|OM\s*SYSTEM\s*OM-\d|\bOM-\d\s*Mark|lumix|finepix|\bIXY\b|\bIXUS\b|exilim|handycam|camcorder|ビデオ|"
                            r"\bD\d{1,4}[A-Z]?\b|\bDf\b|EOS\s?(?:R\d*|M\d*|Kiss\s?(?:X|M)\w*|\d{1,4}D\w*)\b|Nikon\s*Z\s?(?:\d{1,2}(?:\s?I{1,3})?|fc?)\b|"
                            r"X-?(?:T|E|H|S|Pro)\d|X100|\bGFX|\bGR\s?(?:II|III|IV)x?\b|\bGR\s?DIGITAL\b|OM-?D|\bE-?M\d|\bE-?PL?\d|\bE-\d{1,3}\b|"
                            r"ILCE|\bNEX|\bK-(?:1|3|5|7|30|50|70|S\d|r|x)\b|\bKP\b|(?<!Minolta\s)α\s?\d|Sony\s*A7|\bA7[RSC]?\s?(?:I{1,3}|IV|M\d)?\b|RX100|\bGH\d|\bGX\d|(?:powershot|lumix|Canon)\s*G\d{1,2}\b|\bG\d{1,2}\s?X\b|"
                            r"\bP\d{3,4}\b|\bSX\d{2,3}\s?(?:HS|IS)\b|\bSEL\s?\d|\bTG-\d|\bXZ-\d|\bQ\d?\b|Leica\s*(?:M(?:8|9|10|11|-E|-P|240)|M\s?Typ|Q\d?|SL\d?|D-?LUX|V-?LUX|X\d|C-?LUX)\b", re.I)


LENS_LIKE_RE = re.compile(r"(?<![\d.])\d{1,4}(?:\.\d)?(?:-\d{1,4}(?:\.\d)?)?\s?mm\b.*\bF\s?/?\d|(?<![\d.])\d{1,3}(?:\.\d)?-\d{1,3}(?:\.\d)?\s?mm\b|\bSEL\s?\d{3,4}", re.I)  # 6.7-13mm（Nikon 1）のような小数もレンズ（2026-11-22）


# F値を書かない・小数だけのレンズの商品名（「Ai-S Nikkor 50mm 1.2」「Ai 28mm」）もレンズ（2026-11-23 オーナー「フィルムカメラの nikon を選んでもレンズも全て表示される」）。
# 「35mm」だけはフィルムの判（35mm フィルムカメラ）と区別できないので、レンズの系統の名前（Nikkor など）があるときだけ
LENS_MM_RE = re.compile(r"(?<![\d.])(?!35\s?mm)\d{1,4}(?:\.\d)?\s?mm\b", re.I)
LENS_MOUNT_RE = re.compile(r"\b(?:Ai(?:-?S)?|non-?Ai|AF-?[SD]?|New\s?FD|nFD|FD|FL|MD|MC|OM|EF|M42)\b", re.I)   # 35mm に付いたらレンズ（Ai 35mm）


KIT_WORDS_RE = re.compile(r"\bbody\b|ボディ|\bw/|\bwith\b|\+|\bkit\b|付き?|セット|レンズキット", re.I)
# レンズ固定・ボディの名前（後ろにレンズ名があってもカメラ）。「for ◯◯」「◯◯用」はそのボディ用のレンズ
FIXED_BODY_RE = re.compile(r"rolleiflex|rolleicord|\bhexar\b|\bT[234]\b|\bTVS\b|\bGR1\w*|\bX100\w*|\bTC-1\b|\b\d\dTi\b|klasse|natura|"
                           r"\btrip\s*35|\bmju|\bXA\d?\b|\bGA645|\bGW690|\bGSW690|\bGF670|\bbessa|Mamiya\s*[67]\b|minox|"
                           r"Contax\s*(?:G[12]|RTS|aria|139|167|RX|ST|AX|S2)\b|Leica\s*M\d{1,2}\b|Leica\s*(?:III?\w*|II)\b|"
                           r"Hasselblad\s*\d{3}\s?[A-Z]{1,3}\b|\bQL17\b|Electro\s*35|\bSQ-?A\w*\b|\bETR\w*\b|\bC[23]\d0\b|\bM645\b|\b1000S\b|\bCLE\b|\bDf\b|"
                           r"Makina|Pentax\s*67\s*II|Pentax\s*6x7\b|\bRB67\s*Pro|\bRZ67\s*Pro", re.I)
# レンズの系統の名前とレンズメーカー（マウント名の FD・Ai などは含めない）
LENS_FAMILY_RE = re.compile(r"nikkor|zuiko|takumar|rokkor|sekor|elmar|elmarit|summicron|summilux|summaron|noctilux|planar|sonnar|"
                            r"distagon|tessar|biogon|hexanon|zenzanon|xenar|xenotar|symmar|angulon|serenar|nokton|ultron|heliar|skopar|"
                            r"jupiter|helios|industar|biotar|flektogon|pancolar|tamron|sigma|tokina|samyang|rokinon|vivitar|soligor|"
                            r"yongnuo|laowa|7artisans|ttartisan|canon\s+lens", re.I)
BODY_SKIP = r"sel\d+\w*|af(-[spid])?|ef(-s|-m)?|rf|fe|xf|dx|fx|g|ed|vr|ii|iii|iv|if|l39|m39|m42|ltm|c/y|cy|rb67|rz67|sl66|6x[4-9]|645|67"  # 型番に数えない語（マウント・判・レンズの記号）
LENS_MAKER_RE = re.compile(r"\s*(tamron|sigma|tokina|samyang|rokinon|vivitar|soligor|yongnuo|laowa|7artisans|ttartisan|voigtl\w*|zeiss|carl\s+zeiss|schneider|rodenstock|kenko)\b", re.I)
SERIES_BODY_WORDS = {"trip", "electro", "pen", "mju", "xa", "gr", "hexar", "klasse", "natura", "tvs", "contax"}


def body_before_mm(title: str) -> bool:
    """カメラ（ボディ・レンズ付きのボディ・レンズ固定のカメラ）なら True. 2026-11-22 オーナー「フィルムカメラで何故レンズが出る？」.
    1) body・w/・+・kit・付・セット → カメラ  2) レンズ固定・ボディの名前（T2・GR1v・X100V・Rolleiflex・Leica M6 など。「for」「用」が無いとき）→ カメラ
    3) レンズの系統の名前・レンズメーカー（Sekor・Elmar・Nikkor・Tamron など）か「for」「用」→ レンズ  4) 焦点距離より前に英字＋数字の型番 → カメラ."""
    t0 = unicodedata.normalize("NFKC", title)
    if KIT_WORDS_RE.search(t0):
        return True
    has_for = re.search(r"\bfor\b|用", t0, re.I)
    if FIXED_BODY_RE.search(t0) and not has_for:
        return True
    toks = ba.title_to_query(title, 20).split()
    if not has_for and not LENS_MAKER_RE.match(t0):
        for t in toks[1:]:
            tl = t.lower()
            if re.fullmatch(r"\d+(?:\.\d)?(-\d+(?:\.\d)?)?mm", tl) or LENS_FAMILY_RE.search(tl):
                break
            if tl in LENS_LINE_WORDS or re.fullmatch(BODY_SKIP, tl):
                continue
            if re.search(r"[a-zα]", tl) and re.search(r"\d", tl):
                return True   # FM2・OM-2N・D750 などがレンズの系統の語より前にある
    if LENS_FAMILY_RE.search(t0) or has_for:
        return False
    for t in toks[1:]:
        tl = t.lower()
        if re.fullmatch(r"\d+(?:\.\d)?(-\d+(?:\.\d)?)?mm", tl):
            return False
        if tl in LENS_LINE_WORDS or re.fullmatch(BODY_SKIP, tl):
            continue
        if re.search(r"[a-zα]", tl) and re.search(r"\d", tl):
            return True   # FM2・AE-1・D750・F3・EOS 5D など
        if tl in SERIES_BODY_WORDS:
            return True
    return False


def lens_like(title: str) -> bool:
    """焦点距離があるレンズの名前か（ボディの型番が前にある物は body_before_mm で別に見る）."""
    t = unicodedata.normalize("NFKC", title)
    return bool(LENS_LIKE_RE.search(t) or LENS_MM_RE.search(t) or (re.search(r"(?<![\d.])35\s?mm\b", t, re.I) and (LENS_FAMILY_RE.search(t) or LENS_MOUNT_RE.search(t))))


# eBay の検索語は機種の語だけ（2026-11-22 オーナー「まだ sold や now 開くと管理番号が入ってる」「PC のヤフオク入札でも」）。
# 店舗アプリの modelWords と同じ決まりを、拡張機能・毎朝のレポートでも使う（拡張機能は core の modelQuery。test_appraise.ModelQuery で一致を確かめる）
SERIES_WORDS_Q = re.compile(r"coolpix|powershot|ixy|eos|kiss|lumix|finepix|cyber-?shot|pen|om-d|stylus|mju|trip|rolleiflex|rolleicord|leica|hexar|electro|program|z", re.I)
GEN_WORDS_Q = re.compile(r"ii|iii|iv|v|x|mark|mk\d?|n|hp|ttl|program|s|sp|qd|af|md|pro|sd|photomic|as|ftn|ti|hs|\d+(?:\.\d+)?", re.I)
LENS_KEEP_Q = re.compile(r"ai-?s?|non-?ai|af-?[spid]?|ef(?:-s)?|rf|fe|xf|dx|g|l|ii|iii|iv|macro|fisheye|limited|art|pro|gm|is|vr|da|fa|digital")


def model_words(ws: list[str]) -> list[str]:
    """メーカー＋機種の語だけを残す（「Olympus ZUIKO AUTO-W 24mm F2 E66」→「Olympus ZUIKO 24mm F2」、「Nikon F3 N031」→「Nikon F3」）."""
    if not ws:
        return ws
    t = " ".join(ws)
    if LENS_LIKE_RE.search(t) and not body_before_mm(t):
        out = [ws[0]]
        for w in ws[1:]:
            l = w.lower()
            if (re.fullmatch(r"\d+(?:\.\d)?(-\d+(?:\.\d)?)?mm", l) or re.match(r"f/?\d", l) or l in LENS_LINE_WORDS or LENS_FAMILY_RE.search(l)
                    or LENS_KEEP_Q.fullmatch(l)):
                out.append(w)
        return out[:7]  # Nikon AF-S DX 18-55mm F3.5-5.6 G VR の VR まで
    out, model = [ws[0]], False
    for w in ws[1:]:
        l = w.lower()
        if not model and (SERIES_WORDS_Q.fullmatch(l) or l == "new"):
            out.append(w)
            continue
        if not model and ((re.search(r"[a-zα]", l) and re.search(r"\d", l)) or FIXED_BODY_RE.search(w) or re.fullmatch(r"\d{2,4}", l)):
            out.append(w)
            model = True
            continue
        if model and GEN_WORDS_Q.fullmatch(l):
            out.append(w)
            continue
        if model:
            break
    return out if model else ws[:3]


def model_query(title: str) -> str:
    return " ".join(model_words(ba.title_to_query(title, 20).split()))


def model_query_consts() -> dict:
    """拡張機能の core に渡す決まり（Python の正規表現をそのまま JS に）."""
    return {"lensLike": LENS_LIKE_RE.pattern, "kitWords": KIT_WORDS_RE.pattern, "fixedBody": FIXED_BODY_RE.pattern,
            "lensFamily": LENS_FAMILY_RE.pattern, "lensMaker": LENS_MAKER_RE.pattern, "bodySkip": BODY_SKIP,
            "seriesBody": sorted(SERIES_BODY_WORDS), "lensLine": sorted(LENS_LINE_WORDS),
            "series": SERIES_WORDS_Q.pattern, "gen": GEN_WORDS_Q.pattern, "lensKeep": LENS_KEEP_Q.pattern}


def rec_cat(kind: str, titles: list[str]) -> str:
    """おすすめの分類: mf / af（レンズ）、film / digital（カメラ）. 同じ機種の売れた商品名の多数決."""
    if kind != "lens" and titles and all(lens_like(t) and not body_before_mm(t) for t in titles):
        kind = "lens"   # 「カメラ」に入ったレンズ（ボディの型番が無く、焦点距離と F値がある）はレンズとして分ける
    if kind == "lens":
        af = sum(1 for t in titles if (AF_STRONG_RE.search(t) or re.search(r"\bSEL\s?\d", t, re.I)) and not re.search(r"\bMF\b|manual\s*focus", t, re.I))
        mf = sum(1 for t in titles if MF_RE.search(t) and not AF_STRONG_RE.search(t))
        return "af" if af > mf else "mf"
    dg = sum(1 for t in titles if DIGITAL_CAM_RE.search(t))
    return "digital" if dg * 2 > len(titles) else "film"


def rec_key(title: str) -> tuple[str, str, str] | None:
    """おすすめ用の機種まとめ（表示名, 比較キー, camera/lens）. ブランド不明なら None."""
    toks = ba.title_to_query(title, 20).split()
    if not toks or toks[0].lower() not in BRAND_NAMES:
        return None
    brand, rest = BRAND_NAMES[toks[0].lower()], [t for t in toks[1:] if t.lower() not in BRAND_SKIP]
    if any(t.lower() in ("rolleiflex", "rolleicord") for t in toks):   # 「Zeiss Rolleiflex 2.8F Planar」→ メーカーは Rollei（2026-11-22）
        brand, rest = "Rollei", [t for t in toks if t.lower() not in BRAND_SKIP | {"zeiss", "carl"}]
    def norm(t: str) -> list[str]:
        t = t.lower()
        return ["newfd"] if t in ("nfd", "fdn") else ["ai-s"] if t == "ais" else [t]
    low = [x for t in rest for x in norm(t)]
    has_mm = any(re.fullmatch(r"\d+(?:\.\d)?(-\d+(?:\.\d)?)?mm", t) for t in low)
    zoom = any(re.fullmatch(r"\d{1,3}-\d{2,3}mm", t) for t in low)   # ズームの焦点距離は F値が無くてもレンズ（AF-S DX 18-140mm）
    has_f = zoom or any(re.fullmatch(r"f\d+(\.\d+)?(-\d+(\.\d+)?)?[a-z]?", t) for t in low)  # F5.6E・F1.4G・F2.8L も F値（2026-11-22 200-500mm F5.6E がカメラに入っていた）
    kind = "lens" if has_mm and (has_f or "lens" in low or lens_like(title)) and not CAMERA_WORDS & set(low) and not body_before_mm(title) else "camera"

    def wanted(t: str) -> bool:
        if kind == "lens":
            # Nikon Z・Contax G のレンズは系統の語を残す（無いと MF のレンズと見分けられない。2026-11-22）
            return bool(t in LENS_LINE_WORDS or re.fullmatch(r"\d+(?:\.\d)?(-\d+(?:\.\d)?)?mm", t) or re.fullmatch(r"f\d.*", t)
                        or (t == "z" and brand == "Nikon") or (t == "g" and brand == "Contax"))
        return bool((re.search(r"\d", t) and t != "35mm") or t in CAMERA_LINE_WORDS)

    keep, shown = [], []
    for orig in rest:
        ks = [t for t in norm(orig) if wanted(t) and t not in keep]
        if ks:
            keep += ks
            shown.append(orig)
    if not any(re.search(r"\d", t) for t in keep):
        # 数字の無い機種名（Spotmatic・Hexar・Contaflex など）は、カメラだけ、名前の語を2つまで使う（2026-11-20）
        names = [t for t in rest if t.lower() not in CAMERA_WORDS | ba.NOISE_TOKENS and len(t) >= 2 and not re.search(r"\d", t)]
        if kind != "camera" or not names:
            return None
        keep, shown = [t.lower() for t in names[:2]], names[:2]
    if kind == "camera":
        # 表示名にシリーズの語を残す（「Canon 310」→「Canon IXY 310」、「Nikon fc」→「Nikon Z fc」。2026-11-22）。比べるキーは変えない
        series = set(ba.LINE_WORDS) | SERIES_BODY_WORDS | {"z", "d-lux", "v-lux", "c-lux", "trip", "electro", "zoom", "system", "eos"}
        shown = [t for t in rest if t.lower() in series or t in shown or FIXED_BODY_RE.fullmatch(t)]   # SQ-A・CLE・Df も名前に残す
        seen = set()
        shown = [t for t in shown if not (t.lower() in seen or seen.add(t.lower()))]
    label = " ".join([brand] + shown)
    return label, " ".join([brand.lower()] + keep), kind


def store_calc(usd: float, cfg: dict, fx: float, price_jpy: int | None = None) -> dict:
    """店頭仕入れ: 国内送料なし・消費税は値札（税込）にそのまま含まれる."""
    item = ba.Item(url="", expected_price_usd=usd, domestic_shipping_jpy=0, current_price_jpy=price_jpy)
    return ba.calculate(item, cfg, fx)


def recommendations(rows: list[dict], cfg: dict, fx: float) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        title = r.get("ebay_query") or r.get("title") or ""
        if not title or ba.ACCESSORY_RE.search(title):
            continue
        flags = ba.detect_flags(r.get("title", ""))
        if {"付属品のみ?", "アダプター等", "セット品"} & set(flags):
            continue
        k = rec_key(title)
        if not k:
            continue
        label, key, kind = k
        g = groups.setdefault(key, {"label": label, "brand": "Carl Zeiss" if label.startswith("Carl Zeiss") else label.split()[0], "kind": kind,
                                    "sales": [], "lists": [], "titles": []})
        g["titles"].append(r.get("title") or title)
        tier = ba.source_tier(r)
        rec = {"title": r.get("title", ""), "usd": round(r["_price"], 2), "junk": "ジャンク" in flags,
               "date": r.get("date", ""), "purchase": ba._float_or_none(r.get("purchase_jpy")), "tier": tier}
        # 販売履歴＝自分・Chatwork・頒布会（2026-11-18 ユーザー決定: 頒布会も売れたものとみなす。appraise.SURE_TIER）
        (g["sales"] if tier <= ba.SURE_TIER else g["lists"]).append(rec)

    out = []
    for g in groups.values():
        good = [s for s in g["sales"] if not s["junk"]]
        base = good or [s for s in g["lists"] if not s["junk"]]
        if not base:
            continue
        usd = round(statistics.median([s["usd"] for s in base]), 2)
        calc = store_calc(usd, cfg, fx)
        # 実際の利益（販売額と仕入値の両方がある自分・Chatworkの実績だけ）
        profits = [store_calc(s["usd"], cfg, fx, int(s["purchase"]))["profit_now_jpy"]
                   for s in g["sales"] if s["purchase"]]
        profit = round(statistics.median(profits)) if profits else None
        n = len(g["sales"])
        out.append({
            "label": g["label"], "brand": g["brand"], "kind": g["kind"],
            "cat": rec_cat(g["kind"], g["titles"] or [g["label"]]),   # 表示名は型の語を落とすので（IXY 310 → Canon 310）、元の商品名で決める
            "sales": n, "lists": len(g["lists"]), "usd": usd,
            "basis": (["販売実績", "Chatwork実績", "頒布会"][min(s["tier"] for s in good)] if good else "鑑定表（推定）"),
            "maxBuy": calc["max_bid_jpy"], "breakeven": calc["breakeven_bid_jpy"],
            "profit": profit, "profitN": len(profits),
            "score": profit * n if profit is not None and profit > 0 else None,
            "last": max((s["date"] for s in g["sales"]), default=""),
            "examples": [s["title"] for s in (g["sales"] or g["lists"])[:3]],
        })
    # 並び順: 利益×販売回数 → 販売回数 → 上限額
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), -x["sales"], -x["maxBuy"]))
    return out


# ---------------------------------------------------------------------------
# 入札候補（毎朝の入札リスト reports/bid_report_*.csv → スマホの「入札」タブ）
# ---------------------------------------------------------------------------

BID_MAX_AGE_H = 48  # これより古い入札リストは届けない（終わった出品ばかりになるため）
SKIP_FLAGS = ("付属品のみ?", "アダプター等")


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def bid_candidates(reports: Path | None = None, now: datetime | None = None) -> dict | None:
    """最新の入札リストから ◎○ と 仮◎○ を取り出す. 無い・古いときは None."""
    reports = reports or ROOT / "reports"
    files = sorted(reports.glob("bid_report_*.csv"), key=lambda p: p.stat().st_mtime) if reports.exists() else []
    if not files:
        return None
    f = files[-1]
    made = datetime.fromtimestamp(f.stat().st_mtime)
    if ((now or datetime.now()) - made).total_seconds() > BID_MAX_AGE_H * 3600:
        return None
    items = []
    for rank, r in enumerate(csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines())):
        flags = [x for x in (r.get("注意") or "").split(" / ") if x]
        if any(x in SKIP_FLAGS for x in flags):
            continue
        good = r.get("判定") in ("◎", "○")
        tent = not good and r.get("仮判定") in ("◎", "○")
        if not (good or tent):
            continue
        weak = good and r.get("根拠の強さ") == "確認してから"  # 鑑定表・リサーチ（推定）が根拠 → 確認してから（頒布会は確認不要。2026-11-18）
        url = r.get("ヤフオクURL") or ""
        m = re.search(r"/auction/([^/?#]+)", url)
        items.append({
            "id": m.group(1) if m else url, "url": url, "title": r.get("タイトル", ""),
            "mark": r["判定"] if good else r["仮判定"], "tent": tent, "check": tent or weak, "trusted": r.get("枠") == "安心",
            "price": _num(r.get("現在価格")), "bids": _num(r.get("入札数")), "end": r.get("終了", ""),
            "ship": _num(r.get("国内送料")), "shipBasis": r.get("国内送料の根拠", ""),
            "usd": _num(r.get("仮の想定USD") if tent else r.get("想定販売価格USD")),
            "basis": r.get("仮の根拠") if tent else r.get("価格の根拠", ""),
            "maxBid": _num(r.get("仮の上限入札額") if tent else r.get("上限入札額")),
            "profit": _num(r.get("仮の利益(現在価格)") if tent else r.get("利益(現在価格)")),
            "margin": None if tent else _num(r.get("利益率%")),
            "breakeven": None if tent else _num(r.get("損益分岐入札額")),
            "taxNote": r.get("入札額の税", ""),
            "sold": r.get("仮のsoldURL") if tent else r.get("eBay販売済みURL", ""),
            "now": r.get("仮の出品中URL") if tent else r.get("eBay出品中URL", ""),
            "flags": [x for x in flags if x not in ("動作品", "美品")],
            "desc": r.get("説明文", ""),
            "rank": rank,   # Mac の入札リストの並び（売れ筋・$99超が上、勝ちやすさ1割未満は下。2026-11-18〜20 の決定）
            "win": ba.win_label(_num(r.get("勝ちやすさ")), _num(r.get("勝ちやすさの件数"))).replace("勝ちやすさ ", "") if hasattr(ba, "win_label") else "",
            "caution": r.get("出品者の注意", ""),
        })
    items.sort(key=lambda x: (x["check"], x["tent"], x["end"] or "9"))
    return {"built": f"{made:%Y-%m-%d %H:%M}", "items": items, "reserve": reserve_list(reports, now)}


def reserve_list(reports: Path, now: datetime | None = None) -> list[dict]:
    """最新の「今日の予約リスト」（reserve_list_*.csv）→ [{id, kind}]（予約 or 即決）. スマホの入札タブの一番上に同じ順で出す."""
    files = sorted(reports.glob("reserve_list_*.csv"), key=lambda p: p.stat().st_mtime) if reports.exists() else []
    if not files:
        return []
    f = files[-1]
    if ((now or datetime.now()) - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() > BID_MAX_AGE_H * 3600:
        return []
    out = []
    for r in csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()):
        m = re.search(r"/auction/([^/?#]+)", r.get("ヤフオク") or "")
        if m:
            out.append({"id": m.group(1), "kind": "即決" if "即決" in (r.get("区分") or "") else "予約"})
    return out


SWEET_NOTES = ("レンズ狙い目スペック", "レンズ世代の高い順")  # knowledge/講座/ の講座資料のまとめ（「狙い目」タブ。2026-11-19 ユーザー指定）


def sweet_notes(root: Path | None = None) -> list[dict]:
    """スマホアプリの「狙い目」タブに出す講座資料のまとめ（knowledge/講座/〈名前〉.md）.
    講座資料は公開しないので、アプリのページには書かず、暗号化して届く相場データ（store_data.json）に入れる."""
    folder = (root or ROOT) / "knowledge" / "講座"
    out = []
    for name in SWEET_NOTES:
        p = folder / f"{name}.md"
        if p.exists():
            out.append({"name": name, "md": p.read_text(encoding="utf-8")})
    return out


def trivia(path: Path | None = None) -> list[dict]:
    """data/trivia.csv（機種の豆知識）→ [{"m": 機種, "t": 豆知識}]. 店舗アプリが機種名の下に1行で出す."""
    import csv
    p = path or (ROOT / "data" / "trivia.csv")
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        # 「確認」が 確認済み の行だけ（2026-11-21 オーナー「未確認のエピソードはいらない。調べてわかったものだけ」）
        return [{"m": r["機種"].strip(), "t": r["豆知識"].strip()} for r in csv.DictReader(f)
                if (r.get("機種") or "").strip() and (r.get("豆知識") or "").strip() and (r.get("確認") or "").strip() == "確認済み"]


def candidate_refs(ship_usd: float, path: Path | None = None) -> list[dict]:
    """data/candidate_prices.csv（ゼニガメの下調べ。販売店の価格が中心で売れた額ではない）→ 店舗アプリの仮の相場（tier 3・確認してから）.
    アプリ側で、販売実績などほかの相場が当たる機種には使わない（相場なしを減らすためだけ）."""
    import csv
    p = path or (ROOT / "data" / "candidate_prices.csv")
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = (r.get("機種") or "").strip()
            try:
                v = float(str(r.get("候補$（送料別）") or "").replace("$", "").replace(",", ""))
            except ValueError:
                continue
            if t and v > 0:
                out.append({"title": t, "price": round(v + ship_usd, 2), "tier": 3, "source": "下調べ", "tokens": sorted(ba._tokens(t)), "acc": False,
                            "d": (r.get("日付") or "").strip()})
    return out


def build(cfg: dict, fx: float) -> dict:
    data = bx.build_data(cfg, fx)
    data["sweet"] = sweet_notes()
    rows = []
    for name in REC_SOURCES:
        p = ROOT / "data" / name
        if p.exists():
            rows += ba.read_sales(p)
    data["recs"] = recommendations(rows, cfg, fx)
    data["refs"] += candidate_refs(float(cfg["shipping"].get("deduction_usd", 0)))
    data["trivia"] = trivia()
    data["bids"] = bid_candidates()
    data["kind"] = "aurora-store"
    # 店舗アプリ →「共有_店舗」（調べた・購入）の送り先。鍵は共有_店舗に行を足すことしかできない（ledger_sync.store_endpoint）
    try:
        import ledger_sync
        ep = ledger_sync.store_endpoint()
    except Exception:  # キーチェーンの無い環境（テスト・クラウド）
        ep = None
    if ep:
        data["inbox"] = ep
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="店舗仕入れアプリのデータを作る")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "data" / "store_data.json")
    ap.add_argument("--fx", type=float, help="為替（省略時は自動取得、失敗時は config.toml）")
    ap.add_argument("--no-icloud", action="store_true", help="iCloud Drive にコピーしない")
    ap.add_argument("--no-publish", action="store_true", help="iPhone への自動配信をしない")
    args = ap.parse_args(argv)

    CORE_JS.parent.mkdir(exist_ok=True)
    CORE_JS.write_text(core_js(), encoding="utf-8")
    ba.import_extension_memo()  # 拡張機能で「直す」した相場（ダウンロード/aurora_price_memo.csv）
    cfg = ba.load_config(None)
    fx = args.fx or (cfg["fx"].get("auto_fetch") and ba.fetch_fx()) or float(cfg["fx"]["usd_jpy"])
    data = build(cfg, fx)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nb = len(data["bids"]["items"]) if data.get("bids") else 0
    print(f"出力: {args.out}（相場 {len(data['refs'])}件・おすすめ候補 {len(data['recs'])}機種・入札候補 {nb}件・"
          f"為替 {fx:.2f}円・{args.out.stat().st_size // 1024}KB）", file=sys.stderr)
    if not args.no_icloud:
        # 自動実行（launchd）からは macOS が iCloud Drive を見ること自体を止める（exists() でも PermissionError）。
        # その場合も配信は続ける
        try:
            if not ICLOUD.parent.exists():
                raise FileNotFoundError("iCloud Drive がありません")
            ICLOUD.mkdir(exist_ok=True)
            shutil.copy(args.out, ICLOUD / args.out.name)
            print(f"iCloud Drive にコピー: iCloud Drive › aurora › {args.out.name}", file=sys.stderr)
        except OSError as e:
            print(f"iCloud Drive にコピーできませんでした（{e.__class__.__name__}）。スマホへは暗号化配信で届けます", file=sys.stderr)
    if not args.no_publish and args.out == ROOT / "data" / "store_data.json":
        import store_publish  # 暗号化して iPhone へ自動配信（未設定なら何もしない）
        store_publish.publish(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
