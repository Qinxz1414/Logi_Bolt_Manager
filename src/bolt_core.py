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
import time
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


class PairingError(BoltError):
    """配对流程失败（超时、设备拒绝等）。"""


class PairingCancelled(BoltError):
    """用户取消了配对流程。"""


# 接收器型号的中文显示名（receiver_kind → 展示名）
_RECEIVER_KIND_LABELS = {
    "bolt": "Bolt 接收器",
    "unifying": "优联 (Unifying) 接收器",
    "nano": "Nano 接收器",
    "lightspeed": "Lightspeed 接收器",
    "27mhz": "27MHz 老式接收器",
}
# 可以发起配对的接收器型号（EX100 27MHz 太老，不支持）
_PAIRABLE_KINDS = {"bolt", "unifying", "nano", "lightspeed"}


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


@dataclass(frozen=True)
class ReceiverChoice:
    """接入主机的接收器候选项（多接收器选择用）。"""

    path: str  # 该接收器长报文集合（Col02）的 HID 路径，作为唯一标识
    name: str = "接收器"  # 枚举到的产品名，如 "Bolt Receiver"


@dataclass
class ReceiverInfo:
    """接收器自身的信息。"""

    name: str = "Logi Bolt Receiver"
    serial: str | None = None
    path: str | None = None
    max_devices: int | None = None
    remaining_pairings: int | None = None  # -1 / None 表示不限或读不出
    kind: str | None = None  # bolt / unifying / nano / lightspeed
    can_unpair: bool = True
    can_pair: bool = True
    devices: list[DeviceInfo] = field(default_factory=list)

    @property
    def kind_label(self) -> str:
        return _RECEIVER_KIND_LABELS.get((self.kind or "").lower(), self.kind or "接收器")

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

    def __init__(self, receiver=None, path: str | None = None) -> None:
        self._receiver = receiver
        self._path = path  # 指定要打开的接收器（多接收器场景）；None = 取第一个

    # ----- 生命周期 -----
    def open(self) -> ReceiverSession:
        if self._receiver is not None:
            return self
        self._receiver = self._open_receiver(self._path)
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
    def _long_report_infos():
        """枚举所有接入的接收器，返回每个接收器的长报文集合（Col02）信息。"""
        from logitech_receiver import base, exceptions

        try:
            return [i for i in base.receivers() if _LONG_REPORT_INTERFACE in i.path]
        except exceptions.NoReceiver as exc:  # 枚举阶段的底层失败
            raise ReceiverNotFound(f"接收器无法访问：{exc}") from exc
        except OSError as exc:
            raise ReceiverNotFound(
                f"打开 HID 设备失败：{exc}\n"
                "接收器可能被其它程序独占（Logi Options+ / Logi Tune / GHUB），请先退出它们。"
            ) from exc

    @classmethod
    def _open_receiver(cls, path: str | None = None):
        from logitech_receiver import base, receiver

        infos = cls._long_report_infos()

        if not infos:
            raise ReceiverNotFound(
                "未找到 Logitech 接收器。\n"
                "支持：Bolt (046D:C548)、优联 Unifying (C52B/C532)、Lightspeed (C539)、Nano。\n"
                "请确认接收器已直接插在主机 USB 口上（不要走 Hub）。"
            )
        if path is not None:
            infos = [i for i in infos if str(i.path) == path]
            if not infos:
                raise ReceiverNotFound("指定的接收器不存在或已被拔出，请重新选择。")

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
        kind = _clean(getattr(r, "receiver_kind", None))
        may_unpair = bool(getattr(r, "may_unpair", False))
        info = ReceiverInfo(
            name=str(getattr(r, "name", None) or "接收器"),
            serial=_clean(getattr(r, "serial", None)),
            path=str(getattr(r, "path", "") or "") or None,
            max_devices=getattr(r, "max_devices", None),
            kind=kind,
            can_unpair=may_unpair,
            can_pair=kind in _PAIRABLE_KINDS,
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
            devices.append(_device_info(d))
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

        if not getattr(r, "may_unpair", False):
            raise BoltError(
                f"{_RECEIVER_KIND_LABELS.get((getattr(r, 'receiver_kind', '') or '').lower(), '该接收器')}"
                "不支持解除配对（协议限制，只允许用新配对替换旧设备）。"
            )

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


def _device_info(d) -> DeviceInfo:
    """把 solaar 的 Device 对象整理成 DeviceInfo（容错一切读不出的字段）。"""
    slot = int(getattr(d, "number", 0))
    online: bool | None
    try:
        online = bool(d.online)
    except Exception:  # noqa: BLE001 - 设备状态读取可能因驱动/占用而任意报错
        online = None
    return DeviceInfo(
        slot=slot,
        # 接收器返回的名字带定长填充，去掉尾部空格
        name=_clean(getattr(d, "name", None)) or "?",
        serial=_clean(getattr(d, "serial", None)),
        wpid=_clean(getattr(d, "wpid", None)),
        kind=_kind_of(d),
        online=online,
    )


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
# 接收器 USB PID → 显示名（从 HID 路径解析，无需打开设备）
# 枚举返回的 product 常是通用的 "USB Receiver"，区分度全靠 PID
_RECEIVER_PID_NAMES = {
    "C548": "Bolt 接收器",
    "C52B": "Unifying 优联接收器",
    "C532": "Unifying 优联接收器",
    "C539": "Lightspeed 接收器",
}


def list_receivers() -> list[ReceiverChoice]:
    """枚举所有接入的接收器（不做任何打开操作，只读 HID 描述）。

    每个物理接收器对应一条长报文集合（Col02）记录，path 作为唯一标识。
    枚举失败（没有接收器、被独占）返回空列表——调用方据此提示。
    """
    try:
        infos = ReceiverSession._long_report_infos()
    except BoltError:
        return []
    choices: list[ReceiverChoice] = []
    seen: set[str] = set()
    for i in infos:
        p = str(i.path or "")
        if not p or p in seen:
            continue
        seen.add(p)
        pid = ""
        lowered = p.upper()
        if "&PID_" in lowered:  # ...VID_046D&PID_C548&MI_02...
            pid = lowered.split("&PID_")[1].split("&")[0]
        name = _RECEIVER_PID_NAMES.get(pid) or str(getattr(i, "product", None) or "接收器")
        choices.append(ReceiverChoice(path=p, name=name))
    return choices


def snapshot(path: str | None = None) -> ReceiverInfo:
    """打开接收器、读出信息与设备、关掉，返回快照。path 指定多接收器中的哪一个。"""
    with ReceiverSession(path=path) as session:
        return session.info()


def unpair_device(slot: int, path: str | None = None) -> int:
    """解除指定槽位的配对（一次性会话）。path 指定多接收器中的哪一个。"""
    with ReceiverSession(path=path) as session:
        return session.unpair(slot)


# --------------------------------------------------------------------------- #
# 配对（添加设备）
# --------------------------------------------------------------------------- #
def run_pairing(
    session: ReceiverSession,
    notify,
    timeout: int = 30,
    cancel=None,
) -> DeviceInfo:
    """把一台新设备配到接收器上（阻塞，应在后台线程调用）。

    流程复刻 Solaar 的 `solaar pair`：
    - Bolt：写发现寄存器广播 → 设备应答（地址/认证/名称）→ pair_device 写入
      → 若需认证则展示 passkey 指引 → 等锁关闭。
    - 其他（Unifying/Nano/Lightspeed）：set_lock 开锁 → 等设备连接通知。

    notify(event, data) 用于进度上报，事件：
        discover {kind, timeout} — 请操作新设备进入配对模式
        found    {name}          — 发现了设备
        passkey  {mode, text}    — 需要用户在新设备上输入/按出 passkey
        pairing  {}              — 正在完成配对
    cancel 是 threading.Event，置位后尽快返回并抛 PairingCancelled。
    成功返回新设备的 DeviceInfo。
    """
    import threading
    import time as _time

    from logitech_receiver import base, hidpp10, hidpp10_constants, notifications

    if cancel is None:
        cancel = threading.Event()

    r = session.receiver
    kind = (getattr(r, "receiver_kind", "") or "").lower()
    if kind not in _PAIRABLE_KINDS:
        raise BoltError(f"{_RECEIVER_KIND_LABELS.get(kind, '该接收器')}不支持配对新设备。")

    _hidpp10 = hidpp10.Hidpp10()
    known = {d.number for d in r}
    original_handle = r.handle

    class _HookedHandle(int):
        """给句柄挂上通知处理钩子（复刻 solaar cli/pair.py 的做法）。"""

        def notifications_hook(self, n):
            nonlocal known
            if n.devnumber == 0xFF:
                notifications.process(r, n)
            elif n.sub_id == 0x41 and len(n.data) == base.SHORT_MESSAGE_SIZE - 4:
                # 0x41 = 设备连接通知
                if n.devnumber not in known:
                    r.pairing.new_device = r.register_new_device(n.devnumber, n)
                elif r.re_pairs:  # 替换式配对（Nano）：旧槽位信息作废
                    del r[n.devnumber]
                    r.pairing.new_device = r.register_new_device(n.devnumber, n)

    def _pump() -> None:
        """读一条通知并分发；短超时便于及时响应取消。"""
        raw = base.read(r.handle, timeout=0.5)
        if raw:
            n = base.make_notification(*raw)
            if n:
                r.handle.notifications_hook(n)

    def _cancelled() -> bool:
        if not cancel.is_set():
            return False
        return True

    # 配对通知需要 WIRELESS 通告位；结束 时仅在原本没开的情况下恢复
    old_flags = 0
    flags_known = True
    try:
        old_flags = _hidpp10.get_notification_flags(r) or 0
        if not (old_flags & hidpp10_constants.NotificationFlag.WIRELESS):
            _hidpp10.set_notification_flags(r, old_flags | hidpp10_constants.NotificationFlag.WIRELESS)
    except Exception:  # noqa: BLE001 - 个别接收器不支持通告位读写，配对仍可继续
        flags_known = False
        LOG.debug("读写通知标志失败，继续配对", exc_info=True)

    r.handle = _HookedHandle(int(r.handle))
    pairing = r.pairing
    start = _time.time()
    try:
        if kind == "bolt":
            notify("discover", {"kind": kind, "timeout": timeout})
            r.discover(timeout=timeout)
            while (pairing.discovering or _time.time() - start < 5) and not _cancelled():
                if pairing.device_address and pairing.device_authentication and pairing.device_name:
                    break
                _pump()
            if _cancelled():
                r.discover(cancel=True)
                raise PairingCancelled("已取消配对。")
            if pairing.device_authentication is None:
                raise PairingError(
                    f"{timeout} 秒内没有发现可配对的设备。\n"
                    "请确认设备支持 Bolt、已通电，并长按配对键（黄色闪电）直到指示灯快闪。"
                )
            notify("found", {"name": _clean(pairing.device_name) or "?"})
            r.pair_device(
                address=pairing.device_address,
                authentication=pairing.device_authentication,
                entropy=20 if pairing.device_kind == hidpp10_constants.DEVICE_KIND.keyboard else 10,
            )
            notify("pairing", {})
            pairing_start = _time.time()
            while (pairing.lock_open or _time.time() - pairing_start < 5) and not _cancelled():
                if pairing.device_passkey:
                    break
                _pump()
            if _cancelled():
                raise PairingCancelled("已取消配对。")
            if pairing.device_passkey:
                if pairing.device_authentication & 0x01:
                    notify("passkey", {"mode": "keyboard", "text": str(pairing.device_passkey)})
                else:
                    bits = f"{int(pairing.device_passkey):010b}"
                    arrows = " → ".join("右" if b == "1" else "左" for b in bits)
                    notify("passkey", {"mode": "buttons", "text": arrows})
            while pairing.lock_open and not _cancelled():
                _pump()
            if _cancelled():
                raise PairingCancelled("已取消配对。")
        else:
            notify("discover", {"kind": kind, "timeout": timeout})
            r.set_lock(False, timeout=timeout)
            while (pairing.lock_open or _time.time() - start < 5) and not _cancelled():
                if pairing.new_device:
                    break
                _pump()
            if _cancelled():
                r.set_lock(False)  # 关锁
                raise PairingCancelled("已取消配对。")
    finally:
        r.handle = original_handle
        if flags_known and not (old_flags & hidpp10_constants.NotificationFlag.WIRELESS):
            try:
                _hidpp10.set_notification_flags(r, old_flags)
            except Exception:  # noqa: BLE001
                LOG.debug("恢复通知标志失败", exc_info=True)

    if pairing.new_device:
        return _device_info(pairing.new_device)
    error = getattr(pairing, "error", None)
    if error:
        raise PairingError(f"配对失败：{error}")
    raise PairingError(
        "配对没有完成：接收器锁已关闭但没有新设备注册。\n"
        "常见原因：设备被按到了其他通道、passkey 超时，或设备此前配对在别的接收器上。"
    )


def pair_device(notify, timeout: int = 30, cancel=None, path: str | None = None) -> DeviceInfo:
    """配对新设备（一次性会话）。notify/cancel 语义见 run_pairing。path 指定接收器。"""
    with ReceiverSession(path=path) as session:
        return run_pairing(session, notify, timeout=timeout, cancel=cancel)


def pair_event_text(event: str, data: dict) -> str | None:
    """把配对进度事件翻译成面向用户的一句话（GUI 与 CLI 共用）。"""
    if event == "discover":
        timeout = data.get("timeout", 30)
        if data.get("kind") == "bolt":
            return (
                f"请长按新设备上的配对键（黄色闪电标志），直到设备指示灯快闪。"
                f"（{timeout} 秒内有效）"
            )
        return f"请打开新设备电源，或将其通道开关切到未占用的通道。（{timeout} 秒内有效）"
    if event == "found":
        return f"发现设备：{data.get('name', '?')}"
    if event == "pairing":
        return "正在完成配对…"
    if event == "passkey":
        if data.get("mode") == "keyboard":
            return f"请在新设备上输入数字 {data.get('text', '')}，然后按回车键。"
        return f"请在新设备上依次按下：{data.get('text', '')}\n然后同时按下左键和右键。"
    return None


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
    # 实测 UI 最小需求 857x603（无高 DPI 缩放），默认给足余量；由 GUI 按 DPI 再缩放
    "geometry": "980x640",
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
# 可能独占接收器的官方软件进程名前缀（小写）
_CONFLICT_MARKERS = ("logioptionsplus", "logitune", "lghub", "logitech")


def conflicting_processes() -> list[str]:
    """返回正在运行、可能独占接收器的官方软件进程名。"""
    try:
        import psutil
    except ImportError:
        return []

    markers = _CONFLICT_MARKERS
    found: list[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001 - 进程可能已退出
            continue
        if name and any(name.startswith(m) for m in markers):
            found.append(name)
    return sorted(set(found))


def _conflicting_services() -> list[str]:
    """查找正在运行、且可执行文件路径匹配冲突标记的 Windows 服务。

    Options+ 的 OptionsPlusUpdaterService（LocalSystem）会在进程被杀后几秒内
    自动重新拉起 agent——只杀进程不杀服务等于白杀。内核驱动（\\drivers\\ 路径）
    一律跳过，绝不碰。
    """
    try:
        import psutil
    except ImportError:
        return []
    if sys.platform != "win32" or not hasattr(psutil, "win_service_iter"):
        return []

    markers = _CONFLICT_MARKERS
    found: list[str] = []
    try:
        for svc in psutil.win_service_iter():
            try:
                info = svc.as_dict()
            except Exception:  # noqa: BLE001 - 服务枚举可能部分失败
                continue
            binpath = (info.get("binpath") or "").lower()
            name = (info.get("name") or "").lower()
            if "\\drivers\\" in binpath:  # 内核驱动不动
                continue
            if (info.get("status") == "running") and any(m in binpath or m in name for m in markers):
                found.append(info["name"])
    except Exception:  # noqa: BLE001 - 服务枚举失败不影响杀进程
        LOG.debug("枚举 Windows 服务失败", exc_info=True)
    return sorted(set(found))


def _stop_service(name: str) -> bool:
    """通过 Windows API 停止服务（不依赖外部命令）。需要管理员权限，失败返回 False。"""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    SC_MANAGER_ALL_ACCESS = 0xF003F
    SERVICE_STOP = 0x0020
    SERVICE_QUERY_STATUS = 0x0004
    SERVICE_CONTROL_STOP = 1

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    advapi32.OpenSCManagerW.restype = wintypes.SC_HANDLE
    advapi32.OpenServiceW.argtypes = (wintypes.SC_HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
    advapi32.OpenServiceW.restype = wintypes.SC_HANDLE
    advapi32.ControlService.argtypes = (wintypes.SC_HANDLE, wintypes.DWORD, ctypes.c_void_p)
    advapi32.ControlService.restype = wintypes.BOOL
    advapi32.CloseServiceHandle.argtypes = [wintypes.SC_HANDLE]
    advapi32.CloseServiceHandle.restype = wintypes.BOOL

    mgr = advapi32.OpenSCManagerW(None, None, SC_MANAGER_ALL_ACCESS)
    if not mgr:
        return False  # 通常是没有管理员权限
    try:
        svc = advapi32.OpenServiceW(mgr, name, SERVICE_STOP | SERVICE_QUERY_STATUS)
        if not svc:
            return False
        try:
            status = (wintypes.DWORD * 7)()  # SERVICE_STATUS 结构体的 7 个 DWORD
            return bool(advapi32.ControlService(svc, SERVICE_CONTROL_STOP, status))
        finally:
            advapi32.CloseServiceHandle(svc)
    finally:
        advapi32.CloseServiceHandle(mgr)


def kill_conflicting_processes(timeout: float = 3.0) -> tuple[list[str], list[str], list[str], list[str]]:
    """彻底结束可能独占接收器的官方软件（Logi Options+ / G HUB 等）。

    三步走：
    1. 停掉相关的 Windows 服务（Options+ 的 updater 服务会自动复活进程）
    2. 结束进程：updater 类先杀（掐断复活源头），再温和 terminate、
       超时后强 kill
    3. 复查两轮（每轮 1.5 秒），把复活/漏网的进程再杀一遍

    :returns: (已结束的进程名列表, 结束后仍在运行的进程名列表,
               已停止的服务名列表, 停止失败的服务名列表)
    """
    try:
        import psutil
    except ImportError:
        return [], [], [], []

    services = _conflicting_services()
    stopped_services = [n for n in services if _stop_service(n)]
    failed_services = [n for n in services if n not in stopped_services]

    markers = _CONFLICT_MARKERS

    def _find_targets() -> list:
        targets: list = []
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except Exception:  # noqa: BLE001 - 进程可能已退出
                continue
            if name and any(name.startswith(m) for m in markers):
                targets.append(proc)
        # updater 类先杀：它是拉起其他进程的源头
        targets.sort(key=lambda p: ("updater" not in (p.info.get("name") or "").lower()))
        return targets

    targets = _find_targets()
    if not targets and not services:
        return [], [], [], []

    attempted: set[str] = set()
    for _round in range(3):  # 初杀 + 两轮复活复查
        for proc in targets:
            attempted.add((proc.info.get("name") or proc.name() or "").lower())
            try:
                proc.terminate()
            except psutil.Error:
                continue
        _gone, alive = psutil.wait_procs(targets, timeout=timeout if _round == 0 else 0.5)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                continue
        psutil.wait_procs(alive, timeout=1.0)
        time.sleep(1.5)  # 给服务/守护逻辑复活的时间窗
        targets = _find_targets()
        if not targets:
            break

    remaining = conflicting_processes()
    return sorted(attempted), remaining, stopped_services, failed_services
