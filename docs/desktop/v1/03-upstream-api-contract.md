# 03 · 上游 AutoTranscriber API 契约与风险面报告

> 侦察员：arch-scout · 日期：2026-10-04
> 上游位置：`third_party/AutoTranscriber/`（vendored 快照，**本次侦察零改动**）
> 证据标记：`【代码】`=源码行号 · `【实测】`=本机运行输出 · `【推断】`=逻辑推导，未直接验证 · `【未实测】`=无法验证

---

## 0. TL;DR — 主上最需要先知道的 5 件事

| # | 结论 | 性质 |
|---|---|---|
| 1 | **`separator.py:16` 硬编码了原作者的 conda 路径** `C:/Users/<upstream-author>\miniconda3\envs\AutoTranscriber\python.exe`。主上机器上 `has_demucs()` 恒为 `False`，`separate_audio()` 直接抛 `FileNotFoundError`（**连 HPSS 兜底都走不到**）。所有需要分离的产品路径必须由适配层自己实现分离。 | 致命 |
| 2 | **mp3/wav/flac/ogg/opus/aiff 可读；m4a/aac/wma/mp4/webm 全部读不了**（libsndfile 1.2.2 不支持）。但 PyAV 能读全部 —— 适配层加一层 PyAV 解码兜底即可全覆盖，**无需改上游**。 | 致命但可解 |
| 3 | **MIDI 轨名是空字符串**，且 `pretty_midi`/`mido` 的轨名 meta event 走 latin-1 编码，写中文轨名必崩 `UnicodeEncodeError`。已实测字节级 UTF-8 写入 + MuseScore 4 能正确显示中文轨名。 | 致命但可解 |
| 4 | **`perceptual_filter` 的 `melody_split=True`（默认值）不是"过滤"而是"重写"**：实测把 4 和弦的 18 个音符砍到 3 个。这是路径 4/5 音质的主要风险源。 | 高危 |
| 5 | **性能没有任何问题**：3 分钟音频 CQT 耗时 0.46~0.82s、内存 5.6~11.2MB；全流程估算 < 15s。瓶颈只在音源分离（Demucs，分钟级）。 | 好消息 |

---

## 1. 上游能力总表 — 6 条产品路径判定

| # | 产品路径 | 判定 | 说明 |
|---|---|---|---|
| 1 | 拖入音频 → **全自动扒谱（双轨：人声+伴奏）** | **需 fork 等价物**（可用替代方案绕过） | 上游 `main.py:444-514` 有完整双轨逻辑，但依赖 `separate_audio` → 因缺陷 1 不可用。适配层须自己分离，然后复用 `transcribe_file` 的核心算法链。**分离模块用我方实现即可，扒谱部分不动上游。** |
| 2 | 拖入音频 → **只扒伴奏** | **需小适配** | 上游有 `--track accompaniment`（`main.py:88`），但同样被 `has_demucs()` 门禁挡住（`main.py:445` 硬 `sys.exit(1)`）。适配层分离后直接调 `transcribe_file(no_vocals)`。**唯一工作量是分离。** |
| 3 | 拖入音频 → **只扒人声旋律** | **需小适配** | 上游 `estimate_vocal_pitch`（pYIN，`pitch_estimation.py:246`）纯 librosa 实现，**零外部依赖，可直接用**。分离 vocals 后调它即可。CREPE 路径（`crepe_wrapper.py`）不可用（见 §2.6），但 pYIN 够用。**工作量只在分离。** |
| 4 | 拖入音频 → **基本扒谱（乐器/单音轨）**，下拉可选 | **直接满足** | `main.py:519-528` 模式 B 完全独立，不碰 separator。`transcribe_file` + `write_midi` 即可。**唯一须注意**：`perceptual_filter` 默认参数会毁和弦（见 §8.3）。 |
| 5 | 功能页 2：基本扒谱，**单音轨/多音轨可选** | **直接满足 + 小适配** | 单轨 = `write_midi`，多轨 = `write_multitrack_midi`（`midi_writer.py:321`）。但**轨名为空**，需适配层补（见 §6）。 |
| 6 | **直接导入已分离好的音频**（vocals.wav + instrumental.wav） | **需小适配** | 上游**没有这个入口**——`separate_audio` 只接受单个 `input_path`。但底层函数 `load_audio` 接受任意路径，`transcribe_file(path)` 也接受任意路径。适配层只需提供"两个路径"的 UI 与编排，**底层一行上游代码都不用改**。工作量 = 编排 + 轨名。 |

**汇总**：0 条「直接满足且零适配」，2 条（4/5）基本直接满足，3 条（2/3/6）只差分离或编排，1 条（1）需要我方实现分离。**没有一条路径需要修改上游源码** —— 全部靠适配层编排绕过。

---

## 2. API 契约（逐函数实证）

以下签名全部来自 `inspect.signature()` 实测输出，非人工誊抄。

### 2.1 `load_audio` — `audio_loader.py:7`
```python
def load_audio(file_path: str, sr: int = 22050, mono: bool = True,
               offset: float = 0.0, duration: float = None) -> tuple
```
- **实现**：`audio_loader.py:44-51`，纯 `librosa.load(...)` 转发，**无任何格式探测/兜底逻辑**
- **返回**：`(y: np.ndarray(shape=(n,)), sr: int)` 【实测】
- **无 ffmpeg fallback**【代码】

### 2.2 `preprocess` — `audio_loader.py:54`
```python
def preprocess(y: np.ndarray, sr: int) -> np.ndarray
```
- 去直流 + 峰值归一化到 [-1,1]（`audio_loader.py:71-76`）。**`sr` 参数未被使用**（形参冗余，无害）

### 2.3 `compute_cqt` — `spectral.py:8`
```python
def compute_cqt(y, sr, hop_length=512, fmin=65.41, fmax=2093.0, bins_per_octave=36) -> tuple
```
- **返回**：`(cqt, times, freqs)`；`cqt.shape = (n_bins, n_frames)`，`dtype=float32`【实测】
- `n_bins = ceil(bins_per_octave * log2(fmax/fmin))`（`spectral.py:40`）
- **实测**：`fmin=65.41, fmax=2093, bpo=36` → `n_bins=180`，但 `freqs[-1]=2053.20`（**实际不到 2093**，CQT 量化取整所致）
- ⚠️ **`fmax` 硬编码在 `main.py:163` 与 `main.py:231`，函数本身可传参** —— 见 §8.2

