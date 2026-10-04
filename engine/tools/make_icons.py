"""
make_icons.py — 生成应用图标全套

## 设计意图
图标是「频谱 + 音符」的几何抽象。配色取自四方向之一的**深海声谱（Abyss）**：
深靛蓝底（非纯黑，守红线 B-2）+ 青绿/电光蓝柱状频谱。不用品红。

## 输出
Tauri 2 要求 `src-tauri/icons/icon.ico` 存在（Windows 资源嵌入），
同时需要 32/128/256/512 的 PNG 供各平台使用。本脚本一次生成全套。

用法：python engine/tools/make_icons.py
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw

# 与 theme/themes.ts 的 Abyss 方向保持一致
BG_TOP = (20, 24, 43)      # #14182B 深靛
BG_BOTTOM = (28, 34, 58)
BAR_LOW = (34, 211, 190)   # 青绿 #22D3BE
BAR_HIGH = (96, 165, 250)  # 电光蓝 #60A5FA
GLOW = (45, 212, 191)


def draw_icon(size: int) -> Image.Image:
    """画一枚 size×size 的图标。"""
    ss = size * 4  # 超采样后缩放，边缘更干净
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方形底（Windows 会再套一层圆角，这里做小圆角）
    radius = int(ss * 0.22)
    d.rounded_rectangle(
        [0, 0, ss - 1, ss - 1],
        radius=radius,
        fill=BG_TOP,
    )
    # 底部渐变：逐行叠加，制造深度
    for y in range(ss):
        t = y / max(1, ss - 1)
        c = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
        d.line([(0, y), (ss, y)], fill=c + (255,))

    # 频谱柱：中间高两边低，模拟音频包络
    n_bars = 7
    margin = ss * 0.22
    usable = ss - margin * 2
    gap = ss * 0.032
    bar_w = (usable - gap * (n_bars - 1)) / n_bars
    heights = [0.34, 0.58, 0.86, 1.0, 0.78, 0.52, 0.30]
    base_y = ss * 0.74

    for i, hf in enumerate(heights):
        x0 = margin + i * (bar_w + gap)
        h = usable * 0.52 * hf
        y0 = base_y - h
        c = tuple(
            int(BAR_LOW[j] + (BAR_HIGH[j] - BAR_LOW[j]) * (i / (n_bars - 1)))
            for j in range(3)
        )
        d.rounded_rectangle(
            [x0, y0, x0 + bar_w, base_y],
            radius=int(bar_w * 0.42),
            fill=c + (255,),
        )

    # 顶部一道细高光，呼应 Abyss 的横向频谱线
    ly = int(ss * 0.30)
    d.line(
        [(margin, ly), (ss - margin, ly)],
        fill=GLOW + (110,),
        width=max(1, int(ss * 0.012)),
    )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    # engine/tools → 项目根
    root = os.path.dirname(os.path.dirname(here))
    out_dir = os.path.join(root, "src-tauri", "icons")
    os.makedirs(out_dir, exist_ok=True)

    master = draw_icon(1024)
    master.save(os.path.join(out_dir, "icon.png"))

    for size in (32, 128, 256, 512):
        draw_icon(size).save(os.path.join(out_dir, f"{size}x{size}.png"))
        if size == 256:
            draw_icon(size).save(os.path.join(out_dir, "128x128@2x.png"))
        if size == 512:
            draw_icon(size).save(os.path.join(out_dir, "icon.png"))

    # Windows 资源需要的 .ico（多尺寸合一）
    ico_sizes = [16, 32, 48, 64, 128, 256]
    frames = [draw_icon(s) for s in ico_sizes]
    frames[0].save(
        os.path.join(out_dir, "icon.ico"),
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=frames[1:],
    )

    print(f"图标已生成：{out_dir}")
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        print(f"  {f:20s} {os.path.getsize(p) // 1024}KB")


if __name__ == "__main__":
    main()
