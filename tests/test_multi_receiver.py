"""多接收器枚举与按路径打开的测试（mock 底层枚举，不碰真实硬件）。"""

from types import SimpleNamespace

import pytest

import bolt_core as core


def _info(path: str, product: str):
    return SimpleNamespace(path=path, product=product)


def _patch_infos(monkeypatch, infos):
    monkeypatch.setattr(core.ReceiverSession, "_long_report_infos", staticmethod(lambda: infos))


def test_list_receivers_dedupes_by_path(monkeypatch):
    infos = [
        _info("p1_col02", "USB Receiver"),
        _info("p1_col02", "USB Receiver"),  # 同一接收器重复集合
        _info("p2_col02", "Bolt Receiver"),
    ]
    _patch_infos(monkeypatch, infos)
    choices = core.list_receivers()
    assert [c.path for c in choices] == ["p1_col02", "p2_col02"]
    assert choices[1].name == "Bolt Receiver"


def test_list_receivers_empty_on_error(monkeypatch):
    def boom():
        raise core.ReceiverNotFound("未找到")

    monkeypatch.setattr(core.ReceiverSession, "_long_report_infos", staticmethod(boom))
    assert core.list_receivers() == []


def test_session_open_with_matching_path(monkeypatch):
    infos = [_info("p1_col02", "USB Receiver"), _info("p2_col02", "Bolt Receiver")]
    _patch_infos(monkeypatch, infos)
    created = []

    def fake_create(_base, info):
        created.append(info)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr("logitech_receiver.receiver.create_receiver", fake_create)
    with core.ReceiverSession(path="p2_col02") as session:
        session.close()
    assert [str(i.path) for i in created] == ["p2_col02"]


def test_session_open_with_unknown_path(monkeypatch):
    _patch_infos(monkeypatch, [_info("p1_col02", "USB Receiver")])
    with pytest.raises(core.ReceiverNotFound, match="不存在或已被拔出"):
        core.ReceiverSession(path="missing").open()


def test_session_open_no_receivers(monkeypatch):
    _patch_infos(monkeypatch, [])
    with pytest.raises(core.ReceiverNotFound, match="未找到 Logitech 接收器"):
        core.ReceiverSession().open()
