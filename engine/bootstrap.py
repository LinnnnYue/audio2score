"""
bootstrap — 引擎首启引导安装

## 为什么需要（主上拍板：「首启动引导装也行」）
实测本机 `engine/.venv` = **5.1GB**，其中 torch 一家占 4.4GB。
NSIS installer 若把这 5.1GB 打进安装包，体积不可接受（且 D 盘只剩 39GB）。

故定策略：**应用壳（约 4MB）随包分发，引擎在首次运行时按需安装。**

## 三档安装（按体积/质量递进）
| 档 | 内容 | 体积 | 装机时长 | 适用 |
|---|---|---|---|---|
| `basic` | librosa/scipy/pretty_midi/soundfile/av | ~200MB | 1-2 min | 基本扒谱（不分离） |
| `+demucs`（默认） | basic + torch(cu124) + demucs | ~5.1GB | 8-20 min | 完整功能（分离人声伴奏） |
| `+mcp` | +demucs + mcp SDK | ~5.1GB | +10s | 附带 MCP 服务端 |

## 关键工程约束
1. **必须用 `--index-url` 指定 torch 的 cu124 源**，否则装到 CPU 版（2.5GB），
   分离会慢 20 倍。**这是最容易踩的坑。**
2. **每档都有 pip 缓存复用**——用户中途失败重试时，第二次装快得多
3. **进度可见**——torch 有近 3GB，pip 默认进度条在 Windows 无 TTY 时不刷新，
   必须显式传 `--progress-bar on`
4. **失败可重试**——不写半成品标记文件，检测靠真实 import

## 落点
安装位置默认 `%LOCALAPPDATA%\\bapu\\engine`（用户级，无需管理员权限）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── 安装档位 ──
TIER_BASIC = "basic"
TIER_FULL = "full"
TIER_MCP = "mcp"

# ── 基础依赖（三档共用）──
BASE_PACKAGES = [
    "librosa>=0.10", "numpy>=1.24", "scipy>=1.10",
    "pretty_midi>=0.2.10", "soundfile>=0.12", "av",
]

# ── Demucs（音源分离）──
# ⚠️ 踩坑实录（P0，最伤小白的一类）：初版 TIER_FULL 的 packages 是**空列表**，
# 只装了 torch，**漏了 demucs**。后果：用户选「完整（推荐）」、描述写着
# 「可分离人声/伴奏」、下载 5.1GB 等了 20 分钟，装完发现分离功能仍然不可用
# （走 HPSS 降级，质量差）。而 demucs 本体只有 792KB ——
# **装了 99.98% 却漏了最后的 0.02%**。
# 这类「承诺了但没兑现」比功能缺失更伤用户：他付出成本后才被发现。
DEMUCS_PACKAGES = ["demucs"]

TIERS: dict[str, dict] = {
    TIER_BASIC: {
        "label": "基础 · 约 200MB · 1-2 分钟",
        "desc": "基本扒谱 + 全部音频格式。不含人声/伴奏分离。",
        # 能给小白看懂的「能做/不能做」
        "can": ["基本扒谱（单音轨/多音轨）", "导入已分离音频直接扒谱", "mp3/m4a 等全格式读取"],
        "cannot": ["分离人声与伴奏"],
        "disk_mb": 1200,
        "packages": list(BASE_PACKAGES),
        "torch": False,
    },
    TIER_FULL: {
        "label": "完整 · 约 5.2GB · 10-25 分钟",
        "desc": "含 GPU 版 PyTorch 与 Demucs。推荐。",
        "can": ["全自动双轨扒谱（人声+伴奏）", "只扒伴奏 / 只扒人声旋律", "基本扒谱、已分离直入", "全部音频格式"],
        "cannot": [],
        "disk_mb": 7500,
        "packages": list(BASE_PACKAGES) + list(DEMUCS_PACKAGES),
        "torch": True,
    },
    TIER_MCP: {
        "label": "完整 + MCP · 约 5.2GB · 10-25 分钟",
        "desc": "在完整基础上加装 MCP，供 AI 助手调用扒谱。",
        "can": ["完整档全部功能", "AI 助手（WorkBuddy/Claude Code 等）直接调用"],
        "cannot": [],
        "disk_mb": 7500,
        "packages": list(BASE_PACKAGES) + list(DEMUCS_PACKAGES) + ["mcp>=2.0"],
        "torch": True,
    },
}

# ── 镜像源 ──
# 为什么必须做：PyTorch 的 CUDA wheel 约 2.5GB，从官方源（境外）下载在国内
# 常年只有几十 KB/s，甚至直接超时 —— 这是「首启安装体验」的头号杀手，
# 也是小白最容易在这里放弃的地方。
# 实测（2026-10-04）：清华 PyPI 与阿里云 pytorch-wheels 均 200 可达，
# 且阿里云含 `torch-2.6.0+cu124-cp313-cp313-win_amd64.whl`（与本机匹配）。
MIRRORS: dict[str, dict] = {
    "cn": {
        "label": "国内镜像（推荐）",
        "hint": "清华 PyPI + 阿里云 PyTorch，速度快很多",
        "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "torch_index": "https://mirrors.aliyun.com/pytorch-wheels/cu124",
    },
    "official": {
        "label": "官方源",
        "hint": "境外源，国内可能很慢；镜像不可用时选它",
        "pypi": None,
        "torch_index": "https://download.pytorch.org/whl/cu124",
    },
}
DEFAULT_MIRROR = "cn"

# 进度回调：(阶段, 0~1, 说明文字)
ProgressFn = "callable"


@dataclass
class InstallResult:
    ok: bool
    engine_dir: str
    python: str
    tier: str
    elapsed: float
    message: str = ""
    log: list[str] = field(default_factory=list)


def default_engine_dir() -> Path:
    """
    默认安装位置：用户级，无需管理员权限。

    **开发态短路**：若本文件旁已有 `.venv`（仓库开发者场景），
    直接用它而不返回用户目录——否则开发时每次都要装一遍 5.1GB。
    """
    here = Path(__file__).resolve().parent
    if (here / ".venv" / "Scripts" / "python.exe").is_file():
        return here
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "bapu" / "engine"


def find_base_python() -> str | None:
    """
    找一个能用的 Python 3.10+ 作为建 venv 的基底。

    优先级：
      1. 项目自带的 engine/.venv（开发态）
      2. 系统 python（py launcher）
      3. 常见安装路径
    """
    # 开发态：项目自带
    here = Path(__file__).resolve().parent
    dev_venv = here / ".venv" / "Scripts" / "python.exe"
    if dev_venv.is_file():
        return str(dev_venv)

    # py launcher（Windows 最稳）
    for exe in ("py", "python", "python3"):
        try:
            r = subprocess.run(
                [exe, "-c", "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0 and r.stdout.strip():
                maj, _, mino = r.stdout.strip().partition(".")
                if int(maj) >= 3 and int(mino or 0) >= 9:
                    return exe
        except Exception:  # noqa: BLE001
            continue
    return None


def check_installed(engine_dir: Path) -> dict:
    """
    检测引擎是否已就绪。**靠真实 import 判定，不看标记文件**——
    标记文件会在用户手动删包后骗人。
    """
    py = engine_dir / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        return {"ready": False, "reason": "解释器不存在", "python": None}

    script = (
        "import json,sys\n"
        "r={}\n"
        "try:\n"
        "    import numpy,scipy,pretty_midi,soundfile,librosa,av\n"
        "    r['basic']=True\n"
        "except Exception as e:\n"
        "    r['basic']=False; r['basicErr']=str(e)[:200]\n"
        "try:\n"
        # 同样要求 __file__ 非 None，避免命名空间包造成的假成功（见下方注释）
        "    import torch,demucs\n"
        "    r['demucs']= bool(getattr(torch,'__file__',None)) and bool(getattr(demucs,'__file__',None))\n"
        "    r['cuda']=bool(torch.cuda.is_available())\n"
        "    r['torchVer']=torch.__version__\n"
        "except Exception as e:\n"
        "    r['demucs']=False; r['demucsErr']=str(e)[:200]\n"
        "try:\n"
        # 踩坑实录：干净环境实测 basic 档时，`import mcp` 竟**成功**，
        # 但 pip list 里根本没有 mcp，且 `mcp.__file__ is None`。
        # 根因：某包（numba 系）注册了顶层命名空间包 `mcp`，使 import 假成功。
        # 正解：要求 `__file__` 非 None（即真实模块文件），否则判为未安装。
        # 教训：**import 成功不等于包真的装了**，须验证 __file__。
        "    import mcp\n"
        "    r['mcp']= bool(getattr(mcp,'__file__',None))\n"
        "except Exception:\n"
        "    r['mcp']=False\n"
        "print(json.dumps(r))\n"
    )
    try:
        r = subprocess.run(
            [str(py), "-c", script],
            capture_output=True, text=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "reason": f"检测失败 {type(e).__name__}", "python": str(py)}

    data["python"] = str(py)
    data["ready"] = bool(data.get("basic"))
    if not data["ready"]:
        data.setdefault("reason", data.get("basicErr", "基础依赖缺失"))
    return data


def create_venv(engine_dir: Path, progress=None) -> str:
    """建 venv，返回 python 路径。"""
    base = find_base_python()
    if not base:
        raise RuntimeError(
            "找不到可用的 Python 3.9+。\n\n"
            "请先安装 Python：https://www.python.org/downloads/\n"
            "安装时务必勾选「Add Python to PATH」。"
        )

    if progress:
        progress("venv", 0.02, "正在创建运行环境…")

    venv_dir = engine_dir / ".venv"
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        [base, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise RuntimeError(f"创建环境失败：\n{r.stderr[:400]}")

    py = venv_dir / "Scripts" / "python.exe"
    if not py.is_file():
        raise RuntimeError("创建环境后找不到解释器。")

    if progress:
        progress("venv", 0.08, "正在升级 pip…")

    r = subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True, text=True, timeout=300,
        creationflags=getprocess_flags(),
    )
    if r.returncode != 0:
        raise RuntimeError(f"升级 pip 失败：\n{r.stderr[:300]}")

    return str(py)


def getprocess_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


# 网络类失败的识别关键词。用途：给出「换镜像」这种**可操作**的建议，
# 而不是干巴巴一句「安装失败」——小白看到后者只能放弃。
_NET_HINTS = ("timeout", "timed out", "connection", "ssl", "temporary failure",
              "read timed out", "urlopen", "proxy", "failed to establish",
              "could not find a version", "no matching distribution")


def _pip_install(py: str, args: list[str], progress, stage: str,
                 base_pct: float, span: float, log: list[str],
                 pypi: str | None = None, label: str | None = None) -> None:
    """
    跑一次 pip install，向上报进度。

    `pypi` 为 PyPI 镜像地址（None = 官方源）。
    注意：args 里若已含 `--index-url`（torch 专用源），则忽略 pypi，
    否则两个 index 参数会打架。
    """
    shown = label or args[0]
    if progress:
        progress(stage, base_pct, f"正在下载安装：{shown}…")

    cmd = [py, "-m", "pip", "install", "--progress-bar", "on"]
    has_index = any(a == "--index-url" for a in args)
    if pypi and not has_index:
        cmd += ["-i", pypi]
    cmd += args

    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=3600,
        creationflags=getprocess_flags(),
    )
    tail = (r.stdout or "")[-600:] + (r.stderr or "")[-600:]
    log.append(f"$ pip install {' '.join(args[:3])}…\n{tail.strip()[:400]}")

    if r.returncode != 0:
        low = tail.lower()
        if any(h in low for h in _NET_HINTS):
            raise RuntimeError(
                f"下载 {shown} 时网络中断或超时。\n\n"
                f"可以试试：\n"
                f"  1. 换一个源重试（默认已用国内镜像，可在选项里切官方源）\n"
                f"  2. 检查网络或代理设置\n"
                f"  3. 改用「基础」档（约 200MB，无需下载 PyTorch）\n\n"
                f"技术详情：\n{tail.strip()[-400:]}"
            )
        if "no space left" in low or "disk" in low:
            raise RuntimeError(
                f"磁盘空间不足，安装 {shown} 时中断。\n\n"
                f"请清理磁盘后重试。完整档需要约 5.2GB。\n\n"
                f"技术详情：\n{tail.strip()[-400:]}"
            )
        raise RuntimeError(
            f"安装 {shown} 失败。\n\n技术详情：\n{tail.strip()[-400:]}"
        )
    if progress:
        progress(stage, base_pct + span, f"{shown} 已装好")


def check_disk_space(target: Path, need_mb: int) -> tuple[bool, str]:
    """
    安装前检查目标盘剩余空间。

    为什么要在**下载前**检查：完整档要下 5.2GB，如果下到一半空间不足，
    pip 会留下半装状态，用户重试还得再下一次 —— 对小白是不可恢复的挫败。
    提前拦住，代价是 0，收益是省掉一次 20 分钟的浪费。
    """
    try:
        import shutil as _sh

        # 必须 resolve：相对路径的 Path.anchor 是空串，会导致提示里盘符缺失
        probe = target.resolve()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = _sh.disk_usage(str(probe))
        free_mb = usage.free / (1024 * 1024)
        where = probe.anchor or str(probe)
        if free_mb < need_mb:
            return False, (
                f"磁盘空间不足。\n\n"
                f"  需要：约 {need_mb / 1024:.1f} GB\n"
                f"  可用：{free_mb / 1024:.1f} GB（{where}）\n\n"
                f"请清理磁盘后重试，或改用「基础」档（约 200MB）。"
            )
        return True, f"磁盘可用 {free_mb / 1024:.1f} GB / 需要约 {need_mb / 1024:.1f} GB"
    except Exception:  # noqa: BLE001 — 检查失败不阻断安装
        return True, ""


def install(tier: str = TIER_FULL, engine_dir: Path | None = None,
            mirror: str = DEFAULT_MIRROR, progress=None) -> InstallResult:
    """
    执行安装。返回 InstallResult，**不抛异常**（便于 UI 展示失败原因）。

    Parameters
    ----------
    tier : TIER_BASIC / TIER_FULL / TIER_MCP
    engine_dir : 安装位置，默认 %LOCALAPPDATA%/bapu/engine
    mirror : MIRRORS 的键（"cn" 国内镜像 / "official" 官方源）
    progress : 回调 (stage, 0~1, 说明)
    """
    t0 = time.time()
    engine_dir = Path(engine_dir) if engine_dir else default_engine_dir()
    log: list[str] = []

    if tier not in TIERS:
        return InstallResult(
            False, str(engine_dir), "", tier, 0.0,
            message=f"未知的安装档位：{tier}", log=log,
        )
    if mirror not in MIRRORS:
        mirror = DEFAULT_MIRROR

    spec = TIERS[tier]
    pypi = MIRRORS[mirror]["pypi"]
    torch_index = MIRRORS[mirror]["torch_index"]

    try:
        # ── 磁盘预检（在下载任何东西之前）──
        need_mb = spec.get("disk_mb", 1200)
        ok, disk_msg = check_disk_space(engine_dir, need_mb)
        if not ok:
            return InstallResult(
                False, str(engine_dir), "", tier, time.time() - t0,
                message=disk_msg, log=log,
            )
        if disk_msg:
            log.append(f"[磁盘预检] {disk_msg}")

        py = create_venv(engine_dir, progress)

        # ── torch 必须单独装且指定 CUDA 源 ──
        # 踩坑实录（部署第一大坑）：不指定 --index-url，pip 会装 CPU 版
        # torch，分离人声会慢约 20 倍，用户会以为程序坏了。
        # 源码走 MIRRORS，默认国内镜像（官方源在国内常年几十 KB/s）。
        if spec.get("torch"):
            _pip_install(
                py,
                ["torch", "torchaudio", "--index-url", torch_index],
                progress, "torch", 0.10, 0.55, log,
                label="PyTorch（GPU 版，约 2.5GB）",
            )

        # ── 其余包（含 demucs）──
        pkgs = list(spec.get("packages", []))
        if pkgs:
            _pip_install(
                py, pkgs, progress, "deps",
                0.65 if spec.get("torch") else 0.15, 0.30, log,
                pypi=pypi,
                label="扒谱依赖与 Demucs" if spec.get("torch") else "扒谱基础依赖",
            )

        if progress:
            progress("verify", 0.95, "正在验证安装…")

        check = check_installed(engine_dir)
        if not check["ready"]:
            return InstallResult(
                False, str(engine_dir), py, tier, time.time() - t0,
                message=f"安装后自检未通过：{check.get('reason')}", log=log,
            )

        # ── 承诺验证：档位说能做到的，必须真的做到 ──
        # 踩坑实录：TIER_FULL 曾漏装 demucs，用户下完 5.2GB 才发现分离不能用。
        # 「描述说支持」不等于「装完真能用」，必须实测 import 判定。
        if spec.get("torch") and not check.get("demucs"):
            return InstallResult(
                False, str(engine_dir), py, tier, time.time() - t0,
                message=(
                    "安装完成，但人声/伴奏分离所需的 Demucs 不可用。\n\n"
                    "请重试一次；若反复失败请改用「基础」档并反馈问题。"
                ),
                log=log,
            )

        if progress:
            progress("verify", 1.0, "安装完成")

        parts = ["基础功能 ✓"]
        if check.get("demucs"):
            parts.append(f"人声分离 ✓（{'GPU 加速' if check.get('cuda') else 'CPU 模式'}）")
        else:
            parts.append("人声分离 ✗（本档不含）")
        if check.get("mcp"):
            parts.append("MCP ✓")

        return InstallResult(
            True, str(engine_dir), py, tier, time.time() - t0,
            message=" · ".join(parts), log=log,
        )

    except Exception as e:  # noqa: BLE001
        return InstallResult(
            False, str(engine_dir), "", tier, time.time() - t0,
            message=f"{type(e).__name__}: {str(e)[:500]}", log=log,
        )


def install_status(engine_dir: Path | None = None) -> dict:
    """给 UI 用的状态报告。"""
    engine_dir = Path(engine_dir) if engine_dir else default_engine_dir()
    check = check_installed(engine_dir)
    return {
        "engineDir": str(engine_dir),
        "ready": check["ready"],
        "reason": check.get("reason", ""),
        "python": check.get("python"),
        "basic": check.get("basic", False),
        "demucs": check.get("demucs", False),
        "cuda": check.get("cuda", False),
        "torchVersion": check.get("torchVer", ""),
        "mcp": check.get("mcp", False),
        # 档位详情：带上「能做/不能做」与磁盘需求。
        # 小白判断不了「200MB vs 5.2GB」哪个该选，得告诉他**选完能干什么**。
        "tiers": [
            {
                "id": k,
                "label": v["label"],
                "desc": v["desc"],
                "can": v.get("can", []),
                "cannot": v.get("cannot", []),
                "diskMB": v.get("disk_mb", 0),
            }
            for k, v in TIERS.items()
        ],
        "mirrors": [
            {"id": k, "label": v["label"], "hint": v["hint"]}
            for k, v in MIRRORS.items()
        ],
        "defaultMirror": DEFAULT_MIRROR,
        # 「还缺什么能力」——用于给已装用户提供增量升级入口，
        # 而不是让他整个重装一遍
        "missing": (
            ["demucs"] if (check["ready"] and not check.get("demucs")) else []
        ),
    }


def _force_utf8_stdio() -> None:
    """
    强制 stdout/stderr 使用 UTF-8。

    ## 为什么必须显式设置
    当 stdout 被重定向到管道（Tauri 用 Stdio::piped() 捕获就是这种情况）时，
    Python 会用**系统 locale 编码**而非 UTF-8 —— 中文 Windows 上是 cp936(GBK)。
    于是中文 JSON 被编成 GBK 字节，Rust 侧按 UTF-8 解码得到一堆替换字符，
    界面上就是「◆◆◆◆」乱码。

    **绝不能依赖环境**：双击启动的应用不继承开发者的 shell 环境，
    所以必须在本进程内显式声明。errors="replace" 兜底，避免编码异常直接崩进程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 老版本/非标准流：忽略即可
            pass


