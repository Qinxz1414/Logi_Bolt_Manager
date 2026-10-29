#!/usr/bin/env python
"""下载官方 hidapi.dll（打包用）。

为什么需要这个脚本：pip 的 `hidapi` 包在 Windows 上只装 C 扩展（hid.*.pyd），
不带独立的 hidapi.dll；而本项目改过的 hidapi/hidapi_impl.py 是通过 ctypes 直接加载
hidapi.dll 的。之前这个 DLL 靠手工拷贝，是构建不可复现的根因，现在统一从这里获取。

产物：build/hidapi/hidapi.dll（被 src/BoltManager.spec 打进包，也被源码运行时的
PATH 注入逻辑使用），该目录已在 .gitignore 中。

用法：
    python tools/fetch_hidapi.py            # 下载（已存在则跳过）
    python tools/fetch_hidapi.py --force    # 强制重新下载
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HIDAPI_VERSION = "0.15.0"
ZIP_URL = f"https://github.com/libusb/hidapi/releases/download/hidapi-{HIDAPI_VERSION}/hidapi-win.zip"
ZIP_SHA256 = "d18c43ec9506a2f6d7faa9c7e0a342c4b64fbae521b71b5d4ac0777fd24dda93"
ZIP_MEMBER = "x64/hidapi.dll"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下载官方 hidapi.dll 到 build/hidapi/")
    ap.add_argument("--dest", default=str(REPO_ROOT / "build" / "hidapi"), help="输出目录")
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = ap.parse_args(argv)

    dest_dir = Path(args.dest).resolve()
    dest = dest_dir / "hidapi.dll"

    if dest.is_file() and not args.force:
        print(f"[跳过] {dest} 已存在（--force 可强制重新下载）")
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"下载 {ZIP_URL}")
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "hidapi-win.zip"
        with urllib.request.urlopen(ZIP_URL) as resp, zip_path.open("wb") as f:
            f.write(resp.read())

        actual = sha256_of(zip_path)
        if actual != ZIP_SHA256:
            print(f"[FATAL] zip 校验失败\n  期望 sha256: {ZIP_SHA256}\n  实际 sha256: {actual}", file=sys.stderr)
            return 1
        print(f"zip sha256 校验通过: {actual}")

        with zipfile.ZipFile(zip_path) as z:
            if ZIP_MEMBER not in z.namelist():
                print(f"[FATAL] {ZIP_MEMBER} 不在压缩包里", file=sys.stderr)
                return 1
            dll_bytes = z.read(ZIP_MEMBER)

    dest.write_bytes(dll_bytes)
    print(f"[OK] {dest}  ({len(dll_bytes)} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
