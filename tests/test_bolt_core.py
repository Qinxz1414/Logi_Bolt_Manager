"""BoltManager 数据层单元测试（不需要插接收器）。

运行：pytest tests/
"""

from __future__ import annotations

import json

import pytest

import bolt_core as core
from bolt_core import DeviceInfo, ReceiverInfo, ReceiverSession


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeReceiver:
    """模拟 solaar Receiver 的最小接口，只实现 unpair 用得到的部分。"""

    def __init__(self, *, has_force=True, force_result=True, raise_exc=None, may_unpair=True, kind="bolt"):
        self.serial = "  ABCD1234  "  # 故意带填充空格，测试 _clean
        self.name = "Bolt Receiver"
        self.max_devices = 6
        self.path = r"\\?\HID#VID_046D&PID_C548&MI_02&Col02#xxx"
        self.receiver_kind = kind
        self.may_unpair = may_unpair
        self.re_pairs = False
        self.force_calls: list[int] = []
        self.legacy_calls: list[int] = []
        self._has_force = has_force
        self._force_result = force_result
        self._raise_exc = raise_exc
        if not has_force:
            # 模拟没有公开 API 的旧版 Solaar：实例上没有可调用的 force_unpair_slot
            self.force_unpair_slot = None  # type: ignore[method-assign]

    def force_unpair_slot(self, slot: int) -> bool:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.force_calls.append(slot)
        return self._force_result

    def __iter__(self):
        return iter([])

    def _unpair_device(self, slot: int, force: bool = False) -> None:
        self.legacy_calls.append(slot)


# --------------------------------------------------------------------------- #
# DeviceInfo / ReceiverInfo
# --------------------------------------------------------------------------- #
def test_device_info_labels():
    d = DeviceInfo(slot=3, name="MX Master 3S", serial="BEFB13E3", wpid="B035", kind="mouse", online=True)
    assert d.kind_label == "鼠标"
    assert d.status_label == "在线"

    offline = DeviceInfo(slot=1, name="x", kind="keyboard", online=False)
    assert offline.kind_label == "键盘"
    assert offline.status_label == "离线"

    unknown = DeviceInfo(slot=1, name="x", kind=None, online=None)
    assert unknown.kind_label == "未知"
    assert unknown.status_label == "未知"

    as_dict = unknown.as_dict()
    assert as_dict["status"] == "未知" and as_dict["kind_label"] == "未知"


def test_receiver_info_remaining_label():
    info = ReceiverInfo(max_devices=6, remaining_pairings=-1)
    info.devices = [DeviceInfo(slot=1, name="a")]
    # -1 表示接收器没有报告剩余次数 → 用 (上限 - 已配对) 展示
    assert info.remaining_label == "5"

    info2 = ReceiverInfo(max_devices=6, remaining_pairings=2)
    assert info2.remaining_label == "2"

    info3 = ReceiverInfo(max_devices=None)
    assert info3.remaining_label == "—"


def test_receiver_info_serial_cleaned():
    session = ReceiverSession(receiver=FakeReceiver())
    info = session.info()
    assert info.serial == "ABCD1234"  # 两端的填充空格被去掉
    assert info.max_devices == 6


# --------------------------------------------------------------------------- #
# unpair：公开 API / 旧版回退 / 错误路径
# --------------------------------------------------------------------------- #
def test_unpair_uses_public_api():
    fake = FakeReceiver(has_force=True, force_result=True)
    assert ReceiverSession(receiver=fake).unpair(3) == 3
    assert fake.force_calls == [3]
    assert fake.legacy_calls == []


def test_unpair_falls_back_to_legacy():
    fake = FakeReceiver(has_force=False)
    assert ReceiverSession(receiver=fake).unpair(4) == 4
    assert fake.legacy_calls == [4]
    assert fake.force_calls == []


def test_unpair_rejected_by_receiver():
    fake = FakeReceiver(force_result=False)
    with pytest.raises(core.BoltError, match="槽位 3"):
        ReceiverSession(receiver=fake).unpair(3)


def test_unpair_translates_no_such_device():
    from logitech_receiver import exceptions

    fake = FakeReceiver(raise_exc=exceptions.NoSuchDevice(number=3, receiver=None, error="gone"))
    with pytest.raises(core.DeviceNotFound):
        ReceiverSession(receiver=fake).unpair(3)


