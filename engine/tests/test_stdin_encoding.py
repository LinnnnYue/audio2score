"""
test_stdin_encoding.py — 非 ASCII 路径经 stdin 传递的编码回归测试

## 为什么这份测试必须存在

bridge.py 的协议是「stdin 单行 JSON」。Rust 侧 `writeln!` 写出的是 **UTF-8 字节**，
而 Python 读**管道** stdin 时若按系统 locale 解码（中文 Windows = cp936/GBK），
含中文的音频路径会当场变成乱码，最终表现为
「文件不存在，可能已被移动或删除」。

该故障的四个特性使它几乎不可能被自然测出：

1. **纯 ASCII 路径永远不触发** —— 开发机用 `test.wav`、`a` 这类路径，
   测一百遍都是绿的。
2. **只有真正读 stdin 的调用才触发** —— 拖入文件时的探测走的是另一条
   *已*配好 UTF-8 的通道（run_once），一切正常；一点「开始扒谱」走常驻
   sidecar（当时漏配），立刻炸。于是症状成了「拖入能过、一点扒谱就挂」。
3. **报错文案把人引向错误方向** —— 「文件不存在，可能已被移动或删除」
   听起来像用户把文件挪走了，而不是编码问题。
4. **只在中文/日文等非 ASCII 路径 + 中文 Windows 上复现** ——
   两者缺一不可，覆盖面极窄。

本项目真踩过：主上老公的机器上首首歌都失败，而换一首纯英文路径的音频就正常。

## 断言策略

不比对「成功与否」，而是断言**路径往返逐字相等** ——
这是编码正确性的精确判据：只要有一个字节被错误解码，字符串就必然不等。

并在**强制 GBK 的最恶劣环境**下重复一遍（模拟 stdin 编码被外部压成 GBK），
验证进程内的 reconfigure 兜底真的生效 —— 而不是只靠 Rust 侧的环境变量。

用法：python engine/tests/test_stdin_encoding.py
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = ROOT / ".venv" / "bin" / "python3"  # 非 Windows
BRIDGE = ROOT / "bridge.py"

PASS = 0
FAIL = 0


def make_wav(path: Path, seconds: float = 0.4) -> None:
    """生成一个够大的单声道 wav（validate_audio_file 要求 > 1024 字节）。"""
    rate = 44100
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        # 440Hz 正弦，振幅给足以免被判成静音
        frames = b"".join(
            struct.pack("<h", int(12000 * __import__("math").sin(2 * 3.14159265 * 440 * i / rate)))
            for i in range(n)
        )
        w.writeframes(frames)


def call_bridge(target: str, env_extra: dict) -> tuple[int, str, str]:
    """用 subprocess + PIPE 复刻 Rust 侧的调用形态（UTF-8 字节写入 stdin）。"""
    req = json.dumps(
        {"cmd": "probe", "id": "enc-1", "payload": {"path": target}},
        ensure_ascii=False,
    )
    env = dict(os.environ)
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        env.pop(k, None)
    env.update(env_extra)

    p = subprocess.run(
        [str(PY), str(BRIDGE)],
        input=req.encode("utf-8"),   # ← 与 Rust writeln! 的字节完全一致
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=180,
    )
    return (
        p.returncode,
        p.stdout.decode("utf-8", "replace"),
        p.stderr.decode("utf-8", "replace"),
    )


def extract_result(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            return obj.get("data")
        if obj.get("type") == "error":
            return {"_error": obj.get("message"), "_detail": obj.get("detail")}
    return None


def run_case(name: str, cases: list[tuple[str, dict]]) -> None:
    global PASS, FAIL
    print(f"\n── {name} ──")

    # 三种「非 ASCII 一应俱全」的文件名：中文、空格、全角括号、与号
    names = ["测试音频.wav", "测试 音频（括号）.wav", "日本語テスト&サンプル.wav"]

    tmp = tempfile.mkdtemp(prefix="bapu_enc_")
    target = Path(tmp) / "中文目录" / names[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    make_wav(target)
    print(f"  夹具：{target}")
    print(f"  存在：{target.is_file()}  大小：{target.stat().st_size} 字节")

    for label, env_extra in cases:
        rc, out, err = call_bridge(str(target), env_extra)
        data = extract_result(out)

        if data is None:
            print(f"  ✗ [{label}] 未取到 result（rc={rc}）")
            print(f"      stdout={out.strip()[:200]!r}")
            print(f"      stderr={err.strip()[:200]!r}")
            FAIL += 1
            continue

        if "_error" in data:
            print(f"  ✗ [{label}] 引擎报错：{data['_error']}")
            print(f"      detail={str(data.get('_detail'))[:160]}")
            FAIL += 1
            continue

        # 核心断言：路径逐字往返
        got_path = data.get("path")
        got_name = data.get("name")
        if got_path != str(target) or got_name != target.name:
            print(f"  ✗ [{label}] 路径往返不一致（编码被破坏）")
            print(f"      期望 path={str(target)!r}")
            print(f"      实际 path={got_path!r}")
            print(f"      期望 name={target.name!r}")
            print(f"      实际 name={got_name!r}")
            FAIL += 1
            continue

        print(f"  ✓ [{label}] 路径逐字一致，size={data.get('size')}")
        PASS += 1


def main() -> int:
    print("=" * 74)
    print("bridge stdin 非 ASCII 路径编码回归测试")
    print(f"解释器：{PY}")
    print("=" * 74)

    if not PY.is_file():
        print(f"✗ 找不到引擎解释器：{PY}")
        return 1
    if not BRIDGE.is_file():
        print(f"✗ 找不到 bridge.py：{BRIDGE}")
        return 1

    run_case(
        "默认环境（本机 locale，中文 Windows 即 cp936）",
        [("默认", {})],
    )
    run_case(
        "最恶劣环境：stdin 编码被外部压成 GBK / Latin-1",
        [
            ("PYTHONIOENCODING=gbk", {"PYTHONIOENCODING": "gbk"}),
            ("PYTHONIOENCODING=cp1252", {"PYTHONIOENCODING": "cp1252"}),
        ],
    )
    run_case(
        "Rust 侧实际下发的环境（双保险中的另一道）",
        [("PYTHONUTF8=1", {"PYTHONUTF8": "1"})],
    )

    print("\n" + "=" * 74)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