### 2.4 `compute_spectral_flux` — `spectral.py:70`
```python
def compute_spectral_flux(cqt: np.ndarray) -> np.ndarray
```
- 返回 `(n_frames-1,)`【实测】（`np.diff` 少一帧，调用方需注意）
- 对数幅度 + 半波整流 + 沿频率轴求和 + 归一化（`spectral.py:87-98`）

### 2.5 `detect_onsets` — `onset_detection.py:8`
```python
def detect_onsets(flux, sr, hop_length=512, threshold=0.5,
                  min_distance=3, smooth_sigma=1.0) -> np.ndarray
```
- **返回**（文档说是 ndarray，实际是 tuple）【实测】：`(onset_frames: np.ndarray[int], onset_times: np.ndarray[float])`
- 三层兜底：局部 max 归一化 → 局部均值自适应 → 绝对阈值（`onset_detection.py:70-89`）
- ⚠️ **默认 `threshold=0.5`，但 `main.py:96` 传的默认是 `0.3`** —— 实际生效值是 0.3

### 2.6 `estimate_pitches` — `pitch_estimation.py:384`
```python
def estimate_pitches(cqt, freqs, times, sr, hop_length=512, n_peaks=6,
                     min_freq=65.41, max_freq=2093.0,
                     threshold_factor=0.05, use_harmonic_sieve=True) -> list
```
- **返回**：`list[list[dict]]`，长度 = `n_frames`【实测】
- **帧内 note 字段（实测精确值）**：`{'pitch': int, 'frequency': float, 'amplitude': float}` —— **没有 start/end**
- 内部走 `_harmonic_sieve`（`pitch_estimation.py:101`），NNLS 模板拟合 + 预计算谐波表

### 2.7 `estimate_pitches_onset_driven` — `pitch_estimation.py:510`
```python
def estimate_pitches_onset_driven(cqt, freqs, times, onset_frames, onset_times,
                                  sr, hop_length=512, n_peaks=5,
                                  min_freq=65.41, max_freq=2093.0,
                                  threshold_factor=0.08) -> list
```
- **返回**：`list[dict]`【实测】
- **onset note 字段（实测精确值，6 个键）**：
  `{'onset_frame': int, 'onset_time': float, 'pitch': int, 'frequency': float, 'amplitude': float, 'snr': float}`
  —— 与 docstring `pitch_estimation.py:554-561` 完全一致 ✅
- 用 `_spectral_delta_filter`（`pitch_estimation.py:472`）只取"新进入"的能量

### 2.8 `estimate_vocal_pitch` — `pitch_estimation.py:246`
```python
def estimate_vocal_pitch(y, sr, hop_length=512, fmin=65.41, fmax=2093.0,
                         min_duration_frames=3) -> list
```
- **返回**：`list[dict]`【实测】
- **note 字段（实测精确值，4 个键）**：`{'start': float, 'end': float, 'pitch': int, 'velocity': int}`
- 内部 `librosa.pyin` + voiced_prob>0.3 + 双层 medfilt（`pitch_estimation.py:293-300`）
- velocity 由帧 RMS 映射：`40 + 80 * min(1, e/rms_ref)`（`pitch_estimation.py:323`）
- **纯 librosa/scipy，零外部依赖**【代码】—— 这是路径 3 的关键优势

### 2.9 `estimate_vocal_pitch_crepe` — `crepe_wrapper.py:27`
```python
def estimate_vocal_pitch_crepe(audio_path: str, hop_length=320, model='tiny',
                                energy_threshold=0.02,
                                min_note_duration_frames=5) -> list
```
- **返回**：`list[dict]`（JSON 反序列化，`crepe_wrapper.py:90`），字段 `{'start','end','pitch','velocity'}`【代码 `_crepe_run.py:187`】
- ⚠️ **子进程模型**：`CREPE_PYTHON` 指向 `<上游根>/crepe_env/Scripts/python.exe`（`crepe_wrapper.py:13`）【实测不存在】
- ⚠️ **副作用缺陷**：`_ensure_crepe_script()`（`crepe_wrapper.py:19-24`）在脚本缺失时**写入文件到上游目录** —— 这与 B-1 只读红线直接冲突。适配层**绝不能调用**此函数，除非预先保证 `_crepe_run.py` 存在（当前存在 ✅）
- **建议**：路径 3 用 `estimate_vocal_pitch`（pYIN），**放弃 CREPE**

### 2.10 `track_notes` — `note_tracking.py:7`
```python
def track_notes(frame_notes, onset_frames, onset_times, times, sr,
                hop_length=512, min_note_duration=4, pitch_hysteresis=1,
                velocity_scale=80.0, min_amplitude=0.1) -> list
```
- **纯 legacy 转发**（`note_tracking.py:18` → `_track_notes_legacy`）。`pitch_hysteresis` 参数**被忽略**（透传给下层但下层签名 `note_tracking.py:25` 无此参数）
- 返回字段同 §2.8

### 2.11 `track_notes_onset_driven` — `note_tracking.py:56`
```python
def track_notes_onset_driven(onset_notes, cqt, freqs, times, sr,
                             hop_length=512, max_gap_frames=3,
                             decay_ratio=0.25, min_note_frames=2,
                             max_note_seconds=4.0, velocity_scale=80.0,
                             snr_threshold=0.5, track_semitones=1.0,
                             max_drift_semitones=3.0) -> list
```
- **返回**：`list[dict]`【实测】，字段 `{'start','end','pitch','velocity'}` ✅
- **黑箱警告**：内部 `_spectral_delta_filter` 之外全部是 O(帧数 × onset 数) 的 Python 循环 + 每帧 `np.where` 全数组扫描。实测 5.2s 音频仅 0.02s，但**长音频需实测**（见 §8.1）
- **`snr_threshold` 默认 0.5，`main.py:261` 传 0.3** —— 实际更宽松

### 2.12 `perceptual_filter` — `perceptual_filter.py:16`
```python
def perceptual_filter(notes, outlier_semitones=12, outlier_window=0.5,
                      min_duration=0.05, min_velocity=1, merge_gap=0.08,
                      merge_semitones=2, harmonic_check=True,
                      max_simultaneous=6, max_notes_per_beat=8,
                      remove_low_amplitude=True, melody_split=True) -> list
```
- **返回**：`list[dict]`，字段 `{'start','end','pitch','velocity'}`【实测】
- ⚠️ **`remove_low_amplitude` 参数是死的** —— `perceptual_filter.py:27` 声明但函数体内**从未使用**（L71-105 无引用）
- ⚠️ **`melody_split=True`（默认）会重写音符，不是过滤**（见 §8.3）
- `_remove_pitch_outliers`（`perceptual_filter.py:224`）是 O(n²) 双层循环 + 每音一次全表 median —— 长音频性能陷阱

