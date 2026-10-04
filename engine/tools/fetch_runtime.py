#!/usr/bin/env python3
"""
fetch_runtime — 拉取并裁剪「随包分发的独立 Python 运行时」

## 为什么需要这个脚本

引导安装的本质是「用 Python 建 venv」。若目标机器**没有 Python**，
第一步就死。故把解释器本身随包分发到 `src-tauri/runtime/python`。

该目录约 50MB，**不入 git**（见 .gitignore）。新克隆的仓库里没有它，
直接打包会得到一个「装完报『内置运行时缺失』」的残废包。本脚本就是
把这份二进制补回来的唯一入口。

## 为什么不用系统已装的 Python 而要多此一举

| 方案 | 前置条件 | 版本可控 | 管理员权限 | 安装包增量 |
|------|---------|---------|-----------|-----------|
| 让用户自己装 Python | 用户会装、且装在 3.10–3.13 | ✗ | 需要 | 0 |
| 引导下载官方安装包 | 联网、要过 UAC 向导 | ✓ | 需要 | 0 |
| **随包分发独立运行时** | **无** | **✓** | **不需要** | ~50MB |

第三行是唯一对小白成立的方案：零点击、零前置、零权限。

## 版本选择的硬约束

torch cu124 的 Windows wheel 只覆盖 cp310–cp313（见 bootstrap.py 的
TORCH_PY_MIN/MAX）。**不能捆 3.14**：完整档会 100% 装不上，且报错是
pip 的「Could not find a version ... (from versions: none)」——
与「镜像不可用」逐字相同，用户会误判成网络问题去反复换源，永远换不好。
本脚本在最后一步会实测版本落点，越界即中止。

## 用法

    python engine/tools/fetch_runtime.py           # 拉取（已存在则跳过）
    python engine/tools/fetch_runtime.py --force    # 重新拉取
    python engine/tools/fetch_runtime.py --check    # 只体检，不下载

## 裁剪清单与理由

删掉的全是**建 venv 与跑 torch/demucs 完全用不到**的东西：

    tcl/ DLLs/tcl86t.dll DLLs/tk86t.dll DLLs/_tkinter.pyd   图形界面运行时
    Lib/tkinter/ Lib/idlelib/ Lib/turtledemo/ Lib/turtle.py  同上
    DLLs/_test*.pyd                                           测试用扩展
    include/ libs/                                            C 扩展编译用

关于最后一项：理论上源码编译 C 扩展需要头文件，但那只在「该包没有
Windows wheel」时才需要，而那时用户机器上还得有 MSVC 工具链——绝大多数
机器没有。留着这几 MB 救不了那种场景，只是白占体积，故一并删。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# ── 被捆绑的运行时版本（改这里 = 换版本，改完必须重跑本脚本的体检）──
PBS_TAG = "20261003"
PY_FULL = "3.13.16"
ASSET = f"cpython-{PY_FULL}+{PBS_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"

# torch cu124 支持的版本区间，与 engine/bootstrap.py 的 TORCH_PY_MIN/MAX 对齐
TORCH_MIN = (3, 10)
TORCH_MAX = (3, 13)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_ROOT = REPO_ROOT / "src-tauri" / "runtime"
PY_DIR = RUNTIME_ROOT / "python"
CACHE_DIR = REPO_ROOT / ".tmp" / "rt"

# 每个镜像前缀会拼在完整 GitHub 地址前。空串 = 直连。
# 实测（本机 2026-10）：直连 0.7~50 KB/s（且频繁超时），gh-proxy.com 达 3.6 MB/s。
# 故加速通道优先，直连作为「代理全挂了」的最后退路。
MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "",
]

# 相对 python/ 的路径，删掉即省体积（见模块文档的裁剪清单）
PRUNE = [
    "tcl",
    "include",
    "libs",
    "Lib/tkinter",
    "Lib/idlelib",
    "Lib/turtledemo",
    "Lib/turtle.py",
    "Lib/test",
    "pythonw.exe",
    "DLLs/tcl86t.dll",
    "DLLs/tk86t.dll",
    "DLLs/_tkinter.pyd",
]
PRUNE_GLOB = [
    "DLLs/_test*.pyd",
    "DLLs/_ctypes_test.pyd",
]


def _force_utf8_stdio() -> None:
    """中文 Windows 的 stdout 默认是 GBK，打印中文与进度符号会炸。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def say(msg: str) -> None:
    print(msg, flush=True)


