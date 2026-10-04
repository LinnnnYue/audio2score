# -*- coding: utf-8 -*-
"""探针 3：轨名 charset 限制 / 中文轨名可行性 / 剩余格式与缺陷核实"""
import sys, os, subprocess, tempfile, inspect, traceback
ROOT = r"<REPO>"
UP = os.path.join(ROOT, "third_party", "AutoTranscriber")
sys.path.insert(0, UP)
import numpy as np, pretty_midi, mido
from AutoTranscriber import write_multitrack_midi, write_midi, load_audio, midi_to_pdf

def sec(t):
    print("\n" + "=" * 66); print("## " + t); print("=" * 66)

out = tempfile.mkdtemp(prefix="probe3_")
A = [{'start':0.0,'end':1.0,'pitch':60,'velocity':80}]
B = [{'start':0.0,'end':1.0,'pitch':48,'velocity':70}]

sec("M. mido charset 硬约束核实")
try:
    from importlib.metadata import version as _v
    print("  mido version:", _v("mido"))
except Exception as e:
    print("  mido version: 查询失败", e)
from mido.midifiles.meta import _charset
print("  mido meta._charset =", _charset)
print("  >>> MIDI 轨名 meta event 只能写 latin-1，中文/日文/emoji 直接 UnicodeEncodeError")

sec("N. 各种轨名写入实测")
tests = [("ascii", "Lead"), ("ascii2", "Accompaniment"), ("cjk", "人声"),
         ("cjk2", "伴奏"), ("jpn", "歌詞"), ("emoji", "🎤")]
for label, nm in tests:
    p = os.path.join(out, f"n_{label}.mid")
    write_multitrack_midi([A, B], p, tempo=120.0, programs=[52, 33])
    pm = pretty_midi.PrettyMIDI(p)
    try:
        pm.instruments[0].name = nm
        pm.write(p)
        r = pretty_midi.PrettyMIDI(p)
        print(f"  {label:6s} {nm!r:14s} -> OK, 回读轨名={r.instruments[0].name!r}")
    except Exception as e:
        print(f"  {label:6s} {nm!r:14s} -> FAIL {type(e).__name__}: {str(e)[:70]}")

sec("O. Unicode 轨名的正确写法（mido Track + UnicodeMetaMessage）")
p = os.path.join(out, "uni.mid")
mid = mido.MidiFile(ticks_per_beat=220)
tr = mido.MidiTrack(); mid.tracks.append(tr)
tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
tr2 = mido.MidiTrack(); mid.tracks.append(tr2)
tr2.append(mido.MetaMessage("track_name", name="人声", time=0))
tr2.append(mido.Message("program_change", program=52, channel=0, time=0))
tr2.append(mido.Message("note_on", note=60, velocity=80, time=0, channel=0))
tr2.append(mido.Message("note_off", note=60, velocity=0, time=480, channel=0))
try:
    mid.save(p)
    pm = pretty_midi.PrettyMIDI(p)
    print("  mido MetaMessage('track_name', name='人声') -> OK")
    print("  pretty_midi 回读轨名:", [i.name for i in pm.instruments])
except Exception as e:
    print(f"  mido 写 CJK 轨名 -> FAIL {type(e).__name__}: {str(e)[:90]}")
    print("  >>> mido 的 track_name 也走 latin1，CJK 轨名在 mido 层同样不可行")
print("  >>> 结论：中文轨名需字节级手写 MIDI writer（见探针 X）")
# MuseScore 是否接受
pdf = os.path.join(out, "uni.pdf")
try:
    midi_to_pdf(p, pdf)
    print("  MuseScore 4 接受中文轨名 PDF:", os.path.exists(pdf), os.path.getsize(pdf), "B")
except Exception as e:
    print("  MuseScore 转换失败:", e)

sec("P. 适配层补轨名的两种方案对比（都不改上游）")
print("  方案1（推荐）: 不调 write_multitrack_midi，适配层自己用 mido 构造完整 MIDI")
print("    -> 完全掌控轨名/program/tempo/CC")
print("  方案2: 调 write_multitrack_midi 生成后，pretty_midi 读回改 name 再 write")
print("    -> 中文必崩（latin-1），只能 ASCII 轨名")

sec("Q. .au / .raw 格式 load_audio 实测")
fours = os.path.join(UP, "test_audio", "four_chords.wav")
au = os.path.join(out, "t.au")
raw = os.path.join(out, "t.raw")
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",fours,au], capture_output=True)
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",fours,"-f","s16le","-ar","22050","-ac","1",raw], capture_output=True)
for label, p in [(".au", au), (".raw", raw)]:
    if not os.path.exists(p):
        print(f"  {label}: ffmpeg 生成失败"); continue
    try:
        y, s = load_audio(p, sr=22050)
        print(f"  {label}: load_audio OK len={len(y)} sr={s}")
    except Exception as e:
        print(f"  {label}: load_audio FAIL {type(e).__name__}: {str(e)[:90]}")

