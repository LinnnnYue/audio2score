"""
probe_ext_path.py — 验证 Windows 扩展路径前缀（\\?\）是否导致 Python 启动失败

背景：Rust 侧 engine_dir() 用了 canonicalize()，返回的路径带 `\\?\` 前缀
（如 `\\?\D:\...`）。诊断日志显示 invoke 进了 run_once 却永不返回，
怀疑与此有关。本脚本直接对比两种路径的启动结果。

跑法：engine/.venv/Scripts/python.exe engine/tools/probe_ext_path.py
"""

import os
import subprocess
import sys

REAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(REAL, "bridge.py")
PY_REAL = os.path.join(REAL, ".venv", "Scripts", "python.exe")

# Windows 扩展路径前缀：\\?\
EXT_PREFIX = "\\\\?\\"


def main() -> int:
    py_ext = EXT_PREFIX + PY_REAL
    req = '{"cmd":"modes","id":"t"}\n'

    print(f"解释器(普通)存在: {os.path.isfile(PY_REAL)}  -> {PY_REAL}")
    print(f"解释器(扩展)存在: {os.path.isfile(py_ext)}  -> {py_ext}")
    print(f"bridge 存在: {os.path.isfile(BRIDGE)}")
    print()

    results = {}
    for label, exe in (("普通路径", PY_REAL), ("扩展路径", py_ext)):
        try:
            r = subprocess.run(
                [exe, BRIDGE],
                input=req,
                capture_output=True,
                text=True,
                timeout=25,
            )
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            last = lines[-1][:70] if lines else "(无输出)"
            results[label] = len(lines) > 0
            print(f"{label}: rc={r.returncode}  输出 {len(lines)} 行")
            print(f"          末行: {last}")
            if r.returncode != 0:
                err = (r.stderr or "").strip().splitlines()
                if err:
                    print(f"          stderr: {err[-1][:100]}")
        except subprocess.TimeoutExpired:
            results[label] = False
            print(f"{label}: 超时（25s）——疑似卡死")
        except Exception as e:  # noqa: BLE001
            results[label] = False
            print(f"{label}: 异常 {type(e).__name__}: {str(e)[:100]}")
        print()

    ok_both = all(results.values())
    if results.get("普通路径") and not results.get("扩展路径"):
        print("结论：扩展路径前缀（\\\\?\\）是罪魁祸首 —— 须在 Rust 侧去掉")
    elif ok_both:
        print("结论：两种路径都能跑 —— 扩展路径无罪，问题在别处")
    else:
        print("结论：普通路径也有问题，需进一步排查")

    return 0 if ok_both else 1


if __name__ == "__main__":
    sys.exit(main())
