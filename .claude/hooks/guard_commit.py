#!/usr/bin/env python3
"""コミット・プッシュの直前に、公開してはいけないものが入っていないか確認する（PreToolUse / Bash）。

止める条件（.claude/rules/情報管理.md）:
- data/・knowledge/・reports/・extension/ のファイルが git に入っている（ステージ済み・コミット済み）
- `git add -f`（.gitignore を無視して追加）
- ステージした変更にトークンや秘密のURLらしい文字列がある
止めるときは終了コード 2（Claude Code が操作を中止し、理由を Claude に伝える）。
"""
import json
import os
import re
import subprocess
import sys

PROTECTED = ["data", "knowledge", "reports", "extension"]
SECRET_PATTERNS = [
    r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"gho_[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"script\.google\.com/macros/s/[A-Za-z0-9_-]{20,}",
    r"docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]{20,}",
]


def git(root, *args):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def block(msg):
    print("【公開チェックで止めました】" + msg + "\n（.claude/rules/情報管理.md）", file=sys.stderr)
    sys.exit(2)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\b", cmd):
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()

    if re.search(r"\bgit\s+add\b[^;&|]*\s(-f|--force)\b", cmd):
        block("`git add -f` は .gitignore を無視して非公開のファイルを入れてしまうので使えません。")

    is_commit = re.search(r"\bgit\s+(-\S+\s+)*commit\b", cmd)
    is_push = re.search(r"\bgit\s+(-\S+\s+)*push\b", cmd)
    if not (is_commit or is_push):
        return

    tracked = git(root, "ls-files", "--cached", "--", *PROTECTED).split()
    if is_push:
        tracked += git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", *PROTECTED).split()
    if tracked:
        names = "、".join(sorted(set(tracked))[:5])
        block(f"非公開のフォルダのファイルが git に入っています: {names}。"
              " `git rm --cached <ファイル>` で外してください。")

    if is_commit:
        diff = git(root, "diff", "--cached", "-U0")
        added = "\n".join(l for l in diff.splitlines()
                          if l.startswith("+") and not l.startswith("+++"))
        for p in SECRET_PATTERNS:
            m = re.search(p, added)
            if m:
                block(f"ステージした変更にトークン・秘密のURLらしい文字列があります（{m.group(0)[:12]}…）。"
                      "キーチェーン（tools/keychain.py）に移してください。")


if __name__ == "__main__":
    main()
