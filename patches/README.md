# 补丁说明

这里放的是对 **Solaar 1.1.20** 的修改，采用标准 unified diff（`git apply` 可直接消费）。
之所以不直接放"改好的整份文件"：diff 能让人一眼看清改了哪几行，便于 review，也方便向上游提 PR。

## 文件与作用

| 补丁 | 改动 | 作用 |
| --- | --- | --- |
| `hidapi/hidapi_impl.py.patch` | +109 / −0，8 hunks | Bolt 接收器双集合虚拟句柄；`0x83` 长寄存器读改走短报文 |
| `logitech_receiver/base.py.patch` | +11 / −1 | `0x83` 也按长报文封装 |
| `logitech_receiver/receiver.py.patch` | +9 / −1 | `extract_serial()` 容错 `None` |
| `logitech_receiver/desktop_notifications.py.patch` | +9 / −1 | 通知探测多捕获 `ImportError` |

路径中的 `hidapi/`、`logitech_receiver/` 就是 site-packages 下的相对路径，
补丁按 `-p1` 语义生成（`a/hidapi/hidapi_impl.py` → `b/hidapi/hidapi_impl.py`）。

## 应用 / 撤销

```bat
python tools\apply_patches.py --check     :: 只检查能否干净应用
python tools\apply_patches.py             :: 应用（幂等）
python tools\apply_patches.py --revert    :: 撤销，逐字节还原成上游原样
```

脚本会优先用 `git apply`（强制关闭 autocrlf，保证 LF 不被改写），没有 git 时回退到 GNU `patch`。
应用/撤销都做过字节级校验，`--revert` 后与原版文件完全一致。

## 每个补丁都带 GPL 修改声明

被修改的文件在文件头保留了原始版权，并新增了：

```
## MODIFIED FILE - not the original Solaar source.
## Project: BoltManager - Windows GUI for Logi Bolt receivers
## Date:    2026-10-29
## Base:    Solaar 1.1.20 (GPL-2.0-or-later, see LICENSE)
## Change:  ...
```

这是 GPL-2.0 §2(a) 对"修改过的文件需带显著修改说明"的要求，请勿删除。

## 重新生成

改完 site-packages 里的文件并验证功能后：

```bat
:: 1) 下载对应版本的 solaar 源码包并解压，拿到 lib/ 目录
python tools\make_patches.py --upstream C:\path\to\solaar-1.1.20\lib
:: 2) 检查差异是否只剩你的改动
git diff patches/
```

`make_patches.py` 会统一归一为 LF，并保留 git 风格的 `diff --git` 头。

## 上游关系

上游代码：Solaar `1.1.20`（GPL-2.0-or-later）。想确认哪些是上游自带的能力、哪些是本项目改的，
见 [../docs/技术说明.md](../docs/技术说明.md) 第 5 节。
