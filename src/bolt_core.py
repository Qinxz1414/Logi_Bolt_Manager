#!/usr/bin/env python
"""BoltManager 数据层。

只负责一件事：和 Logi Bolt 接收器对话（读配对列表、读接收器信息、解除配对），
并把结果整理成普通 Python 对象。**不导入 tkinter**，因此可以被测试与脚本直接复用。

依赖打完补丁的 Solaar 1.1.20（补丁见仓库 patches/ 目录）。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

LOG = logging.getLogger("boltmgr")

APP_NAME = "BoltManager"
RECEIVER_USB_ID = "046D:C548"
# Bolt 接收器在 MI_02 上暴露 Col01（短报文）与 Col02（长报文）两个 HID 集合，
# 长报文集合才是 solaar 的入口。
_LONG_REPORT_INTERFACE = "Col02"

# 设备类型（HID++ DEVICE_KIND）的中文显示名
_KIND_LABELS = {
    "keyboard": "键盘",
    "mouse": "鼠标",
    "numpad": "小键盘",
    "presenter": "演示器",
    "headset": "耳机",
    "trackball": "轨迹球",
    "touchpad": "触摸板",
    "remote": "遥控器",
    "gamepad": "手柄",
    "unknown": "未知",
    "?": "未知",
}


# --------------------------------------------------------------------------- #
# 启动期环境准备
# --------------------------------------------------------------------------- #
def _prepare_dll_search_path() -> None:
    """让 ctypes 能找到 build/hidapi/hidapi.dll（tools/fetch_hidapi.py 的产物）。

    打包后的 exe 里 DLL 就在 _internal/，走应用目录搜索，无需此处理。
    注意：Python 3.8+ 的 ctypes 默认安全加载模式不查 PATH，必须 add_dll_directory。
    """
    dll_dir = Path(__file__).resolve().parents[1] / "build" / "hidapi"
    if not dll_dir.is_dir():
        return
    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(dll_dir))
    except (AttributeError, OSError):  # 非 Windows 或目录不可用
        pass


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


_prepare_dll_search_path()
_install_gi_stub()


# --------------------------------------------------------------------------- #
# 错误类型
# --------------------------------------------------------------------------- #
class BoltError(RuntimeError):
    """所有「可预期的」失败。message 直接面向最终用户，不含堆栈。"""


class ReceiverNotFound(BoltError):
    """找不到（或无法打开）Logi Bolt 接收器。"""


class DeviceNotFound(BoltError):
    """接收器上不存在指定槽位。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeviceInfo:
    """接收器上的一条配对记录。"""

    slot: int
    name: str
    serial: str | None = None
    wpid: str | None = None
    kind: str | None = None
    online: bool | None = None  # None = 状态读不出来

    @property
    def kind_label(self) -> str:
        return _KIND_LABELS.get((self.kind or "").lower(), self.kind or "未知")

    @property
    def status_label(self) -> str:
        if self.online is None:
            return "未知"
        return "在线" if self.online else "离线"

    def as_dict(self) -> dict:
        data = asdict(self)
        data["kind_label"] = self.kind_label
        data["status"] = self.status_label
        return data


@dataclass
class ReceiverInfo:
    """接收器自身的信息。"""

    name: str = "Logi Bolt Receiver"
    serial: str | None = None
    path: str | None = None
    max_devices: int | None = None
    remaining_pairings: int | None = None  # -1 / None 表示不限或读不出
    devices: list[DeviceInfo] = field(default_factory=list)

    @property
    def paired(self) -> int:
        return len(self.devices)

    @property
    def remaining_label(self) -> str:
        if self.max_devices is None:
            return "—"
        if self.remaining_pairings is None or self.remaining_pairings < 0:
            return f"{max(self.max_devices - self.paired, 0)}"
        return f"{max(self.remaining_pairings, 0)}"

    def as_dict(self) -> dict:
        data = asdict(self)
        data["paired"] = self.paired
        data["remaining_label"] = self.remaining_label
        data["devices"] = [d.as_dict() for d in self.devices]
        return data


