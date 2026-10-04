"""进度单调性自测：跑若干模式，统计进度事件是否出现倒退。"""
import json
import os
import subprocess
import sys

# 本脚本位于 engine/tests/，故引擎根需上溯一级
ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ENGINE, ".venv", "Scripts", "python.exe")
BRIDGE = os.path.join(ENGINE, "bridge.py")
WAV = os.path.join(ENGINE, "..", "third_party", "AutoTranscriber", "test_audio", "chord_progression.wav")
PRESEP_V = os.path.join(ENGINE, "..", ".tmp", "presep", "my_vocals.wav")
PRESEP_A = os.path.join(ENGINE, "..", ".tmp", "presep", "my_instrumental.wav")
OUT = os.path.join(ENGINE, "..", ".tmp")

CASES = [
    ("full_auto", WAV, []),
    ("accompaniment", WAV, []),
    ("vocals", WAV, []),
    ("basic", WAV, []),
    ("basic_vocals", WAV, []),
    ("basic_accompaniment", WAV, []),
    ("basic_multi", WAV, []),
    # 多文件逐轨：三轨各占 1/3 进度轴，最容易出现「每轨各自从 0 起算」的倒退
    ("basic_multi", WAV, [WAV, WAV]),
    ("pre_separated", PRESEP_V, [PRESEP_A]),
]

total_bad = 0
for mode, inp, extras in CASES:
    payload = {
        "cmd": "transcribe",
        "id": "j",
        "payload": {
            "mode": mode,
            "input_path": os.path.abspath(inp),
            "output_path": os.path.abspath(os.path.join(OUT, f"mono_{mode}.mid")),
            "extra_inputs": [os.path.abspath(e) for e in extras],
        },
    }
    proc = subprocess.run(
        [PY, BRIDGE],
        input=json.dumps(payload) + "\n",
        capture_output=True,
        text=True,
        timeout=600,
    )

    last, bad, n, final, result_ok = -1.0, 0, 0, 0.0, False
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"  [非 JSON 污染] {line[:80]}")
            continue
        if obj.get("type") == "progress":
            pct = obj["pct"]
            n += 1
            if pct < last - 1e-9:
                bad += 1
            last = max(last, pct)
            final = pct
        elif obj.get("type") == "result":
            result_ok = True
        elif obj.get("type") == "error":
            print(f"  [ERROR] {obj.get('message')}")

    status = "OK " if (bad == 0 and result_ok and final >= 0.999) else "BAD"
    label = f"{mode}+{len(extras)}" if extras else mode
    print(
        f"{status} {label:21s} 事件 {n:3d} | 倒退 {bad} | 终值 {final:.3f} | 结果 {'有' if result_ok else '无'}"
    )
    total_bad += bad

print(f"\n总倒退次数：{total_bad}")
sys.exit(0 if total_bad == 0 else 1)
