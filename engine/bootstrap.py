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

TIERS: dict[str, dict] = {
    TIER_BASIC: {
        "label": "基础（1-2 分钟，约 200MB）",
        "desc": "支持基本扒谱与全部音频格式。不含人声/伴奏分离。",
        "packages": [
            "librosa>=0.10", "numpy>=1.24", "scipy>=1.10",
            "pretty_midi>=0.2.10", "soundfile>=0.12", "av",
        ],
    },
    TIER_FULL: {
        "label": "完整（8-20 分钟，约 5.1GB）",
        "desc": "额外含 GPU 版 PyTorch 与 Demucs，可分离人声/伴奏。推荐。",
        "packages": [],  # torch 需特殊 index，单独处理
        "torch": True,
    },
    TIER_MCP: {
        "label": "完整 + MCP（8-20 分钟，约 5.1GB）",
        "desc": "在完整基础上加装 MCP SDK，供 AI 助手调用扒谱。",
        "packages": ["mcp>=2.0"],
        "torch": True,
    },
}

TORCH_INDEX = "https://download.pytorch.org/whl/cu124"

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


def _pip_install(py: str, args: list[str], progress, stage: str,
                 base_pct: float, span: float, log: list[str]) -> None:
    """跑一次 pip install，向上报进度。"""
    if progress:
        progress(stage, base_pct, f"安装中：{args[0]}…")

    cmd = [py, "-m", "pip", "install", "--progress-bar", "on", *args]
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=3600,
        creationflags=getprocess_flags(),
    )
    tail = (r.stdout or "")[-600:] + (r.stderr or "")[-600:]
    log.append(f"$ pip install {' '.join(args[:3])}…\n{tail.strip()[:400]}")

    if r.returncode != 0:
        raise RuntimeError(
            f"安装 {args[0]} 失败。\n\n"
            f"常见原因：网络不通，或磁盘空间不足。\n"
            f"详细信息：\n{tail.strip()[-500:]}"
        )
    if progress:
        progress(stage, base_pct + span, f"{args[0]} 完成")


def install(tier: str = TIER_FULL, engine_dir: Path | None = None,
            progress=None) -> InstallResult:
    """
    执行安装。返回 InstallResult，**不抛异常**（便于 UI 展示失败原因）。

    Parameters
    ----------
    tier : TIER_BASIC / TIER_FULL / TIER_MCP
    engine_dir : 安装位置，默认 %LOCALAPPDATA%/bapu/engine
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

    spec = TIERS[tier]
    try:
        py = create_venv(engine_dir, progress)

        # ── torch 必须单独装且指定 cu124 源 ──
        # 踩坑实录（部署第一大坑）：不指定 --index-url，pip 会装 CPU 版
        # torch（约 2.5GB），分离人声会慢 20 倍，用户会以为程序坏了。
        if spec.get("torch"):
            _pip_install(
                py,
                ["torch", "torchaudio", "--index-url", TORCH_INDEX],
                progress, "torch", 0.10, 0.55, log,
            )

        # ── 其余包 ──
        pkgs = list(spec.get("packages", []))
        # basic 档也要装 librosa 等基础包
        if not pkgs or spec.get("torch"):
            pkgs = TIERS[TIER_BASIC]["packages"] + pkgs

        if pkgs:
            _pip_install(
                py, pkgs, progress, "deps",
                0.65 if spec.get("torch") else 0.15, 0.3, log,
            )

        if progress:
            progress("verify", 0.95, "正在验证安装…")

        check = check_installed(engine_dir)
        if not check["ready"]:
            return InstallResult(
                False, str(engine_dir), py, tier, time.time() - t0,
                message=f"安装后自检未通过：{check.get('reason')}", log=log,
            )

        if progress:
            progress("verify", 1.0, "安装完成")

        summary = f"基础依赖 ✓"
        if check.get("demucs"):
            summary += f" · Demucs ✓（CUDA {'✓' if check.get('cuda') else '✗'}）"
        else:
            summary += " · Demucs ✗（人声分离将降级）"
        if check.get("mcp"):
            summary += " · MCP ✓"

        return InstallResult(
            True, str(engine_dir), py, tier, time.time() - t0,
            message=summary, log=log,
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
        "tiers": [
            {"id": k, "label": v["label"], "desc": v["desc"]}
            for k, v in TIERS.items()
        ],
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="扒谱助手 · 引擎安装器")
    ap.add_argument("--tier", default=TIER_FULL, choices=list(TIERS))
    ap.add_argument("--dir", help="安装位置（默认 %%LOCALAPPDATA%%/bapu/engine）")
    ap.add_argument("--status", action="store_true", help="只查状态")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(install_status(Path(args.dir) if args.dir else None),
                         ensure_ascii=False, indent=2))
        raise SystemExit(0)

    t0 = time.time()
    last = [0.0]

    def _p(stage: str, pct: float, msg: str) -> None:
        if pct - last[0] >= 0.01 or pct >= 1.0:
            last[0] = pct
            bar = "█" * int(pct * 28) + "░" * (28 - int(pct * 28))
            sys.stdout.write(f"\r  [{bar}] {pct * 100:5.1f}%  {msg[:46]:<46}")
            sys.stdout.flush()

    res = install(
        args.tier,
        Path(args.dir) if args.dir else None,
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