# --------------------------------------------------------------------------- #
# 会话：一次打开、多次读写、最后关闭
# --------------------------------------------------------------------------- #
class ReceiverSession:
    """一次接收器会话。用法：

        with ReceiverSession() as s:
            print(s.info().serial)
            s.unpair(3)

    每个实例独占一个 HID 句柄；GUI 的后台线程各自建会话即可，互不干扰。
    构造时可直接传入 receiver 对象（单元测试用），跳过真实硬件。
    """

    def __init__(self, receiver=None) -> None:
        self._receiver = receiver

    # ----- 生命周期 -----
    def open(self) -> ReceiverSession:
        if self._receiver is not None:
            return self
        self._receiver = self._open_receiver()
        return self

    def close(self) -> None:
        if self._receiver is None:
            return
        try:
            self._receiver.close()
        except Exception:  # noqa: BLE001 - 关闭失败没有补救意义
            LOG.debug("关闭接收器失败", exc_info=True)
        finally:
            self._receiver = None

    def __enter__(self) -> ReceiverSession:
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ----- 内部 -----
    @staticmethod
    def _open_receiver():
        from logitech_receiver import base, exceptions, receiver

        try:
            infos = [i for i in base.receivers() if _LONG_REPORT_INTERFACE in i.path]
        except exceptions.NoReceiver as exc:  # 枚举阶段的底层失败
            raise ReceiverNotFound(f"接收器无法访问：{exc}") from exc
        except OSError as exc:
            raise ReceiverNotFound(
                f"打开 HID 设备失败：{exc}\n"
                "接收器可能被其它程序独占（Logi Options+ / Logi Tune / GHUB），请先退出它们。"
            ) from exc

        if not infos:
            raise ReceiverNotFound(
                "未找到 Logi Bolt 接收器。\n"
                f"请确认已插入标称 {RECEIVER_USB_ID} 的接收器，并直接插在主机 USB 口上（不要走 Hub）。"
            )

        try:
            r = receiver.create_receiver(base, infos[0])
        except OSError as exc:
            raise ReceiverNotFound(f"接收器打开失败：{exc}\n请先退出 Logi Options+ 等会独占接收器的软件。") from exc
        if r is None:
            raise ReceiverNotFound("接收器存在但初始化失败，请重新插拔后再试。")
        return r

    @property
    def receiver(self):
        if self._receiver is None:
            self.open()
        return self._receiver

    # ----- 读取 -----
    def info(self) -> ReceiverInfo:
        r = self.receiver
        info = ReceiverInfo(
            name=str(getattr(r, "name", None) or "Logi Bolt Receiver"),
            serial=_clean(getattr(r, "serial", None)),
            path=str(getattr(r, "path", "") or "") or None,
            max_devices=getattr(r, "max_devices", None),
        )
        info.remaining_pairings = self._remaining_pairings()
        info.devices = self._devices()
        return info

    def _remaining_pairings(self) -> int | None:
        try:
            value = self.receiver.remaining_pairings(cache=False)
        except Exception:  # noqa: BLE001 - 读不出就当作「未知」
            LOG.debug("读取剩余可配对数失败", exc_info=True)
            return None
        return None if value is None else int(value)

    def _devices(self) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        for d in self.receiver:
            slot = int(getattr(d, "number", 0))
            online: bool | None
            try:
                online = bool(d.online)
            except Exception:  # noqa: BLE001 - 设备状态读取可能因驱动/占用而任意报错
                online = None
            devices.append(
                DeviceInfo(
                    slot=slot,
                    # 接收器返回的名字带定长填充，去掉尾部空格
                    name=_clean(getattr(d, "name", None)) or "?",
                    serial=_clean(getattr(d, "serial", None)),
                    wpid=_clean(getattr(d, "wpid", None)),
                    kind=_kind_of(d),
                    online=online,
                )
            )
        devices.sort(key=lambda x: x.slot)
        return devices

    # ----- 写入 -----
    def unpair(self, slot: int) -> int:
        """解除指定槽位的配对，返回该槽位号。

        优先用 Solaar 的公开方法 `force_unpair_slot()`；旧版本没有该方法时
        回退到 `_unpair_device()`，并显式忽略「槽位本就没有设备」的情况。
        """
        r = self.receiver
        slot = int(slot)

        from logitech_receiver import exceptions

        force = getattr(r, "force_unpair_slot", None)
        try:
            if callable(force):
                if force(slot):
                    return slot
            elif hasattr(r, "_unpair_device"):
                r._unpair_device(slot, force=True)  # noqa: SLF001 - 旧版回退路径
                return slot
        except exceptions.NoSuchDevice as exc:
            raise DeviceNotFound(f"槽位 {slot} 上没有已配对的设备。") from exc
        except OSError as exc:
            raise BoltError(
                f"写入接收器失败：{exc}\n接收器可能被其它程序占用，请退出 Logi Options+ 后重试。"
            ) from exc

        # 走到这里说明寄存器写没有被接收器确认
        raise BoltError(
            f"接收器没有接受对槽位 {slot} 的解除配对请求。\n"
            "常见原因：槽位本身就是空的、接收器被其它软件独占，或设备正在使用中。"
        )

    def rename(self, slot: int, name: str) -> None:  # pragma: no cover - 预留
        """预留：Bolt 接收器不支持写设备名。"""
        raise NotImplementedError("Bolt 接收器不支持修改设备名")


