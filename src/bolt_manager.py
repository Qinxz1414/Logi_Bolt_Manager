#!/usr/bin/env python
"""Logi Bolt 接收器管理器（GUI + CLI）。

读取 Logi Bolt 接收器（USB 046D:C548）上已配对的设备清单，并可解除指定设备的配对。
依赖打完补丁的 Solaar 1.1.20（补丁见仓库 patches/ 目录）。

用法：
    python bolt_manager.py                   # 打开图形界面
    python bolt_manager.py --list            # 命令行列出配对设备
    python bolt_manager.py --info            # 接收器 + 设备详情
    python bolt_manager.py --list --json     # 机器可读输出（脚本/CI 用）
    python bolt_manager.py --unpair 3 --yes  # 解除槽位 3 的配对
    python bolt_manager.py --watch 2         # 持续监控设备增减
    python bolt_manager.py --version
"""

from __future__ import annotations

__version__ = "1.1.0"

import argparse
import json
import logging
import queue
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import bolt_core as core

LOG = logging.getLogger("boltmgr")

AUTO_REFRESH_CHOICES = {
    "关闭": 0,
    "5 秒": 5,
    "15 秒": 15,
    "30 秒": 30,
    "60 秒": 60,
}


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def _configure_logging(debug: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING)
    level = logging.DEBUG if debug else logging.ERROR
    for name in ("logitech_receiver", "solaar", "hidapi"):
        logging.getLogger(name).setLevel(level)


def _print_error(message: str) -> None:
    print(f"错误: {message}", file=sys.stderr)


def _cmd_list(as_json: bool) -> int:
    try:
        info = core.snapshot()
    except core.BoltError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            _print_error(str(exc))
        return 1

    if as_json:
        print(core.to_json(info))
    else:
        print(core.format_devices(info.devices))
    return 0


def _cmd_info(as_json: bool) -> int:
    try:
        info = core.snapshot()
    except core.BoltError as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            _print_error(str(exc))
        return 1

    if as_json:
        print(core.to_json(info))
        return 0

    print(core.format_snapshot(info))
    print(f"  接口  {info.path or '—'}")
    print(f"  槽位  已配对 {info.paired}，上限 {info.max_devices or '—'}，剩余可配对 {info.remaining_label}")
    conflicts = core.conflicting_processes()
    if conflicts:
        print(f"  注意  这些程序正在运行，可能占用接收器：{', '.join(conflicts)}")
    return 0


def _cmd_unpair(slot: int, assume_yes: bool, as_json: bool) -> int:
    try:
        info = core.snapshot()
    except core.BoltError as exc:
        return _fail(exc, as_json)

    target = next((d for d in info.devices if d.slot == slot), None)
    if target is None:
        return _fail(
            core.DeviceNotFound(
                f"槽位 {slot} 上没有已配对的设备。当前已配对槽位："
                f"{', '.join(str(d.slot) for d in info.devices) or '无'}"
            ),
            as_json,
        )

    if not assume_yes:
        prompt = f"确定解除槽位 {slot}「{target.name}」(序列号 {target.serial or '-'}) 的配对吗？[y/N] "
        if not sys.stdin or not sys.stdin.isatty():
            return _fail(core.BoltError("非交互环境下需要显式加 --yes 才会执行解除配对。"), as_json)
        try:
            answer = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消。")
            return 1

    try:
        core.unpair_device(slot)
    except core.BoltError as exc:
        return _fail(exc, as_json)

    if as_json:
        payload = {"ok": True, "unpaired": slot, "name": target.name, "serial": target.serial}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"已解除槽位 {slot}「{target.name}」的配对。")
    return 0


