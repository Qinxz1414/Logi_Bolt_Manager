# 贡献指南

感谢愿意帮忙。这个项目很小，流程也尽量简单。

## 报 Issue 前先做三件事

1. 确认接收器直连 USB 口（不要经 Hub），并且设备管理器里能看到 `MI_02` 接口。
2. 退出 Logi Options+ / Logi Tune / G HUB 等会占用 HID 的软件后重试。
3. 附上排查信息：

```bat
python src\bolt_manager.py --version
python src\bolt_manager.py --list --json
python src\bolt_manager.py --list --debug 2> debug.log
python -c "import hidapi.hidapi_impl as h; print('patched:', hasattr(h,'_BoltSplitHandle'))"
```

> `--list --json` 里的序列号属于个人设备信息，介意的话可以打码后再贴。

## 开发环境

```bat
git clone <this-repo> && cd logi-bolt-manager
python -m venv .venv && .venv\Scripts\activate
pip install --no-deps solaar==1.1.20    :: 必须 --no-deps，PyGObject 在 Windows 上装不了
pip install -r requirements-dev.txt
python tools\apply_patches.py          :: 必须有补丁，否则读不到 Bolt 接收器
python tools\fetch_hidapi.py           :: 下载 hidapi.dll（源码运行也需要）
python src\bolt_manager.py --list      :: 冒烟测试
python tools\verify_patches.py         :: 无硬件自检
python -m ruff check src tools         :: 提交前必过
```

## 改代码的约定

- Python 3.10+，`from __future__ import annotations` 已在用，类型标注尽量补全。
- 行宽 120，`ruff` 配置见 `pyproject.toml`，提交前跑 `ruff check`（CI 也会跑）。
- GUI 里所有可能阻塞的操作（读 HID）必须走 `run_bg()` 后台线程，结果通过队列回主线程。
- 注释用中文，写"为什么"而不是"做了什么"——踩过的坑请写进注释或 `docs/技术说明.md`。

## 改补丁的流程

`patches/` 是生成物，不要手改 `.patch` 文件本身。正确顺序：

1. 在本地 site-packages 里改 `solaar` / `logitech_receiver` / `hidapi` 的对应文件；
2. 跑 `python src\bolt_manager.py --list` 验证功能真的通了；
3. 修改被改文件头部的 `MODIFIED FILE` 声明（写清本次改了什么）；
4. 下载对应版本 Solaar 源码，执行 `python tools\make_patches.py --upstream <lib 目录>`；
5. `python tools\apply_patches.py --revert` → `--check` → 应用，确认可逆；
6. 提交 `patches/` 与文档的变更。

## 新增设备兼容性

如果你在别的 Bolt 设备（键盘、耳机、轨迹球）上验证通过，欢迎提 PR 更新 README 的
「已验证环境」表，或直接在 Issue 里留下设备型号 + 槽位 + 在线状态截图。

## 提交信息

参考 Conventional Commits 的轻量版本：

```
feat: 支持显示设备电量
fix: 修正 0x83 报文在某些固件上的超时
docs: 补充升级 Solaar 的步骤
chore: 升级 ruff 到 0.7
```

## 许可

本项目以 GPL-2.0-or-later 发布。提交 PR 即表示你同意以同一许可提供你的贡献，
并确认你有权这样做（例如没有把别处的专有代码贴进来）。