def _clean(value) -> str | None:
    """把 solaar 的定长字段（可能带尾部空格/None）整理成可展示字符串。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    text = str(value).strip()
    return text or None


def _kind_of(device) -> str | None:
    try:
        kind = device.kind
    except Exception:  # noqa: BLE001 - 类型读不出来不影响主流程
        return None
    if kind is None:
        return None
    return str(kind).strip().lower() or None


# --------------------------------------------------------------------------- #
# 便捷函数（一次性读快照）
# --------------------------------------------------------------------------- #
def snapshot() -> ReceiverInfo:
    """打开接收器、读出信息与设备、关掉，返回快照。"""
    with ReceiverSession() as session:
        return session.info()


def unpair_device(slot: int) -> int:
    """解除指定槽位的配对（一次性会话）。"""
    with ReceiverSession() as session:
        return session.unpair(slot)


# --------------------------------------------------------------------------- #
# 格式化：给人看 / 给机器看
# --------------------------------------------------------------------------- #
def _display_width(text: str) -> int:
    """按终端显示宽度计算（中文算 2 列），用于对齐表格。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(width - _display_width(text), 0)


def format_devices(devices: list[DeviceInfo]) -> str:
    if not devices:
        return "接收器上没有已配对的设备。"
    rows = [
        (f"槽位{d.slot}", d.name, d.kind_label, f"序列号={d.serial or '-'}", d.status_label) for d in devices
    ]
    widths = [max(_display_width(row[i]) for row in rows) for i in range(4)]
    lines = []
    for row in rows:
        cells = [_pad(row[i], widths[i]) for i in range(4)]
        lines.append("  ".join(cells) + "  " + row[4])
    return "\n".join(lines)


def format_snapshot(info: ReceiverInfo) -> str:
    head = f"接收器 {info.name}"
    if info.serial:
        head += f"  序列号={info.serial}"
    if info.max_devices:
        head += f"  已配对 {info.paired}/{info.max_devices}"
    lines = [head, format_devices(info.devices)]
    return "\n".join(lines)


def to_json(info: ReceiverInfo, *, ok: bool = True, error: str | None = None) -> str:
    if not ok:
        return json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=2)
    payload = {"ok": True}
    payload.update(info.as_dict())
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_csv(devices: list[DeviceInfo]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["槽位", "设备名称", "类型", "序列号", "产品ID", "状态"])
    for d in devices:
        writer.writerow([d.slot, d.name, d.kind_label, d.serial or "", d.wpid or "", d.status_label])
    return buf.getvalue()


def to_tsv(devices: list[DeviceInfo]) -> str:
    """制表符分隔，适合直接粘进 Excel / 聊天窗口。"""
    lines = ["槽位\t设备名称\t类型\t序列号\t状态"]
    lines += [f"{d.slot}\t{d.name}\t{d.kind_label}\t{d.serial or '-'}\t{d.status_label}" for d in devices]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 设置（窗口几何、自动刷新间隔）
# --------------------------------------------------------------------------- #
def config_dir() -> Path:
    """设置目录；可用 BOLTMANAGER_CONFIG_DIR 覆盖（测试用）。"""
    override = os.environ.get("BOLTMANAGER_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / APP_NAME
    return Path(os.path.expanduser("~")) / ".config" / "boltmgr"


def settings_path() -> Path:
    return config_dir() / "settings.json"


DEFAULT_SETTINGS: dict = {
    "geometry": "840x560",
    "auto_refresh_seconds": 15,
}


def load_settings() -> dict:
    """读取设置，损坏/缺失时返回默认值（永不抛异常）。"""
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = settings_path().read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except (OSError, ValueError):
        return data
    if isinstance(loaded, dict):
        for key in DEFAULT_SETTINGS:
            if key in loaded:
                data[key] = loaded[key]
    return data


def save_settings(settings: dict) -> bool:
    """保存设置；失败不抛异常（只影响下次启动的窗口位置）。"""
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = {**DEFAULT_SETTINGS, **{k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}}
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        LOG.debug("保存设置失败", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# 单实例保护
# --------------------------------------------------------------------------- #
_single_instance_handle = None


def acquire_single_instance() -> bool:
    """GUI 单实例锁（Windows 命名互斥体）。

    两个进程同时操作接收器会互相打断，所以第二个实例直接提示退出。
    返回 True 表示拿到锁；非 Windows 平台恒为 True（不做限制）。
    """
    global _single_instance_handle
    if sys.platform != "win32":
        return True
    if _single_instance_handle is not None:
        return True

    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\BoltManager.SingleInstance")
    if not handle:
        return True  # 建不了锁就不拦人
    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _single_instance_handle = handle
    return True


# --------------------------------------------------------------------------- #
# 环境检查
# --------------------------------------------------------------------------- #
def conflicting_processes() -> list[str]:
    """返回正在运行、可能独占接收器的官方软件进程名。"""
    try:
        import psutil
    except ImportError:
        return []

    markers = ("logioptionsplus", "logitune", "lghub", "logitech")
    found: list[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001 - 进程可能已退出
            continue
        if name and any(name.startswith(m) for m in markers):
            found.append(name)
    return sorted(set(found))
