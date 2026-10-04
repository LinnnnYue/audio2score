# -*- coding: utf-8 -*-
"""上游契约实测探针（只读 third_party，不修改）"""
import sys, os, io, json, time, traceback

ROOT = r"<REPO>"
UP = os.path.join(ROOT, "third_party", "AutoTranscriber")
sys.path.insert(0, UP)

import numpy as np
import tempfile

def sec(t):
    print("\n" + "=" * 70)
    print("## " + t)
    print("=" * 70)

# ---------------- 0. 环境与版本 ----------------
sec("0. 环境版本")
import librosa, scipy, soundfile, pretty_midi
print("librosa", librosa.__version__)
print("scipy  ", scipy.__version__)
print("soundfile", soundfile.__version__)
print("pretty_midi", pretty_midi.__version__)
print("numpy  ", np.__version__)
try:
    import av; print("av      ", av.__version__)
except Exception as e:
    print("av FAIL", e)

# ---------------- 1. import 上游包 ----------------
sec("1. import AutoTranscriber")
try:
    import AutoTranscriber as AT
    print("import OK, __version__ =", AT.__version__)
except Exception:
    traceback.print_exc()
    sys.exit(1)

# ---------------- 2. 函数签名（inspect 实证） ----------------
sec("2. 函数签名 inspect.signature 实证")
import inspect
from AutoTranscriber import (load_audio, preprocess, compute_cqt,
                             compute_spectral_flux, detect_onsets,
                             estimate_pitches, estimate_pitches_onset_driven,
                             estimate_vocal_pitch, track_notes,
                             track_notes_onset_driven, perceptual_filter,
                             write_midi, write_multitrack_midi,
                             merge_tracks_to_piano, separate_audio,
                             has_demucs, midi_to_pdf, find_musescore,
                             estimate_vocal_pitch_crepe, has_crepe)
targets = [load_audio, preprocess, compute_cqt, compute_spectral_flux,
           detect_onsets, estimate_pitches, estimate_pitches_onset_driven,
           estimate_vocal_pitch, track_notes, track_notes_onset_driven,
           perceptual_filter, write_midi, write_multitrack_midi,
           merge_tracks_to_piano, separate_audio, has_demucs,
           estimate_vocal_pitch_crepe, has_crepe, midi_to_pdf, find_musescore]
for fn in targets:
    try:
        print(f"{fn.__module__.split('.')[-1]}.{fn.__name__}{inspect.signature(fn)}")
    except Exception as e:
        print(fn.__name__, "SIG FAIL", e)

# ---------------- 3. 能力探测 ----------------
sec("3. has_demucs() / has_crepe() / find_musescore()")
print("has_demucs()      =", has_demucs())
print("has_crepe()       =", has_crepe())
ms = find_musescore()
print("find_musescore()  =", ms)
print("musescore exists  =", bool(ms and os.path.exists(ms)))

# ---------------- 4. load_audio 格式矩阵 ----------------
sec("4. load_audio 音频格式支持矩阵（ffmpeg 生成样本，逐个实测）")
import subprocess
tmpdir = tempfile.mkdtemp(prefix="probe_fmt_")
src = os.path.join(UP, "test_audio", "chord_progression.wav")
print("源文件:", src, os.path.getsize(src), "bytes")

cases = [
    ("wav",    ["-i", src]),
    ("mp3",    ["-i", src, "-codec:a", "libmp3lame", "-b:a", "192k", f"{tmpdir}/t.mp3"]),
    ("m4a",    ["-i", src, "-codec:a", "aac", "-b:a", "192k", f"{tmpdir}/t.m4a"]),
    ("aac",    ["-i", src, "-codec:a", "aac", f"{tmpdir}/t.aac"]),
    ("ogg",    ["-i", src, "-codec:a", "libvorbis", f"{tmpdir}/t.ogg"]),
    ("opus",   ["-i", src, "-codec:a", "libopus", f"{tmpdir}/t.opus"]),
    ("wma",    ["-i", src, "-codec:a", "wmav2", f"{tmpdir}/t.wma"]),
    ("aiff",   ["-i", src, f"{tmpdir}/t.aiff"]),
    ("flac",   ["-i", src, f"{tmpdir}/t.flac"]),
    ("mp4",    ["-i", src, "-codec:a", "aac", f"{tmpdir}/t.mp4"]),
    ("webm",   ["-i", src, "-codec:a", "libopus", f"{tmpdir}/t.webm"]),
]
made = []
for name, args in cases:
    out = args[-1]
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args,
                       capture_output=True, text=True)
    ok = r.returncode == 0 and os.path.exists(out)
    made.append((name, out if ok else None))
    print(f"  ffmpeg 生成 {name:5s} -> {'OK' if ok else 'FAIL ' + r.stderr[:120]}")

