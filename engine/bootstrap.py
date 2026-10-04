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
import shutil
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
#
# ⚠️ 版本范围（2026-10-05）：demucs 4.1.0（PyPI 2026-07-11）起把模型下载
# **默认走 HuggingFace Hub**（新增 `demucs/hf.py`，依赖 huggingface-hub）。
# `separator.py` 的「本地 → 官方直链 → 国内镜像」三跳正是针对该行为写的，
# 故锁定次版本范围：允许补丁号升级，挡住可能改变下载行为的破坏性变更。
# 踩坑实录：老公机器首次分离报 `WinError 10060` 请求 huggingface.co 失败，
# 重试到 5/5 结束（详见 separator.py 顶部注释）。
DEMUCS_PACKAGES = ["demucs>=4.1,<4.2"]

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
#
# ⚠️ 踩坑实录（2026-10-04，主上实测「国内源和官方源都装不上 torch」）：
# 镜像地址必须满足 **PEP 503 目录索引** 规范（即 `<index>/<包名>/` 可访问）。
#   · 上海交大  https://mirror.sjtu.edu.cn/pytorch-wheels/cu124/torch/  → 200 ✓
#   · 官方      https://download.pytorch.org/whl/cu124/torch/           → 200 ✓
#   · 阿里云    https://mirrors.aliyun.com/pytorch-wheels/cu124/torch/  → 404 ✗
#     阿里云那个是 **扁平文件目录**（find-links 风格），只能配 `-f/--find-links`，
#     一旦当 `--index-url` 用，pip 去请求 `/cu124/torch/` 得到 404，报：
#       ERROR: Could not find a version that satisfies the requirement torch
#              (from versions: none)
#     这条报错与「Python 版本不匹配」的报错**逐字相同**，极易误判成网络问题。
#   · 清华 / 中科大 无 pytorch-wheels 路径（404），不能用作 torch 源。
MIRRORS: dict[str, dict] = {
    "cn": {
        "label": "国内镜像（推荐）",
        "hint": "上海交大 PyTorch + 清华 PyPI，速度快很多",
        "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "torch_index": "https://mirror.sjtu.edu.cn/pytorch-wheels/cu124",
    },
    "official": {
        "label": "官方源",
        "hint": "境外源，国内可能很慢；镜像不可用时选它",
        "pypi": None,
        "torch_index": "https://download.pytorch.org/whl/cu124",
    },
}
DEFAULT_MIRROR = "cn"

# ── Python 版本约束（torch 专用）──
# torch cu124 的 Windows wheel 只覆盖 cp39–cp313（2026-10-04 实测：
# SJTU 与官方源上均 **不存在 cp314 的 wheel**）。
# 若基底 Python 是 3.14，pip 会把索引里所有 torch wheel 按 cp 标签过滤掉，
# 于是**换任何源都报** `from versions: none` —— 这正是
# 「国内源和官方源都装不上」的真正主因。
TORCH_PY_MIN = (3, 10)
TORCH_PY_MAX = (3, 13)

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


