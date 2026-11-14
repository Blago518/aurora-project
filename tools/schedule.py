#!/usr/bin/env python3
"""update_all.py を Mac で自動実行する（毎朝＋Mac にログインしたとき）. Mac 標準の launchd を使う.

使い方（aurora フォルダで）:
    python3 tools/schedule.py install     # 設定（毎朝 7:03 と ログイン時）
    python3 tools/schedule.py install --hour 6 --minute 30
    python3 tools/schedule.py status      # 設定されているか・最後の実行結果
    python3 tools/schedule.py uninstall   # やめる
    python3 tools/schedule.py run         # 今すぐ1回動かす（設定の確認用）

結果は reports/update_all.log に残り、終わると Mac の通知が出る。
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LABEL = "com.aurora.update-all"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = ROOT / "reports" / "update_all.log"


def plist(hour: int, minute: int) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(HERE / "update_all.py"), "--notify"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": True,  # ログインしたとき（Mac の起動時）にも1回
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                 "PYTHONIOENCODING": "utf-8"},
    }


def _launchctl(*args: str) -> int:
    return subprocess.call(["launchctl", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install(hour: int, minute: int) -> None:
    if sys.platform != "darwin":
        print("Mac でだけ使えます。")
        sys.exit(1)
    LOG.parent.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(PLIST))  # 前の設定があれば外す
    with open(PLIST, "wb") as f:
        plistlib.dump(plist(hour, minute), f)
    if _launchctl("bootstrap", domain, str(PLIST)) != 0:
        _launchctl("load", "-w", str(PLIST))  # 古い macOS
    print(f"""自動実行を設定しました: 毎朝 {hour}:{minute:02d} と、Mac にログインしたとき（今もすぐ1回動きます）。
結果: reports/update_all.log（終わると Mac の通知が出ます）
※ 初回に「python3 がダウンロードフォルダ／iCloud Drive にアクセスしようとしています」と出たら［許可］を押してください。""")


def uninstall() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}", str(PLIST))
    if PLIST.exists():
        PLIST.unlink()
    print("自動実行をやめました。")


def status() -> None:
    if not PLIST.exists():
        print("自動実行は設定されていません（python3 tools/schedule.py install）")
    else:
        p = plistlib.loads(PLIST.read_bytes())
        t = p["StartCalendarInterval"]
        print(f"自動実行: 設定済み（毎朝 {t['Hour']}:{t['Minute']:02d} と ログイン時）")
    if LOG.exists():
        tail = LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:]
        print("— 最後の実行（reports/update_all.log の末尾）—\n" + "\n".join(tail))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="update_all.py の自動実行（launchd）")
    ap.add_argument("cmd", choices=["install", "uninstall", "status", "run"])
    ap.add_argument("--hour", type=int, default=7)
    ap.add_argument("--minute", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "install":
        install(a.hour, a.minute)
    elif a.cmd == "uninstall":
        uninstall()
    elif a.cmd == "status":
        status()
    else:
        _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LABEL}")
        print("動かしました。少し待ってから python3 tools/schedule.py status で結果を見られます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
