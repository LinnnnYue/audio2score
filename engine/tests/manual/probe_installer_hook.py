"""回归：NSIS 钩子能否清掉「安装目录内」与「%LOCALAPPDATA%\\bapu 内」的残留进程。

背景：钩子原先只按 $INSTDIR 匹配，漏掉引擎目录（%LOCALAPPDATA%\\bapu）。
本脚本构造两个替身进程分别落在两个根目录下，跑一次真实静默安装，
验证两者都被清掉。

替身用 sys32\\ping.exe 的副本：它不需要任何依赖即可运行并挂起，
且其可执行文件路径就在目标目录内 —— 与「引擎 python.exe 位于引擎目录内」
在钩子的判据（ExecutablePath.StartsWith(dir)）上完全等价。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"])
INSTALL_DIR = LOCAL / "扒谱助手"
BAPU_DIR = LOCAL / "bapu" / "probe"

REPO = Path(__file__).resolve().parents[2]
SETUP = (REPO / "src-tauri" / "target" / "release" / "bundle" / "nsis"
         / "扒谱助手_0.1.0_x64-setup.exe")

PING = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ping.exe"
CREATE_NO_WINDOW = 0x08000000

STUB_INSTALL = INSTALL_DIR / "bapu_probe_ping.exe"
STUB_BAPU = BAPU_DIR / "bapu_probe_ping.exe"


def alive(p: subprocess.Popen) -> bool:
    return p.poll() is None


def start_stub(exe: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [str(exe), "-n", "600", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def cleanup() -> None:
    for f in (STUB_INSTALL, STUB_BAPU):
        try:
            if f.exists():
                f.unlink()
        except OSError:
            pass
    try:
        if BAPU_DIR.exists() and not any(BAPU_DIR.iterdir()):
            BAPU_DIR.rmdir()
    except OSError:
        pass


def main() -> int:
    if not SETUP.is_file():
        print(f"找不到安装包：{SETUP}")
        return 1
    if not PING.is_file():
        print(f"找不到替身源：{PING}")
        return 1

    print(f"$INSTDIR 替身落点 : {INSTALL_DIR}")
    print(f"bapu   替身落点 : {BAPU_DIR}")
    print()

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    BAPU_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PING, STUB_INSTALL)
    shutil.copy2(PING, STUB_BAPU)

    pa = start_stub(STUB_INSTALL)
    pb = start_stub(STUB_BAPU)
    time.sleep(2.5)

    before_a, before_b = alive(pa), alive(pb)
    print(f"[1] 安装前：$INSTDIR 替身 alive={before_a}（pid {pa.pid}）")
    print(f"           bapu    替身 alive={before_b}（pid {pb.pid}）")
    if not (before_a and before_b):
        print("    替身未能存活，测试作废")
        cleanup()
        return 1

    print("[2] 跑静默安装 /S …")
    t0 = time.time()
    r = subprocess.run([str(SETUP), "/S"], timeout=900,
                       creationflags=CREATE_NO_WINDOW)
    print(f"    安装器退出码={r.returncode}  耗时={time.time() - t0:.0f}s")

    time.sleep(3)
    after_a, after_b = alive(pa), alive(pb)
    print(f"[3] 安装后：$INSTDIR 替身 alive={after_a}")
    print(f"           bapu    替身 alive={after_b}")

    # 收尾：无论结果如何都结束残留替身
    for p in (pa, pb):
        if alive(p):
            p.kill()
    cleanup()
    print(f"[4] 已清理替身文件（$INSTDIR 内替身存在={STUB_INSTALL.exists()}）")

    print()
    if before_a and not after_a and before_b and not after_b:
        print("PASS：两个根目录的残留进程均被安装器钩子清掉。")
        return 0
    print("FAIL：钩子未清干净 —— "
          f"$INSTDIR 被杀={before_a and not after_a}，"
          f"bapu 被杀={before_b and not after_b}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
