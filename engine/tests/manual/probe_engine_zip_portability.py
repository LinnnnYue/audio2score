"""判定 engine.zip 能否直接给另一台机器使用。

只解压 venv 的「启动三件套」（pyvenv.cfg + Scripts/python.exe，共约 300KB），
不碰 torch（4.4GB）。三个场景：

  A 原样解压       —— home 指向本机 Python310（存在）→ 预期可启动
  B home 改为应用自带 runtime（3.13.16）—— 模拟老公机器上「唯一确定存在」的解释器
  C home 改为不存在的路径 —— 模拟老公机器的真实情况（他的用户名与本机不同）

结论判据：venv 的 python.exe 是「按 pyvenv.cfg 的 home + version 拼 DLL 名」
去找基础解释器的，包内**不含 python3xx.dll**。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

ZIP = Path(os.path.expandvars(r"%LOCALAPPDATA%\bapu\engine.zip"))
PROBE = Path(os.environ["TEMP"]) / "engine_zip_probe"
RUNTIME_PY_DIR = Path(os.environ["LOCALAPPDATA"]) / "扒谱助手" / "runtime" / "python"
BASE310 = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Python" / "Python310"

CREATE_NO_WINDOW = 0x08000000


def extract_minimal(dst: Path) -> Path:
    """解压 pyvenv.cfg + Scripts/python.exe，返回 .venv 目录。"""
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    want = {".venv/pyvenv.cfg", ".venv/Scripts/python.exe"}
    with zipfile.ZipFile(ZIP) as z:
        for n in z.namelist():
            if n in want:
                z.extract(n, dst)
    return dst / ".venv"


def write_cfg(venv: Path, home: str, version: str) -> None:
    (venv / "pyvenv.cfg").write_text(
        f"home = {home}\n"
        f"include-system-site-packages = false\n"
        f"version = {version}\n",
        encoding="utf-8",
    )


def run(venv: Path) -> str:
    py = venv / "Scripts" / "python.exe"
    if not py.is_file():
        return "（python.exe 缺失）"
    try:
        r = subprocess.run(
            [str(py), "-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True, text=True, timeout=60,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:  # noqa: BLE001
        return f"启动异常 {type(e).__name__}: {e}"
    if r.returncode == 0:
        return f"启动成功 → Python {r.stdout.strip()}"
    err = (r.stderr or r.stdout or "").strip().splitlines()
    return "启动失败 → " + (err[-1] if err else f"rc={r.returncode}")


def main() -> int:
    if not ZIP.is_file():
        print(f"找不到 {ZIP}")
        return 1

    print(f"包: {ZIP}  ({ZIP.stat().st_size / 1024**3:.2f} GB)")
    print(f"本机 Python310: {'存在' if BASE310.is_dir() else '不存在'}  ({BASE310})")
    print(f"应用 runtime : {RUNTIME_PY_DIR}")
    print()

    venv = extract_minimal(PROBE)
    dlls_310 = list(BASE310.glob("python3*.dll")) if BASE310.is_dir() else []
    dlls_313 = list(RUNTIME_PY_DIR.glob("python3*.dll"))
    print(f"Python310 目录内 DLL: {[d.name for d in dlls_310]}")
    print(f"runtime   目录内 DLL: {[d.name for d in dlls_313]}")
    print()

    print("── 场景 A：原样（home = 本机 Python310）──")
    print("   ", run(venv))

    print("── 场景 B：home 改为应用自带 runtime（3.13）──")
    write_cfg(venv, str(RUNTIME_PY_DIR), "3.10.6")
    print("   ", run(venv))

    print("── 场景 C：home 改为老公机器的实际路径（不存在）──")
    write_cfg(venv, r"C:\Users\Administrator\AppData\Local\bapu\engine\.venv-base", "3.10.6")
    print("   ", run(venv))

    print("── 场景 D：home 改到 runtime 且版本写成 3.13（骗过 DLL 查找）──")
    write_cfg(venv, str(RUNTIME_PY_DIR), "3.13.16")
    print("   ", run(venv))

    shutil.rmtree(PROBE, ignore_errors=True)
    print("\n（探针目录已清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
