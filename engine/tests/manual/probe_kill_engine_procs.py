"""回归：kill_engine_processes 能否解除「被进程持有」的文件占用。

构造与线上同源的现场：
  1. 用 engine/.venv 的解释器起一个子进程，**真实打开** TimGM6mb.sf2
     并持有句柄（open 后 sleep），其可执行文件路径落在 engine_dir 之内；
  2. 父进程尝试 rename 该文件 —— 期望失败（复现 WinError 32）；
  3. 调用 kill_engine_processes(engine_dir)；
  4. 再次 rename —— 期望成功。

同时验证「祖先链排除」：调用方自身不得被杀。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_DIR = HERE.parent
sys.path.insert(0, str(ENGINE_DIR))

import bootstrap  # noqa: E402

VENV_PY = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
SF2 = (ENGINE_DIR / ".venv" / "Lib" / "site-packages"
       / "pretty_midi" / "TimGM6mb.sf2")

HOLD = "import time;f=open(r'{}','rb');f.read(16);time.sleep(30)"


def try_rename(p: Path) -> str:
    alt = p.with_suffix(p.suffix + ".probe")
    try:
        os.rename(p, alt)
        os.rename(alt, p)
        return "可写（未被占用）"
    except OSError as e:
        return f"被占用：{e}"


def main() -> int:
    fails: list[str] = []

    if not VENV_PY.is_file():
        print(f"跳过：找不到 {VENV_PY}")
        return 0
    if not SF2.is_file():
        print(f"跳过：找不到 {SF2}")
        return 0

    print(f"engine_dir = {ENGINE_DIR}")
    print(f"探针进程   = {VENV_PY}")
    print()

    # ── 1) 起一个真实持有 sf2 句柄的进程 ──
    child = subprocess.Popen(
        [str(VENV_PY), "-c", HOLD.format(str(SF2))],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(f"[1] 已起占用进程 pid={child.pid}")
    time.sleep(3)
    if child.poll() is not None:
        print("    子进程提前退出，测试作废")
        return 1

    # ── 2) 确认占用成立 ──
    before = try_rename(SF2)
    print(f"[2] 清理前：{before}")
    if "可写" in before:
        print("    ⚠️ 未能构造出占用现场，无法验证修复")
        child.kill()
        return 1

    # ── 3) 执行清理 ──
    log: list[str] = []
    killed = bootstrap.kill_engine_processes(ENGINE_DIR, log)
    print(f"[3] kill_engine_processes → 结束 {killed} 个进程")
    for line in log:
        print(f"    {line}")

    # ── 4) 验证占用已解除 ──
    child_rc = child.poll()
    time.sleep(1)
    child_rc = child.poll() if child_rc is None else child_rc
    after = try_rename(SF2)
    print(f"[4] 清理后：{after}   （探针进程 rc={child_rc}）")

    if killed < 1:
        fails.append("未结束任何进程")
    if child_rc is None:
        fails.append("占用进程仍存活")
        child.kill()
    if "可写" not in after:
        fails.append("文件仍被占用")

    # ── 5) 祖先链排除：调用方必须活着 ──
    print(f"[5] 调用方仍存活：{os.getpid() > 0}")
    if os.getpid() in bootstrap._ancestor_pids():
        print("    祖先链含自身 ✓")
    else:
        fails.append("祖先链未含自身")

    print()
    if fails:
        print("FAIL：" + "；".join(fails))
        return 1
    print("PASS：占用被解除，且未误杀调用方。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