def location_file() -> Path:
    """安装位置记录文件的路径（用户级，与安装位置无关）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "bapu" / "location.json"


def save_location(engine_dir: Path) -> None:
    """
    记录本次安装位置。

    ## 为什么必须记录
    用户可以把引擎装到任意盘（`--dir`），但宿主（Rust）需要知道去哪里找
    那个 venv。若只认死的 `%LOCALAPPDATA%/bapu/engine`，
    用户选了 D 盘也是白选 —— 装完了宿主仍然找不到，仍报「引擎未就绪」。
    所以安装成功后把位置落盘，由宿主读取。
    """
    try:
        lf = location_file()
        lf.parent.mkdir(parents=True, exist_ok=True)
        lf.write_text(
            json.dumps({"engine_dir": str(engine_dir)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — 记录失败不应让安装本身失败
        pass


def load_location() -> Path | None:
    """读取上次记录的安装位置。不存在或已失效则返回 None。"""
    try:
        lf = location_file()
        if not lf.is_file():
            return None
        data = json.loads(lf.read_text(encoding="utf-8"))
        p = Path(data["engine_dir"])
        # 只认「目录里真有 venv 解释器」的位置，避免返回一个已被删除的路径
        if (p / ".venv" / "Scripts" / "python.exe").is_file():
            return p
        if (p / ".venv" / "bin" / "python").is_file():
            return p
        return None
    except Exception:  # noqa: BLE001
        return None


def default_engine_dir() -> Path:
    """
    默认安装位置：用户级，无需管理员权限。

    **优先级**：
      1. 曾记录过的自定义安装位置（用户可能装在 D 盘）
      2. 开发态短路：本文件旁已有 `.venv`（仓库开发者场景）
      3. 默认：`%LOCALAPPDATA%/bapu/engine`
    """
    recorded = load_location()
    if recorded is not None:
        return recorded

    here = Path(__file__).resolve().parent
    if (here / ".venv" / "Scripts" / "python.exe").is_file():
        return here

    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "bapu" / "engine"


def _python_version_of(cmd: list[str]) -> tuple[int, int] | None:
    """跑一次解释器问版本号。失败（不存在/超时/非解释器）返回 None。"""
    try:
        r = subprocess.run(
            [*cmd, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    lines = (r.stdout or "").strip().splitlines()
    if not lines:
        return None
    maj, _, mino = lines[-1].partition(".")
    try:
        return int(maj), int(mino)
    except ValueError:
        return None


def iter_base_python_candidates() -> list[list[str]]:
    """
    按优先级列出可能的基底解释器（命令前缀）。

    ⚠️ 必须用 `py -3.x` 精确指定版本：Windows 上 `py` 的「默认版本」是
    用户最后安装的那个（本机实测为 3.14），直接跑 `py` 必然踩到
    「torch 无 cp314 wheel」的坑。
    """
    out: list[list[str]] = []

    # 0. **应用内置的独立运行时**（由 Rust 侧经环境变量 BAPU_BASE_PYTHON 下发）
    #
    #    ⚠️ 必须排第一位，理由有二：
    #    ① 小白机器上 PATH 里**根本没有 Python**。没有这一项，安装会在第一步
    #       就死——装引擎需要 Python 来建 venv，而机器上没有 Python，
    #       这是纯粹的鸡生蛋死结。应用自带运行时正是为了拆掉这个死结。
    #    ② 用户机器上即便有 Python，版本也不受控。本机 `py` 的默认版本实测
    #       是 3.14，而 torch cu124 没有 cp314 wheel → 完整档 100% 失败。
    #       包内运行时是 3.13.x，确定落在 TORCH_PY_MIN..MAX 区间内。
    #
    #    仍然照常走下面的版本校验：若下发来的解释器不合规（包被替换等），
    #    会被 find_base_python 拒绝并记进 rejects，不会静默建出坏 venv。
    forced = (os.environ.get("BAPU_BASE_PYTHON") or "").strip()
    if forced:
        out.append([forced])

    # 1. 开发态：项目自带 venv
    here = Path(__file__).resolve().parent
    dev_venv = here / ".venv" / "Scripts" / "python.exe"
    if dev_venv.is_file():
        out.append([str(dev_venv)])

    # 2. py launcher 精确指定（由新到旧，均在 torch 支持区间内）
    for minor in range(TORCH_PY_MAX[1], TORCH_PY_MIN[1] - 1, -1):
        out.append(["py", "-3.{}".format(minor)])

    # 3. py launcher 默认版本（用户恰好在合规区间时可用）
    out.append(["py"])

    # 4. PATH 里的裸命令
    out.append(["python"])
    out.append(["python3"])

    # 5. uv 管理的 Python。Astral 的 `py -3.x` 别名不认它，
    #    只能直接扫目录 —— 本机实测 `py -3.12` 为 None 而 uv 的 3.12 实际存在。
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        uv_root = Path(appdata) / "uv" / "python"
        try:
            if uv_root.is_dir():
                for d in sorted(uv_root.iterdir(), reverse=True):
                    exe = d / "python.exe"
                    if exe.is_file():
                        out.append([str(exe)])
        except OSError:
            pass

    # 6. 常见安装路径兜底（装了但没进 PATH 的情况）
    local = os.environ.get("LOCALAPPDATA") or ""
    for minor in range(TORCH_PY_MAX[1], TORCH_PY_MIN[1] - 1, -1):
        tag = "Python3{}".format(minor)
        if local:
            out.append([str(Path(local) / "Programs" / "Python" / tag / "python.exe")])
        out.append(["C:\\{}\\python.exe".format(tag)])
        out.append(["C:\\Program Files\\{}\\python.exe".format(tag)])

    return out


def find_base_python(rejects: list | None = None) -> list[str] | None:
    """
    找一个能建 venv 的基底 Python，**且版本必须在 torch 支持区间内**。

    ## 为什么必须卡版本，而不是「能跑就行」
    torch cu124 的 Windows wheel 只覆盖 cp39–cp313。若基底是 3.14，
    建出的 venv 里 pip 找不到任何 torch wheel，报：
        ERROR: Could not find a version that satisfies the requirement torch
               (from versions: none)
    这条报错与「镜像索引不可用」**逐字相同**——用户和开发者都会误判成网络问题，
    于是「换个源重试」永远无效。宁可在这里明确拒绝 3.14，
    也不要让用户下完 2.5GB 才发现装不上。

    rejects 若传入列表，会填入 (命令, 版本) 以便向用户解释为何被拒。
    """
    for cmd in iter_base_python_candidates():
        v = _python_version_of(cmd)
        if v is None:
            continue
        if TORCH_PY_MIN <= v <= TORCH_PY_MAX:
            return cmd
        if rejects is not None:
            rejects.append((" ".join(cmd), "{}.{}".format(v[0], v[1])))
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
    rejects: list = []
    base = find_base_python(rejects)
    if not base:
        # 把「为什么被拒」摊开说清楚：用户多半装了 3.14 而不知道 torch 不支持
        detail = ""
        if rejects:
            shown = "、".join(
                "{}（{}.{}）".format(c, v.split(".")[0], v.split(".")[1])
                for c, v in rejects[:5]
            )
            detail = (
                "\n\n检测到的 Python：\n  {}\n\n"
                "PyTorch 的 GPU 版目前只提供到 Python {}.{} 的安装包，"
                "更新的版本会导致依赖装不上（报「找不到 torch」）。".format(
                    shown, TORCH_PY_MAX[0], TORCH_PY_MAX[1]
                )
            )
        raise RuntimeError(
            "找不到可用的 Python（需要 {}.{} – {}.{}）。{}"
            "\n\n解决办法（任选其一）：\n"
            "  1. 安装 Python {}.{}（推荐）：https://www.python.org/downloads/release/python-31210/\n"
            "     安装时勾选「Add Python to PATH」\n"
            "  2. 改用「基础」档安装（约 200MB，不需要 PyTorch，无需任何额外 Python）\n"
            "  3. 若已装旧版本 Python，重启本程序让它被检测到".format(
                TORCH_PY_MIN[0], TORCH_PY_MIN[1], TORCH_PY_MAX[0], TORCH_PY_MAX[1],
                detail, TORCH_PY_MAX[1], TORCH_PY_MAX[1] - 1,
            )
        )

    if progress:
        progress("venv", 0.02, "正在创建运行环境…")

    venv_dir = engine_dir / ".venv"
    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    # ── 既有 venv：解释器版本不符则清空重建 ──
    # 踩坑实录（2026-10-04，主上机器的真实现场）：
    # 安装失败会留下一个**半装 venv**。实测主上机上的
    #   %LOCALAPPDATA%/bapu/engine/.venv
    # 的 pyvenv.cfg 是 `home = C:\Python314`、`version = 3.14.6`（torch 装不上，
    # 但 librosa 等从 PyPI 装的包留了下来，共 495MB）。
    #
    # 为什么必须在建 venv 前清掉：`python -m venv <已存在目录>` 默认
    # `clear=False` —— 它会把 pyvenv.cfg 和 python.exe 换成新解释器，
    # **却不会删除 site-packages 里的旧包**。于是会得到「3.10 的解释器 +
    # 3.14 编译的 .pyd 残留」这种混血环境，报错信息与真实原因毫无关系，
    # 排查成本极高。
    #
    # 策略：版本不符 → 清空重建（主上机器的 3.14 残留正属此列）；
    #       版本一致 → 保留，交给 pip 做增量修复（避免「基础档加装完整档」
    #       白重下 200MB 基础包）。
    old_cfg = venv_dir / "pyvenv.cfg"
    if old_cfg.is_file():
        old_ver = None
        try:
            for line in old_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("version"):
                    _, _, raw = line.partition("=")
                    parts = raw.strip().split(".")
                    old_ver = (int(parts[0]), int(parts[1]))
                    break
        except Exception:  # noqa: BLE001 — 读不出就当不符，宁可重建
            old_ver = None

        target_ver = _python_version_of(base)
        if old_ver is None or target_ver is None or old_ver != target_ver:
            if progress:
                progress(
                    "venv", 0.02,
                    "清理上次未完成的环境（{}.{}）…".format(
                        old_ver[0], old_ver[1]
                    ) if old_ver else "清理上次未完成的环境…",
                )
            shutil.rmtree(venv_dir, ignore_errors=True)

    r = subprocess.run(
        [*base, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True, timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise RuntimeError(f"创建环境失败：\n{r.stderr[:400]}")

    py = venv_dir / "Scripts" / "python.exe"
    if not py.is_file():
        raise RuntimeError("创建环境后找不到解释器。")

    # ── 二次校验：基底探测与实际建出的 venv 必须一致 ──
    # 为什么还要再查一次：基底探测是「探测命令」，建 venv 是「执行命令」，
    # 中间可能被 PATH 变化、py launcher 别名等因素干扰。信任但验证。
    v = _python_version_of([str(py)])
    if v is None or not (TORCH_PY_MIN <= v <= TORCH_PY_MAX):
        got = "{}.{}".format(*v) if v else "未知"
        raise RuntimeError(
            "创建出的环境 Python 版本为 {}，超出 PyTorch 支持的 "
            "{}.{} – {}.{} 区间。\n\n"
            "请安装 Python {}.{} 后重试，或改用「基础」档。".format(
                got, TORCH_PY_MIN[0], TORCH_PY_MIN[1], TORCH_PY_MAX[0], TORCH_PY_MAX[1],
                TORCH_PY_MAX[1], TORCH_PY_MAX[1] - 1,
            )
        )

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


# ─────────────────────────────────────────────────────────────
# 引擎目录内的残留进程清理
# ─────────────────────────────────────────────────────────────

_K32 = None


def _kernel32():
    """
    懒加载并配置好 kernel32 的函数签名（进程级缓存）。

    ⚠️ **必须显式设 restype**：这批 API 返回 HANDLE（64 位指针），
    ctypes 默认按 `c_int` 处理会**截断高 32 位** —— 表现为
    `OpenProcess` 看似成功、句柄却是垃圾值，后续调用全部失败，
    而且不报错（BOOL 返回 0）。这类静默截断极难排查，故集中在这里设一次。
    """
    global _K32
    if _K32 is not None:
        return _K32

    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k.Process32FirstW.restype = wintypes.BOOL
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k.Process32NextW.restype = wintypes.BOOL
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    _K32 = k
    return k


def _snapshot_processes() -> list[tuple[int, int, str]]:
    """枚举进程，返回 [(pid, ppid, exe_name)]。非 Windows / 失败返回空表。"""
    if os.name != "nt":
        return []

    import ctypes
    from ctypes import wintypes

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            # ULONG_PTR：x64 下 8 字节，必须用 c_size_t 而非 c_ulong
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    try:
        k = _kernel32()
    except Exception:  # noqa: BLE001
        return []

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    # INVALID_HANDLE_VALUE 是 (HANDLE)-1，x64 下为 2**64-1
    if not snap or int(snap) == (1 << 64) - 1:
        return []

    out: list[tuple[int, int, str]] = []
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append(
                (int(e.th32ProcessID), int(e.th32ParentProcessID), e.szExeFile)
            )
            ok = k.Process32NextW(snap, ctypes.byref(e))
    except Exception:  # noqa: BLE001
        pass
    finally:
        k.CloseHandle(snap)
    return out


def _process_image_path(pid: int) -> str | None:
    """取进程可执行文件的完整路径。权限不足 / 系统进程返回 None。"""
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        k = _kernel32()
    except Exception:  # noqa: BLE001
        return None

    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(4096)
        n = wintypes.DWORD(len(buf))
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value or None
        return None
    except Exception:  # noqa: BLE001
        return None
    finally:
        k.CloseHandle(h)


def _terminate_pid(pid: int) -> bool:
    if os.name != "nt":
        return False

    from ctypes import wintypes

    PROCESS_TERMINATE = 0x0001
    try:
        k = _kernel32()
    except Exception:  # noqa: BLE001
        return False

    h = k.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(k.TerminateProcess(h, 1))
    except Exception:  # noqa: BLE001
        return False
    finally:
        k.CloseHandle(h)


def _norm_path(p: str) -> str:
    """比较用归一化：绝对化 + normcase（Windows 上转小写与反斜杠）。"""
    try:
        return os.path.normcase(os.path.abspath(p))
    except Exception:  # noqa: BLE001
        return os.path.normcase(p)


def _ancestor_pids() -> set[int]:
    """
    自身 + 全部祖先的 pid 集合 —— 这些**绝不能杀**。

    ## 为什么必须排除祖先链
    本进程自己就可能位于 engine_dir 内：用 venv 的解释器跑 bootstrap.py
    是受支持的用法（开发态/手测都会这么跑）。若把「路径在引擎目录内的
    进程」一律杀光，第一个倒下的就是**正在执行安装的自己** ——
    表现为安装器无声消失，UI 上只有「安装进程异常退出，没有返回结果」。
    更隐蔽的一层：应用（宿主）启动的 bridge sidecar 若是本进程的父，
    同样会被误杀。故按 pid 链向上回溯，直至 0（System Idle）。
    """
    parents = {pid: ppid for pid, ppid, _ in _snapshot_processes()}
    keep: set[int] = set()
    cur = os.getpid()
    while cur and cur not in keep:
        keep.add(cur)
        cur = parents.get(cur, 0)
    return keep


def kill_engine_processes(engine_dir, log: list[str] | None = None) -> int:
    """
    结束所有「可执行文件位于 `engine_dir` 之内」的进程。返回结束的数量。

    ## 为什么这件事必须由引擎自己做，而不是交给安装器钩子
    线上案例（2026-10-05，主上老公的机器）：装「扒谱依赖与 Demucs」时报
        [WinError 32] 另一个程序正在使用此文件，进程无法访问。:
        '…\\AppData\\Local\\bapu\\engine\\.venv\\Lib\\site-packages\\
         pretty_midi\\TimGM6mb.sf2'

    已装的 NSIS 钩子（installer-hooks.nsh）判据是 `$INSTDIR`，
    而引擎默认落在 `%LOCALAPPDATA%\\bapu\\engine`，**用户还可能改到 D 盘** ——
    钩子**天然覆盖不到引擎目录**。于是「卸载旧版 → 装新版 → 首启装引擎」
    这条链路上，旧引擎的进程能全身而退，再到新版开始写文件时与 pip 抢句柄。

    → 清理点必须收敛到「每次装引擎都会跑」的这里，与安装器彻底解耦。

    ## 为什么不用 `taskkill /IM python.exe`
    会误杀用户自己的 Python（本项目用户里确有开发者）。判据只能是
    「可执行文件路径位于 engine_dir 之内」——与 NSIS 钩子同一思路。

    ## 为什么用 Win32 直接实现，而不是拉 PowerShell
    ① 零依赖、零额外进程（bootstrap.py 的既有约束是「只用标准库」）；
    ② 用户机器上的 PowerShell 可能被执行策略禁用；
    ③ 启动 PowerShell 本身要 0.5–1s，而这里每次装引擎只跑一次，
       但**失败重试路径**会再跑一次，能省则省。
    """
    if os.name != "nt":
        return 0

    target = _norm_path(str(engine_dir))
    if not target:
        return 0
    prefix = target.rstrip("\\/") + os.sep

    keep = _ancestor_pids()
    killed = 0
    details: list[str] = []

    for pid, _ppid, name in _snapshot_processes():
        # 0/4 是 System Idle / System，杀不动也不该碰
        if pid in keep or pid <= 4:
            continue
        path = _process_image_path(pid)
        if not path:
            # 取不到路径（系统进程 / 权限不足）：**宁可不杀**。
            # 判据不完整时动手，正是「误杀用户自己的 Python」的来源。
            continue
        if not _norm_path(path).startswith(prefix):
            continue
        if _terminate_pid(pid):
            killed += 1
            details.append("{} (pid {})".format(name or "?", pid))

    if killed:
        # 给内核一点时间回收句柄 —— 立刻重试会撞上尚未释放的锁。
        time.sleep(0.6)

    if log is not None and details:
        log.append("[清理残留进程] 已结束 {} 个：{}".format(killed, "、".join(details)))
    return killed


# 网络类失败的识别关键词。用途：给出「换镜像」这种**可操作**的建议，
# 而不是干巴巴一句「安装失败」——小白看到后者只能放弃。
#
# ⚠️ 注意：刻意**不**包含 "could not find a version" / "no matching distribution"。
# 这两句是 pip 的「索引里没有可用包」，根因多在 Python 版本过新或镜像索引格式不对，
# 换源往往无效。把它归进网络类会让提示指向「检查网络」，把人带偏——
# 主上实测时就因此以为「国内源和官方源都装不上」是网络问题。
_NET_HINTS = ("timeout", "timed out", "connection", "ssl", "temporary failure",
              "read timed out", "urlopen", "proxy", "failed to establish")

# pip 的「索引里找不到包」关键词（单独处理，见 _pip_install）
_NO_DIST_HINTS = ("could not find a version", "no matching distribution")

# 文件被占用类关键词（Windows：另一进程持有句柄）。
#
# ⚠️ 实测（2026-10-05，本机 engine/.venv）：
#   `import pretty_midi` / `PrettyMIDI()` **不持有** TimGM6mb.sf2 的句柄
#   （用「起进程 import 后父进程 rename 该文件」验证，三次全部 NOT LOCKED）。
#   故占用者**不是**「有引擎进程 import 了 pretty_midi」这么简单 ——
#   按可能性排序的真实来源：
#     ① 杀毒软件的实时防护：.sf2 是 4MB 数据文件，pip 写入耗时较长，
#        扫描窗口大，且线上报错**恰恰是这一个文件**，与该假设吻合；
#     ② 上一次失败的 pip / 引擎进程：句柄回收有延迟；
#     ③ 正在运行扒谱任务的引擎（synthesize 阶段才加载 soundfont）。
#   → 对策不是「杜绝占用」，而是「清进程 + 短暂等待 + 重试一次」。
_LOCK_HINTS_EN = ("winerror 32", "being used by another process",
                  "process cannot access the file")
_LOCK_HINTS_ZH = ("另一个程序正在使用此文件",)


def _looks_file_locked(text: str) -> bool:
    low = text.lower()
    if any(h in low for h in _LOCK_HINTS_EN):
        return True
    return any(h in text for h in _LOCK_HINTS_ZH)


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

    # ── 占用型失败：清进程 → 等一等 → 重试一次 ──
    # 线上案例：装 pretty_midi 时报 [WinError 32] 另一个程序正在使用此文件
    # （…\pretty_midi\TimGM6mb.sf2）。这类失败**重试往往就好**，原因有二：
    #   ① 杀软实时防护在文件落地瞬间持有句柄，通常几百毫秒即释放；
    #   ② 上一次失败的 pip / 引擎进程刚被结束，句柄回收有延迟。
    # 不重试的代价是「用户下完 2.5GB 的 torch，卡在最后一步而放弃」；
    # 重试的代价仅一次若干秒的等待 —— 显然该重试。
    r = None
    tail = ""
    for attempt in (0, 1):
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
            creationflags=getprocess_flags(),
        )
        tail = (r.stdout or "")[-600:] + (r.stderr or "")[-600:]
        log.append(f"$ pip install {' '.join(args[:3])}…\n{tail.strip()[:400]}")
        if r.returncode == 0 or attempt == 1:
            break
        if not _looks_file_locked(tail):
            break
        if progress:
            progress(stage, base_pct, "文件被占用，正在清理后重试…")
        # py = <engine_dir>/.venv/Scripts/python.exe → parents[2] 即 engine_dir
        try:
            kill_engine_processes(Path(py).resolve().parents[2], log)
        except Exception:  # noqa: BLE001 — 清理失败不应中断重试
            pass
        time.sleep(2.0)

    if r is not None and r.returncode != 0:
        low = tail.lower()
        # ── 文件被占用：给出「小白也能照做」的自救步骤 ──
        # 放在最前，因为它的处置方式与其他失败完全不同（不是网络、不是源）。
        if _looks_file_locked(tail):
            raise RuntimeError(
                f"安装 {shown} 时，文件被其他程序占用（已自动清理并重试一次）。\n\n"
                f"这通常是杀毒软件的实时防护在扫描新下载的文件导致的，"
                f"重启后再试多半就能通过。\n\n"
                f"可以试试：\n"
                f"  1. 重启电脑后重新安装（最有效，会清掉所有残留进程与文件锁）\n"
                f"  2. 暂时关闭杀毒软件的实时防护，装完再打开\n"
                f"  3. 打开任务管理器，结束所有 python 进程后重试\n\n"
                f"技术详情：\n{tail.strip()[-400:]}"
            )
        # ── 「索引里找不到包」：与网络中断分开报 ──
        # 这条路径的典型根因是 Python 版本过新 / 镜像索引格式不对，
        # 换源大概率无效，必须把排查方向说对。
        if any(h in low for h in _NO_DIST_HINTS):
            raise RuntimeError(
                f"所选源里找不到可用的 {shown}。\n\n"
                f"常见原因（按可能性排序）：\n"
                f"  1. 本机 Python 版本过新 —— PyTorch 目前只提供到 "
                f"Python {TORCH_PY_MAX[0]}.{TORCH_PY_MAX[1]} 的安装包\n"
                f"  2. 该镜像未同步此包（可换一个源重试）\n"
                f"  3. 所选档位与可用镜像不匹配\n\n"
                f"可以试试：\n"
                f"  1. 换一个源重试\n"
                f"  2. 改用「基础」档（约 200MB，无需下载 PyTorch）\n\n"
                f"技术详情：\n{tail.strip()[-400:]}"
            )
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

        # ── 先判「目标盘是否存在」──
        # 踩坑实录（主上实测）：把安装位置填成不存在的盘符（如 Z:）时，
        # 这里原本会静默放行（异常被吞），一路走到创建 venv 才炸，
        # 报出来的是 `FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'Z:\'`
        # —— 对小白完全不可读，也指不出该怎么办。
        # 盘不存在属于「目标不可用」，必须在下载前用大白话拦下。
        anchor = probe.anchor
        if anchor and not Path(anchor).exists():
            return False, (
                f"安装位置不可用：{anchor} 盘不存在或未就绪。\n\n"
                f"请点「浏览…」另选一个位置（建议非系统盘，"
                f"剩余空间需 ≥ {need_mb / 1024:.1f} GB）。"
            )

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
    except PermissionError as e:  # noqa: BLE001
        # 只读目录 / 无权限：这也是「目标不可用」，同样要拦住并说清。
        return False, (
            f"安装位置没有写入权限。\n\n"
            f"  位置：{target}\n"
            f"  原因：{e}\n\n"
            f"请换一个位置（如 D 盘，或用户目录下的文件夹）。"
        )
    except Exception:  # noqa: BLE001 — 其余检查失败不阻断安装
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
    # ⚠️ 必须 resolve 成绝对路径。
    # 踩坑实录：初次测试传了相对路径 `--dir .tmp/customtest`，
    # location.json 里就存了 `".tmp\customtest"` —— 下次从别的 cwd 读取时
    # 会解析到完全不同的位置，宿主导不到 venv。位置记录必须是绝对的。
    engine_dir = (
        Path(engine_dir) if engine_dir else default_engine_dir()
    ).resolve()
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

        # ── 清理引擎目录内的残留进程（必须在 pip 之前）──
        # 与安装器钩子分工：钩子管 $INSTDIR（安装目录），这里管引擎目录
        # （默认 %LOCALAPPDATA%\bapu\engine，用户可能改到别的盘）——
        # 后者是钩子覆盖不到的盲区，也是 [WinError 32] 的产生地。
        # 详细因果见 kill_engine_processes 的文档。
        killed = kill_engine_processes(engine_dir, log)
        if killed and progress:
            progress("venv", 0.01, f"已关闭 {killed} 个残留进程…")

        py = create_venv(engine_dir, progress)

        # ── torch 必须单独装且指定 CUDA 源 ──
        # 踩坑实录（部署第一大坑）：不指定 --index-url，pip 会装 CPU 版
        # torch，分离人声会慢约 20 倍，用户会以为程序坏了。
        #
        # 踩坑实录（第二大坑，2026-10-04）：镜像源必须满足 PEP 503
        # （`<index>/torch/` 可访问），且 Python 版本必须在 3.10–3.13。
        # 两者任一不满足，报错都是同一句「from versions: none」，
        # 用户会以为「换源就能解决」而去反复折腾源——其实换源无效。
        if spec.get("torch"):
            # 源回退链：主源失败自动换另一源。镜像偶发 502/限流时，
            # 让用户自己回引导页换源 = 2.5GB 白等一轮 + 重启程序，体验极差。
            idx_chain: list[str] = [torch_index]
            for _m in MIRRORS.values():
                _ti = _m.get("torch_index")
                if _ti and _ti not in idx_chain:
                    idx_chain.append(_ti)

            last_err: Exception | None = None
            for i, idx in enumerate(idx_chain):
                host = idx.split("//")[-1].split("/")[0]
                try:
                    _pip_install(
                        py,
                        ["torch", "torchaudio", "--index-url", idx,
                         # pip 默认 --retries 5；2.5GB 的传输值得更宽容，
                         # 单次块读超时给到 180s（这不是整体下载超时）。
                         "--retries", "10", "--timeout", "180"],
                        progress, "torch", 0.10, 0.55, log,
                        label=(
                            "PyTorch（GPU 版，约 2.5GB）" if i == 0
                            else "PyTorch（备用源 {}）".format(host)
                        ),
                    )
                    last_err = None
                    break
                except RuntimeError as e:
                    last_err = e
                    log.append("[torch 源回退] {} 失败：{}".format(
                        idx, str(e).splitlines()[0][:120]
                    ))
                    if i + 1 < len(idx_chain) and progress:
                        progress("torch", 0.10, "该源不可用，正在换备用源…")

            if last_err is not None:
                raise last_err

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

        # 记录位置：用户可能装在非默认盘，宿主需要据此找到 venv
        save_location(engine_dir)

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


_PY_COMPAT_CACHE: dict | None = None


def python_compat(fresh: bool = False) -> dict:
    """
    本机是否存在可用于 PyTorch 的 Python（3.10–3.13）。

    ## 为什么要前置到「选择页」就报
    完整档要下 2.5GB。若本机 Python 是 3.14，等用户点了「安装」、建完 venv
    才失败，是白等一轮 + 一次挫败。前置告知，用户可以直接改选「基础」档
    —— 代价 0，收益是省掉一次失败。项目里磁盘预检用的是同一套思路。

    结果做进程级缓存：状态查询会被 UI 调多次，而 Python 探测要跑一串
    subprocess，不缓存会在界面加载时明显卡顿。
    """
    global _PY_COMPAT_CACHE
    if _PY_COMPAT_CACHE is not None and not fresh:
        return _PY_COMPAT_CACHE

    rejects: list = []
    cmd = find_base_python(rejects)
    if cmd:
        _PY_COMPAT_CACHE = {
            "ok": True,
            "cmd": " ".join(cmd),
            "detail": "",
        }
        return _PY_COMPAT_CACHE

    got = "、".join(v for _, v in rejects[:3]) or "未检测到"
    _PY_COMPAT_CACHE = {
        "ok": False,
        "cmd": "",
        "detail": (
            "本机未找到 PyTorch 支持的 Python（需要 {}.{} – {}.{}）。"
            "已检测到的版本：{}。安装「完整」档会失败；"
            "可改用「基础」档，或安装 Python {}.{} 后重启本程序。"
        ).format(
            TORCH_PY_MIN[0], TORCH_PY_MIN[1],
            TORCH_PY_MAX[0], TORCH_PY_MAX[1],
            got, TORCH_PY_MAX[1], TORCH_PY_MAX[1] - 1,
        ),
    }
    return _PY_COMPAT_CACHE


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
        # 本机 Python 是否满足 PyTorch 要求（选择页据此显示警告）
        "pythonCompat": python_compat(),
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
                # 该档位是否依赖 PyTorch（UI 据此判断 pythonCompat 警告是否相关）
                "needsTorch": bool(v.get("torch")),
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


def _dir_bytes(root: Path) -> int:
    """目录总字节数。用于迁移前的空间校验与进度分母。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def _free_bytes_at(path: Path) -> int:
    """`path` 所在卷的剩余字节。路径尚不存在时沿父目录上溯。"""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(str(probe)).free


