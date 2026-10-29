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

__version__ = "1.3.3"

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


def _cmd_pair(timeout: int, as_json: bool) -> int:
    """交互式配对：进度写 stderr，结果写 stdout（管道下 JSON 可解析）。"""

    def notify(event: str, data: dict) -> None:
        text = core.pair_event_text(event, data)
        if text:
            print(text, file=sys.stderr, flush=True)

    try:
        dev = core.pair_device(notify, timeout=timeout)
    except core.BoltError as exc:
        return _fail(exc, as_json)

    if as_json:
        print(json.dumps({"ok": True, "paired": dev.as_dict()}, ensure_ascii=False, indent=2))
    else:
        print(f"已配对：槽位 {dev.slot} {dev.name}（{dev.kind_label}，序列号 {dev.serial or '-'}）")
    return 0


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
    mode.add_argument("--pair", action="store_true", help="配对一台新设备（按提示在设备上操作）")
    mode.add_argument(
        "--watch",
        type=float,
        nargs="?",
        const=2.0,
        metavar="SECONDS",
        help="持续监控设备增减（默认每 2 秒），Ctrl+C 退出",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（可与 --list/--info/--unpair 组合）")
    parser.add_argument("--timeout", type=int, default=30, metavar="SECONDS", help="--pair 的等待时长（默认 30 秒）")
    parser.add_argument("-y", "--yes", action="store_true", help="解除配对时不询问确认（脚本用）")
    parser.add_argument("--debug", action="store_true", help="打印 solaar/hidapi 调试日志到 stderr")
    parser.add_argument("--version", action="version", version=f"BoltManager {__version__}")
    args = parser.parse_args(argv)

    if args.watch is not None:
        _configure_logging(args.debug)
        return _cmd_watch(args.watch, args.json)

    if not (args.list or args.info or args.unpair is not None or args.pair):
        if args.json or args.yes or args.timeout != 30:
            parser.error("--json/--yes/--timeout 需要与 --list、--info、--unpair、--pair 或 --watch 一起使用")
        return -1  # 交给 GUI

    _configure_logging(args.debug)

    if args.unpair is not None:
        return _cmd_unpair(args.unpair, args.yes, args.json)
    if args.pair:
        return _cmd_pair(args.timeout, args.json)
    if args.info:
        return _cmd_info(args.json)
    return _cmd_list(args.json)


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #
# 窗口尺寸设计（按 96 DPI 标准物理像素计，实际运行时按系统 DPI 缩放）：
# 实测完整 UI 最小需求 857x603（工具栏按钮组 + 设备表 5 列 + 详情面板 238px），
# 最小尺寸须 ≥ 该需求，否则控件被裁剪；默认尺寸在需求之上留出呼吸空间。
MIN_WINDOW = (880, 620)
DEFAULT_WINDOW = (980, 640)


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
        self._ui_scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        icon = _asset_path("bolt.ico")
        if icon:
            try:
                root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

        # 先占住底部状态栏，再让主内容填充剩余空间（顺序反了状态栏会盖住工具栏）
        self.status = tk.StringVar(value="正在读取接收器…")
        self._status_lbl = ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 4))
        self._status_lbl.pack(fill="x", side="bottom")

        # 冲突进程警告条：检测到 Logi Options+ / G HUB 等独占接收器的软件时显示。
        # 平时 pack_forget 隐藏；显示时用 before=outer 插在状态栏之上、主内容之下。
        self._conflict_names: list[str] = []
        self.var_conflict = tk.StringVar(value="")
        self.conflict_bar = ttk.Frame(root, padding=(12, 6))
        ttk.Label(
            self.conflict_bar,
            text="⚠",
            foreground="#9a6700",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(0, 6))
        # 文字不 expand（否则按钮被推到最右，窗口窄时挤出可视区），按钮紧跟文字之后；
        # wraplength 限制文字最大宽度，进程名很长时换行而不是把按钮挤出去
        ttk.Label(
            self.conflict_bar,
            textvariable=self.var_conflict,
            foreground="#9a6700",
            wraplength=520,
            justify="left",
        ).pack(side="left")
        self.btn_kill_conflicts = ttk.Button(
            self.conflict_bar, text="结束相关进程", width=14, command=self.on_kill_conflicts
        )
        self.btn_kill_conflicts.pack(side="left", padx=(8, 0))

        outer = ttk.Frame(root, padding=(12, 12, 12, 8))
        outer.pack(fill="both", expand=True)
        self._outer_frame = outer

        # 接收器信息条
        head = ttk.LabelFrame(outer, text="接收器", padding=(10, 6, 10, 8))
        head.pack(fill="x")
        self.var_head = tk.StringVar(value="正在读取…")
        ttk.Label(head, textvariable=self.var_head, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.var_sub = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.var_sub, foreground="#666", font=("Segoe UI", 9)).pack(
            anchor="w", pady=(3, 0)
        )

        # 主体：左表格 + 右详情
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(8, 8))

        # 详情面板：显式锁定宽高并关掉传播。不锁的话，不同设备的文本宽度不同，
        # 面板请求尺寸跟着变，整个窗口会随选择"跳动"——这是布局稳定性的关键。
        # 注意：面板内部子控件用 grid 管理，必须调 grid_propagate(False)，
        # pack_propagate 对 grid 布局的子控件不生效。
        detail = ttk.LabelFrame(body, text="设备详情", padding=(12, 10), width=238, height=380)
        detail.pack(side="right", fill="y", padx=(10, 0))
        detail.grid_propagate(False)
        detail.pack_propagate(False)
        detail.grid_columnconfigure(1, weight=1, minsize=150)
        self._detail_vars: dict[str, tk.StringVar] = {}
        self._detail_status_lbl: ttk.Label | None = None
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
            ttk.Label(detail, text=label, foreground="#777").grid(
                row=row, column=0, sticky="nw", padx=(0, 10), pady=(0, 10)
            )
            var = tk.StringVar(value="—")
            self._detail_vars[key] = var
            # 序列号/产品 ID 是定宽十六进制，用等宽字体更易读
            mono = key in ("serial", "wpid")
            lbl = ttk.Label(
                detail,
                textvariable=var,
                wraplength=130,
                justify="left",
                anchor="nw",
                font=("Consolas", 9) if mono else None,
            )
            lbl.grid(row=row, column=1, sticky="new", pady=(0, 10))
            if key == "status":
                self._detail_status_lbl = lbl

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
        self.btn_pair = ttk.Button(bar, text="添加设备…", width=11, command=self.on_pair)
        self.btn_pair.pack(side="left", padx=(6, 0))
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
        s = self._ui_scale
        min_w, min_h = round(MIN_WINDOW[0] * s), round(MIN_WINDOW[1] * s)
        self.root.minsize(min_w, min_h)
        geometry = str(self.settings.get("geometry") or "")
        safe = self._safe_geometry(geometry, min_w, min_h)
        try:
            self.root.geometry(safe)
        except tk.TclError:
            pass

    def _default_geometry(self) -> str:
        """无有效记忆尺寸时使用：按 DPI 缩放的默认尺寸，位置交给窗口管理器。"""
        s = self._ui_scale
        return f"{round(DEFAULT_WINDOW[0] * s)}x{round(DEFAULT_WINDOW[1] * s)}"

    def _safe_geometry(self, geometry: str, min_w: int, min_h: int) -> str:
        """校验记忆的窗口尺寸/位置：无效退回默认；偏小则钳制到最小尺寸（不拒绝，\
        否则旧版本保存的小尺寸会永远生效）；位置越界（换显示器、分辨率变小）时丢弃位置。"""
        match = re.fullmatch(r"(\d+)x(\d+)((?:[+-]\d+){0,2})", geometry or "")
        if not match:
            return self._default_geometry()
        w, h, pos = int(match.group(1)), int(match.group(2)), match.group(3)
        # 旧版默认尺寸（小于真实内容需求）视为未设置，升级后应用新默认
        if (w, h) == (840, 560):
            return self._default_geometry()
        # 尺寸钳制：小于最小尺寸（旧版默认/换小屏）时抬到最小，过大视为损坏
        if w > 4000 or h > 3000:
            return self._default_geometry()
        w, h = max(w, min_w), max(h, min_h)

        if not pos:
            return f"{w}x{h}"
        parts = re.findall(r"[+-]\d+", pos)
        try:
            x = int(parts[0])
            y = int(parts[1]) if len(parts) > 1 else None
        except (IndexError, ValueError):
            return f"{w}x{h}"
        if y is None:
            return f"{w}x{h}{x:+d}"
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 允许负坐标（副屏在左/上），但至少留 120px 在可见区域内
        if x > screen_w - 120 or y > screen_h - 80 or x < -w + 120 or y < -40:
            return f"{w}x{h}"
        return f"{w}x{h}{x:+d}{y:+d}"

    def _restore_auto_refresh(self) -> None:
        seconds = int(self.settings.get("auto_refresh_seconds") or 0)
        label = next((k for k, v in AUTO_REFRESH_CHOICES.items() if v == seconds), "关闭")
        self.cb_auto.set(label)

    def _warn_conflicts(self) -> None:
        """检测独占接收器的官方软件，显示/隐藏底部警告条。"""
        self._conflict_names = core.conflicting_processes()
        names = ", ".join(self._conflict_names[:2]) + ("…" if len(self._conflict_names) > 2 else "")
        if self._conflict_names:
            total = f"（共 {len(self._conflict_names)} 个进程）" if len(self._conflict_names) > 1 else ""
            self.var_conflict.set(f"检测到 {names} {total}正在运行，可能独占接收器导致读取/配对失败。")
            self.conflict_bar.pack(side="bottom", fill="x", before=self._outer_frame)
        else:
            self.conflict_bar.pack_forget()

    def on_kill_conflicts(self) -> None:
        """结束冲突进程（后台线程执行，完成后自动刷新）。"""
        if not self._conflict_names or self.busy:
            return
        self.btn_kill_conflicts.config(state="disabled")
        self.set_status("正在结束冲突进程…")
        self.run_bg(core.kill_conflicting_processes, "kill_conflicts")

    def _finish_kill_conflicts(self, killed: list[str], remaining: list[str]) -> None:
        self.btn_kill_conflicts.config(state="normal")
        if remaining:
            names = ", ".join(remaining[:3]) + ("…" if len(remaining) > 3 else "")
            self.set_status(f"部分进程结束失败（可能需要管理员权限）：{names}。")
            self._warn_conflicts()
            return
        self._warn_conflicts()
        self.set_status(f"已结束 {len(killed)} 个冲突进程 ✔ 正在重新读取接收器…")
        self.refresh(quiet=True)

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
        if not busy:
            self._update_button_states()
            return
        for btn in (self.btn_refresh, self.btn_unpair, self.btn_pair, self.btn_copy, self.btn_export):
            btn.config(state="disabled")

    def _update_button_states(self) -> None:
        """按当前接收器的能力开关按钮（Nano 不能解配对、27MHz 不能配对等）。"""
        info = self.info
        can_unpair = bool(info and info.can_unpair)
        can_pair = bool(info and info.can_pair)
        self.btn_unpair.config(state="normal" if can_unpair else "disabled")
        self.btn_pair.config(state="normal" if can_pair else "disabled")

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
                    # 每次刷新后重查冲突进程（用户可能中途启动/退出了 Options+）
                    self._warn_conflicts()
                elif tag == "kill_conflicts":
                    self._finish_kill_conflicts(payload[0], payload[1])
                elif tag == "pair_progress":
                    self._update_pair_dialog(payload)
                elif tag == "pair":
                    self._finish_pair(ok, payload)
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
        # 序列号完整显示，不再截断——这正是用户排查设备时最需要的字段
        self.var_head.set(f"{info.name}　已配对 {info.paired}/{info.max_devices or '?'}")
        self.var_sub.set(
            f"{info.kind_label}　·　序列号 {info.serial or '—'}　·　剩余可配对 {info.remaining_label}"
            f"　·　接口 {_interface_text(info.path)}"
        )

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
        # _set_busy(False) 在 _render 之前执行，能力按钮要按新数据再刷一次
        self._update_button_states()

    def _render_failure(self, message: str) -> None:
        self.info = None
        self.var_head.set("接收器不可用")
        self.var_sub.set(message.splitlines()[0] if message else "")
        self.tree.delete(*self.tree.get_children())
        self._show_detail()
        self._update_button_states()
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
        # 连接状态着色：在线绿、离线灰，一眼可辨
        if self._detail_status_lbl is not None:
            color = {"在线": "#1a7f37", "离线": "#999999"}.get(values["status"], "#666666")
            self._detail_status_lbl.config(foreground=color)

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

    # ---------------- 添加设备（配对向导） ----------------
    def on_pair(self) -> None:
        if self.busy:
            return
        if not (self.info and self.info.can_pair):
            messagebox.showinfo("提示", "当前接收器不支持配对新设备。", parent=self.root)
            return
        self._pair_cancel = threading.Event()
        self._pair_name: str | None = None

        dialog = tk.Toplevel(self.root)
        dialog.title("添加设备")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._pair_dialog = dialog

        frame = ttk.Frame(dialog, padding=(18, 16))
        frame.pack(fill="both", expand=True)
        self.var_pair_step = tk.StringVar(value="正在准备配对…")
        ttk.Label(frame, textvariable=self.var_pair_step, wraplength=340, justify="left", font=(
            "Segoe UI", 10,
        )).pack(anchor="w")
        self.var_pair_detail = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.var_pair_detail, wraplength=340, justify="left", foreground="#666").pack(
            anchor="w", pady=(10, 0)
        )
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="取消", width=10, command=self._cancel_pair).pack(side="right")

        self._set_busy(True)
        self.set_status("配对进行中，请在设备上操作…")
        self.run_bg(
            lambda: core.run_pairing(
                core.ReceiverSession(), notify=self._pair_notify, cancel=self._pair_cancel
            ),
            "pair",
        )

    def _pair_notify(self, event: str, data: dict) -> None:
        """配对线程的进度回调（线程安全：只往队列里放）。"""
        self.q.put(("pair_progress", True, (event, data)))

    def _update_pair_dialog(self, payload) -> None:
        event, data = payload
        if event == "found":
            self._pair_name = data.get("name")
        text = core.pair_event_text(event, data)
        if not text:
            return
        if event == "discover":
            self.var_pair_step.set(text)
        else:
            self.var_pair_detail.set(text)
            if event == "passkey":
                self.var_pair_step.set(f"发现设备：{self._pair_name or '新设备'}，按提示完成认证")

    def _cancel_pair(self) -> None:
        if getattr(self, "_pair_cancel", None):
            self._pair_cancel.set()

    def _finish_pair(self, ok: bool, payload) -> None:
        cancelled = bool(getattr(self, "_pair_cancel", None) and self._pair_cancel.is_set())
        dialog = getattr(self, "_pair_dialog", None)
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
            self._pair_dialog = None
        if ok:
            name = payload.name if hasattr(payload, "name") else "?"
            slot = payload.slot if hasattr(payload, "slot") else "?"
            self.set_status(f"已配对槽位 {slot}「{name}」✔")
            messagebox.showinfo("添加设备", f"配对成功：槽位 {slot}「{name}」", parent=self.root)
            self.refresh(quiet=True)
        elif cancelled:
            self.set_status("已取消配对。")
        else:
            messagebox.showerror("添加设备失败", payload, parent=self.root)
            self.set_status("配对失败。")

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
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass  # 非 Windows 主题不存在时忽略
    # 行距放宽一点，表头加粗——密度太高会显得挤，也不利于扫读
    style.configure("Treeview", rowheight=28)
    style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
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