print("\n--- load_audio() 读取实测 ---")
fmt_result = {}
for name, path in made:
    if not path:
        fmt_result[name] = "N/A(未生成)"
        continue
    try:
        y, sr = load_audio(path, sr=22050)
        info = f"OK len={len(y)} sr={sr} dur={len(y)/sr:.2f}s peak={np.abs(y).max():.4f}"
        fmt_result[name] = "可读"
    except Exception as e:
        info = f"FAIL {type(e).__name__}: {str(e)[:150]}"
        fmt_result[name] = "不可读"
    print(f"  {name:5s}: {info}")

print("\n--- soundfile 直读对照（判断失败归属 librosa 还是底层解码器） ---")
import soundfile as sf
for name, path in made:
    if not path: continue
    try:
        d, s = sf.read(path)
        print(f"  {name:5s}: soundfile OK  sr={s} shape={d.shape}")
    except Exception as e:
        print(f"  {name:5s}: soundfile FAIL {type(e).__name__}: {str(e)[:90]}")

print("\n--- av(PyAV) 直读对照 ---")
import av
for name, path in made:
    if not path: continue
    try:
        c = av.open(path)
        st = c.streams.audio[0]
        print(f"  {name:5s}: av OK  codec={st.codec_context.name} sr={st.codec_context.sample_rate} layout={st.codec_context.layout.name}")
        c.close()
    except Exception as e:
        print(f"  {name:5s}: av FAIL {type(e).__name__}: {str(e)[:90]}")

print("\n--- librosa 使用的后端链 ---")
try:
    print("soundfile 后端可用:", sf.__libsndfile_version__)
except Exception as e:
    print("sf version query fail", e)
try:
    import audioread
    print("audioread 已安装:", audioread.__version__)
except ImportError:
    print("audioread: 未安装（librosa 的 audioread 回退路径不可用）")

# ---------------- 5. 端到端 note 字段 + write_midi ----------------
sec("5. 端到端 transcribe + note 字段 + write_midi 产物")
wav = os.path.join(UP, "test_audio", "chord_progression.wav")
outdir = tempfile.mkdtemp(prefix="probe_mid_")
mid_path = os.path.join(outdir, "probe.mid")

y, sr = load_audio(wav, sr=22050)
y = preprocess(y, sr)
print(f"音频: {len(y)/sr:.2f}s @ {sr}Hz")

t0 = time.time()
cqt, times, freqs = compute_cqt(y, sr, hop_length=512, fmin=65.41,
                                 fmax=2093.0, bins_per_octave=36)
t_cqt = time.time() - t0
print(f"compute_cqt: {t_cqt:.2f}s  cqt.shape={cqt.shape} "
      f"n_bins={cqt.shape[0]} n_frames={cqt.shape[1]} "
      f"dtype={cqt.dtype} cqt.nbytes/1e6={cqt.nbytes/1e6:.1f}MB")
print(f"  freqs[0]={freqs[0]:.2f} freqs[-1]={freqs[-1]:.2f} "
      f"times[-1]={times[-1]:.2f}")

t0 = time.time()
flux = compute_spectral_flux(cqt)
print(f"compute_spectral_flux: {time.time()-t0:.3f}s shape={flux.shape}")

t0 = time.time()
onset_frames, onset_times = detect_onsets(flux, sr, hop_length=512, threshold=0.3)
print(f"detect_onsets: {time.time()-t0:.2f}s -> {len(onset_frames)} onsets")

t0 = time.time()
frame_notes = estimate_pitches(cqt, freqs, times, sr, hop_length=512,
                               n_peaks=5, threshold_factor=0.1)
