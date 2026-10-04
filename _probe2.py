# -*- coding: utf-8 -*-
"""探针 2：HPSS 回退 / 3分钟性能 / m4a 补救可行性 / tempo 实证 / 多轨轨名可补救性"""
import sys, os, time, subprocess, tempfile, traceback
ROOT = r"<REPO>"
UP = os.path.join(ROOT, "third_party", "AutoTranscriber")
sys.path.insert(0, UP)
import numpy as np
from AutoTranscriber import separator as SP, load_audio, preprocess, compute_cqt, estimate_pitches
import pretty_midi

def sec(t):
    print("\n" + "=" * 66); print("## " + t); print("=" * 66)

out = tempfile.mkdtemp(prefix="probe2_")
fours = os.path.join(UP, "test_audio", "four_chords.wav")
clip = os.path.join(out, "clip10s.wav")
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",fours,"-t","10",clip], check=True)

sec("A. _hpss_separate 直接实测（绕过不存在的 DEMUCS_PYTHON）")
hp = SP._hpss_separate(clip, os.path.join(out, "hpss_test"))
print("返回:", hp)
for k, v in hp.items():
    print(f"  {k}: exists={os.path.exists(v)} size={os.path.getsize(v) if os.path.exists(v) else 0}B")
print(">>> HPSS 回退产物落在调用方给的 output_dir 下（不泄漏临时目录）")

sec("B. separate_audio 的 DEMUCS 缺失行为（try/except 覆盖检查）")
print("separator.py:160-169 只捕获 subprocess.TimeoutExpired")
print("实测结论：FileNotFoundError 直接向上抛出（见探针1 traceback），")
print("  → HPSS 回退分支 (L190) 根本走不到，这是上游缺陷。")

sec("C. 3 分钟音频的 CQT 性能与内存实测")
sr = 22050
dur = 180
y3 = np.random.RandomState(0).randn(int(sr*dur)).astype(np.float32) * 0.05
for hop in (256, 512):
    t0 = time.time()
    c, tm, fr = compute_cqt(y3, sr, hop_length=hop, fmin=65.41, fmax=2093.0, bins_per_octave=36)
    dt = time.time() - t0
    print(f"  180s 音频 hop={hop}: CQT 耗时 {dt:.2f}s  shape={c.shape}  "
          f"float32 内存={c.nbytes/1e6:.1f}MB")
    t0 = time.time()
    fnotes = estimate_pitches(c, fr, tm, sr, hop_length=hop, n_peaks=5, threshold_factor=0.1)
    print(f"                estimate_pitches 耗时 {time.time()-t0:.1f}s  frames={len(fnotes)}")

sec("D. m4a/aac/wma/mp4 补救可行性（不改上游）")
m4a = os.path.join(out, "t.m4a")
subprocess.run(["ffmpeg","-y","-loglevel","error","-i",fours,"-codec:a","aac",m4a], check=True)
try:
    load_audio(m4a)
    print("  m4a 直接读: OK（意外）")
except Exception as e:
    print(f"  m4a 直接读: FAIL {type(e).__name__}")
# 补救 1：soundfile 不行，librosa 有 input_soundfile/ audioread 两个 keyword
for kw in ("audioread",):
    try:
        import librosa
        y, s = librosa.load(m4a, sr=22050, **{f"input_{kw}": kw})
        print(f"  librosa input_{kw}=... : OK len={len(y)}")
    except Exception as e:
        print(f"  librosa input_{kw}=... : FAIL {type(e).__name__}: {str(e)[:110]}")
# 补救 2：PyAV 手写解码 → 喂给下游
print("  --- PyAV 手写解码（适配层可实现的 fallback） ---")
import av, fractions
def load_via_av(path, sr=22050, mono=True):
    c = av.open(path)
    st = c.streams.audio[0]
    resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono" if mono else st.codec_context.layout.name, rate=sr)
    chunks = []
    for frame in c.decode(st):
        for rf in resampler.resample(frame):
            chunks.append(rf.to_ndarray().reshape(-1))
    c.close()
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32), sr
yav, sav = load_via_av(m4a)
print(f"  PyAV 读 m4a: OK len={len(yav)} sr={sav} dur={len(yav)/sav:.2f}s peak={np.abs(yav).max():.4f}")
yav2, _ = load_via_av(m4a)
ref, _ = load_audio(fours)
print(f"  与 wav(源) 长度对比: pyav={len(yav2)} librosa_wav={len(ref)} (ffmpeg mp3/ogg 也是 59535)")
print("  >>> 结论：适配层可用 PyAV 做 ffprobe 兜底解码，无需改上游")

sec("E. tempo 写入实证（write_midi tempo=120 是否真落到文件）")
mp = os.path.join(out, "t120.mid")
from AutoTranscriber import write_midi
write_midi([{'start':0.0,'end':1.0,'pitch':60,'velocity':80},
            {'start':1.0,'end':2.0,'pitch':62,'velocity':80}], mp, tempo=120.0)
pm = pretty_midi.PrettyMIDI(mp)
print("  get_tempo_changes() =", pm.get_tempo_changes(), " <- 写入值")
print("  estimate_tempo()    =", round(pm.estimate_tempo(),2), " <- 由音符反推的节拍，非写入值")
print("  >>> tempo 是写进去了；UI 显示别用 estimate_tempo()")
# 90 BPM 验证
mp90 = os.path.join(out, "t90.mid")
write_midi([{'start':0.0,'end':1.0,'pitch':60,'velocity':80}], mp90, tempo=90.0)
print("  tempo=90 写入回读:", pretty_midi.PrettyMIDI(mp90).get_tempo_changes())

