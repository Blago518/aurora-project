#!/usr/bin/env python3
"""プログラムを直したら、その場でテストを回す（PostToolUse / Edit・Write）。

対象: tools/ と store/ の .py・.js・.toml・.html。
Python・拡張機能・スマホのアプリの判定一致もテストで確かめる。
失敗したら終了コード 2 で結果の最後の部分を Claude に伝える（直し途中なら続けてよい）。
"""
import json
import os
import subprocess
import sys

EXTS = (".py", ".js", ".toml", ".html")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or (data.get("tool_response") or {}).get("filePath") or ""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    rel = os.path.relpath(os.path.abspath(path), root) if path else ""
    if not (rel.startswith(("tools" + os.sep, "store" + os.sep)) and rel.endswith(EXTS)):
        return
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tools", "-q"],
                           cwd=root, capture_output=True, text=True, timeout=170)
    except subprocess.TimeoutExpired:
        print("テストが時間切れ（170秒）になりました。", file=sys.stderr)
        sys.exit(2)
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-40:])
        print(f"【テスト失敗】{rel} を直した後のテストが通りません。"
              "直し途中なら続けて、最後に通ることを確かめてください。\n" + tail, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