if __name__ == "__main__":
    import argparse

    _force_utf8_stdio()

    ap = argparse.ArgumentParser(description="扒谱助手 · 引擎安装器")
    ap.add_argument("--tier", default=TIER_FULL, choices=list(TIERS))
    ap.add_argument("--dir", help="安装位置（默认 %%LOCALAPPDATA%%/bapu/engine）")
    ap.add_argument(
        "--mirror", default=DEFAULT_MIRROR, choices=list(MIRRORS),
        help="下载源（cn=国内镜像 / official=官方源）",
    )
    ap.add_argument("--status", action="store_true", help="只查状态")
    ap.add_argument("--list-tiers", action="store_true", help="列出档位与能力")
    args = ap.parse_args()

    if args.list_tiers:
        for k, v in TIERS.items():
            print(f"\n【{k}】{v['label']}")
            print(f"  {v['desc']}")
            print(f"  磁盘需求：约 {v.get('disk_mb', 0) / 1024:.1f} GB")
            print("  能做：")
            for c in v.get("can", []):
                print(f"    ✓ {c}")
            if v.get("cannot"):
                print("  不能做：")
                for c in v["cannot"]:
                    print(f"    ✗ {c}")
        print("\n镜像：")
        for k, v in MIRRORS.items():
            mark = "（默认）" if k == DEFAULT_MIRROR else ""
            print(f"  {k:9s} {v['label']}{mark} — {v['hint']}")
        raise SystemExit(0)

    if args.status:
        print(json.dumps(install_status(Path(args.dir) if args.dir else None),
                         ensure_ascii=False, indent=2))
        raise SystemExit(0)

    t0 = time.time()
    last = [0.0]

    def _p(stage: str, pct: float, msg: str) -> None:
        # 不再按 1% 粒度节流：pip 的下载阶段可能长时间停在同一个百分比，
        # 用户会以为卡死。改为「消息变化就刷新，或百分比跳变≥1%」才刷。
        last[0] = pct
        bar = "█" * int(pct * 28) + "░" * (28 - int(pct * 28))
        sys.stdout.write(f"\r  [{bar}] {pct * 100:5.1f}%  {msg[:46]:<46}")
        sys.stdout.flush()

    spec = TIERS.get(args.tier, {})
    print(f"档位：{spec.get('label', args.tier)}")
    print(f"镜像：{MIRRORS[args.mirror]['label']}")
    print(f"位置：{Path(args.dir) if args.dir else default_engine_dir()}")
    need = spec.get("disk_mb", 0)
    if need:
        print(f"磁盘：需要约 {need / 1024:.1f} GB")
    print()

    res = install(
        args.tier,
        Path(args.dir) if args.dir else None,
        mirror=args.mirror,
        progress=_p,
    )
    sys.stdout.write("\r" + " " * 84 + "\r")

    if res.ok:
        print(f"安装完成（{time.time() - t0:.0f} 秒）")
        print(f"  位置 : {res.engine_dir}")
        print(f"  档位 : {res.tier}")
        print(f"  状态 : {res.message}")
        raise SystemExit(0)
    else:
        print(f"安装失败：{res.message}")
        if res.log:
            print("\n最后一条日志：")
            print(res.log[-1][:600])
        raise SystemExit(1)
