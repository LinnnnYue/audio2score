"""
make_icons.py — 从 SVG 母版生成应用图标全套

## 为什么改成 SVG 驱动（2026-10-07）
主上验收官网图标后发话：「官网的图标设计的很不错，比软件图标好看，
可以全权替换软件的所有图标包括小图标了」。

旧方案（霜蓝玻璃 · 浅霜蓝底 + 深钢蓝频谱柱）是 PIL 手绘的：
改一个圆角半径要动代码、改一次配色要重算三段渐变。新方案改用 SVG 母版，
形状与官网图标同源（六边形 + 双八分音符，青 #6fe3ff → 紫 #a99cff），
在矢量层面可读可改，栅格化交给 `tauri icon`（项目自带的官方工具）。

## 母版
  src-tauri/icons/source/icon.svg        —— 主母版（48px 起使用）
  src-tauri/icons/source/icon-small.svg  —— 小尺寸版（16/24/32px 帧，图案放大 + 线加粗）
  src-tauri/icons/source/favicon.svg     —— 前端标签页图标（本脚本顺带同步）

## 造型（2026-10-07 定稿）
**白底圆角方块 + 青(#6fe3ff)→紫(#a99cff) 渐变六边形音符**，几何与官网 logo 逐字一致。
底板照搬官网页头 .brand-mark 在 light 主题下的规格：
白底 rgba(255,255,255,.94) + 极淡描边 rgba(30,70,150,.20) + 冷蓝微光。

⚠️ 中途走过弯路：第一版做成**深墨底**（以为官网是深色），主上指出官网那枚是白底、
「很干净很纯净很舒服」，遂改回白底。造型的权威来源是官网页头，不是印象。

## 为什么要给小尺寸单独一版
线宽是相对量。主母版在 1024 画布上线宽 2，缩到 16px 只剩 0.6px ——
糊成一团。小尺寸版把图案放大到约 70%、线宽加到 2.5，16px 下线宽约 1px，
六边形与符头都还认得出来。Windows 任务栏/开始菜单常用的正是 24–32px。

## 输出
  src-tauri/icons/  全套 PNG + icon.ico（7 档：16/24/32 小尺寸版，48/64/128/256 主母版）

用法：python engine/tools/make_icons.py
前置：src-tauri 下可执行 npx tauri（项目 node_modules 自带 @tauri-apps/cli）
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile

from PIL import Image

# ── 尺寸规划 ────────────────────────────────────────────────
ICO_SMALL = (16, 24, 32)      # 走 icon-small.svg
ICO_LARGE = (48, 64, 128, 256)  # 走 icon.svg

# PNG 组：各平台用，一律取主母版的精确栅格
PNG_MAP = {
    "32x32.png": "32x32.png",
    "128x128.png": "128x128.png",
    "128x128@2x.png": "128x128@2x.png",
    "256x256.png": "128x128@2x.png",
    "512x512.png": "icon.png",
    "icon.png": "icon.png",
}


def render_svg(svg: str, out_dir: str, cwd: str) -> None:
    """调用项目自带的 tauri icon 把 SVG 栅格化成全套。

    走 `cmd /c` + 参数列表：既避免 shell 引号拼接，又让中文路径经由
    Windows 宽字符 API 传递（此前的项目路径里带空格与中文）。
    """
    exe = ["cmd", "/c", "npx"] if os.name == "nt" else ["npx"]
    cmd = exe + ["tauri", "icon", svg, "-o", out_dir]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True)
    if r.returncode != 0:
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        raise SystemExit(f"tauri icon 失败（{os.path.basename(svg)}）：\n{out}\n{err}")


def pack_ico(frames: list[tuple[int, Image.Image]], out_path: str) -> None:
    """写出多帧 ICO（PNG 压缩帧，Win10/11 原生支持）。

    自己拼而不用 PIL 的 ICO 编码器，是因为需要**每档用不同的源图**：
    PIL 只能拿一张基础图按 sizes 缩放，做不到「16px 用加粗版、256px 用精致版」。

    ICO 结构：ICONDIR(6B) + ICONDIRENTRY(16B × n) + 各帧数据
    """
    blobs: list[bytes] = []
    for _, im in frames:
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    n = len(frames)
    head = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries = b""
    for (size, _), blob in zip(frames, blobs):
        b = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)

    with open(out_path, "wb") as f:
        f.write(head + entries + b"".join(blobs))


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))          # engine/tools → 项目根
    tauri = os.path.join(root, "src-tauri")
    src_dir = os.path.join(tauri, "icons", "source")
    out_dir = os.path.join(tauri, "icons")

    icon_svg = os.path.join(src_dir, "icon.svg")
    small_svg = os.path.join(src_dir, "icon-small.svg")
    for p in (icon_svg, small_svg):
        if not os.path.isfile(p):
            raise SystemExit(f"找不到图标母版：{p}")

    tmp = tempfile.mkdtemp(prefix="a2s-icons-")
    try:
        big_dir = os.path.join(tmp, "big")
        small_dir = os.path.join(tmp, "small")
        render_svg(icon_svg, big_dir, tauri)
        render_svg(small_svg, small_dir, tauri)

        os.makedirs(out_dir, exist_ok=True)

        # ── 1) PNG 组 ──
        for dst, src in PNG_MAP.items():
            shutil.copyfile(os.path.join(big_dir, src), os.path.join(out_dir, dst))

        master = Image.open(os.path.join(out_dir, "icon.png")).convert("RGBA")
        small_master = Image.open(os.path.join(small_dir, "icon.png")).convert("RGBA")

        # ── 2) ICO：小尺寸加粗版 + 大尺寸精致版 ──
        # 32px 直接用 tauri 的精确栅格；16/24 没有现成帧，从 512 缩放。
        large_src = {
            48: "64x64.png",
            64: "64x64.png",
            128: "128x128.png",
            256: "128x128@2x.png",
        }
        frames: list[tuple[int, Image.Image]] = []
        for s in ICO_SMALL:
            if s == 32:
                im = Image.open(os.path.join(small_dir, "32x32.png")).convert("RGBA")
            else:
                im = small_master.resize((s, s), Image.LANCZOS)
            frames.append((s, im))
        for s in ICO_LARGE:
            im = Image.open(os.path.join(big_dir, large_src[s])).convert("RGBA")
            frames.append((s, im.resize((s, s), Image.LANCZOS)))

        ico_path = os.path.join(out_dir, "icon.ico")
        pack_ico(frames, ico_path)

        # ── 自检 1：ico 必须含全部档位 ──
        # 不校验就会静默交付一个只有 16×16 的 exe（本项目真踩过）——
        # Windows 把那张小图放大显示，看起来就是「糊、暗、丑」。
        got = sorted(s for s, _ in frames)
        want = sorted(ICO_SMALL + ICO_LARGE)
        if got != want:
            raise SystemExit(f"icon.ico 档位不完整：期望 {want}，实际 {got}")

        # ── 自检 2：四角必须透明 ──
        # 渐变一旦被逐行涂满整个画布，圆角就被填平，交出去是个直角砖头。
        with Image.open(os.path.join(out_dir, "256x256.png")) as png:
            rgba = png.convert("RGBA")
            w, h = rgba.size
            corners = [rgba.getpixel(p)[3] for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        if any(a > 8 for a in corners):
            raise SystemExit(f"四角未透明（alpha={corners}）：圆角被填平了")

        # ── 自检 3：小尺寸下认得出 + 底板必须是白底 ──
        #
        # 判据得跟着设计走。当前方案是「白底 + 青紫渐变线框」（照搬官网页头），
        # 于是守两件事：
        #   a) 16px 时线条仍成"形" —— 非白像素太少就说明糊没/消失了
        #   b) 底板是浅色（白底）—— 哪天被误改回深底，在这拦下
        #
        # 沿革：旧版守「中心平均亮度 ≥ 140」（亮底方案）→ 换深墨底时改成
        # 「极差 ≥ 90」（深底上的亮线）→ 换回白底后极差天然只有 ~60，必然误报。
        # 三次换判据的教训：判据本身也是设计的一部分，改设计就得重审判据。
        with Image.open(ico_path) as ico:
            ico.size = (16, 16)
            tiny = ico.convert("RGBA")
        ink = 0
        for y in range(tiny.size[1]):
            for x in range(tiny.size[0]):
                r, g, b, a = tiny.getpixel((x, y))
                if a > 60 and (255 - min(r, g, b)) > 40:
                    ink += 1
        if ink < 18:
            raise SystemExit(f"16px 下图案几乎不可辨（非白像素 {ink} < 18），会糊成一团")

        # 底板取顶部中央（图案在中心，这一点必是底）。白底 → 三通道都很亮。
        r, g, b, a = master.getpixel((master.size[0] // 2, int(master.size[1] * 0.06)))
        if a > 0 and min(r, g, b) < 200:
            raise SystemExit(
                f"底板不是白底（#{r:02x}{g:02x}{b:02x}，最小值 {min(r,g,b)} < 200）—— "
                "外观红线：底板须为浅色，禁纯黑"
            )

        # ── 顺带同步前端 favicon（同一份源，避免两处漂移）──
        fav_src = os.path.join(src_dir, "favicon.svg")
        fav_dst = os.path.join(root, "src", "public", "favicon.svg")
        if os.path.isfile(fav_src) and os.path.isdir(os.path.dirname(fav_dst)):
            shutil.copyfile(fav_src, fav_dst)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"图标已生成：{out_dir}")
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p):
            print(f"  {f:20s} {os.path.getsize(p) // 1024}KB")
    print(f"  icon.ico 档位：{got}（16/24/32 加粗版，48+ 精致版）")
    print(f"  16px 非白像素 {ink} / 四角透明 ✓ / 底板白底 ✓")
    print("  favicon 已同步 → src/public/favicon.svg")


if __name__ == "__main__":
    sys.exit(main())
