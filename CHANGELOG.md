# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-29

首个开源版本。

### 新增

- Windows GUI（tkinter）：列出 Logi Bolt 接收器上的配对设备，支持解除指定设备配对
- 命令行：`--list`、`--list --json`、`--version`、`--debug`
- 自动检测 Logi Options+ 是否占用接收器，并在状态栏提示
- `tools/apply_patches.py`：应用 / 检查 / 撤销补丁，跨平台、幂等、可逐字节还原
- `tools/make_patches.py`：从当前环境重新生成 `patches/`，便于跟随上游升级
- `tools/fetch_hidapi.py`：从 hidapi 官方 Release 下载 `hidapi.dll`（带 sha256 校验），
  消除"手工拷 DLL"这一构建不可复现的隐患
- `tools/verify_patches.py`：无硬件自检（补丁指纹 + 导入链路），供 CI 使用
- GitHub Actions：Windows 上跑补丁检查 + ruff + 打包，`v*` 标签自动发 Release
  （发布包附带 LICENSE / THIRD-PARTY-NOTICES / patches，满足 GPL 源码可得要求）
- `tools/rebuild.cmd`：一键打包 one-folder exe
- 技术文档：[docs/技术说明.md](docs/技术说明.md)（根因分析、补丁逐条讲解、升级清单）

### 修复（相对上游 Solaar 1.1.20）

- **接收器枚举失败**：Bolt 接收器（`046D:C548`）在 `MI_02` 上把短报文（`Col01`）与长报文（`Col02`）
  拆成两个 HID 集合，Windows 严格校验报文长度，导致单个句柄无法完成握手 + 寄存器读写。
  改为打开两个集合、用虚拟句柄按 report id 路由。
- **长寄存器读被拒**：`0x83` 操作码未被视为长报文，且 Bolt 固件要求这类请求以 7 字节短报文发出、
  以 20 字节长报文回复。
- **序列号读取中断**：个别固件不响应 `BOLT_UNIQUE_ID`，`extract_serial()` 抛 `AttributeError`。
- **无 GTK 环境导入失败**：通知模块只捕获 `ValueError`，Windows 上抛的是 `ImportError`。

### 变更

- 目录结构整理为 `src/`、`patches/`、`tools/`、`docs/`，构建脚本不再硬编码 Python 安装路径
- 代码拆出数据层 / CLI / GUI，新增 `__version__`，CLI 改用 `argparse`
- 设备名与序列号去除固件返回的定长填充空格
- 通过 `ruff check`；补丁统一 LF 换行

### 已知问题

- 解除配对依赖 `Receiver._unpair_device()` 私有方法
- 未做代码签名，Windows SmartScreen 可能拦截
- 仅支持 Windows
- `pip install solaar` 在 Windows 上会因 PyGObject 构建失败，
  必须按 README 的两步安装（`--no-deps` + 手动补齐 PyYAML/psutil/typing_extensions）