print(f"estimate_pitches: {time.time()-t0:.2f}s -> {len(frame_notes)} frames")
if frame_notes and frame_notes[0]:
    print("  逐帧 note 字段:", sorted(frame_notes[0][0].keys()))
    print("  样例:", frame_notes[0][0])

t0 = time.time()
onset_notes = estimate_pitches_onset_driven(cqt, freqs, times, onset_frames,
                                            onset_times, sr, hop_length=512,
                                            n_peaks=5, threshold_factor=0.1)
print(f"estimate_pitches_onset_driven: {time.time()-t0:.2f}s -> {len(onset_notes)} onset notes")
if onset_notes:
    print("  onset note 字段:", sorted(onset_notes[0].keys()))
    print("  样例:", onset_notes[0])

t0 = time.time()
notes = track_notes_onset_driven(onset_notes, cqt, freqs, times, sr,
                                 hop_length=512, decay_ratio=0.25,
                                 snr_threshold=0.3)
print(f"track_notes_onset_driven: {time.time()-t0:.2f}s -> {len(notes)} notes")
if notes:
    print("  最终 note 字段:", sorted(notes[0].keys()))
    print("  样例:", notes[0])

t0 = time.time()
pf = perceptual_filter(notes, outlier_semitones=12, min_duration=0.06,
                       harmonic_check=True, max_simultaneous=6,
                       max_notes_per_beat=8)
print(f"perceptual_filter: {time.time()-t0:.2f}s -> {len(pf)} notes")
if pf:
    print("  滤波后字段:", sorted(pf[0].keys()))

t0 = time.time()
write_midi(pf, mid_path, tempo=120.0, program=0)
print(f"write_midi: {time.time()-t0:.3f}s -> {mid_path}")

# ---------------- 6. pretty_midi 回读 ----------------
sec("6. pretty_midi 回读 write_midi 产物")
pm = pretty_midi.PrettyMIDI(mid_path)
print(f"instruments 数 = {len(pm.instruments)}")
print(f"tempo = {pm.estimate_tempo()}")
print(f"get_tempo_changes = {pm.get_tempo_changes()}")
print(f"秒数 = {pm.get_end_time()}  resolution = {pm.resolution}")
for i, ins in enumerate(pm.instruments):
    print(f"  track[{i}] name={ins.name!r} program={ins.program} "
          f"is_drum={ins.is_drum} notes={len(ins.notes)}")
    if ins.notes:
        ps = [n.pitch for n in ins.notes]
        vs = [n.velocity for n in ins.notes]
        print(f"    pitch range = {min(ps)}..{max(ps)}, "
              f"velocity range = {min(vs)}..{max(vs)}")
        print(f"    前3个音符: {[(round(n.start,3), round(n.end,3), n.pitch, n.velocity) for n in ins.notes[:3]]}")

# ---------------- 7. 多轨 MIDI 轨名 ----------------
sec("7. write_multitrack_midi 轨名 / program")
mt_path = os.path.join(outdir, "probe_mt.mid")
write_multitrack_midi([pf, notes[:20] if notes else []], mt_path,
                      tempo=100.0, programs=[0, 1])
pm2 = pretty_midi.PrettyMIDI(mt_path)
print(f"tempo = {pm2.estimate_tempo()}, instruments = {len(pm2.instruments)}")
for i, ins in enumerate(pm2.instruments):
    print(f"  track[{i}] name={ins.name!r} program={ins.program} notes={len(ins.notes)}")
print(">>> 结论：write_multitrack_midi 无 track_names 参数，轨名由 pretty_midi 默认决定")

# ---------------- 8. merge_tracks_to_piano ----------------
sec("8. merge_tracks_to_piano 输入契约（是否需要 ttype）")
try:
    m = merge_tracks_to_piano([(pf, 'vocal'), (notes[:30], 'accompaniment')])
    print(f"OK -> {len(m)} notes, 字段 {sorted(m[0].keys()) if m else 'n/a'}")
    print("  velocity 固定值:", sorted({n['velocity'] for n in m}) if m else 'n/a')
except Exception:
    traceback.print_exc()
try:
    merge_tracks_to_piano([(pf,)])   # 故意不传 ttype
    print("单元素 tuple 无 ttype 也 OK")
except Exception as e:
    print("单元素 tuple 缺 ttuple ->", type(e).__name__, str(e)[:120])