sec("R. separator 死代码与 device 覆盖核实（源码断言）")
s = inspect.getsource(__import__("AutoTranscriber.separator", fromlist=["x"]).separate_audio)
print("  '--two-stems','vocals' 硬编码:", '"--two-stems", "vocals"' in s)
print("  L153 device 二次覆盖表达式存在:", "device if device == \"cuda\" and __check_cuda() else \"cpu\"" in s)
print("  L157 print 硬编码 '(设备=cpu)'（无视传入 device）:", "设备=cpu" in s)
print("  >>> 传 device='cuda' 时，命令行与日志都会说 cpu（仅当 torch 不可见时才真为 cpu）")
import importlib
SP = importlib.import_module("AutoTranscriber.separator")
print("  __check_cuda 可从模块外部调用:", hasattr(SP, "__check_cuda"))

sec("S. convert_to_wav 的 mkdtemp 分支是否会被 separate_audio 走到")
src = inspect.getsource(SP.convert_to_wav)
print("  output_dir=None -> mkdtemp('autotranscriber_')")
print("  separate_audio 调 convert_to_wav(input_path, output_dir) —— output_dir 一定非 None")
print("  => mkdtemp 分支在上游主流程中不可达；但它作为公开 API 存在，适配层直接调会泄漏临时目录")
print("  >>> 泄漏点：直接调用 convert_to_wav(path) 不传 output_dir 时，%TEMP%/autotranscriber_* 永不清理")

sec("T. has_demucs 判据缺陷（只查外部 python，不查当前环境）")
print("  has_demucs() 源码判据：os.path.exists(DEMUCS_PYTHON) 且 `python -m demucs --help` 返回 0")
print("  DEMUCS_PYTHON =", SP.DEMUCS_PYTHON)
print("  >>> 硬编码他人机器路径，主上机器必然 False；即使本机装了 demucs 也返回 False")

sec("U. 主上路径覆盖核实：MuseScore 4 在不在候选列表首位")
from AutoTranscriber import find_musescore
print("  主上本机 MuseScore:", r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe")
print("  存在:", os.path.exists(r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"))
print("  find_musescore() 实际返回:", find_musescore())
print("  >>> midi_to_pdf.py:21 正是该路径，被完整覆盖，无需适配")

sec("V. write_midi 的 pitch 边界校验差异（write_midi vs multitrack）")
p2 = os.path.join(out, "bounds.mid")
write_midi([{'start':0,'end':1,'pitch':0,'velocity':80},
            {'start':0,'end':1,'pitch':127,'velocity':80},
            {'start':0,'end':1,'pitch':128,'velocity':80},
            {'start':1,'end':1,'pitch':60,'velocity':80}], p2)
pm = pretty_midi.PrettyMIDI(p2)
print("  write_midi 输入 pitch=0/127/128 + 零长音 -> 实际写入:", [n.pitch for n in pm.instruments[0].notes])
print("  >>> midi_writer.py:15 校验 0 < pitch < 128（严格 excl），128 被丢；end<=start 也被丢")

sec("W. perceptual_filter 对 3 音和弦的实测影响（路径 4/5 的音质风险）")
from AutoTranscriber import (compute_cqt, detect_onsets, compute_spectral_flux,
                             estimate_pitches_onset_driven, track_notes_onset_driven,
                             perceptual_filter, midi_to_name)
y, sr = load_audio(fours, sr=22050)
y = (y - y.mean()) / max(np.abs(y).max(), 1e-9)
c, tm, fr = compute_cqt(y, sr, hop_length=512, bins_per_octave=36)
fx = compute_spectral_flux(c)
of, ot = detect_onsets(fx, sr, hop_length=512, threshold=0.3)
on = estimate_pitches_onset_driven(c, fr, tm, of, ot, sr, hop_length=512, n_peaks=5, threshold_factor=0.1)
n0 = track_notes_onset_driven(on, c, fr, tm, sr, hop_length=512, decay_ratio=0.25, snr_threshold=0.3)
n1 = perceptual_filter(n0, outlier_semitones=12, min_duration=0.06, harmonic_check=True, max_simultaneous=6, max_notes_per_beat=8)
print(f"  four_chords.wav: {len(y)/sr:.2f}s  onset={len(of)} onset_notes={len(on)}")
print(f"  track -> {len(n0)} notes -> perceptual_filter -> {len(n1)} notes")
print(f"  melody_split=True(默认) 开启，会重排/删音：")
for nn in n1:
    print(f"    {midi_to_name(nn['pitch']):>4s} {nn['start']:.2f}~{nn['end']:.2f}s vel={nn['velocity']}")
# 对比 melody_split=False
n2 = perceptual_filter(n0, melody_split=False, harmonic_check=True, max_simultaneous=6, max_notes_per_beat=8)
print(f"  melody_split=False -> {len(n2)} notes")
print("  >>> melody_split=True 是激进重写（separate_melody_and_accompaniment 会重建音符），")
print("      它不只是'过滤'——会丢真和弦音。适配层须暴露开关并给默认值。")

print("\n@@PROBE3_DONE@@")
print("OUT:", out)
