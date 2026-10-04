"""
make_icon_preview.py — 生成图标预览对照图

## 为什么要有它
图标是唯一「无法靠日志验证」的产物：`icon.ico 33KB` 完全可能是一张
糊成一坨的方块。数值自检（亮度 / 对比度 / 圆角 alpha）能挡住定量问题，
但「好不好看、在小尺寸下认不认得出」只有眼睛能判。
主上每次验收图标都需要一张能直接看的对照图 —— 与其临时拼，不如固化下来。

## 对照内容
  1. 深色任务栏模拟条：16/24/32/48/64 实际像素尺寸并排
  2. 大图：浅底与深底各一枚 256
  3. 透明棋盘格：放大看圆角是否真的透（圆角被渐变填平是本项目踩过的坑）

用法：python engine/tools/make_icon_preview.py
输出：docs/icon-preview.png
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",   # 微软雅黑
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    icons = os.path.join(root, "src-tauri", "icons")
    src = os.path.join(icons, "icon.png")
    if not os.path.isfile(src):
        raise SystemExit("找不到 icon.png，请先运行 make_icons.py")

    master = Image.open(src).convert("RGBA")
    W, H = 900, 640
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(canvas)

    f_title = _font(20)
    f_label = _font(13)
    f_tiny = _font(11)

    d.text((24, 20), "扒谱助手 · 图标预览", font=f_title, fill=(28, 40, 52))
    d.text((24, 50), "霜蓝玻璃（frost）· 浅霜蓝底 + 深钢蓝频谱柱", font=f_label, fill=(110, 126, 140))

    def place(sz: int, x: int, y: int) -> Image.Image:
        r = master.resize((sz, sz), Image.LANCZOS)
        canvas.alpha_composite(r, (x, y))
        return r

    # ── 1) 深色任务栏模拟条 ──
    ty = 90
    d.rounded_rectangle([24, ty, W - 24, ty + 78], radius=10, fill=(34, 36, 42, 255))
    d.text((40, ty + 8), "深色背景（任务栏 / 开始菜单）", font=f_tiny, fill=(150, 158, 170))
    x = 44
    for sz in (16, 24, 32, 48, 64):
        place(sz, x, ty + 26 + (64 - sz) // 2)
        d.text((x, ty + 66), f"{sz}px", font=f_tiny, fill=(150, 158, 170))
        x += sz + 26

    # ── 2) 大图：浅底 / 深底 ──
    by = 200
    d.rounded_rectangle([24, by, 444, by + 300], radius=12, fill=(245, 246, 248, 255))
    d.rounded_rectangle([456, by, 876, by + 300], radius=12, fill=(32, 34, 40, 255))
    d.text((44, by + 12), "浅色背景 256px", font=f_tiny, fill=(120, 130, 140))
    d.text((476, by + 12), "深色背景 256px", font=f_tiny, fill=(150, 158, 170))
    place(256, 106, by + 36)
    place(256, 538, by + 36)

    # ── 3) 透明棋盘格：验证圆角真的透明 ──
    cy = 530
    d.text((24, cy), "透明棋盘格 128px（四角应为透明，无直角方块）", font=f_label, fill=(110, 126, 140))
    cb = Image.new("RGBA", (160, 160), (255, 255, 255, 255))
    cd = ImageDraw.Draw(cb)
    for gy in range(0, 160, 16):
        for gx in range(0, 160, 16):
            if (gx // 16 + gy // 16) % 2:
                cd.rectangle([gx, gy, gx + 15, gy + 15], fill=(214, 220, 226, 255))
    canvas.alpha_composite(cb, (24, cy + 26))
    place(128, 40, cy + 42)

    out_dir = os.path.join(root, "docs")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "icon-preview.png")
    canvas.convert("RGB").save(out)
    print(f"预览图已生成：{out}")


if __name__ == "__main__":
    main()