def _volume(p: Path) -> str:
    """
    取路径所在盘符（小写，不含冒号后的部分）。

    ## 为什么单独抽一个函数，而不是就地调 os.path.splitdrive
    为了**可测**。判断「同盘 rename」还是「跨盘复制」是迁移里最关键的分支，
    测试必须能造出「两侧不同盘」的场景（CI 上拿不到第二个物理盘）。

    而打桩 `os.path.splitdrive` 是个陷阱：Windows 上 `os.path` 就是 `ntpath`，
    `pathlib` 的 `PureWindowsPath._parse_args` 内部也在调它。补丁一上，
    连 `Path("C:\\a\\elsewhere").is_file()` 里的路径解析都被换掉，
    临时目录被解析成不存在的路径 —— 表现为「找不到已安装的引擎」，
    而真正的被测逻辑一行都没跑到（本项目实测踩过：3 个用例全红，
    报的还是「引擎不存在」这种与跨盘判定毫无关系的错）。

    故把「问盘符」这个动作收敛成一个可替换的接缝：测试只打桩 `_volume`，
    不碰标准库。
    """
    return os.path.splitdrive(str(p))[0].lower()


def move_engine(
    engine_dir: Path,
    new_dir: Path,
    progress=None,
) -> InstallResult:
    """
    把已装好的引擎迁移到新位置。

    ## 为什么需要它
    默认落点是 `%LOCALAPPDATA%/bapu/engine`（C 盘）。完整档实测 5.4GB，
    而 C 盘恰恰是最容易告急的那个盘。用户在设置里换位置，
    不能要求他重装一遍（重下载 2.5GB 的 PyTorch）。

    ## 安全策略（顺序不可调换）
      1. 校验源引擎真的可用（解释器存在）
      2. 校验目标合法（非源自身、非源子目录、非已有内容的目录）
      3. 跨盘时校验目标剩余空间 ≥ 源体积 × 1.1
      4. 落地：同盘走**原子 rename**（瞬时），跨盘走逐文件**复制**
      5. 用**新位置**的解释器真实 import 一次，确认可用
      6. 写 `location.json`
      7. 以上全过，才删除旧目录

    ## 为什么不能直接用 shutil.move
    它是「先搬走再验证」：跨盘中途失败（断网、磁盘满、被杀软中断）时，
    用户唯一可用的引擎已经半残，重下 5.4GB 的代价极高。
    「新的先立起来，旧的才敢拆」的语义下，最坏情况只是多占一份磁盘。

    ## 为什么移动 venv 目录是安全的
    已核查本项目的所有子进程调用：pip 走 `python -m pip`，
    demucs 走 Python API（`import demucs`），**没有任何一处直接调用
    `Scripts/*.exe`**。而 `pyvenv.cfg` 里记录的 `home` 指向的是
    基础解释器、与 venv 自身位置无关。所以整目录搬迁不会失效。
    """
    t0 = time.time()
    log: list[str] = []

    def note(msg: str) -> None:
        log.append(msg)
        print(msg)

    def report(pct: float, msg: str) -> None:
        if progress:
            progress("move", pct, msg)

    src = Path(engine_dir).expanduser().resolve()
    dst = Path(new_dir).expanduser().resolve()

    def fail(msg: str) -> InstallResult:
        return InstallResult(False, str(src), "", "migrate", time.time() - t0,
                             message=msg, log=log)

    # ── 1) 源必须真的可用 ──
    src_py = src / ".venv" / "Scripts" / "python.exe"
    if not src_py.is_file():
        return fail("找不到已安装的引擎，无法迁移。请先安装引擎。")

    # ── 2) 目标合法性 ──
    if dst == src:
        return fail("目标位置与当前位置相同，无需迁移。")
    if src in dst.parents:
        return fail("目标位置不能位于当前引擎目录内部。")
    if dst.exists() and any(dst.iterdir()):
        return fail("目标文件夹里已有其他文件，请换一个空文件夹。")

    # ── 3) 空间校验 ──
    same_volume = _volume(src) == _volume(dst)
    size = _dir_bytes(src)
    if not same_volume:
        free = _free_bytes_at(dst)
        if free < size * 1.1:
            return fail(
                "目标磁盘空间不足：需要约 {:.1f} GB，可用 {:.1f} GB。".format(
                    size * 1.1 / 1024 ** 3, free / 1024 ** 3
                )
            )

    note("源位置：{}（{:.2f} GB）".format(src, size / 1024 ** 3))
    note("新位置：{}".format(dst))

    # ── 4) 落地 ──
    used_rename = False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if same_volume:
            report(0.05, "同一磁盘，正在移动…")
            os.rename(str(src), str(dst))
            used_rename = True
        else:
            report(0.05, "正在复制文件…")
            pairs: list[tuple[Path, Path]] = []
            for dirpath, _dirnames, filenames in os.walk(src):
                for name in filenames:
                    full = Path(dirpath) / name
                    pairs.append((full, dst / full.relative_to(src)))
            total = max(1, size)
            done = 0
            last_pct = -1
            for full, target in pairs:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(full), str(target))
                try:
                    done += full.stat().st_size
                except OSError:
                    pass
                pct = 0.05 + 0.85 * done / total
                # 节流：1% 粒度上报，否则 5.4GB 会产生数万条进度事件
                if pct - last_pct >= 0.01:
                    last_pct = pct
                    report(pct, "正在复制… {:.1f} / {:.1f} GB".format(
                        done / 1024 ** 3, size / 1024 ** 3))
    except BaseException as e:  # noqa: BLE001
        # 落地阶段失败：新位置可能残留半份，清掉；源位置保持原样
        if not used_rename:
            shutil.rmtree(dst, ignore_errors=True)
        return fail("迁移过程中出错：{}: {}".format(type(e).__name__, str(e)[:200]))

    # ── 5) 用新位置真实 import 一次 ──
    # 不信任「文件复制完了」——venv 能否工作只有解释器自己知道。
    report(0.92, "正在验证新位置的引擎…")
    probe = check_installed(dst)
    if not probe.get("ready"):
        # 回滚：把引擎放回原处，绝不让用户失去可用的引擎
        if used_rename:
            try:
                os.rename(str(dst), str(src))
            except OSError:
                pass
        else:
            shutil.rmtree(dst, ignore_errors=True)
        return fail(
            "新位置验证未通过（{}），已回滚到原位置。".format(
                probe.get("reason", "依赖不可用"))
        )

    # ── 6) 记录新位置 ──
    save_location(dst)

    # ── 7) 拆旧目录（仅复制路径需要；rename 时旧路径已经不存在）──
    if not used_rename:
        report(0.97, "正在清理旧位置…")
        shutil.rmtree(src, ignore_errors=True)

    report(1.0, "迁移完成")
    caps = "Demucs ✓ / CUDA ✓" if probe.get("demucs") and probe.get("cuda") else (
        "Demucs ✓" if probe.get("demucs") else "基础功能"
    )
    return InstallResult(
        True, str(dst), str(dst / ".venv" / "Scripts" / "python.exe"),
        "migrate", time.time() - t0,
        message="引擎已迁移到新位置（{}）。".format(caps),
        log=log,
    )