def _fail(exc: Exception, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
    else:
        _print_error(str(exc))
    return 1


def _cmd_watch(interval: float, as_json: bool) -> int:
    """持续轮询，只在发生变化时输出（Ctrl+C 退出）。"""
    interval = max(interval, 0.5)
    last: dict[int, tuple[str, bool | None]] | None = None

    def emit(text: str, payload: dict | None = None) -> None:
        if as_json:
            print(json.dumps(payload or {"event": text}, ensure_ascii=False), flush=True)
        else:
            print(text, flush=True)

    print(f"每 {interval:g} 秒读取一次接收器，Ctrl+C 退出。", file=sys.stderr, flush=True)
    try:
        while True:
            stamp = time.strftime("%H:%M:%S")
            try:
                info = core.snapshot()
            except core.BoltError as exc:
                emit(f"[{stamp}] 错误: {exc}", {"time": stamp, "event": "error", "error": str(exc)})
                time.sleep(interval)
                continue

            current = {d.slot: (d.name, d.online) for d in info.devices}
            if last is None:
                emit(f"[{stamp}] 接收器 {info.serial or '—'}，已配对 {len(current)} 台")
                for d in info.devices:
                    emit(f"[{stamp}]  + 槽位{d.slot} {d.name} ({d.kind_label}) {d.status_label}")
            else:
                for slot, (name, online) in current.items():
                    if slot not in last:
                        emit(f"[{stamp}]  + 新增设备：槽位{slot} {name} {_online_text(online)}")
                    elif last[slot][1] != online:
                        emit(f"[{stamp}]  ~ 槽位{slot} {name} 状态 → {_online_text(online)}")
                    elif last[slot][0] != name:
                        emit(f"[{stamp}]  ~ 槽位{slot} 名称 {last[slot][0]} → {name}")
                for slot, (name, _online) in last.items():
                    if slot not in current:
                        emit(f"[{stamp}]  - 移除设备：槽位{slot} {name}")
            last = current
            time.sleep(interval)
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 0


def _online_text(online: bool | None) -> str:
    if online is None:
        return "状态未知"
    return "在线" if online else "离线"


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bolt_manager",
        description="读取 Logi Bolt 接收器上的配对设备，并可解除配对。不带参数则打开图形界面。",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="列出配对设备后退出")
    mode.add_argument("--info", action="store_true", help="显示接收器信息与设备详情后退出")
    mode.add_argument("--unpair", type=int, metavar="SLOT", help="解除指定槽位的配对")
    mode.add_argument(
        "--watch",
        type=float,
        nargs="?",
        const=2.0,
        metavar="SECONDS",
        help="持续监控设备增减（默认每 2 秒），Ctrl+C 退出",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（可与 --list/--info/--unpair 组合）")
    parser.add_argument("-y", "--yes", action="store_true", help="解除配对时不询问确认（脚本用）")
    parser.add_argument("--debug", action="store_true", help="打印 solaar/hidapi 调试日志到 stderr")
    parser.add_argument("--version", action="version", version=f"BoltManager {__version__}")
    args = parser.parse_args(argv)

    if args.watch is not None:
        _configure_logging(args.debug)
        return _cmd_watch(args.watch, args.json)

    if not (args.list or args.info or args.unpair is not None):
        if args.json or args.yes:
            parser.error("--json/--yes 需要与 --list、--info、--unpair 或 --watch 一起使用")
        return -1  # 交给 GUI

    _configure_logging(args.debug)

    if args.unpair is not None:
        return _cmd_unpair(args.unpair, args.yes, args.json)
    if args.info:
        return _cmd_info(args.json)
    return _cmd_list(args.json)


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #
def _enable_high_dpi() -> None:
    """开启 DPI 感知，避免高分屏下整个界面被系统拉伸成模糊位图。

    必须在创建 Tk 之前调用：Tk 会在初始化时读一次系统 DPI 作为缩放基准。
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # 1 = SYSTEM_DPI_AWARE（Win8.1+）。per-monitor 需要额外响应 WM_DPICHANGED，
        # Tk 并不处理，用了反而会在跨屏拖动时错乱。
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        return
    except Exception:  # noqa: BLE001 - Win7 没有 shcore
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _asset_path(name: str) -> Path | None:
    """定位随程序分发的资源（源码运行与 PyInstaller 打包两种情况）。"""
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / name)
    candidates.append(Path(__file__).resolve().parent / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _interface_text(path: str | None) -> str:
    """从 HID 路径里取出接口号，例如 ...&MI_02&Col02#... → MI_02。"""
    if not path:
        return "—"
    match = re.search(r"MI_(\d+)", path)
    return f"MI_{match.group(1)}" if match else "—"


def _message_box(text: str, title: str = core.APP_NAME, flags: int = 0x40) -> None:
    """不依赖 Tk 的提示框（用于 Tk 还没起来 / 单实例锁冲突的场景）。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, flags)  # type: ignore[attr-defined]
    else:
        print(f"{title}: {text}", file=sys.stderr)


class App:
    def __init__(self, root: tk.Tk, settings: dict):
        self.root = root
        self.settings = settings
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.info: core.ReceiverInfo | None = None
        self._auto_after: str | None = None
        self._closing = False

        self._build_ui()
        self._apply_geometry()
        self._restore_auto_refresh()
        self._schedule_auto()
        self._warn_conflicts()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(80, self._poll_queue)
        self.refresh()

    # ---------------- 构建界面 ----------------
    def _build_ui(self) -> None:
        root = self.root
        root.title(f"Logi Bolt 接收器管理器 v{__version__}")
        root.minsize(760, 500)
        icon = _asset_path("bolt.ico")
        if icon:
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        # 先占住底部状态栏，再让主内容填充剩余空间（顺序反了状态栏会盖住工具栏）
        self.status = tk.StringVar(value="正在读取接收器…")
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 4)).pack(
            fill="x", side="bottom"
        )

        outer = ttk.Frame(root, padding=(12, 12, 12, 8))
        outer.pack(fill="both", expand=True)

        # 接收器信息条
        head = ttk.LabelFrame(outer, text="接收器", padding=(10, 6, 10, 8))
        head.pack(fill="x")
        self.var_head = tk.StringVar(value="正在读取…")
        ttk.Label(head, textvariable=self.var_head, font=("Segoe UI", 10)).pack(anchor="w")
        self.var_sub = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.var_sub, foreground="#666").pack(anchor="w", pady=(2, 0))

        # 主体：左表格 + 右详情
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(8, 8))

        detail = ttk.LabelFrame(body, text="设备详情", padding=(10, 8), width=200)
        detail.pack(side="right", fill="y", padx=(8, 0))
        detail.pack_propagate(False)
        self._detail_vars: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(
            (
                ("name", "名称"),
                ("kind", "类型"),
                ("slot", "槽位"),
                ("serial", "序列号"),
                ("wpid", "产品 ID"),
                ("status", "连接"),
            )
        ):
            ttk.Label(detail, text=label, foreground="#888").grid(row=row * 2, column=0, sticky="w")
            var = tk.StringVar(value="—")
            self._detail_vars[key] = var
            ttk.Label(detail, textvariable=var, wraplength=180, justify="left").grid(
                row=row * 2 + 1, column=0, sticky="w", pady=(0, 6)
            )

        tree_wrap = ttk.Frame(body)
        tree_wrap.pack(side="left", fill="both", expand=True)
        cols = ("slot", "name", "kind", "serial", "online")
        headers = ("槽位", "设备名称", "类型", "序列号", "状态")
        widths = (56, 230, 70, 150, 60)
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=9, selectmode="browse")
        for c, h, w in zip(cols, headers, widths, strict=True):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, minwidth=44, anchor="center" if c in ("slot", "kind", "online") else "w")
        self.tree.tag_configure("offline", foreground="#999")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self.on_unpair())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.tree.bind("<Delete>", lambda _e: self.on_unpair())

        # 工具栏
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        self.btn_refresh = ttk.Button(bar, text="刷新", width=8, command=self.refresh)
        self.btn_refresh.pack(side="left")
        self.btn_unpair = ttk.Button(bar, text="解除配对", width=11, command=self.on_unpair)
        self.btn_unpair.pack(side="left", padx=(6, 0))
        self.btn_copy = ttk.Button(bar, text="复制", width=8, command=self.on_copy)
        self.btn_copy.pack(side="left", padx=(6, 0))
        self.btn_export = ttk.Button(bar, text="导出 CSV", width=10, command=self.on_export)
        self.btn_export.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="退出", width=8, command=self.on_close).pack(side="right")

        self.cb_auto = ttk.Combobox(bar, state="readonly", width=6, values=list(AUTO_REFRESH_CHOICES))
        self.cb_auto.pack(side="right", padx=(6, 0))
        self.cb_auto.bind("<<ComboboxSelected>>", lambda _e: self.on_auto_changed())
        ttk.Label(bar, text="自动刷新").pack(side="right")

    # ---------------- 设置 / 窗口状态 ----------------
    def _apply_geometry(self) -> None:
        geometry = str(self.settings.get("geometry") or "")
        safe = self._safe_geometry(geometry)
        if safe:
            try:
                self.root.geometry(safe)
            except tk.TclError:
                pass

    def _safe_geometry(self, geometry: str) -> str:
        """校验记忆的窗口尺寸/位置，越界（换显示器、分辨率变小）时退回默认值。"""
        if not geometry:
            return ""
        size = geometry.split("+")[0].split("-")[0]
        try:
            w, h = (int(v) for v in size.split("x"))
        except ValueError:
            return ""
        if not (560 <= w <= 4000 and 380 <= h <= 3000):
            return ""
        pos = geometry[len(size) :]
        if not pos:
            return size

        def _coord(text: str, sign: str) -> int | None:
            if not text.startswith(sign):
                return None
            try:
                return int(text[1:])
            except ValueError:
                return None

        match = re.fullmatch(r"([+-]\d+)([+-]\d+)", pos)
        if not match:
            return size
        x = _coord(match.group(1), "+") or _coord(match.group(1), "-")
        y = _coord(match.group(2), "+") or _coord(match.group(2), "-")
        if x is None or y is None:
            return size
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 允许负坐标（副屏在左/上），但至少留 120px 在可见区域内
        if x > screen_w - 120 or y > screen_h - 80 or x < -w + 120 or y < -40:
            return size
        return geometry

    def _restore_auto_refresh(self) -> None:
        seconds = int(self.settings.get("auto_refresh_seconds") or 0)
        label = next((k for k, v in AUTO_REFRESH_CHOICES.items() if v == seconds), "关闭")
        self.cb_auto.set(label)

    def _warn_conflicts(self) -> None:
        conflicts = core.conflicting_processes()
        if not conflicts:
            return
        names = ", ".join(conflicts[:3]) + ("…" if len(conflicts) > 3 else "")
        self.set_status(f"提示：检测到 {names} 在运行。若读取或解除配对失败，请先退出它再重试。")

    def on_auto_changed(self) -> None:
        seconds = AUTO_REFRESH_CHOICES.get(self.cb_auto.get(), 0)
        self.settings["auto_refresh_seconds"] = seconds
        core.save_settings(self.settings)
        self._schedule_auto()
        self.set_status("自动刷新已关闭。" if seconds == 0 else f"自动刷新：每 {seconds} 秒。")

    def _schedule_auto(self) -> None:
        if self._auto_after is not None:
            try:
                self.root.after_cancel(self._auto_after)
            except tk.TclError:
                pass
            self._auto_after = None
        seconds = int(self.settings.get("auto_refresh_seconds") or 0)
        if seconds > 0 and not self._closing:
            self._auto_after = self.root.after(seconds * 1000, self._auto_tick)

    def _auto_tick(self) -> None:
        self._auto_after = None
        if self._closing:
            return
        if not self.busy:
            self.refresh(quiet=True)
        self._schedule_auto()

    # ---------------- 工具 ----------------
    def set_status(self, text: str) -> None:
        self.status.set(text)

    def run_bg(self, fn, tag: str) -> None:
        """在后台线程执行 fn（读写 HID 会阻塞），结果放进队列由主线程消费。"""

        def worker():
            try:
                self.q.put((tag, True, fn()))
            except core.BoltError as exc:
                LOG.debug("后台任务失败: %s", exc)
                self.q.put((tag, False, str(exc)))
            except Exception as exc:  # noqa: BLE001 - 后台线程不能抛到界面上
                LOG.debug("后台任务异常", exc_info=True)
                self.q.put((tag, False, f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_refresh, self.btn_unpair, self.btn_copy, self.btn_export):
            btn.config(state=state)

    def _poll_queue(self) -> None:
        try:
            while True:
                tag, ok, payload = self.q.get_nowait()
                self._set_busy(False)
                if tag == "refresh":
                    if ok:
                        self._render(payload)
                        stamp = time.strftime("%H:%M:%S")
                        self.set_status(f"共 {len(payload.devices)} 台配对设备 · 最后刷新 {stamp}")
                    else:
                        self._render_failure(payload)
                elif tag == "unpair":
                    if ok:
                        self.set_status(f"已解除槽位 {payload} 的配对 ✔")
                        self.refresh(quiet=True)
                    else:
                        messagebox.showerror("解除配对失败", payload, parent=self.root)
                        self.set_status("解除配对失败。")
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(80, self._poll_queue)

    # ---------------- 渲染 ----------------
    def _render(self, info: core.ReceiverInfo) -> None:
        self.info = info
        serial = info.serial or "—"
        short = serial if len(serial) <= 16 else serial[:12] + "…"
        self.var_head.set(f"{info.name}　序列号 {short}　已配对 {info.paired}/{info.max_devices or '?'}")
        self.var_sub.set(f"剩余可配对 {info.remaining_label}　·　接口 {_interface_text(info.path)}")

        keep = self.selected_slot()
        self.tree.delete(*self.tree.get_children())
        for d in info.devices:
            tags = () if d.online is not False else ("offline",)
            iid = str(d.slot)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(d.slot, d.name, d.kind_label, d.serial or "-", d.status_label),
                tags=tags,
            )
        if keep is not None and self.tree.exists(str(keep)):
            self.tree.selection_set(str(keep))
            self.tree.see(str(keep))
        elif self.tree.get_children():
            # 首次刷新后默认选中第一行，让右侧详情面板有内容
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
        else:
            self._show_detail()

    def _render_failure(self, message: str) -> None:
        self.info = None
        self.var_head.set("接收器不可用")
        self.var_sub.set(message.splitlines()[0] if message else "")
        self.tree.delete(*self.tree.get_children())
        self._show_detail()
        self.set_status("读取失败：" + message.replace("\n", " "))

    def _show_detail(self) -> None:
        slot = self.selected_slot()
        device = None
        if slot is not None and self.info:
            device = next((d for d in self.info.devices if d.slot == slot), None)

        values = {
            "name": device.name if device else "—",
            "kind": device.kind_label if device else "—",
            "slot": str(device.slot) if device else "—",
            "serial": (device.serial or "—") if device else "—",
            "wpid": (device.wpid or "—") if device else "—",
            "status": device.status_label if device else "—",
        }
        for key, value in values.items():
            self._detail_vars[key].set(value)

    def selected_slot(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    # ---------------- 动作 ----------------
    def refresh(self, quiet: bool = False) -> None:
        if self.busy:
            return
        self._set_busy(True)
        if not quiet:
            self.set_status("正在读取接收器…")
        self.run_bg(core.snapshot, "refresh")

    def on_unpair(self) -> None:
        slot = self.selected_slot()
        if slot is None:
            messagebox.showinfo("提示", "请先在列表中选择要解除配对的设备。", parent=self.root)
            return
        device = next((d for d in (self.info.devices if self.info else []) if d.slot == slot), None)
        name = device.name if device else "?"
        serial = (device.serial if device else None) or "-"
        if not messagebox.askyesno(
            "确认解除配对",
            f"确定要解除槽位 {slot} 设备「{name}」(序列号 {serial}) 的配对吗？\n\n"
            "解除后该设备将无法通过此接收器连接，需重新配对才能使用。",
            parent=self.root,
            icon="warning",
        ):
            return
        if self.busy:
            return
        self._set_busy(True)
        self.set_status(f"正在解除槽位 {slot} 的配对…")
        self.run_bg(lambda: core.unpair_device(slot), "unpair")

    def on_copy(self) -> None:
        if not self.info or not self.info.devices:
            self.set_status("没有可复制的设备。")
            return
        text = core.to_tsv(self.info.devices)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"已复制 {len(self.info.devices)} 行到剪贴板。")

    def on_export(self) -> None:
        if not self.info or not self.info.devices:
            self.set_status("没有可导出的设备。")
            return
        default = f"bolt-devices-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出设备列表",
            defaultextension=".csv",
            initialfile=default,
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            # utf-8-sig：Excel 直接双击打开不会乱码
            Path(path).write_text(core.to_csv(self.info.devices), encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.set_status(f"已导出到 {path}")

    def on_close(self) -> None:
        self._closing = True
        try:
            self.settings["geometry"] = self.root.winfo_geometry()
        except tk.TclError:
            pass
        core.save_settings(self.settings)
        self.root.destroy()


def _gui() -> int:
    _enable_high_dpi()

    settings = core.load_settings()
    if not core.acquire_single_instance():
        _message_box(
            "已经有一个 BoltManager 窗口在运行。\n\n"
            "两个实例同时读写接收器会互相打断，请先关闭正在运行的那个。",
            "BoltManager 已在运行",
        )
        return 1

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass  # 非 Windows 主题不存在时忽略
    App(root, settings)
    root.mainloop()
    return 0


def _configure_stdio() -> None:
    """统一输出编码。

    管道/重定向时强制 UTF-8：否则 Windows 会按本地代码页（GBK）写出，脚本和 CI 解析
    `--json` 时会拿到非法 UTF-8。直接连控制台时保持系统编码不动，避免中文变乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            else:
                stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    _configure_stdio()

    logging.basicConfig(level=logging.WARNING)
    for name in ("logitech_receiver", "solaar"):
        logging.getLogger(name).setLevel(logging.ERROR)

    rc = _cli(argv)
    if rc >= 0:
        return rc
    return _gui()


if __name__ == "__main__":
    raise SystemExit(main())
