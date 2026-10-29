#!/usr/bin/env python
"""把 patches/ 下的补丁应用（或撤销）到当前 Python 的 site-packages。

前置条件：已经 `pip install solaar==1.1.20 hidapi`。
补丁基于 Solaar 1.1.20 的原始代码，换版本会因上下文不匹配而失败——这是有意为之的
安全设计，避免把补丁硬套到未经验证的版本上。

用法：
    python tools/apply_patches.py            # 应用 patches/
    python tools/apply_patches.py --check    # 只检查能否干净应用，不落盘
    python tools/apply_patches.py --revert   # 撤销（恢复成上游原样）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCH_DIR = REPO_ROOT / "patches"


def patch_files() -> list[Path]:
    return sorted(PATCH_DIR.rglob("*.patch"))


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def pick_tool() -> tuple[str, list[str]] | None:
    """返回可用的补丁工具：(名称, 命令前缀)。优先 git apply，其次 GNU patch。

    git 强制关闭 autocrlf/eol 转换：补丁内的文件是 LF，若让 git 按 Windows 全局配置
    转成 CRLF，撤销后就无法还原成上游原样。
    """
    if shutil.which("git"):
        return "git-apply", ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply"]
    if shutil.which("patch"):
        return "gnu-patch", ["patch", "-p1", "--binary"]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="应用 patches/ 到 site-packages")
    ap.add_argument("--check", action="store_true", help="只检查，不修改文件")
    ap.add_argument("--revert", action="store_true", help="反向应用，撤销补丁")
    ap.add_argument("--target", default=None, help="site-packages 路径（默认当前解释器的）")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve() if args.target else Path(sysconfig.get_paths()["purelib"])
    patches = patch_files()

    print(f"site-packages : {target}")
    print(f"interpreters  : {sys.executable}")
    if not patches:
        print("[FATAL] patches/ 下没有 .patch 文件", file=sys.stderr)
        return 2
    if not (target / "logitech_receiver").is_dir():
        print(
            f"[FATAL] {target}/logitech_receiver 不存在。\n"
            f"        请先执行：{Path(sys.executable).name} -m pip install solaar==1.1.20 hidapi",
            file=sys.stderr,
        )
        return 2

    tool = pick_tool()
    if tool is None:
        print("[FATAL] 需要 git 或 GNU patch 之一；Windows 上装 Git for Windows 即可", file=sys.stderr)
        return 2
    name, base = tool
    print(f"patch tool    : {name}\n")

    results: list[tuple[str, str]] = []
    for p in patches:
        rel = p.relative_to(PATCH_DIR).as_posix()[: -len(".patch")]
        direction = ["-R"] if args.revert else []
        dry = ["--check"] if args.check else []

        if name == "git-apply":
            rc, out = run(base + direction + dry + [str(p)], target)
        else:
            # GNU patch 没有 --check，用 --dry-run + --forward / --reverse
            extra = ["--dry-run"] if args.check else []
            extra += ["--reverse"] if args.revert else ["--forward"]
            rc, out = run(base + extra + ["-i", str(p)], target)

        if rc == 0:
            results.append((rel, "已撤销" if args.revert else ("可应用" if args.check else "已应用")))
            continue

        # 反向检查：能反向应用说明当前已经是补丁后的状态
        if name == "git-apply":
            rc2, _ = run(base + ["-R", "--check", str(p)], target)
        else:
            rc2, _ = run(base + ["--reverse", "--dry-run", "-i", str(p)], target)
        if rc2 == 0:
            results.append((rel, "已是补丁状态" if not args.revert else "未应用，无需撤销"))
        else:
            results.append((rel, "失败"))
            print(f"\n--- {rel} 失败 ---\n{out}\n", file=sys.stderr)

    width = max(len(r) for r, _ in results)
    print("结果：")
    for rel, status in results:
        print(f"  {rel.ljust(width)}  {status}")

    failed = [r for r, s in results if s == "失败"]
    if failed:
        print(
            "\n[失败] 常见原因：solaar 版本不是 1.1.20，或文件已被手工改动。\n"
            "       处理办法：pip install --force-reinstall --no-deps solaar==1.1.20 后重试。",
            file=sys.stderr,
        )
        return 1

    if not args.check and not args.revert:
        print("\n下一步验证：python src/bolt_manager.py --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
