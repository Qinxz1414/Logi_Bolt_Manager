"""生成启动闪屏图 src/splash.png（单文件 exe 解压期间显示的等待画面）。

只在图标/文案改动后需要重跑一次：
    python tools/make_splash.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "src" / "bolt.ico"
OUT = ROOT / "src" / "splash.png"

W, H = 460, 240
BG = (31, 36, 48)          # 深灰蓝，和主界面 ttk 主题协调
CARD = (42, 49, 64)
FG = (240, 242, 245)
MUTED = (150, 158, 172)
ACCENT = (0, 132, 255)

TITLE = "Logi Bolt 接收器管理器"
SUBTITLE = "正在启动，请稍候…"


def _font(size: int) -> ImageFont.FreeTypeFont:
    """优先微软雅黑（含中文），退回其它可用字体。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "seguiemj.ttf"):
        path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def main() -> int:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((1, 1, W - 2, H - 2), radius=14, outline=CARD, width=2)

    # 左侧应用图标
    if ICON.is_file():
        icon = Image.open(ICON)
        icon = icon.resize((72, 72), Image.LANCZOS).convert("RGBA")
        img.paste(icon, (36, (H - 72) // 2), icon)

    # 右侧文案
    title_font, sub_font = _font(24), _font(15)
    text_x = 132
    draw.text((text_x, 82), TITLE, font=title_font, fill=FG)
    draw.text((text_x, 118), SUBTITLE, font=sub_font, fill=MUTED)

    # 进度条底 + 一小段高亮（纯装饰，提示"在加载"）
    bar = (text_x, 158, W - 40, 164)
    draw.rounded_rectangle(bar, radius=3, fill=CARD)
    draw.rounded_rectangle((text_x, 158, text_x + 96, 164), radius=3, fill=ACCENT)

    img.save(OUT)
    print(f"[OK] {OUT}  {img.size[0]}x{img.size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
