#!/usr/bin/env python
"""无硬件自检：验证补丁指纹与导入链路，不需要插接收器（CI 用）。

用法：
    python tools/verify_patches.py

检查项：
    1. bolt_manager 可导入（gi 桩生效）
    2. hidapi_impl 含 Bolt 虚拟句柄
    3. base.write_register 认识 0x83 长寄存器读
    4. receiver.extract_serial 对 None 容错
    5. desktop_notifications 可导入且降级为「无通知」
"""
from __future__ import annotations

import inspect
import sys

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# 导入顺序有讲究：bolt_manager 必须最先，它负责安装 gi 桩，否则后面两个包导不进来
import bolt_manager  # noqa: E402,F401 - 导入即安装 gi 桩
import hidapi.hidapi_impl as hidapi_impl  # noqa: E402
from logitech_receiver import base  # noqa: E402
from logitech_receiver import receiver  # noqa: E402


def main() -> int:
    checks = [
        ("gi 桩已安装", bolt_manager._GI_STUB_INSTALLED),
        ("hidapi: Bolt 虚拟双句柄", hasattr(hidapi_impl, "_BoltSplitHandle")),
        ("base: 0x83 长寄存器读", "\\x83" in inspect.getsource(base.write)),
        ("receiver: extract_serial 容错 None", receiver.extract_serial(None) is None),
    ]

    try:
        from logitech_receiver import desktop_notifications

        checks.append(("notifications: 可导入且降级", desktop_notifications.available is False))
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] desktop_notifications 导入失败: {exc}", file=sys.stderr)
        checks.append(("notifications: 可导入且降级", False))

    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    if not ok:
        print("\n有检查未通过：补丁没有正确应用？先执行 python tools/apply_patches.py", file=sys.stderr)
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
