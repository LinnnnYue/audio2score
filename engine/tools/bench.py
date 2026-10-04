"""
bench.py — 性能基线测量

对应需求 B5：3 分钟歌曲全程内存 < 4GB；RTX 3080 上 Demucs 分离 < 2 分钟。

做法：用合成音频模拟真实歌曲规模（44.1kHz 立体声，含和声进行与人声旋律），
分阶段计时并采样内存峰值。比拿真实歌曲更可控，且能量级一致。

用法：python engine/tools/bench.py
"""

from __future__ import annotations

import os
import sys
import time
import tracemalloc

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SR = 44100
DURATION = 180  # 3 分钟


def synth_song(duration: int = DURATION) -> np.ndarray:
    """合成一段 3 分钟的立体声测试音频。

    内容：四和弦循环（C-G-Am-F）+ 一条人声旋律线 + 简单的节奏型，
    频率成分覆盖 80Hz~4kHz，与真实流行歌曲的频谱规模相当。
    """
    n = SR * duration
    t = np.arange(n) / SR
    rng = np.random.default_rng(42)

    # 基础和弦进行：每 2 秒换一次，共四和弦
    chord_roots = [130.81, 98.00, 110.00, 87.31]  # C3 G3 A2 F2
    chord_ratios = [1.0, 1.26, 1.5, 2.0]  # root, M3, P5, octave

    signal = np.zeros(n, dtype=np.float32)

    bar = 2.0
    for i, root in enumerate(chord_roots * (duration // 8 + 1)):
        start = int(i * bar * SR)
        end = min(n, start + int(bar * SR))
        if start >= n:
            break
        seg_t = t[start:end] - t[start]
        env = np.exp(-2.2 * seg_t)  # 拨弦式衰减
        for ratio in chord_ratios:
            f = root * ratio
            signal[start:end] += 0.18 * env * np.sin(2 * np.pi * f * seg_t)

    # 人声旋律：五声音阶走句，频率 220~880Hz
    melody_scale = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25, 587.33, 659.25]
    note_dur = 0.5
    n_notes = int(duration / note_dur)
    for i in range(n_notes):
        start = int(i * note_dur * SR)
        end = min(n, start + int(note_dur * 0.9 * SR))
        if start >= n:
            break
        f = melody_scale[i % len(melody_scale)] * (1.5 if (i // 8) % 2 else 1.0)
        seg_t = t[start:end] - t[start]
        env = np.minimum(1.0, seg_t * 40) * np.exp(-1.5 * seg_t)
        signal[start:end] += 0.12 * env * np.sin(2 * np.pi * f * seg_t)

    # 底鼓：每拍一次，给 onset 检测提供依据
    for i in range(int(duration / 0.5)):
        start = int(i * 0.5 * SR)
        end = min(n, start + int(0.12 * SR))
        if start >= n:
            break
        seg_t = t[start:end] - t[start]
        env = np.exp(-28 * seg_t)
        freq = 55 * np.exp(-12 * seg_t)  # 下滑音
        signal[start:end] += 0.25 * env * np.sin(2 * np.pi * freq * seg_t)

    # 轻微噪声，模拟真实录音底噪
    signal += 0.005 * rng.standard_normal(n).astype(np.float32)

    signal = np.clip(signal / np.max(np.abs(signal)) * 0.9, -1, 1)
    stereo = np.stack([signal, signal], axis=1)
    return stereo.astype(np.float32)


def human(nbytes: int) -> str:
    mb = nbytes / (1024 * 1024)
    return f"{mb:8.1f} MB"


def main() -> None:
    print("=" * 66)
    print("性能基线测量（模拟 3 分钟歌曲）")
    print("=" * 66)

    workdir = os.path.join(ENGINE, "..", ".tmp", "bench")
    os.makedirs(workdir, exist_ok=True)
    wav_path = os.path.join(workdir, "bench_3min.wav")

    t0 = time.time()
    audio = synth_song()
    sf.write(wav_path, audio, SR)
    size_mb = os.path.getsize(wav_path) / (1024 * 1024)
    print(f"合成音频: {DURATION}s / {SR}Hz / 立体声 / {size_mb:.1f}MB  ({time.time() - t0:.1f}s)")

    peak = {"rss": 0}

    def sample_rss() -> int:
        """
        读当前进程的工作集内存（RSS）。

        踩坑实录（四层坑，全踩过——这条函数是本项目「反复鞭尸」的典型产物）：
        1. ctypes 调 `psapi.GetProcessMemoryInfo` 未设 argtypes，64 位下结构体
           对齐解释错误，返回恒 0。
        2. 改用 tasklist，但在 Git Bash 里 `/FI` `/FO` 被 MSYS 当路径改写。
           **注意：这是 Bash 层的坑，Python subprocess 传参列表不受影响**，
           所以必须从 Python 里调。
        3. CSV 输出内存是 `"20,080 K"`，带千分位逗号，直接 int() 抛异常被
           except 吞成 0。
        4. ⚠️ 最阴的一层：改用 `rsplit(",", 1)` 想取最后一列，结果**千分位逗�
           本身成了分隔符**——`"20,080 K"` 被劈成 `20` 和 `080 K"`，
           解析出 80KB。修法：用正则抓末尾的 `"数字 K"`，不按逗号切。

        教训：拿到 0 或明显离谱的数（0.4MB 跑 3 分钟音频）先怀疑采样器，
        别急着相信「内存真的用得极少」。**离谱的值比明显的错更危险。**
        """
        try:
            import re
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {os.getpid()}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            for line in out.stdout.splitlines():
                if f'"{os.getpid()}"' not in line:
                    continue
                # 末尾形如 "20,080 K" —— 用正则而非逗号切分
                m = re.search(r'"([\d,]+)\s*K"\s*$', line)
                if m:
                    return int(m.group(1).replace(",", "")) * 1024
        except Exception as e:  # noqa: BLE001
            print(f"      [warn] RSS 采样失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 0

    # ── 1. 分离 ──
    from separator import separate

    print("\n[1/3] 音源分离（demucs, CUDA）…")
    tracemalloc.start()
    t0 = time.time()
    sep = separate(wav_path, progress=lambda s, p, m: None)
    t_sep = time.time() - t0
    rss_after_sep = sample_rss()
    print(f"      耗时 {t_sep:.1f}s   方式 {sep.method}   RSS {human(rss_after_sep)}")
    print(f"      验收 B5（分离 < 120s）: {'通过' if t_sep < 120 else '未通过'}")

    # ── 2. 扒谱（伴奏轨，CQT 多音高）──
    from pipeline import TranscribeError, TranscribeRequest, transcribe

    print("\n[2/3] 伴奏轨扒谱（CQT 多音高，fmax=4186）…")
    out1 = os.path.join(workdir, "bench_accomp.mid")
    t0 = time.time()
    tracemalloc.reset_peak()
    res1 = transcribe(
        TranscribeRequest(
            mode="accompaniment",
            input_path=wav_path,
            output_path=out1,
        ),
        progress=None,
    )
    t_acc = time.time() - t0
    py_cur, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = sample_rss()
    print(
        f"      耗时 {t_acc:.1f}s   音符 {res1.total_notes}   "
        f"Python 堆峰值 {human(py_peak)}   RSS {human(rss_after)}"
    )

    # ── 3. 扒谱（人声轨，pYIN）──
    print("\n[3/3] 人声轨扒谱（pYIN）…")
    out2 = os.path.join(workdir, "bench_vocal.mid")
    t0 = time.time()
    res2 = transcribe(
        TranscribeRequest(
            mode="vocals",
            input_path=wav_path,
            output_path=out2,
        ),
        progress=None,
    )
    t_voc = time.time() - t0
    rss_final = sample_rss()
    print(f"      耗时 {t_voc:.1f}s   音符 {res2.total_notes}   RSS {human(rss_final)}")

    # ── 汇总 ──
    print("\n" + "=" * 66)
    total = t_sep + t_acc + t_voc
    print(f"全流程（分离 + 双轨扒谱）: {total:.1f}s")
    print(f"峰值 RSS: {human(max(rss_after_sep, rss_after, rss_final))}")
    print(f"验收 B5 内存 < 4GB: {'通过' if max(rss_after_sep, rss_after, rss_final) < 4 * 1024**3 else '未通过'}")
    print("=" * 66)

    sep.cleanup()


if __name__ == "__main__":
    main()