def _emit_result(res: InstallResult, elapsed: float) -> None:
    """
    输出机器可读的最终结果。安装与迁移**共用同一协议**，
    宿主（Rust）侧因此只需一套解析逻辑。

    ## 为什么必须结构化输出
    前端不能只看「子进程结束了」就当作成功 ——
    安装失败（网络中断 / 磁盘不足 / pip 报错）同样是正常退出 + 一段可读文本。
    不把结果结构化传出去，用户只会看到界面切回选择页，完全不知道发生了什么。

    ## 为什么先补一个换行
    进度条是用回车符原地刷新的、不换行，所以此刻光标还停在进度条那一行上。
    必须先补换行，否则 JSON 会与进度条残留挤在同一行 ——
    上层按行解析时看到的那行不以花括号开头，会直接漏掉结果。
    这里用 chr(10) 而非转义字符，避免多层转义踩坑。
    """
    sys.stdout.write("\r" + " " * 84 + "\r")
    sys.stdout.write(chr(10))
    sys.stdout.flush()
    print(
        json.dumps(
            {
                "type": "install_result",
                "ok": res.ok,
                # tier 对迁移来说恒为 "migrate"，宿主据此区分两种流程
                "tier": res.tier,
                "message": res.message,
                "elapsed": round(elapsed, 1),
                "engine_dir": res.engine_dir,
                "log_tail": (res.log[-1][:800] if res.log else ""),
            },
            ensure_ascii=False,
        )
    )