### 2.13 `write_midi` — `midi_writer.py:8`
```python
def write_midi(notes: list, output_path: str, tempo: float = 120.0,
               program: int = 0) -> str
```
- 校验 `0 < pitch < 128` 且 `end > start`（`midi_writer.py:15`）【实测：pitch=0/128 与零长音被静默丢弃】
- **无轨名参数**

### 2.14 `write_multitrack_midi` — `midi_writer.py:321`
```python
def write_multitrack_midi(track_notes: list, output_path: str,
                          tempo: float = 120.0, programs: list = None) -> str
```
- `programs[i]` 对应第 i 轨【实测：program=[52,33] 正确写入】
- **无轨名参数**，轨名为空字符串【实测】

### 2.15 `merge_tracks_to_piano` — `midi_writer.py:27`
```python
def merge_tracks_to_piano(track_notes_list: list, max_simultaneous: int = 4) -> list
```
- ⚠️ **输入必须是 `(notes_list, ttype)` 二元组**，`ttype ∈ {'vocal','accompaniment'}`【实测：单元素 tuple 抛 `ValueError: not enough values to unpack`】
- ⚠️ **`max_simultaneous` 参数未被使用**（函数体内无引用，`midi_writer.py:27-132`）
- velocity 硬编码：旋律 80 / 低音 70【实测 `{70, 80}`】
- **有 print**：`midi_writer.py:131` `print(f"      🎹 旋律: ...")` —— 唯一的库内 stdout 输出

### 2.16 `separate_audio` — `separator.py:92`
```python
def separate_audio(input_path: str, output_dir: str = None,
                   model: str = "htdemucs", device: str = "cpu") -> dict
```
详见 §5。**当前不可用**（缺陷 1）。

### 2.17 `has_demucs` — `separator.py:253` / `has_crepe` — `crepe_wrapper.py:105`
- `has_demucs()`：检查 `DEMUCS_PYTHON` 路径存在 + 跑 `python -m demucs --help`【实测 = `False`】
- `has_crepe()`：检查 `CREPE_PYTHON` 存在 + 跑 `python -c "import torchcrepe"`【实测 = `False`】
- ⚠️ 两者都是 **10 秒子进程超时**（`separator.py:259`、`crepe_wrapper.py:112`）。**适配层每次调它们都要付 10~20s 启动代价**，应缓存结果

### 2.18 `midi_to_pdf` — `midi_to_pdf.py:200`
```python
def midi_to_pdf(midi_path: str, pdf_path: str = None,
                force_pianoroll: bool = False) -> str
```
- MuseScore 优先，失败降级 matplotlib【实测：MuseScore 4 成功，2.43s，含冷启动】
- ⚠️ **matplotlib 未装**【实测：`ModuleNotFoundError: No module named 'matplotlib'`】—— 兜底路径实际不可用，**必须保证 MuseScore 可用**
- ⚠️ 超时 120s（`midi_to_pdf.py:90`）

### 2.19 `find_musescore` — `midi_to_pdf.py:16`
- 8 个候选路径 + PATH 兜底（`where` 命令）
- ✅ **主上路径被完整覆盖**：`midi_to_pdf.py:21` = `%ProgramFiles%\MuseScore 4\bin\MuseScore4.exe`
  【实测返回 `C:\Program Files\MuseScore 4\bin\MuseScore4.exe`，文件存在 ✅】

---

## 3. note 数据结构（精确字段表）

三种 note 形态，**字段名不同，适配层必须区分**：

| 阶段 | 函数 | 字段 | 类型 |
|---|---|---|---|
| 帧内 | `estimate_pitches` | `pitch`, `frequency`, `amplitude` | int, float, float |
| onset | `estimate_pitches_onset_driven` | `pitch`, `frequency`, `amplitude`, `snr`, `onset_frame`, `onset_time` | int, float, float, float, int, float |
| 事件（最终） | `track_notes*` / `estimate_vocal_pitch*` / `perceptual_filter` | `start`, `end`, `pitch`, `velocity` | float, float, int, int |

⚠️ **关键陷阱**：
1. 任务书里假设的 `amplitude` 字段**只存在于前两个中间阶段**。最终 note 用的是 **`velocity`（int，1~127）**，不是 amplitude。
2. `write_midi` 只读 `n['velocity']`（`midi_writer.py:18`）。**若把 `_harmonic_sieve` 的输出直接喂给 `write_midi` 会 KeyError** —— 中间必须经 `track_notes*` 转换。
3. 实测 velocity 分布：`track_notes_onset_driven` 输出 46，perceptual_filter 后 36~85，`merge_tracks_to_piano` 硬编码 70/80。

---

## 4. 音频格式支持矩阵（逐个实测，非推测）

**测试方法**：`ffmpeg 5.1.2` 从 `test_audio/chord_progression.wav` 转出各格式 → `load_audio(path, sr=22050)` 实际调用。

| 格式 | `load_audio` | `soundfile` 直读 | `av`(PyAV) 直读 | 依据 |
|---|---|---|---|---|
| `.wav` | ✅ 可读 | ✅ | ✅ | 【实测】 |
| `.mp3` | ✅ 可读 | ✅ mp3float | ✅ | 【实测】 |
| `.flac` | ✅ 可读 | ✅ | ✅ | 【实测】 |
| `.ogg` (Vorbis) | ✅ 可读 | ✅ | ✅ | 【实测】 |
| `.opus` | ✅ 可读 | ✅ | ✅ | 【实测】 |
| `.aiff/.aif` | ✅ 可读 | ✅ pcm_s16be | ✅ | 【实测】 |
| `.au` | ✅ 可读 | ✅ | — | 【实测】len=114660 |
| `.m4a` | ❌ **不可读** | ❌ | ✅ aac | 【实测】`LibsndfileError: Format not recognised` |
| `.aac` | ❌ **不可读** | ❌ | ✅ aac | 【实测】同上 |
| `.mp4` | ❌ **不可读** | ❌ | ✅ aac | 【实测】同上 |
| `.wma` | ❌ **不可读** | ❌ | ✅ wmav2 | 【实测】同上 |
| `.webm` | ❌ **不可读** | ❌ | ✅ opus | 【实测】同上 |
| `.raw` | ❌ **不可读** | — | — | 【实测】`TypeError: samplerate must be specified` |

