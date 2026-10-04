"""
check_install_output.py — 检查 bootstrap 输出的字节结构

目的：验证结构化结果 JSON 是否独占一行（不被 \r 进度条残留污染）。
背景：进度条用 \r 原地刷新不换行，若 JSON 与残留内容挤在同一行，
上层按行解析时会漏掉结果（主上实测踩过「安装进程异常退出，没有返回结果」）。
"""

import subprocess
import sys
from pathlib import Path

# 本脚本在 engine/tools/ 下，需上溯三级到项目根
ROOT = Path(__file__).resolve().parent.parent.parent
PY = ROOT / "engine" / ".venv" / "Scripts" / "python.exe"
BOOT = ROOT / "engine" / "bootstrap.py"
OUT = ROOT / ".tmp" / "ft" / "out.bin"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    target = ROOT / ".tmp" / "ft" / "a"

    proc = subprocess.run(
        [str(PY), str(BOOT), "--tier", "basic", "--mirror", "cn", "--dir", str(target)],
        capture_output=True,
        timeout=600,
    )

    print(f"退出码: {proc.returncode}")
    raw = proc.stdout
    print(f"stdout 字节数: {len(raw)}")

    if not raw:
        print("  ✗ stdout 为空")
        return 1

    lines = [l for l in raw.split(b"\n") if l.strip()]
    print(f"非空行数: {len(lines)}")

    print("\n=== 最后 2 行的结构 ===")
    for l in lines[-2:]:
        head = l[:60]
        starts_with_brace = l.lstrip().startswith(b"{")
        has_cr = b"\r" in l
        print(f"  前60字节={head!r}")
        print(f"    以 {{ 开头={starts_with_brace}  含 \\r={has_cr}")

    # 核心判据：是否存在一行「以 { 开头且含 install_result」
    ok_line = any(
        l.lstrip().startswith(b"{") and b"install_result" in l for l in lines
    )
    print(f"\n=== 判据 ===")
    print(f"  存在「独立成行且以 {{ 开头的 install_result」: {ok_line}")

    if not ok_line:
        print("  ✗ 不合格：JSON 与进度条混流，上层按行解析会漏掉")

    return 0 if ok_line else 1


if __name__ == "__main__":
    sys.exit(main())