# ---------------- 9. estimate_vocal_pitch（pYIN） ----------------
sec("9. estimate_vocal_pitch (pYIN) 实测")
t0 = time.time()
vn = estimate_vocal_pitch(y, sr, hop_length=512, min_duration_frames=3)
print(f"耗时 {time.time()-t0:.2f}s -> {len(vn)} notes")
if vn:
    print("  字段:", sorted(vn[0].keys()), "样例:", vn[0])
    print("  velocity 范围:", min(n['velocity'] for n in vn), max(n['velocity'] for n in vn))

# ---------------- 10. CQT 性能与内存外推 ----------------
sec("10. CQT 性能外推（bins_per_octave=36, hop=512 与 256）")
for hop in (512, 256):
    t0 = time.time()
    c2, tm2, fr2 = compute_cqt(y, sr, hop_length=hop, fmin=65.41,
                               fmax=2093.0, bins_per_octave=36)
    dt = time.time() - t0
    nbin = c2.shape[0]
    nfr = c2.shape[1]
    mb = c2.nbytes / 1e6
    scale = 180.0 / (len(y) / sr)   # 外推到 3 分钟
    print(f"  hop={hop}: {dt:.2f}s cqt={c2.shape} {mb:.1f}MB "
          f"| 外推180s: CQT≈{dt*scale:.1f}s, 内存≈{mb*scale:.0f}MB")

t0 = time.time()
fl_n = estimate_pitches(cqt, freqs, times, sr, hop_length=512,
                        n_peaks=5, threshold_factor=0.1)
print(f"  estimate_pitches(hop512) {time.time()-t0:.2f}s -> "
      f"外推180s ≈ {(time.time()-t0)*180/(len(y)/sr):.0f}s")

t0 = time.time()
vn2 = estimate_vocal_pitch(y, sr, hop_length=512)
print(f"  estimate_vocal_pitch(pyin) {time.time()-t0:.2f}s -> "
      f"外推180s ≈ {(time.time()-t0)*180/(len(y)/sr):.0f}s")

# ---------------- 11. sr 砍高频验证 ----------------
sec("11. sr=22050 对高音的 Nyquist 影响")
print(f"  sr=22050 -> Nyquist = {22050/2} Hz")
print(f"  CQT fmax=2093 Hz，占 Nyquist 的 {2093/11025*100:.1f}%")
print(f"  sr=44100 -> Nyquist = {44100/2} Hz, fmax 占比 {2093/22050*100:.1f}%")
midi_of_2093 = 12 * np.log2(2093/440) + 69
midi_of_11025 = 12 * np.log2(11025/440) + 69
print(f"  fmax=2093Hz 对应 MIDI {midi_of_2093:.1f} "
      f"({['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][int(midi_of_2093)%12]}{int(midi_of_2093)//12-1})")
print(f"  Nyquist=11025Hz 对应 MIDI {midi_of_11025:.1f}（理论可测上限）")

# 高音验证：合成 C6(1046Hz) + A5(880Hz)，看能否检出
tt = np.linspace(0, 2.0, int(22050*2.0), endpoint=False)
hsig = (np.sin(2*np.pi*1046.50*tt) * 0.6 + np.sin(2*np.pi*880.0*tt) * 0.5
        + 0.3*np.sin(2*np.pi*2093.0*tt))
hsig /= np.max(np.abs(hsig))
c3, t3, f3 = compute_cqt(hsig, 22050, hop_length=512, bins_per_octave=36)
fn3 = estimate_pitches(c3, f3, t3, 22050, hop_length=512, n_peaks=5,
                       threshold_factor=0.1)