**根因（代码事实）**：`librosa.load` 在 soundfile 可用时优先走 soundfile（libsndfile **1.2.2**【实测】），且 **audioread 未安装**【实测】→ GStreamer/ffmpeg 回退链**完全不可用**。

**结论**：`audio_loader.py:12-22` 的 docstring 声称支持 m4a/aac/wma/mp4 —— **这是错的**。`separator.py:19-20` 的 `SUPPORTED_FORMATS` 同样错误。

**补救（不改上游，已验证）**：PyAV 手写解码可用，m4a 解码长度 114660 与原 wav 完全一致【实测】。适配层实现：
```
try: load_audio(path)
except: PyAV decode → np.ndarray → 直接喂给 compute_cqt 链路
```

---

## 5. 分离器行为

### 5.1 返回 dict 键名（代码事实 + 实测）
`separator.py:176-185` 声明返回：
```python
{'vocals': str, 'no_vocals': str, 'mixture': str,
 'drums': str, 'bass': str, 'other': str}   # 后三个条件性
```
**但 `drums`/`bass`/`other` 永远是死代码** —— `separator.py:150` 硬编码 `--two-stems vocals`，Demucs 只产 `vocals.wav` + `no_vocals.wav`。【代码】

### 5.2 产物路径结构
`{output_dir}/{model}/{wav_basename}/vocals.wav`（`separator.py:172-178`）
- `output_dir=None` → `输入文件同目录/separated/`（`separator.py:135`）—— **会污染用户目录**
- HPSS 兜底产物：`{output_dir}/hpss/{basename}/{vocals,no_vocals}.wav`【实测】

### 5.3 临时文件生命周期
- `needs_conversion(path)`：`.wav` → False，其余全部 True（`separator.py:29-36`）—— **注意 flac 也会被转码**【实测】
- 转码产物写到 `{output_dir}/{basename}_converted.wav`（`separator.py:74`），流程末尾 `os.remove` 删除（`separator.py:199-207`）
- ⚠️ **`convert_to_wav(path)` 不传 `output_dir` 时走 `tempfile.mkdtemp(prefix="autotranscriber_")`（`separator.py:77`），该目录永不清理**（`separator.py:205` 的 `os.rmdir` 仅在 `temp_wav` 由 mkdtemp 分支产生时才生效，但 `separate_audio` 永远传了 `output_dir`，所以该分支在上游主流程中不可达）→ **适配层若直接调 `convert_to_wav` 会泄漏临时目录**

### 5.4 实测：当前不可用
【实测】`separate_audio` 抛：
```
FileNotFoundError: [WinError 2] 系统找不到指定的文件。
  at separator.py:161 subprocess.run(cmd, ...)
```
`separator.py:160-169` 只 `except subprocess.TimeoutExpired` → **`demucs_ok=False` 的 HPSS 兜底分支（`separator.py:190`）永远走不到**。这是上游最严重的逻辑缺陷。

---

## 6. MIDI 输出特性

| 特性 | 实测结果 |
|---|---|
| 轨名 | **空字符串 `''`**【实测】`write_midi` 与 `write_multitrack_midi` 均无轨名参数 |
| program | `write_midi(program=0)`；`write_multitrack_midi(programs=[52,33])` → **正确写入**【实测】 |
| tempo | ✅ **真写进文件**。`get_tempo_changes() = (array([0.]), array([120.]))`【实测】 |
| ⚠️ tempo 读取陷阱 | `estimate_tempo()` 返回 60/66.3 等**由音符反推的节拍**，不是写入值。**UI 显示 tempo 必须读 `get_tempo_changes()`** |
| resolution | 220 PPQ（pretty_midi 默认，不可配置） |
| is_drum | False |
| 音域/力度 | 上游无钳制，原样写入 |

### 6.1 中文轨名：三重障碍与突破

**障碍 1**：`pretty_midi.Instrument.name = "人声"` → `write()` 崩
```
UnicodeEncodeError: 'latin-1' codec can't encode characters in position 0-1
```
根因【实测】：`mido.midifiles.meta._charset == 'latin1'`

**障碍 2**：`mido.MetaMessage("track_name", name="人声")` → 同样崩（mido 层也走 latin1）【实测】

**障碍 3**：UTF-8 字节直写后，`pretty_midi` 回读得到 mojibake `'äººå£°'`【实测】（可接受）

**突破（已实测验证）**：字节级手写 MIDI writer 直接把 UTF-8 放进 `FF 03` track_name：
- MuseScore 4 转 PDF **rc=0，成功**【实测】
- MuseScore 4 转 MusicXML 后 `part-name` = `['Soprano, 人声', 'Piano, 伴奏']` —— **中文正确解析**【实测】
- 程序号自动映射：52 → "Soprano"，0 → "Piano"

**推荐方案**：适配层自建轻量 MIDI writer（约 60 行，已在探针中验证），完全绕开 pretty_midi/mido 的 latin-1 限制。这同时解决轨名、program、tempo 全部三个需求。

### 6.2 MuseScore 4 headless 导出能力（实测）
| 格式 | 结果 |
|---|---|
| `.pdf` | ✅ 22522 B |
| `.musicxml` | ✅ 4802 B |
| `.mxl` | ✅ 1720 B |
| `.mp3` | ✅ 81083 B |
| `.wav` | ✅ 1764046 B |
| `.flac` | ✅ 147174 B |
| `.ogg` | ✅ 65550 B |
| `.png` | ❌ 未产出（rc=0 但无文件） |
| `.svg` | ❌ 未产出（同上，实际生成 `out-1.svg` 带序号后缀） |

⚠️ PNG/SVG 实际生成但文件名带 `-1` 后缀，需 glob 匹配。**转换耗时：冷启动 0.92s，热 0.94s**【实测】—— 很快，不是 UX 瓶颈。

---

## 7. 进度上报可行性

### 7.1 上游现状
- **零回调、零 logging、零 generator yield**【实测 grep：`callback|progress|on_progress|yield|logging` 在 .py 中只命中 `main.py:114 def print_progress` 与 README】
- `print()` 语句分布【实测 grep -c】：
  ```
  main.py:54  separator.py:12  crepe_wrapper.py:12
  midi_to_pdf.py:6  midi_writer.py:1  其余模块:0
  ```
- 核心算法模块（spectral / pitch_estimation / note_tracking / perceptual_filter / onset_detection）**完全静默**

### 7.2 三方案对比

