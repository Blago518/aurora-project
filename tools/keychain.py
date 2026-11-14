"""秘密情報（API トークン・合言葉など）の保存. Mac はキーチェーン、それ以外は data/.secrets_<service>.json（権限600）.

ファイルやチャット、GitHub には書かない。
"""
from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def _file(service: str) -> Path:
    return DATA / f".secrets_{service}.json"


def get(service: str, name: str) -> str | None:
    if platform.system() == "Darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", name, "-w"],
                           capture_output=True, text=True)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    f = _file(service)
    return json.loads(f.read_text(encoding="utf-8")).get(name) if f.exists() else None


def set(service: str, name: str, value: str) -> None:  # noqa: A001
    if platform.system() == "Darwin":
        subprocess.run(["security", "add-generic-password", "-U", "-s", service, "-a", name, "-w", value],
                       check=True, capture_output=True)
        return
    f = _file(service)
    d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    d[name] = value
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")
    f.chmod(0o600)


def copy_to_clipboard(text: str) -> bool:
    """Mac のクリップボードへ（同じ Apple ID の iPhone でもそのまま貼れる＝ユニバーサルクリップボード）."""
    if platform.system() != "Darwin":
        return False
    return subprocess.run(["pbcopy"], input=text, text=True).returncode == 0