mid = fn3[len(fn3)//2]
names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
print("  输入 C6(1046.5)+A5(880)+C7(2093) → 检出:",
      [f"{names[n['pitch']%12]}{n['pitch']//12-1}({n['frequency']:.0f}Hz)"
       for n in mid])

# ---------------- 12. n_peaks 与和弦识别 ----------------
sec("12. n_peaks 扫描（和弦识别上限）")
fours = os.path.join(UP, "test_audio", "four_chords.wav")
y4, sr4 = load_audio(fours, sr=22050)
y4 = preprocess(y4, sr4)
c4, t4, fr4 = compute_cqt(y4, sr4, hop_length=512, bins_per_octave=36)
print(f"  four_chords.wav: {len(y4)/sr4:.2f}s, cqt={c4.shape}")
for npk in (2, 3, 4, 5, 6, 8, 10, 12):
    fr = estimate_pitches(c4, fr4, t4, sr4, hop_length=512, n_peaks=npk,
                          threshold_factor=0.1)
    counts = [len(x) for x in fr if x]
    print(f"  n_peaks={npk:2d} -> 非空帧 {len(counts)}, "
          f"平均同时音数 {np.mean(counts) if counts else 0:.2f}, "
          f"最大 {max(counts) if counts else 0}")

# ---------------- 13. separate_audio 实测 ----------------
sec("13. separate_audio() 真实行为（demucs 未装 → 观察 HPSS 回退）")
clip = os.path.join(outdir, "clip10s.wav")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", fours,
                "-t", "10", clip], check=True)
print(f"  10 秒样本: {clip} ({os.path.getsize(clip)} bytes)")
sep_out = os.path.join(outdir, "sep")
try:
    paths = separate_audio(clip, output_dir=sep_out)
    print("  返回 dict 键:", sorted(paths.keys()))
    for k, v in paths.items():
        print(f"    {k:10s} = {v}  exists={os.path.exists(v) if v else None}")
    print("  >>> 产物是否落在 output_dir 内:",
          all(os.path.abspath(v).startswith(os.path.abspath(sep_out))
              for v in paths.values() if v))
except Exception:
    traceback.print_exc()
print("  output_dir 树:")
for r, d, f in os.walk(sep_out):
    for ff in f:
        fp = os.path.join(r, ff)
        print(f"    {os.path.relpath(fp, sep_out)}  {os.path.getsize(fp)}B")

# ---------------- 14. midi_to_pdf ----------------
sec("14. midi_to_pdf 实测（MuseScore 4）")
pdf_path = os.path.join(outdir, "probe.pdf")
t0 = time.time()
p = midi_to_pdf(mid_path, pdf_path)
print(f"  耗时 {time.time()-t0:.2f}s -> {p} exists={os.path.exists(p)} "
      f"size={os.path.getsize(p) if os.path.exists(p) else 0}B")
with open(p, "rb") as f:
    head = f.read(8)
print("  文件头:", head)

# ---------------- 15. crepe_wrapper 静态判定 ----------------
sec("15. crepe_wrapper 环境探测")
from AutoTranscriber import crepe_wrapper as CW
print("  BASE_DIR     =", CW.BASE_DIR)
print("  CREPE_PYTHON =", CW.CREPE_PYTHON)
print("  存在?        =", os.path.exists(CW.CREPE_PYTHON))
print("  CREPE_SCRIPT =", CW.CREPE_SCRIPT, "存在?", os.path.exists(CW.CREPE_SCRIPT))

sec("16. separator 环境探测")
from AutoTranscriber import separator as SP
print("  DEMUCS_PYTHON =", SP.DEMUCS_PYTHON)
print("  存在?          =", os.path.exists(SP.DEMUCS_PYTHON))
print("  SUPPORTED_FORMATS =", sorted(SP.SUPPORTED_FORMATS))
print("  needs_conversion(mp3) =", SP.needs_conversion("x.mp3"))
print("  needs_conversion(wav) =", SP.needs_conversion("x.wav"))
print("  needs_conversion(flac)=", SP.needs_conversion("x.flac"))

# ---------------- 17. 进度/回调机制 ----------------
sec("17. 进度/日志机制 grep")
import subprocess as sp2
r = sp2.run(["grep", "-rn", "-E", "callback|progress|on_progress|yield|logging",
             UP], capture_output=True, text=True, encoding="utf-8", errors="replace")
print(r.stdout[:2000] or "  (无匹配)")
r = sp2.run(["grep", "-rn", "-E", "^\\s*print\\(", UP],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
lines = [l for l in r.stdout.splitlines() if l.strip()]
print(f"  print() 语句总数 = {len(lines)}")
import collections
c = collections.Counter(l.split(":")[0].replace("\\","/").split("/")[-1] for l in lines)
print("  按文件分布:", dict(c))

print("\n\n@@PROBE_DONE@@")
print("TMPDIRS:", tmpdir, outdir)
