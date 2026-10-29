# 第三方组件与许可声明

本项目整体以 **GNU GPL v2 或更高版本（GPL-2.0-or-later）** 发布，见 [LICENSE](LICENSE)。
之所以是这个许可，而不是 MIT/Apache：项目的补丁修改了 **Solaar** 的源代码，Solaar 是
GPL-2.0-or-later 项目，其衍生作品必须沿用同一许可。

## 1. 被修改的上游代码（重要）

| 上游 | 版本 | 许可 | 本项目对其所做的修改 |
| --- | --- | --- | --- |
| [Solaar](https://github.com/pwr-Solaar/Solaar) | 1.1.20 | GPL-2.0-or-later | `patches/` 下 4 个补丁，涉及 2 个包、4 个文件 |

被修改的文件是：

- `hidapi/hidapi_impl.py`（Solaar 随包分发的 hidapi Python 绑定）
- `logitech_receiver/base.py`
- `logitech_receiver/receiver.py`
- `logitech_receiver/desktop_notifications.py`

按 GPL-2.0 §2(a) 的要求，每个被修改的文件都在文件头保留了原始版权声明，
并新增了 `MODIFIED FILE` 声明块，写明修改日期与修改内容摘要。
修改后的完整源代码可通过 `patches/*.patch` + 上游 1.1.20 源码重建，步骤见
[README 的「构建」一节](README.md#从源码构建)。

## 2. 运行时依赖（源码安装时由 pip 拉取）

| 组件 | 许可 | 用途 |
| --- | --- | --- |
| [Solaar](https://github.com/pwr-Solaar/Solaar) | GPL-2.0-or-later | HID++ / Bolt 协议实现 |
| [cython-hidapi](https://github.com/trezor/cython-hidapi) | BSD-3-Clause / GPL-3.0 / 原始许可 三选一 | HID 设备访问，本项目按 **BSD-3-Clause** 使用 |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | 检测 Logi Options+ 是否在运行 |
| [PyYAML](https://pyaml.org/) | MIT | Solaar 的配置读写 |

## 3. 打进发布包（`dist/BoltManager/`）的第三方二进制

发布包是 PyInstaller 的 one-folder 形式，`_internal/` 里包含以下运行时，
分发时请一并保留本声明文件：

| 组件 | 许可 | 说明 |
| --- | --- | --- |
| [Python](https://www.python.org/) 3.13 | PSF License 2.0 | 解释器 `python313.dll` |
| [PyInstaller](https://pyinstaller.org/) 6.x | GPL-2.0-or-later **含 bootloader 例外条款** | 打包器与 bootloader |
| [hidapi](https://github.com/libusb/hidapi) | BSD-3-Clause（HIDAPI 原许可） | `hidapi.dll` |
| [OpenSSL](https://www.openssl.org/) 3.x | Apache-2.0 | `libcrypto-3-x64.dll`、`libssl-3-x64.dll` |
| [Tcl/Tk](https://www.tcl.tk/) 8.6 | BSD-style（Tcl/Tk License） | `tcl86t.dll`、`tk86t.dll`、`tcl8/`、`_tcl_data/` |
| [libffi](https://github.com/libffi/libffi) | MIT | `libffi-8.dll` |
| [zlib](https://zlib.net/) | zlib License | `zlib1.dll` |
| [Expat](https://libexpat.github.io/) | MIT | `pyexpat.pyd` |
| bzip2 / liblzma | bzip2 License / 0BSD | `_bz2.pyd`、`_lzma.pyd` |
| Microsoft UCRT / VCRuntime | Microsoft 可再分发组件条款 | `ucrtbase.dll`、`VCRUNTIME140*.dll`、`api-ms-win-*.dll` |

## 4. 关于二进制分发

如果你把 `dist/BoltManager/` 整个目录（或打包后的 zip）发给别人，请注意：

1. 该二进制受 **GPL-2.0-or-later** 约束，接收者有权获得对应源代码。
2. 因此请在发布页同时提供本仓库链接，或附上 `patches/` 与构建脚本。
3. 保留 `LICENSE` 与本文件，不要删除。

## 5. 商标

"Logitech"、"Logi"、"Bolt"、"MX" 等是 Logitech Europe S.A. 及其关联公司的商标。
本项目是**非官方**第三方工具，与 Logitech 无任何隶属或背书关系；项目名仅用于说明
兼容的硬件型号。Solaar 与本项目之间同样没有隶属关系。
