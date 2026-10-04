"""
probe_single_track_modes — 单轨 / 多轨直扒路径的端到端实测探针

## 要回答的问题
主上把 Demucs 分好的人声单独丢进功能页 2，期望「直接出一轨，用于小提琴演奏」。
为此新增两个模式（`basic_vocals` / `basic_accompaniment`），并修掉
`basic_multi` 的多文件静默丢弃。本探针验证它们真的按预期工作：

    1. `basic_vocals`         → 输出**恰好 1 轨**，轨名 Voice，且**未触发分离**
    2. `basic_accompaniment`  → 输出**恰好 1 轨**，轨名 Accompaniment
    3. `basic_multi`（3 文件）→ 输出**恰好 3 轨**（修复前只会出 1 轨）
    4. 三个模式都不应出现 `separate` 阶段事件（它们都不该调动 Demucs）

## 为何合成音频
仓库内没有测试音频，而这四项断言只与「轨道数量与角色」有关，与音乐内容无关，
故用标准库 `wave` 合成 16bit PCM 信号即可，零额外依赖。

## 用法
    engine/.venv/Scripts/python.exe engine/tests/manual/probe_single_track_modes.py
"""

from __future__ import annotations

import math
import random
import struct
import sys
import tempfile
import wave
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

SAMPLE_RATE = 22050


def _write_wav(path: Path, freqs: list[float], seconds_per_note: float = 0.55) -> None:
    """把一串频率写成单声道 16bit PCM：每个音一个 ADSR 包络，末尾整体淡出。"""
    rng = random.Random(20261005)
    frames: list[int] = []
    per_note = int(SAMPLE_RATE * seconds_per_note)
    for freq in freqs:
        for i in range(per_note):
            t = i / SAMPLE_RATE
            # 简化 ADSR：起音 12ms 爬升、尾音 60ms 衰减，中间保持
            env = min(1.0, i / (SAMPLE_RATE * 0.012))
            remain = per_note - i
            env *= min(1.0, remain / (SAMPLE_RATE * 0.06))
            # 叠一点二次谐波，让它更像乐器而不是纯正弦（纯正弦容易过干净）
            v = 0.62 * math.sin(2 * math.pi * freq * t)
            v += 0.18 * math.sin(2 * math.pi * freq * 2 * t)
            v += rng.uniform(-0.015, 0.015)
            frames.append(max(-32767, min(32767, int(v * env * 26000))))

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames))


def _chord(freqs: list[float]) -> list[float]:
    """把和弦展开成「同时发声」的音符串（每个音连续重复，模拟持续和弦）。"""
    out: list[float] = []
    for f in freqs:
        out.extend([f] * 4)
    return out


def run() -> int:
    from pipeline import TranscribeError, TranscribeRequest, transcribe

    tmp = Path(tempfile.mkdtemp(prefix="bapu-single-track-"))
    melody = tmp / "melody.wav"       # 单声部旋律（人声 / 独奏小提琴）
    accomp = tmp / "accomp.wav"       # 多声部（伴奏 / 钢琴）
    extra1 = tmp / "extra1.wav"
    extra2 = tmp / "extra2.wav"

    # C4 E4 G4 C5 G4 E4 —— 单旋律
    _write_wav(melody, [261.63, 329.63, 392.00, 523.25, 392.00, 329.63])
    # C 大三和弦 + 低八度根音 —— 多音高
    _write_wav(accomp, _chord([130.81, 261.63, 329.63, 392.00]))
    _write_wav(extra1, [220.00, 277.18, 329.63, 440.00])
    _write_wav(extra2, [174.61, 220.00, 261.63, 349.23])

    cases: list[dict] = [
        {"name": "basic_vocals", "mode": "basic_vocals", "extras": [],
         "want_tracks": 1, "want_names": ["Voice"]},
        {"name": "basic_accompaniment", "mode": "basic_accompaniment", "extras": [],
         "want_tracks": 1, "want_names": ["Accompaniment"]},
        # 多轨：3 个文件 → 3 轨，且带上前端会传的逐轨编号（防止同名）
        {"name": "basic_multi×3", "mode": "basic_multi",
         "extras": [str(extra1), str(extra2)],
         "track_names": ["Instrument 1", "Instrument 2", "Instrument 3"],
         "want_tracks": 3,
         "want_names": ["Instrument 1", "Instrument 2", "Instrument 3"]},
        {"name": "basic（回归对照）", "mode": "basic", "extras": [],
         "want_tracks": 1, "want_names": ["Instrument"]},
    ]

    failures: list[str] = []
    for case in cases:
        name, mode = case["name"], case["mode"]
        extras = case["extras"]
        want_tracks, want_names = case["want_tracks"], case["want_names"]
        out = tmp / f"{mode}-{len(extras)}.mid"
        stages: list[str] = []

        def progress(stage: str, pct: float, msg: str) -> None:
            if not stages or stages[-1] != stage:
                stages.append(stage)

        try:
            res = transcribe(
                TranscribeRequest(
                    mode=mode, input_path=str(melody), output_path=str(out),
                    extra_inputs=extras, track_names=case.get("track_names", []),
                ),
                progress=progress,
            )
        except TranscribeError as e:
            print(f"  [FAIL] {name:22s} 抛异常: {e.user_message}")
            failures.append(f"{name}: {e.user_message}")
            continue

        got_tracks = len(res.tracks)
        got_names = [t["name"] for t in res.tracks]
        sep_touched = "separate" in stages
        total = res.total_notes

        ok = (
            got_tracks == want_tracks
            and got_names == want_names
            and not sep_touched
            and total > 0
        )
        tag = "PASS" if ok else "FAIL"
        print(
            f"  [{tag}] {name:22s} 轨数={got_tracks}/{want_tracks}  "
            f"轨名={got_names}  音符={total}  "
            f"走过阶段={','.join(stages)}  separationMethod={res.separation_method!r}"
        )
        if not ok:
            failures.append(
                f"{name}: 轨数 {got_tracks}≠{want_tracks} / 轨名 {got_names}≠{want_names}"
                f" / 分离={sep_touched} / 音符={total}"
            )
        # 修掉多轨静默丢弃的回归哨兵：文件数必须等于轨数
        if mode == "basic_multi" and got_tracks != 1 + len(extras):
            failures.append(
                f"{name}: 多文件被丢弃（输入 {1 + len(extras)} 个文件，只出 {got_tracks} 轨）"
            )

    print()
    if failures:
        print("结果：FAIL")
        for f in failures:
            print(f"  - {f}")
        # 保留 tmp 便于排查
        print(f"临时目录（保留）：{tmp}")
        return 1

    print(f"结果：PASS（全部 {len(cases)} 项）")
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