| 方案 | 做法 | 优点 | 缺点 | 推荐 |
|---|---|---|---|---|
| **A. stdout 解析** | 跑 `main.py` 当子进程，正则抓 `[HH:MM:SS]` 时间戳行 | 零代码 | **只有 4 个粗粒度锚点**（`main.py:450/464/488/521`），无百分比；`阶段 2/3` 内含 2~3 轨串行无法区分；stdout 混 emoji 与中文需处理编码；**且必须走 CLI → 被 `has_demucs()` 门禁挡住路径 1/2/3** | ❌ |
| **B. 直接 import + 自编排** | `import AutoTranscriber` 后自己按步骤调用并计时 | **完全掌控**；可绕开 `main.py` 的所有门禁与 sys.exit；可精确到"第几轨/第几阶段"；无需解析文本 | 需我方实现编排（约 100 行） | ✅ **强烈推荐** |
| **C. fork 改上游** | 注入 callback 参数 | 最"干净" | **违反 B-1 只读红线**，下次同步被覆盖 | ❌ |

### 7.3 方案 B 的进度锚点（已验证可行）
| 阶段 | 上游调用 | 进度来源 |
|---|---|---|
| 解码 | `load_audio` | 单次调用，用文件大小比例假进度 |
| CQT | `compute_cqt` | **返回 `cqt.shape[1]` = 总帧数**，可算真实百分比 |
| onset | `detect_onsets` | 毫秒级，视为瞬时 |
| 音高估计 | `estimate_pitches` | 用 `cqt.shape[1]` 做分母线性插值（实测单帧 ≈0.26ms）【推断】 |
| 追踪+滤波 | `track_notes_onset_driven` / `perceptual_filter` | 用 `len(onset_notes)` / `len(notes)` 做分母 |
| 分离 | 我方实现 | 自己实现，天然有进度 |

**结论：进度上报无需任何上游改动。** 关键洞察是上游核心函数全是**无状态纯函数**，只有 `separator`/`crepe_wrapper`/`midi_to_pdf` 有副作用。

---

## 8. 性能与音质风险

### 8.1 耗时与内存（实测）

**3 分钟（180s）随机音频，`sr=22050`, `fmin=65.41`, `fmax=2093`, `bins_per_octave=36`**：
| hop | CQT 耗时 | CQT shape | float32 内存 | estimate_pitches 耗时 |
|---|---|---|---|---|
| 256 | **0.82s** | (180, 15504) | **11.2 MB** | 2.5s |
| 512 | **0.46s** | (180, 7752) | **5.6 MB** | 1.8s |

**全流程估算（3 分钟歌曲，双轨）**【推断，基于实测外推】：
```
解码 2×0.5s + CQT 2×0.5s + onset 2×0s + estimate_pitches 2×2s
+ track 2×?s + perceptual_filter 2×?s + write 0.1s ≈ 10~15s
```
**内存峰值 < 50 MB**（CQT 主导）。**结论：扒谱本身不是性能瓶颈，无需 GPU、无需流式处理。**

⚠️ **未验证的性能陷阱**：`_remove_pitch_outliers`（`perceptual_filter.py:246-272`）是 O(n²) 双层 Python 循环 + 每音一次全表 `np.median`。若 3 分钟音频产生 3000 个音符 → 900 万次迭代，可能到分钟级。`track_notes_onset_driven` 的追踪循环同理。**建议适配层对 >60s 音频先用短样本试跑并计时**。

### 8.2 `sr=22050` 与 `fmax=2093` 对高音的影响

**关键区分**（这是两个独立的天花板）：
- `sr=22050` → Nyquist = 11025 Hz → MIDI 124.8，**远高于 fmax，不构成瓶颈**【实测】
- `fmax=2093` → MIDI 96.0 = **B6**，这才是真正的天花板【实测】

**实测验证**：
- 输入 G6(1568Hz) + B6(1976Hz) + C7(2093Hz) + E7(2637Hz)
- `fmax=2093` → 检出 `['G6','B6','C7']`，**E7 被切掉**
- `fmax=4186` → 可覆盖 E7

**对产品的影响**：
- 人声音域：女声最高约 C6(1046.5Hz)、男声约 C5(523Hz) → **2093Hz 对人声绰绰有余**，路径 3 不受影响
- 乐器高音声部（如吉他高把位、钟琴）**会被切在 B6 以下**
- ⚠️ **`fmax` 在 `main.py:163` 与 `main.py:231` 硬编码，但 `compute_cqt` 本身可传参** → 适配层直接调 `compute_cqt(..., fmax=4186.0)` 即可，**无需改上游**【代码】

**实测高音验证**：合成 C6(1046.5) + A5(880) + C7(2093) → 检出 `['C6(1047Hz)', 'A5(880Hz)']`，C7(2093) 因正好在 fmax 边界未被检出 —— 印证边界效应。

### 8.3 `n_peaks` 与和弦识别

**实测扫描（`four_chords.wav`，224 帧）**：
| n_peaks | 平均同时音数 | 最大同时音数 |
|---|---|---|
| 2 | 1.96 | 2 |
| 3 | 2.88 | 3 |
| 4 | 2.97 | 4 |
| **5**（main 默认） | **3.03** | 5 |
| 6 | 3.08 | 6 |
| 8 | 3.15 | 8 |
| 10 | 3.18 | 10 |
| 12 | 3.18 | 10（封顶） |

**结论**：`n_peaks` **确实是最硬的和弦音数上限**（`pitch_estimation.py:222` `if len(kept) >= n_peaks: break`）。但实际平均稳定在 ~3.1 —— 超过 8 收益趋零。**主上要的 4~6 音和弦，`n_peaks=6~8` 足够**。

⚠️ `perceptual_filter` 的 `max_simultaneous=6`（`main.py:273`）会**二次收紧**这个上限，且 `_limit_simultaneous_notes`（`perceptual_filter.py:311-318`）有音域偏好：`pitch>=67` 加权 2.0、`pitch<48` 加权 1.5、中音区仅 0.5 —— **中音区和弦音最先被砍**。

### 8.4 ⚠️⚠️ `perceptual_filter(melody_split=True)` 是重写而非过滤 —— 路径 4/5 的头号音质风险

**实测（`four_chords.wav`，一个 4 和弦进行）**：
```
onset=12  onset_notes=39
→ track_notes_onset_driven → 18 notes
→ perceptual_filter(melody_split=True)  →  3 notes   ← 砍掉 83%
   C4 0.23~4.90s vel=85
   E2 1.09~3.90s vel=70
   G3 1.25~2.30s vel=70

→ perceptual_filter(melody_split=False) →  6 notes   ← 合理
```