def _force_utf8_stdio() -> None:
    """
    强制 stdin/stdout/stderr 使用 UTF-8。

    ## 为什么必须显式设置
    当标准流被重定向到管道（Tauri 用 Stdio::piped() 捕获就是这种情况）时，
    Python 会用**系统 locale 编码**而非 UTF-8 —— 中文 Windows 上是 cp936(GBK)。
    - stdout：中文 JSON 被编成 GBK 字节，Rust 侧按 UTF-8 解码得到一堆替换字符，
      界面上就是「◆◆◆◆」乱码。
    - stdin：Rust 写出 UTF-8 字节而这边按 GBK 解码，中文路径当场变乱码。

    stdin 一并处理是**双保险**（本脚本的 stdin 目前被置为 null，暂不读）；
    真正踩过坑的是 bridge.py 的常驻 sidecar，详见那里的说明。

    **绝不能依赖环境**：双击启动的应用不继承开发者的 shell 环境，
    所以必须在本进程内显式声明。errors="replace" 兜底，避免编码异常直接崩进程。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
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
    ap.add_argument(
        "--move-to",
        metavar="DIR",
        help="把已安装的引擎迁移到指定目录（设置页的「更换位置」）",
    )
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

    # ── 迁移：与安装走同一套进度 / 结果协议，宿主无需区分 ──
    if args.move_to:
        t0_move = time.time()

        def _pm(stage: str, pct: float, msg: str) -> None:
            bar = "█" * int(pct * 28) + "░" * (28 - int(pct * 28))
            sys.stdout.write(f"\r  [{bar}] {pct * 100:5.1f}%  {msg[:46]:<46}")
            sys.stdout.flush()

        src_dir = Path(args.dir) if args.dir else default_engine_dir()
        print(f"源位置：{src_dir}")
        print(f"新位置：{Path(args.move_to)}")
        print()
        try:
            mres = move_engine(src_dir, Path(args.move_to), progress=_pm)
        except BaseException as e:  # noqa: BLE001
            # 与安装同样的兜底：任何未捕获异常也必须产出结构化结果
            mres = InstallResult(
                False, str(src_dir), "", "migrate", time.time() - t0_move,
                message="迁移器内部异常：{}: {}".format(
                    type(e).__name__, str(e)[:400]),
            )
        _emit_result(mres, time.time() - t0_move)
        if mres.ok:
            print(f"迁移完成（{time.time() - t0_move:.0f} 秒）")
            print(f"  新位置: {mres.engine_dir}")
            print(f"  状态  : {mres.message}")
            raise SystemExit(0)
        print(f"迁移失败：{mres.message}")
        raise SystemExit(1)

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

    try:
        res = install(
            args.tier,
            Path(args.dir) if args.dir else None,
            mirror=args.mirror,
            progress=_p,
        )
    except BaseException as e:  # noqa: BLE001
        # 兜底：任何未捕获的异常也必须产出结构化结果。
        # 踩坑实录（最伤用户的一类）：install() 内部虽已有 try/except，
        # 但一旦在它之外出事（KeyboardInterrupt / MemoryError / 解释器层错误），
        # 尾部的 JSON 就永远不会输出 —— 上层只看「子进程退出了」，
        # 只能显示成「安装进程异常退出，没有返回结果」，用户完全不知道发生了什么。
        # 失败必须说清楚：是磁盘满、断网，还是别的。
        try:
            _edir = str((Path(args.dir) if args.dir else default_engine_dir()).resolve())
        except Exception:  # noqa: BLE001
            _edir = str(args.dir or "")
        res = InstallResult(
            False, _edir, "", args.tier, time.time() - t0,
            message="安装器内部异常：{}: {}".format(type(e).__name__, str(e)[:400]),
        )
    _emit_result(res, time.time() - t0)

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
