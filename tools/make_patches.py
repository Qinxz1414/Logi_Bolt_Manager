#!/usr/bin/env python
"""从「已打补丁的 site-packages」重新生成 patches/*.patch。

维护者流程（升级 Solaar 或修改补丁后）：

    1. 在本地环境里改好 site-packages 中的 solaar / hidapi 文件并验证功能
    2. 下载对应版本的 Solaar 源码包，解压出 lib/ 目录
    3. python tools/make_patches.py --upstream <解压后的 lib 目录>
    4. git diff 检查 patches/ 的变化，提交

本脚本只做一件事：把「上游原始文件」与「当前已打补丁文件」的差异输出为
标准 unified diff（git apply 可直接消费），并保证换行符统一为 LF。
"""
from __future__ import annotations

import argparse
import difflib
import pathlib
import sys
import sysconfig

# 相对 site-packages 的路径；与上游 lib/ 下的路径一一对应
PATCHED_FILES = [
    "hidapi/hidapi_impl.py",
    "logitech_receiver/base.py",
    "logitech_receiver/receiver.py",
    "logitech_receiver/desktop_notifications.py",
]

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read_lines(path: pathlib.Path) -> list[str]:
    """读入文本并归一为 LF 行列表（保留行尾换行信息由 join 处理）。"""
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    return text.replace("\r\n", "\n").splitlines(keepends=True)


def make_patch(rel: str, upstream_root: pathlib.Path, source_root: pathlib.Path) -> str:
    old = _read_lines(upstream_root / rel)
    new = _read_lines(source_root / rel)
    diff = list(
        difflib.unified_diff(
            old,
            new,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
            lineterm="\n",
        )
    )
    if not diff:
        return ""
    # 补一行 git 风格的头部，便于 git apply / GitHub 展示
    header = f"diff --git a/{rel} b/{rel}\n"
    return header + "".join(diff)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="重新生成 patches/*.patch")
    ap.add_argument(
        "--upstream",
        required=True,
        help="上游 Solaar 源码中的 lib 目录（解压 solaar-<version>.tar.gz 得到）",
    )
    ap.add_argument(
        "--source",
        default=None,
        help="已打补丁的 site-packages 目录（默认使用当前解释器的）",
    )
    ap.add_argument("--out", default=str(REPO_ROOT / "patches"), help="补丁输出目录")
    ap.add_argument(
        "--check",
        action="store_true",
        help="只校验不写入：若磁盘上的 .patch 与当前环境不一致则返回非 0",
    )
    args = ap.parse_args(argv)

    upstream_root = pathlib.Path(args.upstream).resolve()
    source_root = pathlib.Path(args.source).resolve() if args.source else pathlib.Path(
        sysconfig.get_paths()["purelib"]
    )
    out_dir = pathlib.Path(args.out).resolve()

    if not (upstream_root / "logitech_receiver").is_dir():
        print(f"[FATAL] {upstream_root} 看起来不是 Solaar 源码的 lib 目录", file=sys.stderr)
        return 2

    print(f"upstream : {upstream_root}")
    print(f"source   : {source_root}")
    print(f"output   : {out_dir}")

    drift = False
    for rel in PATCHED_FILES:
        patch = make_patch(rel, upstream_root, source_root)
        if not patch:
            print(f"  [!] {rel}: 与上游一致，跳过")
            continue
        target = out_dir / (rel + ".patch")
        target.parent.mkdir(parents=True, exist_ok=True)
        changed = patch.count("\n")
        if args.check:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            status = "OK" if existing == patch else "DRIFT"
            if existing != patch:
                drift = True
            print(f"  [{status}] {target.name}")
        else:
            target.write_text(patch, encoding="utf-8", newline="\n")
            print(f"  [写入] {rel}.patch  ({changed} 行)")

    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
