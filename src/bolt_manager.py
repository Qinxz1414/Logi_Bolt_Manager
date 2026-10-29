#!/usr/bin/env python
"""Logi Bolt 接收器管理器。

读取 Logi Bolt 接收器（USB 046D:C548）上已配对的设备清单，并可解除指定设备的配对。
依赖打完补丁的 Solaar 1.1.20（补丁见仓库 patches/ 目录）。

用法：
    python bolt_manager.py              # 打开图形界面
    python bolt_manager.py --list       # 命令行列出配对设备
    python bolt_manager.py --list --json  # 机器可读输出（脚本/CI 用）
    python bolt_manager.py --version
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

LOG = logging.getLogger("boltmgr")

# 源码运行时让 ctypes 能找到 build/hidapi/hidapi.dll（tools/fetch_hidapi.py 的产物）。
# 打包后的 exe 里 DLL 就在 _internal/，走应用目录搜索，无需此处理。
# 注意：Python 3.8+ 的 ctypes 默认安全加载模式不查 PATH，必须 add_dll_directory。
_dll_dir = Path(__file__).resolve().parents[1] / "build" / "hidapi"
if _dll_dir.is_dir():
    os.environ["PATH"] = str(_dll_dir) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(_dll_dir))
    except (AttributeError, OSError):  # 非 Windows 或目录不可用
        pass

# 不加这条，Bolt 的寄存器读写全部走不通（详见 docs/技术说明.md）
_GI_STUB_INSTALLED = False


def _install_gi_stub() -> bool:
    """注入假的 gi（PyGObject）模块，使 logitech_receiver 能在无 GTK 的 Windows 上导入。

    只覆盖 CLI / 数据层在导入期真正用到的符号；这不是 GTK 替代品，只是让
    `import gi.repository.Gdk` 之类的语句不会抛 ImportError。
    """
    global _GI_STUB_INSTALLED
    if _GI_STUB_INSTALLED or "gi" in sys.modules:
        return True

    import types

    class _ModifierType:
        SHIFT_MASK = 0x1
        CONTROL_MASK = 0x4
        MOD1_MASK = 0x8
        MOD4_MASK = 0x40

    class _Display:
        @staticmethod
        def get_default():
            return None

    class _Keymap:
        @staticmethod
        def get_for_display(display):
            return None

    class _GdkStub:
        Display = _Display
        Keymap = _Keymap
        ModifierType = _ModifierType

    class _GLibStub:
        @staticmethod
        def timeout_add_seconds(*args, **kwargs):
            return 0

        @staticmethod
        def timeout_add(*args, **kwargs):
            return 0

        @staticmethod
        def idle_add(*args, **kwargs):
            return 0

    gi_mod = types.ModuleType("gi")
    gi_mod.__version__ = "0.0.0-stub"
    gi_mod.require_version = lambda namespace, version: None

    repo_mod = types.ModuleType("gi.repository")
    repo_mod.Gdk = _GdkStub
    repo_mod.GLib = _GLibStub
    # 注意：这里**不能**给 repo_mod 设 __path__。Solaar 的通告模块用
    # importlib.util.find_spec("gi.repository.GLib") 探测 GTK 是否可用，
    # 依赖它在没有子模块时抛 ImportError 从而降级为「无通知」；
    # 若设了 __path__=[]，find_spec 会安静返回 None，Solaar 就以为 GTK 可用，
    # 紧接着 `from gi.repository import Gtk` 直接炸掉。
    gi_mod.repository = repo_mod

    sys.modules["gi"] = gi_mod
    sys.modules["gi.repository"] = repo_mod
    _GI_STUB_INSTALLED = True
    return True


_install_gi_stub()


class ReceiverNotFound(RuntimeError):
    """找不到（或无法打开）Logi Bolt 接收器。"""


# ---------------- 数据层 ----------------

def find_receiver():
    """找到 Bolt 接收器并创建 solaar 的 Receiver 对象。"""
    from logitech_receiver import base, receiver

    # Bolt 接收器在 MI_02 上暴露 Col01（短报文）与 Col02（长报文）两个集合，
    # 长报文集合才是 solaar 的入口。
    infos = [i for i in base.receivers() if "Col02" in i.path]
    if not infos:
        return None
    return receiver.create_receiver(base, infos[0])


def list_devices() -> list[dict]:
    """返回接收器上所有配对设备。

    每项为 {"slot": int, "name": str, "serial": str|None, "online": bool|None}
    online 为 None 表示设备状态读不出来（例如被其它厂商软件占用）。
    """
    r = find_receiver()
    if r is None:
        raise ReceiverNotFound("未找到 Logi Bolt 接收器，请确认接收器已插入 USB 口。")

    devices = []
    for d in r:
        try:
            online = bool(d.online)
        except Exception:  # noqa: BLE001 - 设备状态读取可能因驱动/占用而任意报错
            online = None
        devices.append(
            {
                "slot": d.number,
                # 接收器返回的名字/序列号带定长填充，去掉尾部空格
                "name": (d.name or "?").strip(),
                "serial": (d.serial or "").strip() or None,
                "online": online,
            }
        )
    devices.sort(key=lambda x: x["slot"])
    return devices


def unpair_device(slot: int) -> None:
    """解除指定槽位的配对（直接写接收器的 BOLT_PAIRING 寄存器）。"""
    r = find_receiver()
    if r is None:
        raise ReceiverNotFound("未找到 Logi Bolt 接收器。")
    r._unpair_device(slot, force=True)


def options_plus_running() -> bool:
    """Logi Options+ 会独占接收器，操作前最好先退出它。"""
    try:
        import psutil
    except ImportError:
        return False

    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower().startswith("logioptionsplus"):
                return True
        except Exception:  # noqa: BLE001 - 进程可能已退出
            continue
    return False


# ---------------- 命令行 ----------------

def _human_lines(devices: list[dict]) -> list[str]:
    if not devices:
        return ["接收器上没有已配对的设备。"]
    lines = []
    for d in devices:
        status = "未知" if d["online"] is None else ("在线" if d["online"] else "离线")
        serial = d["serial"] or "-"
        lines.append(f"槽位{d['slot']}  {d['name']}  序列号={serial}  {status}")
    return lines


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bolt_manager",
        description="读取 Logi Bolt 接收器上的配对设备，并可解除配对。不带参数则打开图形界面。",
    )
    parser.add_argument("--list", action="store_true", help="列出配对设备后退出")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（需配合 --list）")
    parser.add_argument("--debug", action="store_true", help="打印 solaar/hidapi 调试日志到 stderr")
    parser.add_argument("--version", action="version", version=f"BoltManager {__version__}")
    args = parser.parse_args(argv)

    if not args.list:
        if args.json:
            parser.error("--json 只能与 --list 一起使用")
        return -1  # 交给 GUI

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    if args.debug:
        logging.getLogger("logitech_receiver").setLevel(logging.DEBUG)

    try:
        devices = list_devices()
    except Exception as exc:  # noqa: BLE001 - CLI 顶层统一转成友好错误
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"错误: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, "devices": devices}, ensure_ascii=False, indent=2))
    else:
        for line in _human_lines(devices):
            print(line)
    return 0


# ---------------- 图形界面 ----------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        root.title(f"Logi Bolt 接收器管理器 v{__version__}")
        root.geometry("660x420")
        root.minsize(560, 340)

        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(
            top,
            text="接收器上的配对设备（解除配对不会损坏设备，之后可重新配对）",
        ).pack(anchor="w")

        cols = ("slot", "name", "serial", "online")
        headers = ("槽位", "设备名称", "序列号", "状态")
        widths = (60, 260, 160, 70)
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, headers, widths, strict=True):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c == "slot" else "w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.on_unpair())

        btns = ttk.Frame(root, padding=(10, 0, 10, 6))
        btns.pack(fill="x")
        self.btn_refresh = ttk.Button(btns, text="刷新", command=self.on_refresh)
        self.btn_refresh.pack(side="left")
        self.btn_unpair = ttk.Button(btns, text="解除所选设备配对", command=self.on_unpair)
        self.btn_unpair.pack(side="left", padx=8)
        ttk.Button(btns, text="退出", command=root.destroy).pack(side="right")

        self.status = tk.StringVar(value="正在读取接收器…")
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=(6, 3)).pack(
            fill="x", side="bottom"
        )

        if options_plus_running():
            self.set_status("⚠ 检测到 Logi Options+ 正在运行，可能占用接收器——建议先退出 Options+ 再操作。")

        self.poll_queue()
        self.on_refresh()

    # ----- 工具 -----
    def set_status(self, text: str) -> None:
        self.status.set(text)

    def run_bg(self, fn, done_tag: str) -> None:
        """在后台线程执行 fn（读写 HID 会阻塞），结果放进队列由主线程消费。"""

        def worker():
            try:
                self.q.put((done_tag, True, fn()))
            except Exception as exc:  # noqa: BLE001 - 后台线程不能抛到界面上
                LOG.debug("后台任务失败", exc_info=True)
                self.q.put((done_tag, False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_refresh.config(state=state)
        self.btn_unpair.config(state=state)

    def poll_queue(self) -> None:
        try:
            while True:
                tag, ok, payload = self.q.get_nowait()
                self.busy = False
                self._set_buttons(True)
                if tag == "refresh":
                    if ok:
                        self.fill(payload)
                        self.set_status(f"共 {len(payload)} 个配对设备。")
                    else:
                        self.set_status("读取失败：" + payload)
                elif tag == "unpair":
                    if ok:
                        self.set_status(f"已解除槽位 {payload} 的配对 ✔")
                        self.on_refresh()
                    else:
                        messagebox.showerror("解除配对失败", payload, parent=self.root)
                        self.set_status("解除配对失败。")
        except queue.Empty:
            pass
        self.root.after(120, self.poll_queue)

    def fill(self, devices: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        for d in devices:
            status = "未知" if d["online"] is None else ("在线" if d["online"] else "离线")
            self.tree.insert(
                "", "end", values=(d["slot"], d["name"], d["serial"] or "-", status)
            )

    # ----- 事件 -----
    def on_refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_buttons(False)
        self.set_status("正在读取接收器…")
        self.run_bg(list_devices, "refresh")

    def on_unpair(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择要解除配对的设备。", parent=self.root)
            return
        slot, name, serial, _ = self.tree.item(sel[0], "values")
        if not messagebox.askyesno(
            "确认解除配对",
            f"确定要解除槽位 {slot} 设备「{name}」(序列号 {serial}) 的配对吗？\n\n"
            "解除后该设备将无法通过此接收器连接，需重新配对才能使用。",
            parent=self.root,
        ):
            return
        if self.busy:
            return
        self.busy = True
        self._set_buttons(False)
        self.set_status(f"正在解除槽位 {slot} 的配对…")
        self.run_bg(lambda: (unpair_device(int(slot)), slot)[1], "unpair")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 控制台编码不可写时只做替换、不崩（例如 GBK 控制台遇到生僻字符）
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("logitech_receiver").setLevel(logging.ERROR)
    logging.getLogger("solaar").setLevel(logging.ERROR)

    rc = _cli(argv)
    if rc >= 0:
        return rc

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:  # noqa: BLE001 - 非 Windows 主题不存在时忽略
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
