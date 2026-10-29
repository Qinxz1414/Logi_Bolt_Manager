"""hidapi_impl.close() 幂等性回归测试。

背景：solaar 的失败路径会 double-close——base.write() 失败时先 close(handle)，
半初始化的 Receiver 被 GC 后 __del__ 再 close 一次，C 层 hid_close 对已释放句柄
二次释放导致堆损坏（0xC0000374，程序闪退）。补丁让 close() 幂等。

用假 _hidapi 验证，不需要真实硬件。
"""

from __future__ import annotations

import ctypes

import hidapi.hidapi_impl as impl
import pytest

import bolt_core  # noqa: F401 - 导入即安装 gi 桩，hidapi 才能导入


class _FakeHidapi:
    """记录 hid_close / hid_open_path 调用的替身。"""

    def __init__(self):
        self.closed: list[int] = []
        self.open_results: dict[bytes, int] = {}
        self.error = ctypes.c_char_p(b"fake error")

    def hid_open_path(self, path: bytes):
        return self.open_results.get(path)

    def hid_close(self, handle: int) -> None:
        self.closed.append(int(handle))

    def hid_error(self, _handle) -> ctypes._Pointer:
        return self.error


@pytest.fixture()
def fake_hidapi(monkeypatch):
    fake = _FakeHidapi()
    monkeypatch.setattr(impl, "_hidapi", fake)
    impl._closed_handles.clear()
    impl._closed_tokens.clear()
    impl._virtual_handles.clear()
    return fake


def test_close_real_handle_twice_closes_once(fake_hidapi):
    handle = 0x1111
    fake_hidapi.open_results[b"\\\\path"] = handle

    impl.open_path(b"\\\\path")
    impl.close(handle)
    impl.close(handle)  # solaar __del__ 的第二次 close

    assert fake_hidapi.closed == [handle]


def test_close_virtual_handle_twice_closes_underlying_once(fake_hidapi):
    short_h, long_h = 0xAAAA, 0xBBBB
    bolt_path = b"\\\\?\\HID#VID_046D&PID_C548&MI_02&Col01#a&296e518a&0&0000#..."
    fake_hidapi.open_results[b"\\\\?\\HID#VID_046D&PID_C548&MI_02&Col01#a&296e518a&0&0000#..."] = short_h
    fake_hidapi.open_results[b"\\\\?\\HID#VID_046D&PID_C548&MI_02&Col02#a&296e518a&0&0001#..."] = long_h

    vh = impl.open_path(bolt_path)
    impl.close(vh)
    impl.close(vh)  # 同一 token 的第二次 close

    assert sorted(fake_hidapi.closed) == sorted([short_h, long_h])


def test_reopened_handle_value_can_be_closed_again(fake_hidapi):
    # 系统可能复用句柄值：关闭后重新打开同值句柄，必须能再次正常关闭
    handle = 0x2222
    fake_hidapi.open_results[b"\\\\path"] = handle

    impl.open_path(b"\\\\path")
    impl.close(handle)
    impl.open_path(b"\\\\path")
    impl.close(handle)

    assert fake_hidapi.closed == [handle, handle]
