# -*- coding: utf-8 -*-
"""探针 4：UTF-8 轨名字节级写入 + MuseScore 接受度 / 进度上报可行性"""
import sys, os, subprocess, tempfile, struct, time, re
ROOT = r"<REPO>"
UP = os.path.join(ROOT, "third_party", "AutoTranscriber")
sys.path.insert(0, UP)
import numpy as np, pretty_midi

def sec(t):
    print("\n" + "=" * 66); print("## " + t); print("=" * 66)

out = tempfile.mkdtemp(prefix="probe4_")
MS = r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"

def varlen(n):
    b = [n & 0x7F]; n >>= 7
    while n: b.append((n & 0x7F) | 0x80); n >>= 7
    return bytes(reversed(b))

def build_midi_bytes(tracks, tpq=220, tempo_bpm=120.0):
    """tracks: list of (name_bytes, program, [(start_s,end_s,pitch,vel)])"""
    tempo_us = int(round(60_000_000 / tempo_bpm))
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), tpq)
    body = b""
    for name_bytes, program, notes in tracks:
        tn = b"MTrk"
        ev = b""
        ev += varlen(0) + b"\xFF\x03" + varlen(len(name_bytes)) + name_bytes
        ev += varlen(0) + b"\xFF\x51\x03" + tempo_us.to_bytes(3, "big")
        ev += varlen(0) + b"\xC0" + bytes([program & 0x7F])
        ppq = lambda t: int(round(t * tpq * tempo_bpm / 60.0))
        for (s, e, p, v) in sorted(notes, key=lambda x: x[0]):
            ev += varlen(max(0, ppq(s) - ppq(0))) + b"\x90" + bytes([p & 0x7F, v])
            ev += varlen(max(1, ppq(e) - ppq(s))) + b"\x80" + bytes([p & 0x7F, 0])
        ev += varlen(0) + b"\xFF\x2F\x00"
        body += tn + struct.pack(">I", len(ev)) + ev
    return hdr + body

sec("X. UTF-8 轨名字节级 MIDI 写入 + MuseScore 4 接受度")
tests = {
    "ascii_lead": (b"Lead", 52, [(0.0, 1.0, 60, 80)]),
    "utf8_cjk":   ("人声".encode("utf-8"), 52, [(0.0, 1.0, 60, 80)]),
    "utf8_cjk2":  ("人声".encode("utf-8"), 0,  [(0.0, 1.0, 64, 80)]),
}
paths = {}
for label, (nb, prog, notes) in tests.items():
    p = os.path.join(out, f"bm_{label}.mid")
    with open(p, "wb") as f:
        f.write(build_midi_bytes([(nb, prog, notes)]))
    paths[label] = p
    print(f"  {label:12s} 写入 {os.path.getsize(p)}B")
    try:
        pm = pretty_midi.PrettyMIDI(p)
        print(f"               pretty_midi 回读 OK: name={pm.instruments[0].name!r} prog={pm.instruments[0].program} notes={len(pm.instruments[0].notes)}")
    except Exception as e:
        print(f"               pretty_midi 回读 FAIL {type(e).__name__}: {str(e)[:80]}")

sec("Y. MuseScore 4 对三种轨名的 PDF 转换")
for label, p in paths.items():
    pdf = os.path.join(out, f"bm_{label}.pdf")
    r = subprocess.run([MS, os.path.abspath(p), "-o", os.path.abspath(pdf)],
                       capture_output=True, text=True, timeout=120)
    ok = os.path.exists(pdf)
    print(f"  {label:12s} rc={r.returncode} pdf={'OK ' + str(os.path.getsize(pdf)) + 'B' if ok else 'FAIL'}")
    if not ok:
        print("       stderr:", (r.stderr or r.stdout)[:200])

sec("Z. MuseScore 4 的 -o 多格式能力（适配层导出选项探测）")
p = paths["utf8_cjk"]
for fmt in ("pdf", "png", "mxl", "musicxml", "svg", "mp3", "wav", "flac", "ogg"):
    o = os.path.join(out, f"out.{fmt}")
    try:
        r = subprocess.run([MS, os.path.abspath(p), "-o", os.path.abspath(o)],
                           capture_output=True, text=True, timeout=120)
        ok = os.path.exists(o)
        print(f"  -o .{fmt:9s} rc={r.returncode} {'OK ' + str(os.path.getsize(o)) + 'B' if ok else 'not produced'}")
    except Exception as e:
        print(f"  -o .{fmt:9s} EXC {type(e).__name__}: {str(e)[:70]}")

sec("AA. MuseScore 4 headless 启动耗时（适配层 UX 风险）")
t0 = time.time()
subprocess.run([MS, os.path.abspath(paths["ascii_lead"]),
                "-o", os.path.join(out, "t.pdf")], capture_output=True, timeout=120)
print(f"  冷启动一次转换耗时 {time.time()-t0:.2f}s（MuseScore 4 首次启动需初始化）")
t0 = time.time()
subprocess.run([MS, os.path.abspath(paths["ascii_lead"]),
                "-o", os.path.join(out, "t2.pdf")], capture_output=True, timeout=120)
print(f"  第二次 {time.time()-t0:.2f}s")

sec("AB. 进度上报可行性：CLI stdout 解析")
print("  上游 print 语句数统计（Grep 'print(' 精确计数）")
r = subprocess.run(["grep", "-rn", "--include=*.py", "-c", "print(",
                    UP], capture_output=True, text=True, encoding="utf-8", errors="replace")
print(r.stdout)
print("  main.py 可识别的阶段标记（print_progress L114-115 输出 '[HH:MM:SS] msg'）:")
print("    '阶段 1/3: 音源分离 (Demucs)...'   main.py:450")
print("    '阶段 2/3: 扒谱...'                main.py:464")
print("    '阶段 3/3: 生成 MIDI...'            main.py:488")
print("    '🧠 感知模式扒谱...'                main.py:521")
print("  >>> 只有 4 个粗粒度阶段，无百分比；CQT/音高估计内部零日志")
print("  >>> 且阶段 2/3 内含 2~3 轨串行处理，无法区分当前在扒哪一轨")

sec("AC. 直接 import 上游（绕过 CLI）做进度的可行性")
print("  上游所有核心函数都是无状态纯函数（除 separator/crepe/midi_to_pdf）")
print("  => 适配层可自己分步调用并自行计时上报进度，无需 stdout 解析")
print("  => 唯一无法细粒度的是 estimate_pitches 的帧循环（pitch_estimation.py:415 纯 for 循环）")
print("     单帧耗时 = 0.03s/117帧 ≈ 0.26ms（hop512），适配层可按帧数线性插值假进度")

sec("AD. 适配层「不改上游」能拿到的进度锚点")
cqt_frames = None
from AutoTranscriber import load_audio, preprocess, compute_cqt
y, sr = load_audio(os.path.join(UP, "test_audio", "four_chords.wav"), sr=22050)
y = preprocess(y, sr)
c, tm, fr = compute_cqt(y, sr, hop_length=512, bins_per_octave=36)
print(f"  适配层可自行测量：cqt.shape={c.shape} → 已知帧总数，可算 CQT 阶段真实百分比")
print(f"  onset 阶段为单次 scipy 调用（毫秒级），可视为瞬时")
print(f"  estimate_pitches 阶段：可用 cqt.shape[1] 做分母，线性插值")

print("\n@@PROBE4_DONE@@")
print("OUT:", out)