**根因**：`perceptual_filter.py:80-83` 调用 `separate_melody_and_accompaniment`（`perceptual_filter.py:390`），后者把音符按音域切三块，然后**各自重建**：
- `_extract_melody_line`（`:444`）：每 0.12s 窗只留 1 个音（`:487 active.sort(reverse=True); best = active[0]`）
- `_extract_bass_line`（`:570`）：只保留最低 3 个不同音高（`:587-593`）
- `_filter_mid_notes`（`:669`）：与旋律不协和的音大量丢弃

**这不是"滤波"，是"三轨重编排"**。对路径 4/5（基本扒谱）**必须默认关闭**。
`main.py:266-275` 的 `--perceptual` 模式正是用默认值 → 上游默认行为在乐器多声部场景会严重丢音。

**适配层对策**：路径 4/5 的 `perceptual_filter` 调用**必须显式传 `melody_split=False`**，并在 UI 上把"感知模式"做成显式可选项（默认关）。

---

## 9. 上游缺陷清单

| # | 缺陷 | 位置 | 严重度 | 不改上游的规避方案 |
|---|---|---|---|---|
| D-1 | **硬编码他人 conda 路径** `C:/Users/<upstream-author>\miniconda3\envs\AutoTranscriber\python.exe` | `separator.py:16` | 🔴致命 | 适配层自己调 `demucs.separate` API 或 `python -m demucs` 子进程；**完全绕开 `separator.py`** |
| D-2 | **`FileNotFoundError` 未被捕获**，HPSS 兜底死代码 | `separator.py:160-169` vs `:190` | 🔴致命 | 适配层自己 try/except 后调 `_hpss_separate`（已实测可用）或自建 |
| D-3 | **`has_demucs()` 判据只查外部 python 路径**，本机装了 demucs 也返回 False | `separator.py:253-263` | 🔴致命 | 适配层自建检测（`importlib.util.find_spec('demucs')`） |
| D-4 | **docstring 谎报格式支持**（声称 m4a/aac/wma/mp4 可读，实测全部失败） | `audio_loader.py:12-22`、`separator.py:19-20` | 🔴致命 | 适配层 PyAV 兜底解码（已验证） |
| D-5 | **MIDI 轨名恒为空**，且 pretty_midi/mido 轨名 meta 走 latin-1，中文必崩 | `midi_writer.py:8/321` + mido `_charset='latin1'` | 🔴致命 | 适配层自建字节级 MIDI writer（已验证 MuseScore 4 正确显示中文轨名） |
| D-6 | **`perceptual_filter(melody_split=True)`  aggressive 重写**，4 和弦 18→3 音符 | `perceptual_filter.py:80-83` + `:390` | 🟠高 | 显式传 `melody_split=False` |
| D-7 | **`--two-stems vocals` 硬编码**，`drums/bass/other` 为死代码 | `separator.py:150` vs `:182-185` | 🟠高 | 若需 4 轨，适配层直接跑 `demucs --four-stems` |
| D-8 | **`device` 参数被二次覆盖**，日志硬编码"设备=cpu" | `separator.py:153`、`:157` | 🟡中 | 适配层自建分离命令 |
| D-9 | **`output_dir=None` 污染用户目录**（在音频旁建 `separated/`） | `separator.py:135` | 🟡中 | 适配层总是显式传 `output_dir` |
| D-10 | **`convert_to_wav(path)` 单参调用泄漏 `%TEMP%/autotranscriber_*`** | `separator.py:77` + `:205` | 🟡中 | 适配层总是传 `output_dir` |
| D-11 | **`fmax` 硬编码 2093（B6）**，高音被切 | `main.py:163`、`:231` | 🟡中 | 直接调 `compute_cqt(..., fmax=4186.0)` |
| D-12 | **`remove_low_amplitude` 是死参数** | `perceptual_filter.py:27`（函数体 L65-105 未引用） | 🟢低 | 忽略该参数 |
| D-13 | **`max_simultaneous` 在 `merge_tracks_to_piano` 是死参数** | `midi_writer.py:28`（函数体未引用） | 🟢低 | 忽略该参数 |
| D-14 | **`pitch_hysteresis` 在 `track_notes` 是死参数** | `note_tracking.py:11` → 下层无此形参 | 🟢低 | 忽略该参数 |
| D-15 | **`create_crepe_script` 会往上游目录写文件**，违反只读红线 | `crepe_wrapper.py:19-24` | 🟠高 | 禁用 `estimate_vocal_pitch_crepe`，改用 pYIN |
| D-16 | **matplotlib 未装**，PDF 兜底路径实际不可用 | `midi_to_pdf.py:118` | 🟡中 | 安装 matplotlib；或保证 MuseScore 可用 |
| D-17 | **`has_demucs()`/`has_crepe()` 每次调用付 10s 子进程超时** | `separator.py:259`、`crepe_wrapper.py:112` | 🟢低 | 适配层缓存结果，只查一次 |
| D-18 | **`_remove_pitch_outliers` O(n²)**，长音频性能风险 | `perceptual_filter.py:246-272` | 🟡中 | 长音频分块处理或跳过该步 |
| D-19 | **测试文件硬编码他人路径** `sys.path.insert(0, r"C:/Users/<upstream-author>\Desktop\...")` | `test_nnls_sieve.py:5`、`test_medium_priority.py:5` | 🟢低 | 不跑上游测试；我方自建对拍基线 |
| D-20 | **`requirements.txt` 缺 demucs/torch/matplotlib/mido** | `requirements.txt`（仅 5 个包） | 🟡中 | 适配层自建完整依赖清单 |

---

## 10. 适配层 TODO 清单

**图例**：[不改上游] = 直接可做 ｜ [需替代方案] = 上游不可用，我方另实现

