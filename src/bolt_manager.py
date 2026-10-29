# -*- coding: utf-8 -*-
"""Logi Bolt 接收器管理器（Windows GUI）

基于打过补丁的 Solaar 1.1.20（Windows + Bolt 兼容补丁）。
功能：列出 Bolt 接收器上的所有配对设备，并可解除指定设备的配对。

用法：
    python bolt_manager.py          # 打开图形界面
    python bolt_manager.py --list   # 命令行列出配对设备（用于测试）
"""
import logging
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

logging.basicConfig(level=logging.WARNING)
logging.getLogger("logitech_receiver").setLevel(logging.ERROR)
logging.getLogger("solaar").setLevel(logging.ERROR)


def _install_gi_stub():
    """注入假 gi（PyGObject）模块，使 logitech_receiver 在无 GTK 的 Windows 上可导入。

    仅覆盖 CLI/数据层所需：diversion.py 与 desktop_notifications.py 的导入期符号。
    """
    import types

    if "gi" in sys.modules:
        return

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
    gi_mod.repository = repo_mod
    sys.modules["gi"] = gi_mod
    sys.modules["gi.repository"] = repo_mod


_install_gi_stub()


# ---------------- 数据层 ----------------

def find_receiver():
    """找到 Bolt 接收器并创建 Receiver 对象。"""
    from logitech_receiver import base, receiver

    infos = [i for i in base.receivers() if "Col02" in i.path]
    if not infos:
        return None
    return receiver.create_receiver(base, infos[0])


def list_devices():
    """返回 [(槽位, 名称, 序列号, 在线)] 列表。"""
    r = find_receiver()
    if r is None:
        raise RuntimeError("未找到 Logi Bolt 接收器，请确认接收器已插入 USB 口。")
    devs = []
    for d in r:
        try:
            online = "在线" if d.online else "离线"
        except Exception:
            online = "未知"
        devs.append((d.number, d.name or "?", d.serial or "-", online))
    devs.sort()
    return devs


def unpair_device(slot):
    """解除指定槽位的配对（直接写接收器）。"""
    r = find_receiver()
    if r is None:
        raise RuntimeError("未找到 Logi Bolt 接收器。")
    r._unpair_device(slot, force=True)


def options_plus_running():
    try:
        import psutil

        for p in psutil.process_iter(["name"]):
            try:
                n = (p.info["name"] or "").lower()
                if n.startswith("logioptionsplus"):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ---------------- 命令行模式 ----------------

if "--list" in sys.argv:
    try:
        for slot, name, serial, online in list_devices():
            print(f"槽位{slot}  {name}  序列号={serial}  {online}")
    except Exception as e:
        print("错误:", e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


# ---------------- 图形界面 ----------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q = queue.Queue()
        self.busy = False

        root.title("Logi Bolt 接收器管理器")
        root.geometry("640x400")
        root.minsize(560, 340)

        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(
            top,
            text="接收器上的配对设备（删除配对不会影响设备本身，之后可重新配对）",
        ).pack(anchor="w")

        cols = ("slot", "name", "serial", "online")
        headers = ("槽位", "设备名称", "序列号", "状态")
        widths = (60, 260, 160, 70)
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, headers, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c != "slot" else "center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree.bind("<Double-1>", lambda e: self.on_unpair())

        btns = ttk.Frame(root, padding=(10, 0, 10, 6))
        btns.pack(fill="x")
        self.btn_refresh = ttk.Button(btns, text="刷新", command=self.on_refresh)
        self.btn_refresh.pack(side="left")
        self.btn_unpair = ttk.Button(btns, text="解除所选设备配对", command=self.on_unpair)
        self.btn_unpair.pack(side="left", padx=8)
        self.btn_quit = ttk.Button(btns, text="退出", command=root.destroy)
        self.btn_quit.pack(side="right")

        self.status = tk.StringVar(value="正在读取接收器…")
        bar = ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=(6, 3))
        bar.pack(fill="x", side="bottom")

        if options_plus_running():
            self.set_status("⚠ 检测到 Logi Options+ 正在运行，可能占用接收器——建议先退出 Options+ 再操作。")

        self.poll_queue()
        self.on_refresh()

    # ----- 工具 -----
    def set_status(self, text):
        self.status.set(text)

    def run_bg(self, fn, done_tag):
        """在后台线程执行 fn，结果放入队列 (done_tag, ok, payload)。"""
        def worker():
            try:
                self.q.put((done_tag, True, fn()))
            except Exception as e:
                self.q.put((done_tag, False, str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_queue(self):
        try:
            while True:
                tag, ok, payload = self.q.get_nowait()
                self.busy = False
                self.btn_refresh.config(state="normal")
                self.btn_unpair.config(state="normal")
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

    def fill(self, devs):
        self.tree.delete(*self.tree.get_children())
        for slot, name, serial, online in devs:
            self.tree.insert("", "end", values=(slot, name, serial, online))

    # ----- 事件 -----
    def on_refresh(self):
        if self.busy:
            return
        self.busy = True
        self.btn_refresh.config(state="disabled")
        self.btn_unpair.config(state="disabled")
        self.set_status("正在读取接收器…")
        self.run_bg(list_devices, "refresh")

    def on_unpair(self):
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
        self.btn_refresh.config(state="disabled")
        self.btn_unpair.config(state="disabled")
        self.set_status(f"正在解除槽位 {slot} 的配对…")
        self.run_bg(lambda: (unpair_device(int(slot)), slot)[1], "unpair")


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