# --------------------------------------------------------------------------- #
# 格式化
# --------------------------------------------------------------------------- #
def test_format_devices_empty():
    assert "没有" in core.format_devices([])


def test_format_devices_rows():
    devices = [
        DeviceInfo(slot=3, name="K98M Plus", serial="04F3A3E1", kind="keyboard", online=True),
        DeviceInfo(slot=4, name="MX Master 3S", serial="BEFB13E3", kind="mouse", online=False),
    ]
    text = core.format_devices(devices)
    lines = text.splitlines()
    assert len(lines) == 2
    assert "槽位3" in lines[0] and "键盘" in lines[0] and "在线" in lines[0]
    assert "鼠标" in lines[1] and "离线" in lines[1]


def test_format_snapshot_header():
    info = ReceiverInfo(name="Bolt Receiver", serial="ABC", max_devices=6)
    info.devices = [DeviceInfo(slot=1, name="a")]
    text = core.format_snapshot(info)
    assert "已配对 1/6" in text


def test_display_width_counts_cjk_as_two():
    assert core._display_width("键盘") == 4
    assert core._display_width("ab") == 2
    assert core._pad("键盘", 6) == "键盘  "


def test_to_csv_and_tsv():
    devices = [DeviceInfo(slot=3, name="K98M Plus", serial="S3", wpid="B3A1", kind="keyboard", online=True)]
    csv_text = core.to_csv(devices)
    assert csv_text.splitlines()[0] == "槽位,设备名称,类型,序列号,产品ID,状态"
    assert csv_text.splitlines()[1] == "3,K98M Plus,键盘,S3,B3A1,在线"

    tsv = core.to_tsv(devices)
    assert tsv.splitlines()[0].split("\t") == ["槽位", "设备名称", "类型", "序列号", "状态"]


def test_to_json_ok_and_error():
    info = ReceiverInfo(name="R", serial="S", max_devices=2)
    info.devices = [DeviceInfo(slot=1, name="d", kind="mouse", online=True)]
    payload = json.loads(core.to_json(info))
    assert payload["ok"] is True
    assert payload["paired"] == 1
    assert payload["devices"][0]["kind_label"] == "鼠标"

    err = json.loads(core.to_json(info, ok=False, error="boom"))
    assert err == {"ok": False, "error": "boom"}


# --------------------------------------------------------------------------- #
# 设置读写
# --------------------------------------------------------------------------- #
def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("BOLTMANAGER_CONFIG_DIR", str(tmp_path))
    assert core.load_settings() == core.DEFAULT_SETTINGS

    core.save_settings({"geometry": "900x600", "auto_refresh_seconds": 5, "bogus": "忽略"})
    loaded = core.load_settings()
    assert loaded["geometry"] == "900x600"
    assert loaded["auto_refresh_seconds"] == 5
    assert "bogus" not in loaded  # 未知键不落盘


def test_settings_survive_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("BOLTMANAGER_CONFIG_DIR", str(tmp_path))
    core.settings_path().write_text("{not json", encoding="utf-8")
    assert core.load_settings() == core.DEFAULT_SETTINGS


# --------------------------------------------------------------------------- #
# 其它
# --------------------------------------------------------------------------- #
def test_interface_text():
    from bolt_manager import _interface_text

    path = r"\\?\HID#VID_046D&PID_C548&MI_02&Col02#a&27888818&0&0001#{}"
    assert _interface_text(path) == "MI_02"
    assert _interface_text(None) == "—"
    assert _interface_text(r"\\?\HID#whatever") == "—"


def test_conflicting_processes_runs():
    # 不关心结果（取决于机器上跑了什么），只要求不抛异常
    assert isinstance(core.conflicting_processes(), list)


# --------------------------------------------------------------------------- #
# 多接收器与配对能力
# --------------------------------------------------------------------------- #
def _info_of(fake):
    session = core.ReceiverSession(receiver=fake)
    return session.info()


def test_receiver_capability_bolt():
    info = _info_of(FakeReceiver())
    assert info.kind == "bolt"
    assert info.kind_label == "Bolt 接收器"
    assert info.can_unpair and info.can_pair


def test_receiver_capability_unifying():
    info = _info_of(FakeReceiver(kind="unifying"))
    assert info.kind_label == "优联 (Unifying) 接收器"
    assert info.can_unpair and info.can_pair