| # | TODO | 是否需改上游 | 说明 / 替代方案 |
|---|---|---|---|
| **A. 音频输入层** |
| A-1 | 实现统一 `load_audio_any(path) -> (y, sr)`：先试上游 `load_audio`，失败走 PyAV | [不改上游] | §4 已验证 PyAV 覆盖全部失败格式 |
| A-2 | ffmpeg 常驻探测（`ffmpeg -version`），缺失时给用户明确提示 | [不改上游] | 实测本机 5.1.2 可用 |
| A-3 | 格式白名单 UI（实测可用 7 种：wav/mp3/flac/ogg/opus/aiff/au） | [不改上游] | 兜底解码后实际可支持 12 种 |
| **B. 分离层（最高优先）** |
| B-1 | 实现 `separate(in_path, out_dir) -> {vocals, instrumental}`：直接 `python -m demucs --two-stems vocals`（用**我方 venv 的 python**，非上游硬编码路径） | [需替代方案] | 绕开 D-1/D-2/D-3；建议装 `demucs` 到 `engine/.venv` |
| B-2 | 探测 demucs 可用性：`importlib.util.find_spec('demucs')` + CUDA 检测 | [需替代方案] | 替代 D-3 |
| B-3 | 分离进度上报（demucs 支持 `--progress`） | [需替代方案] | 分离是唯一耗时环节（分钟级），进度必做 |
| B-4 | HPSS 兜底：调上游 `_hpss_separate`（已实测可用）作为 demucs 失败时的二级降级 | [不改上游] | `separator.py:220` |
| B-5 | 若需 4 轨（drums/bass/other），跑 `demucs --four-stems` | [需替代方案] | 绕开 D-7 |
| **C. 扒谱编排层** |
| C-1 | 自建 `transcribe(path, mode) -> notes`，绕开 `main.py`（`has_demucs` 门禁 + sys.exit） | [不改上游] | 直接 import 子模块 |
| C-2 | 双轨编排：分离 → vocals 走 `estimate_vocal_pitch`（pYIN）/ accompaniment 走感知链路 | [不改上游] | 路径 1/2/3 |
| C-3 | 路径 6 编排：接受用户两个已分离路径，跳过 B 段 | [不改上游] | 上游无此入口，纯我方编排 |
| C-4 | `perceptual_filter` 调用**显式传 `melody_split=False`** | [不改上游] | 绕开 D-6，路径 4/5 必做 |
| C-5 | `compute_cqt(..., fmax=4186.0)` 提频上限（可选，按需求） | [不改上游] | 绕开 D-11 |
| C-6 | 禁用 `estimate_vocal_pitch_crepe` | [不改上游] | 绕开 D-15，用 pYIN |
| **D. MIDI 导出层** |
| D-1a | 自建字节级 MIDI writer（~60 行）：轨名 UTF-8 / program / tempo / note | [需替代方案] | 绕开 D-5；已验证 MuseScore 4 正确显示中文轨名 |
| D-2a | tempo 用 `get_tempo_changes()` 回读校验，**禁用 `estimate_tempo()`** | [不改上游] | §6 实测陷阱 |
| D-3a | 中文轨名 → program 自动映射表（人声→52 Soprano / 伴奏→0 Piano 等） | [不改上游] | MuseScore 依据 program 推断乐器名 |
| D-4a | MIDI 边界校验（pitch 1~127、end>start）在导出层显式做 + 告警 | [不改上游] | 上游静默丢弃（`midi_writer.py:15`） |
| **E. 进度与 IPC** |
| E-1 | 用「直接 import + 自编排」实现分阶段进度上报（§7.3 锚点表） | [不改上游] | 绕开 A 方案的所有缺点 |
| E-2 | 缓存 `has_demucs`/`has_crepe` 结果，避免重复 10s 子进程 | [不改上游] | 绕开 D-17（且已改为自建检测） |
| E-3 | 取消能力（用户中止）：核心函数是纯函数，进程级 terminate 即可 | [不改上游] | 建议引擎跑在独立进程/线程池 |
| **F. 导出与集成** |
| F-1 | MuseScore 4 路径**已被上游完整覆盖**（`midi_to_pdf.py:21`），实测可用，无需适配 | [不改上游] | ✅ 唯一「零适配」项 |
| F-2 | PNG/SVG 导出：文件名带 `-1` 后缀，需 glob 匹配 | [不改上游] | §6.2 实测 |
| F-3 | 安装 matplotlib（兜底 PDF 路径依赖） | [不改上游] | 绕开 D-16 |
| F-4 | 长音频（>60s）性能守护：先跑 30s 样本计时外推，超阈值则告警或分块 | [不改上游] | 规避 D-18 |

**TODO 总计 22 条**，其中：
- 不改上游即可做：**15 条**
- 需替代方案（我方另实现，**仍然不改上游**）：**7 条**（B-1/B-2/B-3/B-5、D-1a）
- **需要修改上游源码的：0 条**

---

## 11. 实测记录

### 11.1 环境
```
$ engine/.venv/Scripts/python.exe -c "import librosa, soundfile, av"
librosa 1.0.0
scipy   1.18.1
soundfile 0.14.0  (libsndfile 1.2.2)
pretty_midi 0.2.11.post0
numpy 2.5.3
av 19.0.1
mido 1.3.3  (meta._charset = latin1)
Python 3.13.14
ffmpeg 5.1.2-full_build-www.gyan.dev
MuseScore: C:\Program Files\MuseScore 4\bin\MuseScore4.exe（存在）
```
⚠️ **未安装**：torch / demucs / torchcrepe / matplotlib（`has_demucs()=False`、`has_crepe()=False` 均由此导致）

### 11.2 关键实测输出摘录

**音频格式矩阵**（`load_audio(path, sr=22050)`）：
```
  mp3  : OK len=59535 sr=22050 dur=2.70s peak=0.2855
  m4a  : FAIL LibsndfileError: Error opening '...t.m4a': Format not recognised.
  aac  : FAIL LibsndfileError: ...
  ogg  : OK len=59535
  opus : OK len=59535
  wma  : FAIL LibsndfileError: ...
  aiff : OK len=59535
  flac : OK len=59535
  mp4  : FAIL LibsndfileError: ...
  webm : FAIL LibsndfileError: ...
  au   : OK len=114660
  raw  : FAIL TypeError: samplerate must be specified
soundfile: mp3/ogg/opus/aiff/flac OK；m4a/aac/wma/mp4/webm FAIL
av(PyAV) : 全部 10 种 OK（mp3float/aac/vorbis/opus/wmav2/pcm_s16be/flac）
audioread: 未安装
```

**note 字段实测**：
```
逐帧 note 字段:  ['amplitude', 'frequency', 'pitch']
   样例: {'pitch': 60, 'frequency': 256.65, 'amplitude': 0.494}
onset note 字段: ['amplitude','frequency','onset_frame','onset_time','pitch','snr']
   样例: {'onset_frame':10,'onset_time':0.232,'pitch':60,
          'frequency':261.64,'amplitude':0.542,'snr':1.0}
最终 note 字段:  ['end','pitch','start','velocity']
   样例: {'start':0.232,'end':2.624,'pitch':60,'velocity':46}
滤波后字段:      ['end','pitch','start','velocity']
merge_tracks 输出字段: ['end','pitch','start','velocity'], velocity ∈ {70,80}
```

