"""knowledge_share.md（Mac の knowledge/ をまとめたファイル）から、
読むだけのナレッジページ（HTML、ニューロン付き）を作る。

使い方:
    python3 tools/build_knowledge_page.py knowledge_share.md -o aurora-knowledge.html

入力は「=== ./フォルダ/ノート名.md」の行でノートが区切られたテキスト。
金額の実績・仕入先・人名などは、このスクリプトに渡す前に取り除いておくこと
（このスクリプトは中身をそのまま載せる）。
"""
import argparse
import datetime
import html
import re
from pathlib import Path

TEMPLATE = Path(__file__).with_name("knowledge_page_template.html")

# ページ内リンク（#の後ろ）に使う英字の名前。無いノートは n1, n2… を振る。
KNOWN_IDS = {
    "00_ホーム": "home",
    "方針/入札判断の基準": "bid",
    "方針/利益計算": "profit",
    "方針/ジャンク品の考え方": "junk",
    "ツール/拡張機能の使い方": "ext",
    "ツール/Pythonツールの使い方": "python",
    "ツール/データ更新手順": "update",
    "意思決定ログ": "log",
    "改善アイデア": "ideas",
    "用語集": "glossary",
}
GROUP_ORDER = ["入口", "方針", "ツール", "記録"]


def parse_notes(text):
    notes = []
    for part in re.split(r"^=== \./", text, flags=re.M)[1:]:
        path, _, body = part.partition("\n")
        name = path.strip()
        if name.endswith(".md"):
            name = name[:-3]
        notes.append((name, body.strip()))
    return notes


def group_of(name):
    if name == "00_ホーム":
        return "入口"
    if "/" in name:
        return name.split("/")[0]
    return "記録"


def title_of(name, body):
    if name == "00_ホーム":
        return "ホーム"
    return name.split("/")[-1]


def build(text, date):
    notes = parse_notes(text)
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    notes.sort(key=lambda n: (order.get(group_of(n[0]), len(order)), group_of(n[0])))
    used, blocks, extra = set(), [], 0
    for name, body in notes:
        nid = KNOWN_IDS.get(name)
        if not nid or nid in used:
            extra += 1
            nid = f"n{extra}"
        used.add(nid)
        body = body.replace("</script", "<\\/script")
        blocks.append(
            f'<script type="text/plain" data-id="{nid}" data-name="{html.escape(name)}" '
            f'data-title="{html.escape(title_of(name, body))}" data-group="{html.escape(group_of(name))}">\n'
            f"{body}\n</script>"
        )
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("{{NOTES}}", "\n\n".join(blocks)).replace("{{DATE}}", date), len(blocks)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="knowledge_share.md")
    ap.add_argument("-o", "--out", default="aurora-knowledge.html")
    ap.add_argument("--date", default=datetime.date.today().isoformat(), help="ページに表示する写した日")
    a = ap.parse_args()
    page, n = build(Path(a.src).read_text(encoding="utf-8"), a.date)
    Path(a.out).write_text(page, encoding="utf-8")
    print(f"ノート {n} 件 → {a.out}")


if __name__ == "__main__":
    main()
