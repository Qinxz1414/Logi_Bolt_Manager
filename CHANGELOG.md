# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-10-29

软件本身的第一次大改进：更完整的信息展示、可脚本化的写操作、更顺手的界面。

### 新增

- **接收器信息条**（GUI 顶部）：接收器名称、序列号、已配对 x/上限、剩余可配对数、USB 接口号
- **设备详情面板**（GUI 右侧）：选中设备的类型 / 产品 ID (WPID) / 序列号 / 槽位 / 连接状态，
  首次刷新自动选中第一行
- **复制 / 导出 CSV**：一键把设备清单以 TSV 复制到剪贴板，或导出为 Excel 友好的 CSV（utf-8-sig）
- **自动刷新**：5/15/30/60 秒定时重读接收器，开关状态持久化
- **高 DPI 感知**：高分屏下界面不再是模糊的拉伸位图
- **窗口尺寸/位置记忆**：保存在 `%APPDATA%\BoltManager\settings.json`，换分辨率/拔掉副屏时自动退回安全值
- **GUI 单实例保护**：第二个实例会弹窗提示退出（两个进程同时读写接收器会互相打断）
- CLI 新命令：
  - `--info`：接收器 + 设备完整信息
  - `--unpair SLOT [-y]`：脚本化解除配对（非交互环境必须显式 `--yes`）
  - `--watch [SECONDS]`：持续监控设备增减与上下线，只在变化时输出（Ctrl+C 退出）
- `--json` 现在覆盖 `--list` / `--info` / `--unpair`，错误也输出结构化 JSON
- 单元测试（`tests/`，pytest，不需要插硬件）：数据层格式化、设置读写、解除配对的三条路径

### 变更

- 代码拆分为 `src/bolt_core.py`（数据层，无 GUI 依赖）+ `src/bolt_manager.py`（CLI + GUI）
- 错误统一为 `BoltError` 家族，附可操作的建议（接收器被占用 / 未插入 / 槽位为空各有不同提示）
- GUI 打包配置把 `bolt.ico` 一并打进运行时资源，窗口左上角有图标

### 修复

- 解除配对改走 Solaar 公开方法 `Receiver.force_unpair_slot()`，不再依赖 `_unpair_device()`
  私有方法（旧版本仍会自动回退）——原 1.0.0 已知问题之一消除
- 状态栏与工具栏重叠的布局错误
- 序列号过长时在信息条中截断显示，完整值见详情面板

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

### 已知问题

- 未做代码签名，Windows SmartScreen 可能拦截
- 仅支持 Windows
- `pip install solaar` 在 Windows 上会因 PyGObject 构建失败，
  必须按 README 的两步安装（`--no-deps` + 手动补齐 PyYAML/psutil/typing_extensions）