**write_midi 回读**：
```
instruments 数 = 1
get_tempo_changes = (array([0.]), array([120.]))   ← 写入值正确
estimate_tempo()  = 66.33                       ← 由音符反推，非写入值
track[0] name='' program=0 is_drum=False notes=4
   pitch 55..60, velocity 36..85
```
tempo=90 写入回读：`(array([0.]), array([90.00009]))` ✅

**write_multitrack_midi 回读**：
```
tempo=100 写入, instruments=2
track[0] name='' program=0 notes=4
track[1] name='' program=1 notes=9       ← program 正确，轨名为空
```

**中文轨名三重障碍**：
```
mido meta._charset = latin1
pretty_midi name='人声'   → UnicodeEncodeError: 'latin-1' codec can't encode...
mido MetaMessage CJK      → UnicodeEncodeError（同样）
pretty_midi name='Lead'   → OK, 回读 'Lead'
UTF-8 字节直写 → pretty_midi 回读 'äººå£°'（mojibake，可接受）
             → MuseScore 4 转 PDF rc=0 成功
             → MuseScore 4 转 MusicXML: part-name = ['Soprano, 人声','Piano, 伴奏'] ✅
```

**separate_audio 失败**：
```
[分离] 运行 Demucs... (模型=htdemucs, 设备=cpu)
FileNotFoundError: [WinError 2] 系统找不到指定的文件。
  at separator.py:161 → subprocess.run(cmd, ...)
DEMUCS_PYTHON = C:/Users/<upstream-author>\miniconda3\envs\AutoTranscriber\python.exe (不存在)
→ HPSS 兜底未触发（except 只捕 TimeoutExpired）
_hpss_separate 直接调用：OK
  {'vocals': '.../hpss/clip10s/vocals.wav', 'no_vocals': '.../no_vocals.wav'}
  两文件均 229364B
```

**perceptual_filter 副作用**：
```
four_chords.wav (5.20s): onset=12 onset_notes=39
track_notes_onset_driven → 18 notes
perceptual_filter(melody_split=True)  →  3 notes  (C4/E2/G3)
perceptual_filter(melody_split=False) →  6 notes
```

**性能**：
```
180s 音频, hop=256: CQT 0.82s, (180,15504), 11.2MB; estimate_pitches 2.5s
180s 音频, hop=512: CQT 0.46s, (180,7752),   5.6MB; estimate_pitches 1.8s
estimate_vocal_pitch (pyin, 5.2s 音频): 0.26s（第二次调用；首次 11.87s 含 numba JIT）
```

**MuseScore 4 导出能力**：
```
.pdf rc=0 22522B | .musicxml rc=0 4802B | .mxl rc=0 1720B
.mp3 rc=0 81083B | .wav rc=0 1764046B | .flac rc=0 147174B | .ogg rc=0 65550B
.png rc=0 但未产出（实际 out-1.png）| .svg 同理（out-1.svg）
冷启动 0.92s / 热启动 0.94s
matplotlib 兜底：ModuleNotFoundError: No module named 'matplotlib'
```

**write_midi 边界**：
```
输入 pitch=0/127/128 + 零长音 → 实际写入 [127]
（0 与 128 与 end<=start 被静默丢弃，midi_writer.py:15）
```

### 11.3 明确标注为「未实测」的项

| 项 | 原因 |
|---|---|
| **Demucs 真实分离质量与耗时** | torch/demucs 未安装；`separate_audio` 因 D-2 不可调用 |
| **CREPE 音高估计质量** | `crepe_env` 不存在；且因 D-15 不应调用 |
| **pYIN 在真实人声上的准确率** | `test_audio/` 无真实人声样本；仅在合成/合成器和弦上验证 |
| **torchcrepe 是否需 TensorFlow** | 【代码事实】`crepe_wrapper.py:121` 只 `import torchcrepe`，**不 import tensorflow**。torchcrepe 是 PyTorch 实现，**不需要 TF**。但 `crepe_env` 需装 `torch torchaudio torchcrepe librosa soundfile pretty_midi`（`crepe_wrapper.py:60` 的官方提示）【代码】 |
| **CREPE 是否拖慢启动** | 【推断】`has_crepe()` 走子进程（10s 超时），首次调用有冷启动代价；`estimate_vocal_pitch_crepe` 走 `subprocess.run(timeout=300)`（`crepe_wrapper.py:83`），模型加载在子进程内。**建议直接不用 CREPE**（C-6） |
| **长音频（>60s）端到端耗时** | 仅实测到 5.2s 与 180s 的 CQT/pitch 阶段；`perceptual_filter`/`track_notes` 的长音频表现未测（D-18 风险） |
| **MIDI 在 MuseScore 中的实际显示效果** | 只验证了转换 rc=0 与 MusicXML `part-name` 正确，未做视觉确认 |
| **n_peaks > 8 的收益** | 实测显示 10 与 12 结果相同（封顶 10），未解释封顶原因（推测受 `top_candidates=16` 与 `threshold_factor` 限制） |

### 11.4 上游测试可运行性
`test_nnls_sieve.py:5` 与 `test_medium_priority.py:5` 均 `sys.path.insert(0, r"C:/Users/<upstream-author>\Desktop\CODE\pythonProject\AutoTranscriber")` —— **在主上机器上无法直接运行**（D-19）。需自建对拍基线。

---

## 12. 给适配层的落地建议（最小可行顺序）

1. **先做路径 6**（导入已分离音频）—— 零分离依赖，立刻能验证扒谱质量与 MIDI 导出
2. **同时建 MIDI writer**（D-1a）—— 这是唯一能同时满足轨名/program/tempo 三个需求的方案
3. **再做路径 4/5**（基本扒谱）—— 只加 `melody_split=False`，风险最低
4. **最后做分离**（B-1）—— 装 demucs 到 `engine/.venv`，自建分离器 + 进度上报
5. **路径 1/2/3 在 4 完成后** —— 复用同一编排

**不要用 CLI**（`main.py`）：它被 `has_demucs()` 门禁（`main.py:445`）、硬编码 `fmax`（`:163`）、`melody_split=True` 默认（`:266-275`）三处限制，且只有 4 个粗粒度进度锚点。
