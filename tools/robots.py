#!/usr/bin/env python3
"""ヤフオクの robots.txt（サイトが機械に「ここは読まないで」と示すファイル）に合わせて URL を検査・直す.

2026-11-19 ユーザー決定: 毎朝のヤフオクの読み込みを robots.txt に合わせる。
`/search/`・`/seller/` は `n=`・`s1=`・`o1=`・`mode=`・`select=` などの引数付きが Disallow（読まない）。
並べ替え・件数指定を外して読み、並べ替えは Mac の中でする。`/closedsearch/closedsearch` は Allow。出品ページ `/jp/auction/` は制限なし。

規則の読み方（RFC 9309 と同じ）: `*` は任意の文字列、`$` は末尾。当たった規則のうち一番長いものが勝つ（同じ長さなら Allow）。
ただし**引数付きの Disallow（「?」を含む規則）に当たる URL は、Allow の規則がもっと長くても読まない**（保守的に。
例: `/closedsearch/closedsearch?p=…&n=100` は Allow `/closedsearch/closedsearch` より短い `Disallow: /closedsearch/*?*n=` に当たるので読まない）。

規則は data/yahoo_robots.txt（7日ごとに robots.txt を取り直して保存）→ 読めなければ tools/yahoo_robots.txt（2026-11-19 の写し）。
"""
from __future__ import annotations

import re
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ROBOTS_URL = "https://auctions.yahoo.co.jp/robots.txt"
BUNDLED = HERE / "yahoo_robots.txt"
CACHE = ROOT / "data" / "yahoo_robots.txt"
HOST = "auctions.yahoo.co.jp"

_RULES: list[tuple[bool, str]] | None = None


def parse(text: str) -> list[tuple[bool, str]]:
    """robots.txt → [(allow, パターン)]（User-agent: * の組だけ）."""
    rules: list[tuple[bool, str]] = []
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(val)
        elif key in ("allow", "disallow"):
            in_rules = True
            if "*" in agents and val:
                rules.append((key == "allow", val))
    return rules


def _regex(pattern: str) -> re.Pattern:
    end = pattern.endswith("$")
    body = "".join(".*" if c == "*" else re.escape(c) for c in (pattern[:-1] if end else pattern))
    return re.compile(body + ("$" if end else ""))


def _target(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def allowed(url: str, rules: list[tuple[bool, str]] | None = None) -> bool:
    """この URL を読んでよいか（ヤフオク以外の URL は対象外で True）."""
    if urllib.parse.urlsplit(url).hostname != HOST:
        return True
    rules = load_rules() if rules is None else rules
    t = _target(url)
    hits = [(len(p), a, p) for a, p in rules if _regex(p).match(t)]
    if not hits:
        return True
    if any(not a and "?" in p for _, a, p in hits):  # 引数付きの Disallow に当たれば読まない（保守的）
        return False
    best = max(hits, key=lambda h: (h[0], h[1]))  # 一番長い規則。同じ長さなら Allow
    return best[1]


def safe_url(url: str, rules: list[tuple[bool, str]] | None = None) -> str:
    """Disallow に当たる引数（n=・s1=・o1= など）を外した URL. 外しても読めない URL（パスごと Disallow）はそのまま返す."""
    if urllib.parse.urlsplit(url).hostname != HOST:
        return url
    rules = load_rules() if rules is None else rules
    parts = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    base = parts._replace(query="", fragment="")
    keep = [(k, v) for k, v in pairs
            if allowed(urllib.parse.urlunsplit(base._replace(query=urllib.parse.urlencode([(k, v)]))), rules)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(keep), fragment=""))


def removed_params(url: str, rules: list[tuple[bool, str]] | None = None) -> list[str]:
    """safe_url で外れる引数の名前（警告の表示用）."""
    before = [k for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)]
    after = {k for k, _ in urllib.parse.parse_qsl(urllib.parse.urlsplit(safe_url(url, rules)).query, keep_blank_values=True)}
    return [k for k in before if k not in after]


def param_rules(rules: list[tuple[bool, str]] | None = None) -> list[list[str]]:
    """拡張機能用: 引数付きの Disallow を [パスの正規表現, 引数の末尾（"n=" など）] にする.
    引数は「名前=」が末尾の語で終われば外す（`/search/*?*n=` は名前が n で終わる引数＝n・min なども外す。robots.txt の文字の当たり方と同じ）."""
    out = []
    for a, p in (load_rules() if rules is None else rules):
        if a or "?" not in p:
            continue
        path, _, rest = p.partition("?")
        tail = rest.split("*")[-1]
        if not tail.endswith("="):
            continue
        out.append(["^" + "".join(".*" if c == "*" else re.escape(c) for c in path), tail])
    return out


def load_rules(refresh_days: int = 7, fetch=None) -> list[tuple[bool, str]]:
    """規則を読む（1回の実行で1回だけ）. data/yahoo_robots.txt が古ければ取り直す（失敗したら手元の写し）."""
    global _RULES
    if _RULES is not None:
        return _RULES
    text = ""
    fresh = CACHE.exists() and time.time() - CACHE.stat().st_mtime < refresh_days * 86400
    if CACHE.parent.exists() and not fresh:
        try:
            import appraise as ba
            got = (fetch or ba.http_get)(ROBOTS_URL)
            if "Disallow" in got:
                CACHE.write_text(got, encoding="utf-8")
                text = got
        except Exception as e:  # noqa: BLE001  取れなければ前回の保存か手元の写し
            print(f"robots.txt を取り直せませんでした（{e}）。保存済みの規則で検査します", file=sys.stderr)
    if not text and CACHE.exists():
        text = CACHE.read_text(encoding="utf-8")
    if not text:
        text = BUNDLED.read_text(encoding="utf-8")
    _RULES = parse(text)
    return _RULES


def check(url: str, rules: list[tuple[bool, str]] | None = None) -> str | None:
    """読む前の検査. 読んでよければ URL（Disallow の引数は外した形）、読めなければ None と警告."""
    u = safe_url(url, rules)
    if not allowed(u, rules):
        print(f"⚠ robots.txt で読まない形の URL なので読みません: {url[:120]}", file=sys.stderr)
        return None
    return u