def _human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _dir_bytes(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def probe_runtime(py: Path) -> tuple[int, int] | None:
    """跑一次解释器问版本号。跑不起来返回 None。"""
    try:
        r = subprocess.run(
            [str(py), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return None
    maj, _, minor = line[-1].partition(".")
    try:
        return int(maj), int(minor)
    except ValueError:
        return None


def download(force: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / ASSET

    if out.is_file() and not force and out.stat().st_size > 1_000_000:
        say(f"[1/4] 已缓存：{out.name}（{_human(out.stat().st_size)}），跳过下载")
        return out

    url_path = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PBS_TAG}/{ASSET.replace('+', '%2B')}"
    )

    last_err: Exception | None = None
    for prefix in MIRRORS:
        url = prefix + url_path
        label = prefix or "直连"
        say(f"[1/4] 下载中（{label}）……")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bapu-fetch-runtime"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                declared = int(resp.headers.get("Content-Length") or 0)
                buf = io.BytesIO()
                got = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    buf.write(chunk)
                    got += len(chunk)
                    if declared:
                        pct = got * 100 // declared
                        print(f"\r      {pct:3d}%  {_human(got)} / {_human(declared)}",
                              end="", flush=True)
                print()
            data = buf.getvalue()
            # 完整性：声明了多少就必须收到多少。截断的 tar.gz 会在解压时报
            # 「unexpected end of file」——比这里直接判出来难查得多。
            if declared and len(data) != declared:
                raise OSError(f"下载不完整：{len(data)} / {declared}")
            out.write_bytes(data)
            say(f"      完成　{_human(len(data))}　sha256={hashlib.sha256(data).hexdigest()[:16]}")
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            say(f"      失败：{e}")

    raise SystemExit(f"所有下载通道均失败，最后错误：{last_err}")


def extract(tar_path: Path) -> None:
    if PY_DIR.exists():
        shutil.rmtree(PY_DIR)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    say(f"[2/4] 解压到 {PY_DIR.relative_to(REPO_ROOT)} ……")
    with tarfile.open(tar_path, "r:gz") as tf:
        # 顶层固定是 python/，PBS 的约定
        tf.extractall(RUNTIME_ROOT)  # noqa: S202
    if not (PY_DIR / "python.exe").is_file():
        raise SystemExit("解压后未找到 python/python.exe —— 资产结构与预期不符")


def prune() -> None:
    say("[3/4] 裁剪（tkinter / tcl / 测试扩展 / C 头文件）……")
    before = _dir_bytes(PY_DIR)
    for rel in PRUNE:
        p = PY_DIR / rel
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink(missing_ok=True)
    for pattern in PRUNE_GLOB:
        for p in PY_DIR.glob(pattern):
            p.unlink(missing_ok=True)
    after = _dir_bytes(PY_DIR)
    say(f"      {_human(before)} → {_human(after)}　（省 {_human(before - after)}）")


def verify() -> None:
    """
    体检。三条断言，任一不过即中止——**打包前的最后一道闸**。

    为什么必须实测建 venv 而不只是问版本：裁剪是「按名字删文件」，
    名字写错就会删掉 venv 真正依赖的东西（如 Lib/venv 或 ensurepip 的
    bundled wheel）。那种残缺只有真建一次 venv 才暴露得出来，
    而它一旦随包发出去，小白用户点「安装」时必炸。
    """
    say("[4/4] 体检……")
    py = PY_DIR / "python.exe"

    v = probe_runtime(py)
    if v is None:
        raise SystemExit("捆绑解释器无法启动")
    if not (TORCH_MIN <= v <= TORCH_MAX):
        raise SystemExit(
            f"版本 {v[0]}.{v[1]} 越界（需 {TORCH_MIN[0]}.{TORCH_MIN[1]}–"
            f"{TORCH_MAX[0]}.{TORCH_MAX[1]}）：torch 没有对应 wheel，"
            "完整档必然安装失败。请改 PBS_TAG/PY_FULL 到合规版本。"
        )
    say(f"  ✓ 解释器可启动，版本 {v[0]}.{v[1]} 在 torch 支持区间内")

    tmp_venv = CACHE_DIR / "venv-probe"
    if tmp_venv.exists():
        shutil.rmtree(tmp_venv, ignore_errors=True)
    r = subprocess.run(
        [str(py), "-m", "venv", str(tmp_venv)],
        capture_output=True, text=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise SystemExit(f"建 venv 失败：{(r.stderr or '').strip()[:400]}")
    vpy = tmp_venv / "Scripts" / "python.exe"
    if not vpy.is_file():
        raise SystemExit("venv 里没有 Scripts/python.exe")
    say("  ✓ 可以建出 venv")

    r = subprocess.run(
        [str(vpy), "-m", "pip", "--version"],
        capture_output=True, text=True, timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise SystemExit(f"venv 里的 pip 不可用：{(r.stderr or '').strip()[:400]}")
    say(f"  ✓ venv 自带 pip：{(r.stdout or '').strip()}")

    r = subprocess.run(
        [str(vpy), "-c",
         "import ssl,sqlite3,lzma,bz2,ctypes,hashlib,decimal,zlib,asyncio,json;"
         "print('ok')"],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0 or "ok" not in (r.stdout or ""):
        raise SystemExit(f"标准库残缺（疑似裁剪过度）：{(r.stderr or '').strip()[:400]}")
    say("  ✓ 关键标准库齐全（ssl / sqlite3 / lzma / bz2 / ctypes …）")

    shutil.rmtree(tmp_venv, ignore_errors=True)
    say(f"\n完成。运行时 {_human(_dir_bytes(PY_DIR))} @ {PY_DIR}")
    say("下一步：npm run tauri:build（resources 已配置自动收录该目录）")


def check_only() -> None:
    py = PY_DIR / "python.exe"
    if not py.is_file():
        raise SystemExit(
            f"未找到 {py}\n"
            "该目录不入 git，新克隆的仓库需要先执行：\n"
            "    python engine/tools/fetch_runtime.py"
        )
    verify()


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="拉取随包分发的独立 Python 运行时")
    ap.add_argument("--force", action="store_true", help="忽略缓存，重新下载")
    ap.add_argument("--check", action="store_true", help="只体检，不下载")
    args = ap.parse_args()

    if args.check:
        check_only()
        return 0

    if (PY_DIR / "python.exe").is_file() and not args.force:
        say("运行时已存在，直接体检（--force 可强制重拉）")
        verify()
        return 0

    tar_path = download(args.force)
    extract(tar_path)
    prune()
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
