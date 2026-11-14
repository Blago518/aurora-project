#!/usr/bin/env python3
"""店舗アプリ（iPhone）へ相場データを暗号化して自動配信する.

data/store_data.json を圧縮 → 合言葉で暗号化（PBKDF2-SHA256 20万回 ＋ AES-256-GCM）→ GitHub の **シークレット Gist** に置く。
アプリは開くたびにそれを読み、iPhone の中で合言葉を使って元に戻す。合言葉が無いと誰も読めない。
公開リポジトリ（aurora-project）には置かない。合言葉と GitHub のトークンは Mac のキーチェーンだけに保存する。

使い方（aurora フォルダで）:
    python3 tools/store_publish.py setup   # 初回: GitHub のトークンを入れる → 設定コードがクリップボードに入る → iPhone のアプリに貼る
    python3 tools/store_publish.py         # 配信（build_store.py・update_all.py が毎回実行する）
    python3 tools/store_publish.py code    # 設定コードをもう一度クリップボードへ（iPhone を替えたとき）
"""
from __future__ import annotations

import argparse
import base64
import getpass
import gzip
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "data" / "store_data.json"
SERVICE = "aurora-store"
FILENAME = "store_data.enc"
ITER = 200_000

sys.path.insert(0, str(HERE))
import keychain  # noqa: E402


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return None
    return AESGCM, SHA256, PBKDF2HMAC


def encrypt(data: bytes, passphrase: str, iterations: int = ITER) -> str:
    """圧縮して暗号化し、JSON 文字列にする（store/sync.js の decryptStore で戻せる形）."""
    AESGCM, SHA256, PBKDF2HMAC = _aesgcm()
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=iterations).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, gzip.compress(data), None)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return json.dumps({"v": 1, "iter": iterations, "salt": b(salt), "iv": b(iv), "ct": b(ct)})


def decrypt(text: str, passphrase: str) -> bytes:
    AESGCM, SHA256, PBKDF2HMAC = _aesgcm()
    j = json.loads(text)
    d = base64.b64decode
    key = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=d(j["salt"]), iterations=j["iter"]).derive(passphrase.encode())
    return gzip.decompress(AESGCM(key).decrypt(d(j["iv"]), d(j["ct"]), None))


def setup_code(url: str, passphrase: str) -> str:
    """iPhone のアプリに貼る設定コード（配信 URL と合言葉）."""
    raw = json.dumps({"u": url, "p": passphrase}, separators=(",", ":")).encode()
    return "AURORA1:" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _github(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"https://api.github.com{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "aurora-store-publish"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read() or b"{}")


def publish(src: Path = SRC) -> bool | None:
    """配信したら True、未設定は None、失敗は False."""
    token, gist, pw = (keychain.get(SERVICE, k) for k in ("token", "gist", "pass"))
    if not (token and gist and pw):
        print("スマホへの自動配信: 未設定（python3 tools/store_publish.py setup）。iCloud の store_data.json を手動で選んでください",
              file=sys.stderr)
        return None
    if not _aesgcm():
        print("スマホへの自動配信: 暗号化の部品がありません → python3 -m pip install --user cryptography", file=sys.stderr)
        return False
    try:
        _github("PATCH", f"/gists/{gist}", token, {"files": {FILENAME: {"content": encrypt(src.read_bytes(), pw)}}})
    except (urllib.error.URLError, OSError) as e:
        print(f"スマホへの自動配信: 失敗（{e}）。GitHub のトークンの期限切れなら setup をやり直してください", file=sys.stderr)
        return False
    print("スマホへの自動配信: 完了（アプリを開くと最新になります）", file=sys.stderr)
    return True


def cmd_setup() -> None:
    if not _aesgcm():
        print("暗号化の部品（cryptography）を入れます…")
        if subprocess.call([sys.executable, "-m", "pip", "install", "--user", "cryptography"]) != 0 or not _aesgcm():
            print("入れられませんでした。表示を Claude に送ってください。")
            sys.exit(1)
    if not SRC.exists():
        print("先に python3 tools/build_store.py を実行して data/store_data.json を作ってください。")
        sys.exit(1)
    print("""
スマホへの自動配信を設定します（初回だけ・約3分）。暗号化したデータを GitHub のシークレット Gist に置きます。

 1. https://github.com/settings/tokens/new を開く（GitHub にログイン）
 2. Note: aurora store ／ Expiration: No expiration ／ Select scopes: 「gist」だけにチェック
 3. 一番下の「Generate token」→ 表示された ghp_… をコピー
""")
    token = getpass.getpass("そのトークンを貼り付けて Enter（文字は表示されません）: ").strip()
    pw = keychain.get(SERVICE, "pass") or secrets.token_urlsafe(18)
    try:
        g = _github("POST", "/gists", token, {"description": "aurora store data (encrypted)", "public": False,
                                              "files": {FILENAME: {"content": encrypt(SRC.read_bytes(), pw)}}})
    except urllib.error.HTTPError as e:
        print(f"GitHub に作れませんでした（{e.code}）。トークンの「gist」にチェックが入っているか確認して、もう一度実行してください。")
        sys.exit(1)
    url = f"https://gist.githubusercontent.com/{g['owner']['login']}/{g['id']}/raw/{FILENAME}"
    for k, v in (("token", token), ("gist", g["id"]), ("pass", pw), ("url", url)):
        keychain.set(SERVICE, k, v)
    show_code()


def show_code() -> None:
    url, pw = keychain.get(SERVICE, "url"), keychain.get(SERVICE, "pass")
    if not (url and pw):
        print("まだ設定されていません（python3 tools/store_publish.py setup）")
        sys.exit(1)
    code = setup_code(url, pw)
    copied = keychain.copy_to_clipboard(code)
    print(f"""
{"設定コードをクリップボードにコピーしました。" if copied else "下の設定コードをコピーしてください:"}
{"" if copied else code}
iPhone の「店舗リサーチ」→ 設定タブ →「① 自動更新」の欄に貼り付け →［保存］。
（Mac と iPhone が同じ Apple ID なら、iPhone でそのまま貼り付けられます。設定コードは他人に見せないでください）
""")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="店舗アプリへ相場データを暗号化して配信する")
    ap.add_argument("cmd", nargs="?", choices=["publish", "setup", "code"], default="publish")
    args = ap.parse_args(argv)
    {"setup": cmd_setup, "code": show_code}.get(args.cmd, publish)()
    return 0


if __name__ == "__main__":
    sys.exit(main())
