# 补丁文件清单

这里的 4 个文件是**已打补丁的成品源码**（不是 diff），目录结构对齐 Python 的 `site-packages`，
所以可以直接覆盖安装。

## 文件映射

| 本目录内的文件 | 覆盖到 site-packages 的路径 | 修改要点 |
| --- | --- | --- |
| `hidapi/hidapi_impl.py` | `hidapi/hidapi_impl.py` | Bolt 接收器双集合虚拟句柄；`0x83` 长寄存器读改走短报文 |
| `logitech_receiver/base.py` | `logitech_receiver/base.py` | `0x82/0x83` 强制长报文封装；Centurion 传输支持；固定 HID++ Software ID |
| `logitech_receiver/receiver.py` | `logitech_receiver/receiver.py` | 新增 `BoltReceiver` 类（Bolt 专属寄存器与配对/解配对流程） |
| `logitech_receiver/desktop_notifications.py` | `logitech_receiver/desktop_notifications.py` | gi / Notify 探测加容错，无 GTK 的 Windows 上导入不再报错 |

## 应用方式

推荐用脚本（自动定位当前 Python 的 site-packages）：

```bat
tools\apply_patches.cmd
```

手工方式则是把上表右列当成目标路径，逐个覆盖。**动手前建议先备份原文件**：

```bat
python -c "import sysconfig,shutil,os;sp=sysconfig.get_paths()['purelib'];shutil.copy(os.path.join(sp,'logitech_receiver','base.py'),'base.py.orig')"
```

## 校验

```bat
python -c "from logitech_receiver import base, receiver; print(hex(base.SOLAAR_SOFTWARE_ID)); print(hasattr(receiver,'BoltReceiver'))"
:: 期望输出：0xb  然后  True
```

## 与上游的关系

基于 Solaar `1.1.20`（`solaar/commit` 记录为 `1.1.20-2-g892ff902`），遵循 GPL-2.0-or-later，
每个文件都保留原始版权头。修改点的**原因**见 `docs/技术说明.md`。
