# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：Logi Bolt 接收器管理器（单文件发布）

用法（在项目根目录执行）：
    python -m PyInstaller --noconfirm --clean src\\BoltManager.spec

产物：
    dist\\BoltManager.exe  （one-file 形式，单个可执行文件，直接分发）

one-file 与 one-folder 的取舍：
    + 只有一个 exe，拷走/上传/发给别人都不会漏 _internal
    - 每次启动要把内置资源解压到 %TEMP%\\_MEIxxxx，冷启动慢 1~3 秒
    - 体积略大（不再是 34MB 目录，而是约 15~20MB 单文件）
    - 部分杀软对"单文件自解压"更敏感，偶发误报

若更在意启动速度，把下面 EXE(...) 的 a.binaries/a.datas 改成 []、
exclude_binaries=True，并恢复文件末尾的 COLLECT(...) 即可切回 one-folder。

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

# 启动闪屏：单文件模式解压期间（约 2~4 秒）给用户"在加载"的反馈，
# 应用侧在窗口就绪后调 pyi_splash.close() 关掉它（见 bolt_manager._close_splash）
SPLASH_PNG = os.path.join(SRC_DIR, "splash.png")
splash = Splash(SPLASH_PNG, binaries=a.binaries, datas=a.datas, always_on_top=False) if os.path.isfile(SPLASH_PNG) else None

_exe_head = [pyz, a.scripts]
if splash is not None:
    _exe_head += [splash, splash.binaries]

exe = EXE(
    *_exe_head,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,  # one-file：二进制与数据一起塞进 exe
    name="BoltManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(SRC_DIR, "bolt.ico")],
)
