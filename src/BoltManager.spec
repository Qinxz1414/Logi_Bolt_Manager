# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：Logi Bolt 接收器管理器（单目录发布）

用法（在项目根目录执行）：
    python -m PyInstaller --noconfirm --clean src\\BoltManager.spec

产物：
    dist\\BoltManager\\BoltManager.exe  （one-folder 形式，必须连同 _internal 一起分发）

路径全部由 SPECPATH 推导，不再硬编码 Python 安装目录，
换机器或换 Python 小版本都不需要改这个文件。
"""
import os

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

# SPECPATH 由 PyInstaller 注入，指向本 spec 文件所在目录（src/）
SRC_DIR = SPECPATH
PROJECT_ROOT = os.path.dirname(SRC_DIR)


def _find_hidapi_dll():
    """按常见位置查找 hidapi.dll，返回绝对路径。

    优先级：环境变量 > tools/fetch_hidapi.py 下载的官方 DLL > Python 安装目录。
    """
    import sys

    candidates = []
    env = os.environ.get("HIDAPI_DLL")
    if env:
        candidates.append(env)
    candidates += [
        os.path.join(PROJECT_ROOT, "build", "hidapi", "hidapi.dll"),          # fetch_hidapi.py 产物
        os.path.join(sys.base_prefix, "hidapi.dll"),                          # 手工放进 Python 目录（旧做法）
        os.path.join(sys.base_prefix, "Library", "bin", "hidapi.dll"),        # conda
        os.path.join(sys.base_prefix, "Lib", "site-packages", "hidapi", "hidapi.dll"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise SystemExit(
        "未找到 hidapi.dll。请先执行：python tools/fetch_hidapi.py\n已尝试：\n  " + "\n  ".join(candidates)
    )


HIDAPI_DLL = _find_hidapi_dll()

binaries = [(HIDAPI_DLL, ".")]
datas = collect_data_files("solaar")
# 窗口图标要在运行时用到，作为数据文件一起打进去（EXE 的 icon= 只影响 exe 文件本身）
ICON = os.path.join(SRC_DIR, "bolt.ico")
if os.path.isfile(ICON):
    datas.append((ICON, "."))
hiddenimports = [
    "bolt_core",
    "hidapi.hidapi_impl",
    "logitech_receiver.base",
    "logitech_receiver.receiver",
]
hiddenimports += collect_submodules("solaar.cli")


a = Analysis(
    [os.path.join(SRC_DIR, "bolt_manager.py")],
    pathex=[SRC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BoltManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(SRC_DIR, "bolt.ico")],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BoltManager",
)