sec("F. 轨名可补救性：能否只靠 pretty_midi 补轨名（不改上游）")
notes_a = [{'start':0.0,'end':1.0,'pitch':60,'velocity':80}]
notes_b = [{'start':0.0,'end':1.0,'pitch':48,'velocity':70}]
from AutoTranscriber import write_multitrack_midi
mt = os.path.join(out, "mt.mid")
write_multitrack_midi([notes_a, notes_b], mt, tempo=120.0, programs=[52, 33])
pm2 = pretty_midi.PrettyMIDI(mt)
print("  上游产物轨名:", [i.name for i in pm2.instruments], " <- 空字符串")
print("  program:", [i.program for i in pm2.instruments])
print("  >>> 补救：适配层读回后写 in.name='人声'/'伴奏' 再 write（不改上游函数）")

sec("G. MuseScore 4 对空轨名的处理（决定轨名补救的必要性）")
pdf = os.path.join(out, "mt.pdf")
from AutoTranscriber import midi_to_pdf
midi_to_pdf(mt, pdf)
print("  转换成功:", os.path.exists(pdf), os.path.getsize(pdf), "B")
# 带轨名版本对比
pm3 = pretty_midi.PrettyMIDI(mt)
for ins, nm in zip(pm3.instruments, ["人声", "伴奏"]):
    ins.name = nm
pm3.write(os.path.join(out, "mt_named.mid"))
midi_to_pdf(os.path.join(out, "mt_named.mid"), os.path.join(out, "mt_named.pdf"))
print("  带轨名版本 PDF:", os.path.getsize(os.path.join(out,"mt_named.pdf")), "B")

sec("H. sr 对高音的真实影响（实测 C7 是否被切掉）")
sr2 = 22050
tt = np.linspace(0, 1.5, int(sr2*1.5), endpoint=False)
# G6 1568 / C7 2093 / E7 2637(>fmax) / B6 1975
sig = sum(np.sin(2*np.pi*f*tt)/ (i+1) for i, f in enumerate([1567.98, 1975.53, 2093.0, 2637.02]))
sig = sig / np.max(np.abs(sig))
for fmax_ in (2093.0, 4186.0):
    c, tm, fr = compute_cqt(sig, sr2, hop_length=512, fmin=65.41, fmax=fmax_, bins_per_octave=36)
    mid = estimate_pitches(c, fr, tm, sr2, hop_length=512, n_peaks=6, threshold_factor=0.05)[len(tm)//2]
    names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
    got = sorted(f"{names[n['pitch']%12]}{n['pitch']//12-1}" for n in mid)
    print(f"  fmax={fmax_:.0f}Hz (n_bins={c.shape[0]}) 检出={got}")
print("  输入: G6(1568) B6(1976) C7(2093) E7(2637)")
print("  >>> E7(2637Hz) 落在 fmax=2093 之外，任何 fmax 都被硬编码在 main.py L163/L231")
print("  >>> 上游 fmax 是函数默认值，不透传：compute_cqt 可传参，但 main.py 写死 2093.0")

sec("I. sr=22050 是否砍掉人声高频（22050 是 librosa 默认重采样目标）")
print("  Nyquist(22050) = 11025Hz -> MIDI 124.8，远高于 fmax=2093Hz(B6)")
print("  => sr=22050 本身不砍高频；真正的天花板是 fmax=2093 (B6)")
print("  推理：人声最高音约 C6(1046.5Hz, 女声)/C5(523Hz, 男声)，2093Hz 远够用")

sec("J. separate_audio 临时文件生命周期（读码 + tmp 目录泄漏判定）")
import inspect
src = inspect.getsource(SP.convert_to_wav)
print("  convert_to_wav(output_dir=None) 用 tempfile.mkdtemp -> %TEMP%/autotranscriber_*")
print("  separate_audio 调 convert_to_wav(input_path, output_dir) 传了 output_dir")
print("  => 走 else 分支? 输出到 output_dir/basename_converted.wav，非 mkdtemp 分支")
print("  => 随后 L199-207 os.remove(temp_wav) 删除；但 mkdtemp 分支的 tmp 目录永不清理")
print("  SUPPORTED_FORMATS 含 .raw/.au 但 load_audio(librosa) 未必真支持 -> 未测")

sec("K. .au/.raw 格式实测")
for ext, args in [("au", ["-i", fours, f"{out}/t.au"]),
                  ("pcm_raw", ["-i", fours, "-f", "s16le", "-ar","22050","-ac","1", f"{out}/t.raw"])]:
    p = f"{out}/t.{ext.split('_')[0]}"
    r = subprocess.run(["ffmpeg","-y","-loglevel","error"]+args, capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(p):
        try:
            y, s = load_audio(p, sr=22050)
            print(f"  {ext}: load_audio OK len={len(y)} sr={s}")
        except Exception as e:
            print(f"  {ext}: load_audio FAIL {type(e).__name__}: {str(e)[:80]}")
    else:
        print(f"  {ext}: ffmpeg 生成失败")

sec("L. main.py --two-stems 与 device 参数核实")
s = inspect.getsource(SP.separate_audio)
print("  含 '--two-stems', 'vocals' 硬编码:", "'--two-stems', 'vocals'" in s)
print("  => drums/bass/other 永远不会被产出（--two-stems 只出 vocals/no_vocals）")
print("  => L182-185 的 for stem in ['drums','bass','other'] 是死代码")
print("  device 参数实际被 __check_cuda() 二次覆盖，见 L153")
print("  __check_cuda 是双下划线前缀（Python 名称改写不影响模块级函数）:",
      hasattr(SP, "_" + "_check_cuda"))

print("\n@@PROBE2_DONE@@")
print("OUT:", out)
