"""探针：import pretty_midi 是否持有 TimGM6mb.sf2 的文件句柄。

判据：起一个 python 进程 import pretty_midi 并挂起，然后在父进程尝试
rename 该 .sf2 —— 若被占用则 Windows 抛 WinError 32，与用户侧报错同源。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "..", ".venv", "Scripts", "python.exe")
SF2 = os.path.join(
    HERE, "..", ".venv", "Lib", "site-packages", "pretty_midi", "TimGM6mb.sf2"
)
VENV_PY = os.path.abspath(VENV_PY)
SF2 = os.path.abspath(SF2)


def probe(import_code: str, label: str) -> None:
    if not os.path.isfile(VENV_PY):
        print(f"[{label}] 跳过：找不到 {VENV_PY}")
        return
    if not os.path.isfile(SF2):
        print(f"[{label}] 跳过：找不到 {SF2}")
        return

    p = subprocess.Popen(
        [VENV_PY, "-c", import_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(5)
    if p.poll() is not None:
        out, err = p.communicate()
        print(f"[{label}] 子进程已退出 rc={p.returncode}")
        print(f"        stderr={err.decode('utf-8', 'replace')[:300]}")
        return

    alt = SF2 + ".lockprobe"
    try:
        os.rename(SF2, alt)
        os.rename(alt, SF2)
        print(f"[{label}] NOT LOCKED —— import 不持有句柄")
    except OSError as e:
        print(f"[{label}] LOCKED —— {e}")

    p.kill()
    p.wait(timeout=10)


if __name__ == "__main__":
    print(f"目标文件: {SF2}")
    print(f"解释器  : {VENV_PY}")
    print()
    probe("import time; time.sleep(20)", "A: 空进程基线")
    probe("import pretty_midi, time; time.sleep(20)", "B: import pretty_midi")
    probe(
        "import pretty_midi, time; "
        "pm = pretty_midi.PrettyMIDI(); time.sleep(20)",
        "C: import + 实例化 PrettyMIDI",
    )
    print()
    print("B/C 若为 LOCKED，则残留引擎进程可直接解释用户的 WinError 32。")