def test_receiver_capability_nano_cannot_unpair():
    info = _info_of(FakeReceiver(kind="nano", may_unpair=False))
    assert info.can_unpair is False
    assert info.can_pair is True  # Nano 只能替换式配对
    session = core.ReceiverSession(receiver=FakeReceiver(kind="nano", may_unpair=False))
    session.open()
    with pytest.raises(core.BoltError, match="不支持解除配对"):
        session.unpair(1)


def test_receiver_capability_lightspeed():
    info = _info_of(FakeReceiver(kind="lightspeed"))
    assert info.can_unpair is True


def test_pair_run_requires_pairable_kind():
    fake = FakeReceiver(kind="27mhz")
    session = core.ReceiverSession(receiver=fake)
    session.open()
    with pytest.raises(core.BoltError, match="不支持配对"):
        core.run_pairing(session, lambda *a: None)


# --------------------------------------------------------------------------- #
# 配对：最后一步的即时反馈（confirming 事件先于阻塞的设备注册）
# --------------------------------------------------------------------------- #
class _FakePairing:
    """模拟 receiver.pairing 状态机。"""

    def __init__(self):
        self.discovering = False
        self.device_address = None
        self.device_authentication = None
        self.device_name = None
        self.device_passkey = None
        self.device_kind = 0  # 非键盘即可（不影响断言）
        self.lock_open = False
        self.new_device = None
        self.error = None


class _FakeNotification:
    def __init__(self, sub_id, address, data=b"\x00", devnumber=0xFF):
        self.sub_id = sub_id
        self.address = address
        self.data = data
        self.devnumber = devnumber


def test_pairing_emits_confirming_before_device_registration(monkeypatch):
    """Bolt 配对成功：confirming 通知必须抢在阻塞的 register_new_device 之前发出。"""
    import types

    from logitech_receiver import base as lr_base
    from logitech_receiver import hidpp10 as lr_hidpp10
    from logitech_receiver import notifications as lr_notifications

    events: list[str] = []
    order: list[str] = []

    class FakeHidpp10:
        def get_notification_flags(self, r):
            return 0

        def set_notification_flags(self, r, flags):
            pass

    pairing = _FakePairing()
    new_device = types.SimpleNamespace(
        number=7, name="K98M", serial="SN0001", wpid="B35C", online=True, kind=None
    )

    class FakeBoltReceiver:
        receiver_kind = "bolt"
        re_pairs = False

        def __init__(self):
            self.handle = 5
            self.pairing = pairing

        def __iter__(self):
            return iter([])

        def discover(self, timeout=30, cancel=None):
            pairing.discovering = False
            pairing.device_address = b"\x01\x02\x03\x04\x05\x06"
            pairing.device_authentication = 0x01  # 键盘：数字 passkey
            pairing.device_name = "K98M"

        def pair_device(self, **kwargs):
            pairing.lock_open = True

        def register_new_device(self, number, notification=None):
            order.append("register_start")
            pairing.new_device = new_device
            order.append("register_done")
            return new_device

    receiver = FakeBoltReceiver()

    # base.read 脚本：passkey 请求 → 配对成功（0x54 地址 0x02）→ 之后无通知
    script = [
        _FakeNotification(0x4D, 0x00),  # PASSKEY_REQUEST
        _FakeNotification(0x54, 0x02, data=b"\x00"),  # PAIRING_STATUS 成功
    ]
    monkeypatch.setattr(
        lr_base,
        "read",
        lambda handle, timeout=1.0: (lambda: (script.pop(0),) if script else None)(),
    )
    monkeypatch.setattr(lr_base, "make_notification", lambda *a: a[0])
    monkeypatch.setattr(lr_hidpp10, "Hidpp10", FakeHidpp10)

    def fake_process(r, n):
        if n.sub_id == 0x4D:
            r.pairing.device_passkey = 1234
        elif n.sub_id == 0x54:
            r.pairing.lock_open = False
            r.register_new_device(7)

    monkeypatch.setattr(lr_notifications, "process", fake_process)

    def notify(event, data):
        events.append(event)
        if event == "confirming":
            order.append("confirming_event")

    result = core.run_pairing(core.ReceiverSession(receiver=receiver), notify)  # type: ignore[arg-type]

    assert result.slot == 7 and result.name == "K98M"
    assert "confirming" in events
    # 关键断言：confirming 反馈发生在阻塞注册开始之前
    assert order.index("confirming_event") < order.index("register_start")


def test_pair_event_text_confirming():
    text = core.pair_event_text("confirming", {})
    assert text and "配对已确认" in text
