"""补充判定：cp310 的二进制扩展能否被 3.13 解释器加载。

前一个实验证明「把 pyvenv.cfg 的 home 改到应用自带 runtime(3.13) 后，
解释器本身能启动」。但那只是解释器启动，**二进制包能否 import 是另一回事**。

本实验只从 zip 里取出一个 1MB 级的 cp310 扩展（_cffi_backend.cp310-win_amd64.pyd），
用 3.13 解释器尝试加载 —— 等价于老公机器上改用 runtime 后 `import torch` 的命运
（torch 的核心同样是 `_C.cp310-win_amd64.pyd`，ABI 标签规则一致）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ZIP = Path(os.path.expandvars(r"%LOCALAPPDATA%\bapu\engine.zip"))
PROBE = Path(os.environ["TEMP"]) / "engine_abi_probe"
RUNTIME_PY = (Path(os.environ["LOCALAPPDATA"]) / "扒谱助手"
              / "runtime" / "python" / "python.exe")
BASE310_PY = (Path(os.environ["LOCALAPPDATA"]) / "Programs"
              / "Python" / "Python310" / "python.exe")

TARGET = "_cffi_backend.cp310-win_amd64.pyd"
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    if not ZIP.is_file():
        print(f"找不到 {ZIP}")
        return 1

    target_member = None
    torch_core = None
    with zipfile.ZipFile(ZIP) as z:
        for n in z.namelist():
            base = os.path.basename(n)
            if base == TARGET:
                target_member = n
            if base.startswith("_C.cp") and "torch" in n:
                torch_core = n
        if target_member is None:
            print(f"包内未找到 {TARGET}")
            return 1
        shutil.rmtree(PROBE, ignore_errors=True)
        PROBE.mkdir(parents=True, exist_ok=True)
        z.extract(target_member, PROBE)

    pyd = PROBE / target_member
    print(f"取出: {target_member}")
    print(f"      {pyd.stat().st_size / 1024:.1f} KB")
    print(f"包内 torch 核心二进制: {torch_core}")
    print()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(pyd.parent)
    code = "import _cffi_backend;print('imported ok')"

    for label, py in (("3.10.6（包原本配套）", BASE310_PY),
                      ("3.13.16（应用自带 runtime）", RUNTIME_PY)):
        if not py.is_file():
            print(f"[{label}] 解释器不存在，跳过")
            continue
        r = subprocess.run([str(py), "-c", code], capture_output=True,
                           text=True, timeout=60, env=env,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            print(f"[{label}] {r.stdout.strip()}  → 可加载")
        else:
            msg = (r.stderr or "").strip().splitlines()
            tail = [ln for ln in msg if ln.strip()][-1] if msg else f"rc={r.returncode}"
            print(f"[{label}] 失败 → {tail}")

    shutil.rmtree(PROBE, ignore_errors=True)
    print("\n（探针目录已清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
