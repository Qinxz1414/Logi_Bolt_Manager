"""kill_conflicting_processes() 单元测试：用假 psutil，不碰真实进程。"""

from __future__ import annotations

import types

import pytest

import bolt_core


class _FakeProc:
    def __init__(self, name: str):
        self.info = {"name": name}
        self.name = lambda: name  # noqa: ARG005
        self.dead = False
        self.terminated = False
        self.killed = False
        self.deny = False  # 模拟权限不足

    def terminate(self):
        if self.deny:
            raise fake_psutil.Error("access denied")
        self.terminated = True
        self.dead = True

    def kill(self):
        if self.deny:
            raise fake_psutil.Error("access denied")
        self.killed = True
        self.dead = True


def make_fake_psutil(monkeypatch, procs):
    """构造一个行为接近 psutil 的假模块并注入 sys.modules。"""
    fake = types.ModuleType("psutil")

    class _Error(Exception):
        pass

    fake.Error = _Error
    fake.Process = _FakeProc

    def process_iter(attrs=None):
        return [p for p in procs if not p.dead]

    def wait_procs(procs, timeout=None):
        gone = [p for p in procs if p.dead]
        alive = [p for p in procs if not p.dead]
        return gone, alive

    fake.process_iter = process_iter
    fake.wait_procs = wait_procs
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake)
    return fake


fake_psutil = None  # 模块级引用，供 _FakeProc 抛异常用


@pytest.fixture()
def _bind_fake():
    global fake_psutil  # noqa: PLW0603
    yield
    fake_psutil = None


def test_kill_ends_matching_processes(monkeypatch, _bind_fake):
    global fake_psutil
    agent = _FakeProc("logioptionsplus_agent.exe")
    updater = _FakeProc("logioptionsplus_updater.exe")
    unrelated = _FakeProc("explorer.exe")
    fake_psutil = make_fake_psutil(monkeypatch, [agent, updater, unrelated])

    killed, remaining, stopped, failed = bolt_core.kill_conflicting_processes()

    assert killed == ["logioptionsplus_agent.exe", "logioptionsplus_updater.exe"]
    assert stopped == []  # 假 psutil 无服务接口，跳过
    assert failed == []
    assert agent.terminated and updater.terminated
    assert remaining == []


def test_kill_no_conflicts(monkeypatch, _bind_fake):
    global fake_psutil
    make_fake_psutil(monkeypatch, [_FakeProc("explorer.exe")])

    killed, remaining, stopped, failed = bolt_core.kill_conflicting_processes()

    assert killed == []
    assert remaining == []
    assert stopped == []
    assert failed == []


def test_kill_access_denied_reports_remaining(monkeypatch, _bind_fake):
    global fake_psutil
    agent = _FakeProc("logioptionsplus_agent.exe")
    agent.deny = True
    fake_psutil = make_fake_psutil(monkeypatch, [agent])

    killed, remaining, stopped, failed = bolt_core.kill_conflicting_processes()

    # 目标被尝试过（killed 含其名字），但权限不足没结束掉 → remaining 报告
    assert "logioptionsplus_agent.exe" in killed
    assert remaining == ["logioptionsplus_agent.exe"]
    assert failed == []
