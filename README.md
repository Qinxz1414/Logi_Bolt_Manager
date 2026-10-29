# Logi Bolt 接收器管理器（BoltManager）

给 Windows 用的罗技 **Logi Bolt** 接收器小工具：读取接收器上已配对的设备清单，并能解除指定设备的配对。
底层复用 **Solaar 1.1.20** 的 `logitech_receiver`，针对 Windows + Bolt 接收器（`046D:C548`）打了补丁——这也是本项目最主要的技术价值所在。

```
设备管理器里显示正常、但设备"消失"了 → 用它看接收器里到底还挂着谁
换过鼠标键盘、想把旧设备从接收器里清掉 → 选中 → 解除配对
```

---

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `src/` | 应用源码 |
| `src/bolt_manager.py` | 主程序：tkinter GUI + `--list` 命令行模式 |
| `src/gen_icon.py` | 纯 Python 生成 `bolt.ico`（无第三方依赖） |
| `src/BoltManager.spec` | PyInstaller 打包配置（路径自动推导，不硬编码） |
| `patches/` | **已打补丁的 solaar 源码**，按 site-packages 目录结构摆放 |
| `tools/` | 构建与调试脚本 |
| `tools/rebuild.cmd` | 一键重新打包 exe |
| `tools/apply_patches.cmd` | 一键把补丁还回当前 Python 的 site-packages |
| `tools/solaar-cli.cmd` | 直接调用 solaar 命令行（排查问题时看详细日志） |
| `dist/BoltManager/` | 打包产物（`BoltManager.exe` + `_internal/`） |
| `docs/技术说明.md` | 补丁原理、HID++ / Bolt 协议细节 |

## 直接使用

双击 `dist/BoltManager/BoltManager.exe` 即可，**不需要装 Python**。
注意这是 PyInstaller 的 one-folder 形式，`BoltManager.exe` 和同目录的 `_internal/` 必须待在一起，单独拷 exe 会启动失败。

界面功能：
- 自动列出接收器上的所有配对设备（槽位 / 名称 / 序列号 / 在线状态）
- 选中设备 → 「解除所选设备配对」（双击行也可）
- 状态栏会提示是否检测到 **Logi Options+ 正在运行**（它会抢占接收器，建议先退出）

命令行模式（开发/排查用，要求本机 Python 环境已就绪）：

```bat
python src\bolt_manager.py --list
:: 槽位1  MX Master 3S  序列号=xxxxxxxx  在线
```

## 开发环境

| 项 | 值 |
| --- | --- |
| Python | 3.13（本机为 `C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12`） |
| 依赖 | `solaar==1.1.20`、`hidapi`、`psutil`（运行时）、`pyinstaller`（打包） |
| 关键前置 | `solaar` / `logitech_receiver` **必须是打完补丁的版本**，原版在 Windows 上读不到 Bolt 接收器 |

从零复现环境：

```bat
python -m pip install solaar==1.1.20 hidapi psutil pyinstaller
tools\apply_patches.cmd          :: 覆盖 4 个文件为补丁版
python src\bolt_manager.py --list :: 验证能否读到设备
tools\rebuild.cmd                :: 重新打包到 dist\BoltManager
```

## 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| 提示"未找到 Logi Bolt 接收器" | 接收器没插好、插在 USB Hub 上不稳，或设备管理器里 `MI_02` 接口被禁用；也可能是 Options+ 独占 |
| 解除配对失败 | 先退出 Logi Options+ / Logi Tune，它们会和本工具抢同一个 HID 句柄 |
| exe 换了机器打不开 | 只拷了 exe，漏了 `_internal/` 目录 |
| 打包时报找不到 hidapi.dll | `pip install hidapi`，spec 会自动在 `sys.base_prefix` 下找 |
| 想确认补丁是否生效 | `python -c "from logitech_receiver import base; print(hex(base.SOLAAR_SOFTWARE_ID))"` → 应为 `0xb` |

## 已知限制

- 只做"列设备 + 解配对"，不做配对、不改 DPI/按键（需要按键重映射请用官方 Options+）。
- 解除配对走的是 `Receiver._unpair_device()` 私有方法，Solaar 升级可能改名。
- `--list` 与 GUI 共用同一进程逻辑，同一时刻只能有一个实例操作接收器。
- `--list` 跑到 GBK 控制台时中文会乱码（编码问题，不影响功能）；先 `chcp 65001` 或直接用 `python src\bolt_manager.py --list`。
- 补丁基于 Solaar **1.1.20** 的确切代码，升级 Solaar 后需重新对照 `docs/技术说明.md` 逐点合并。
