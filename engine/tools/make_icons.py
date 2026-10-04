"""
make_icons.py — 生成应用图标全套

## 设计意图（2026-10-04 重做）
主上验收：「感觉有点暗暗丑丑的」。

旧方案是深海声谱（Abyss）——深靛底 + 青绿柱。问题不在配色本身，
而在**图标不是主题预览**：桌面图标常年躺在任务栏与开始菜单里，
底色比应用内背景更暗、更闷，在缩略图上几乎糊成一坨深色方块。
且默认主题已改为霜蓝玻璃（frost，浅色），图标却还是深底的，两者不搭。

新方案：**霜蓝玻璃 · 亮调**。
  · 底：浅霜蓝垂直渐变（#F5FAFD → #A6C7DE），自带玻璃高光与内描边
  · 主体：5 根圆角柱（音频包络），深钢蓝上浅下深渐变，中柱加亮做焦点
  · 谱线：柱下一道半透明横线，把「频谱」锚成「谱面」
亮底 + 深柱的组合在两个方向上都站得住：深色任务栏里亮底醒目，
浅色任务栏里深柱清晰 —— 恒定轮廓，不挑配色。

红线依旧：不用品红，不用纯黑（底为浅蓝白，柱为深钢蓝）。

## 输出
Tauri 2 要求 `src-tauri/icons/icon.ico` 存在（Windows 资源嵌入），
同时需要 32/128/256/512 的 PNG 供各平台使用。本脚本一次生成全套。

用法：python engine/tools/make_icons.py
"""

from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageStat

# 与 theme/themes.ts 的 frost（霜蓝玻璃）方向保持一致
BG_TOP = (245, 250, 253)      # #F5FAFD 顶光
BG_MID = (214, 231, 242)      # #D6E7F2
BG_BOTTOM = (166, 199, 222)   # #A6C7DE 底影
BAR_TOP = (60, 147, 192)      # #3C93C0
BAR_BOTTOM = (23, 88, 126)    # #17587E
BAR_FOCUS_TOP = (82, 168, 212)  # #52A8D4 中柱加亮
BAR_FOCUS_BOTTOM = (31, 111, 156)  # #1F6F9C
BASELINE = (31, 111, 156)

# 音频包络：中间高、两侧低
HEIGHTS = [0.36, 0.62, 1.0, 0.70, 0.42]


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _bg_color(t: float) -> tuple[int, int, int]:
    """三段式底色渐变：顶部更亮的区间更长，读起来像顶光落在玻璃上。"""
    if t < 0.55:
        return _lerp(BG_TOP, BG_MID, t / 0.55)
    return _lerp(BG_MID, BG_BOTTOM, (t - 0.55) / 0.45)


def draw_icon(size: int) -> Image.Image:
    """画一枚 size×size 的图标。"""
    # 超采样后缩放，边缘更干净。大尺寸用 2 倍即可 ——
    # 1024 用 4 倍会造出 4096² 的画布，逐行涂渐变要几秒，收益却看不出来。
    ss = size * (4 if size <= 128 else 2)
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(ss * 0.235)
    mask = Image.new("L", (ss, ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, ss - 1, ss - 1], radius=radius, fill=255)

    # ── 1) 底色渐变（画在独立图层上，再用圆角 mask 贴回）──
    #
    # ⚠️ 绝不能直接往主图逐行涂渐变。踩坑实录：初版（含旧版脚本）是
    # 「先 rounded_rectangle 填底，再 for y: d.line([(0,y),(ss,y)])」——
    # 逐行线**横贯整个画布**，把四角已经挖掉的圆角又填了回来。
    # 体检时一眼看见：四角 alpha 全是 255。等于交付一个直角方块，
    # Windows 任务栏上就是块硬邦邦的深色砖头 —— 主上说的「暗暗丑丑」。
    grad = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(ss):
        gd.line([(0, y), (ss, y)], fill=_bg_color(y / max(1, ss - 1)) + (255,))
    img = Image.composite(grad, Image.new("RGBA", (ss, ss), (0, 0, 0, 0)), mask)

    # ── 2) 玻璃光泽：上半部叠一层白色透明渐变（同样裁进圆角）──
    gloss = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    gl = ImageDraw.Draw(gloss)
    span = int(ss * 0.52)
    for y in range(span):
        a = int(78 * (1 - y / span) ** 1.7)
        if a:
            gl.line([(0, y), (ss, y)], fill=(255, 255, 255, a))
    img = Image.alpha_composite(
        img,
        Image.composite(gloss, Image.new("RGBA", (ss, ss), (0, 0, 0, 0)), mask),
    )
    d = ImageDraw.Draw(img)

    # ── 3) 内描边：顶部提亮、底部压暗，玻璃的厚度感全在这一圈 ──
    inset = max(1, int(ss * 0.011))
    d.rounded_rectangle(
        [inset, inset, ss - 1 - inset, ss - 1 - inset],
        radius=max(2, radius - inset),
        outline=(255, 255, 255, 150),
        width=max(1, int(ss * 0.009)),
    )

    # ── 4) 频谱柱：垂直居中于画布 ──
    n = len(HEIGHTS)
    margin = ss * 0.215
    usable = ss - margin * 2
    gap = ss * 0.055
    bar_w = (usable - gap * (n - 1)) / n
    max_h = ss * 0.60
    # 让「最高柱 + 基线」这一组在画布上垂直居中：base = 中心 + 最高柱高的一半
    base_y = ss * 0.5 + max_h * 0.5

    for i, hf in enumerate(HEIGHTS):
        x0 = margin + i * (bar_w + gap)
        h = max_h * hf
        y0 = base_y - h
        focused = i == n // 2
        top = BAR_FOCUS_TOP if focused else BAR_TOP
        bottom = BAR_FOCUS_BOTTOM if focused else BAR_BOTTOM
        bar = Image.new("RGBA", (max(1, int(bar_w)), max(1, int(h))), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bar)
        bh = bar.height
        for y in range(bh):
            c = _lerp(top, bottom, y / max(1, bh - 1))
            bd.line([(0, y), (bar.width, y)], fill=c + (255,))
        r = int(bar_w * 0.45)
        bmask = Image.new("L", bar.size, 0)
        ImageDraw.Draw(bmask).rounded_rectangle(
            [0, 0, bar.width - 1, bar.height - 1], radius=r, fill=255
        )
        img.paste(bar, (int(x0), int(y0)), bmask)

    # ── 5) 谱线：柱下一道半透明横线，把频谱锚成「谱面」 ──
    ly = base_y + ss * 0.035
    lw = max(1, int(ss * 0.022))
    d.rounded_rectangle(
        [margin - ss * 0.02, ly, ss - margin + ss * 0.02, ly + lw],
        radius=lw // 2,
        fill=BASELINE + (72,),
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

    # ── Windows 资源需要的 .ico（多尺寸合一）──
    #
    # ⚠️ 必须以**大图**为基础保存，`sizes=` 才会真正产出各档。
    # 踩坑实录：初版写成
    #     frames = [draw_icon(s) for s in ico_sizes]   # frames[0] 只有 16×16
    #     frames[0].save("icon.ico", sizes=[...], append_images=frames[1:])
    # 结果 icon.ico **只有 16×16 一档**（527 字节）。PIL 的 ICO 编码器是在
    # 基础图上按 `sizes` 缩放，基础图比目标小就放弃那一档；`append_images`
    # 在这里并不被当作「现成的帧」使用。于是 Windows 拿到一个 16×16 的
    # 图标资源，任务栏/开始菜单把它放大显示 —— 看起来就是「糊、暗、丑」。
    # 主上两轮反馈的「感觉有点暗暗丑丑的」，配色只占一半，另一半是这个。
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_path = os.path.join(out_dir, "icon.ico")
    master.save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes])

    # ── 自检：ico 真的含全部尺寸吗 ──
    # 不校验就会静默交付一个只有小图标的 exe —— 这类「看起来没问题」的产物
    # 正是最难被发现的失败。宁可在这里中止。
    with Image.open(ico_path) as ico:
        got = sorted(ico.info.get("sizes") or [])
    want = [(s, s) for s in ico_sizes]
    if got != want:
        raise SystemExit(f"icon.ico 尺寸不完整：期望 {want}，实际 {got}")

    # ── 自检 2：四角必须透明 ──
    # 圆角一旦被渐变抹平，交出去就是个直角方块。这条断言就是为它设的。
    with Image.open(os.path.join(out_dir, "256x256.png")) as png:
        rgba = png.convert("RGBA")
        w, h = rgba.size
        corners = [rgba.getpixel(p)[3] for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    if any(a > 8 for a in corners):
        raise SystemExit(f"四角未透明（alpha={corners}）：渐变把圆角填平了")

    # ── 自检 3：必须是亮调，且缩到 16px 仍分得清柱子 ──
    # 主上的原始抱怨就是「暗暗丑丑的」。用数字守住，不靠肉眼。
    with Image.open(os.path.join(out_dir, "icon.png")) as png:
        full = png.convert("RGBA")
        w, h = full.size
        mid = full.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8))).convert("L")
        # 用 ImageStat 而非 getdata()：后者在 Pillow 14 会被移除
        mean = ImageStat.Stat(mid).mean[0]
        tiny = full.resize((16, 16), Image.LANCZOS).convert("L")
        row = [tiny.getpixel((x, 8)) for x in range(16)]
        row = [v for v in row if v > 0]
        spread = max(row) - min(row)
    if mean < 140:
        raise SystemExit(f"图标偏暗（中心平均亮度 {mean:.1f} < 140）")
    if spread < 60:
        raise SystemExit(f"16px 下柱体对比不足（极差 {spread} < 60），会糊成一团")

    print(f"图标已生成：{out_dir}")
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        print(f"  {f:20s} {os.path.getsize(p) // 1024}KB")
    print(f"  icon.ico 含尺寸：{got}")
    print(f"  中心平均亮度 {mean:.1f} / 16px 对比极差 {spread} / 四角透明 ✓")


if __name__ == "__main__":
    main()
