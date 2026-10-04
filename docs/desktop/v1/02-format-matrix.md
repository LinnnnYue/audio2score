# 音频格式支持矩阵（实测）

> 真值来源：本机实测代码 + 运行输出，非 README 转述。
> 环境：Windows / Python 3.13.14 / librosa 1.0.0 / soundfile（libsndfile）/ ffmpeg 5.1.2 gyan full_build
> 测试源：`third_party/AutoTranscriber/test_audio/chord_progression.wav`（2.70s），经 ffmpeg 转出各格式

## 1. 上游原生能力实测（直调 `AutoTranscriber.load_audio`）

| 格式 | 扩展名 | 上游直读 | 失败原因 |
|---|---|---|---|
| WAV | `.wav` | ✅ OK | — |
| MP3 | `.mp3` | ✅ OK | — |
| OGG | `.ogg` | ✅ OK | — |
| Opus | `.opus` | ✅ OK | — |
| AIFF | `.aiff` | ✅ OK | — |
| **M4A** | `.m4a` | ❌ **FAIL** | `LibsndfileError: Format not recognised` |
| **AAC** | `.aac` | ❌ **FAIL** | 同上 |
| **WMA** | `.wma` | ❌ **FAIL** | 同上 |

**关键事实**：`third_party/AutoTranscriber/AutoTranscriber/audio_loader.py:44-50` 直接调
`librosa.load(file_path, sr=sr, mono=mono, ...)`。librosa 1.0 的默认加载后端是
soundfile（libsndfile），**不经过 ffmpeg，也不经过 audioread 的 ffmpeg 后端**。

而该文件 `:12-22` 的 docstring 明确声称支持 M4A / AAC / WMA / MP4 / AU——
**文档与实现不符**。这不是我方环境问题，是上游在 librosa 0.10 时代写的声明，
在 librosa 1.0 后端变更后失效，且未加 fallback。

### 为什么这三个格式必须救
- **m4a**：网易云音乐、Apple Music、iTunes 的下载缓存主流格式。主上从音乐平台
  拿到音频后直接拖入的概率极高。
- **aac**：部分手机录音、播客导出
- **wma**：Windows Media 遗留格式，部分老音源库仍在用

任一失败都直接违反需求 G4「主流格式通吃」。

## 2. 我方兜底方案（`engine/format_guard.py`，守住红线 B-1）

**不改上游一字**，在适配层做格式守卫：

```
用户文件 → format_guard.validate_audio_file()   # 廉价校验，挡掉坏输入
        → 扩展名在 NATIVE_EXTS？                  # 实测可信白名单
             是 → 直读上游（零拷贝）
             否 → ffmpeg 转码 → 44.1kHz/16-bit PCM WAV → 交上游
```

### 白名单划分
- `NATIVE_EXTS`（零拷贝）：`.wav .mp3 .flac .ogg .oga .opus .aiff .aif .aifc .au .snd`
- `TRANSCODE_EXTS`（转码）：`.m4a .mp4 .aac .wma .ape .alac .m4b .mpc .tta .wv`

### 转码参数
`-ar 44100 -ac 2 -c:a pcm_s16le -f wav`

**参数选择理由**：demucs 训练采样率为 44.1kHz，librosa 亦以此为原生；保持
44.1k 输出可避免上游再重采样造成二次损失。`-ac 2` 保留立体声供 demucs 使用
（其内部负责转单声道）。16-bit PCM 是 librosa/soundfile 的零争议输入格式。

**踩坑记录**：编码器最初写 `pcm_s16bit`，本机 ffmpeg 报
`Unknown encoder 'pcm_s16bit'`。`ffmpeg -encoders` 实查该 gyan full_build 仅提供
`pcm_s16le` / `pcm_s16be` / `pcm_s24le` 等，**无 `pcm_s16bit` 别名**。WAV 封装器
默认按小端解释 PCM，故改用 `pcm_s16le`。教训：ffmpeg 编码器名不可凭记忆，
不同构建差异大，必须实查。

## 3. 兜底后复测（7/7 全通）

| 格式 | 结果 | 时长 | 走转码 |
|---|---|---|---|
| aac | ✅ OK | 2.79s | 是 |
| aiff | ✅ OK | 2.70s | 否 |
| m4a | ✅ OK | 2.74s | 是 |
| mp3 | ✅ OK | 2.70s | 否 |
| ogg | ✅ OK | 2.70s | 否 |
| opus | ✅ OK | 2.70s | 否 |
| wma | ✅ OK | 2.69s | 是 |

时长差（2.69~2.79s）源于各格式的帧对齐与编码器延迟，属正常，不影响扒谱。

## 4. 异常输入对抗测试（8/8 全拦）

| 输入 | 拦截提示 |
|---|---|
| 不存在的文件 | 文件不存在，可能已被移动或删除。 |
| 传入目录 | 这是一个文件夹，不是音频文件。 |
| 0 字节空文件 | 文件是空的（0 字节），请换一个音频文件。 |
| 500 字节过小 | 文件只有 500 字节，太小，不可能是有效的音频。 |
| 纯文本伪装 .mp3 | （先被体积校验拦下） |
| 伪造 RIFF 头的 .wav | 音频读取失败：LibsndfileError: ...（含底层原因） |
| 不支持的扩展名 | 暂不支持 .xyz 格式。已支持：… |
| 中文 + 空格路径 | 正常处理（路径含 `中文 空格 测试.wav` 走通全流程） |

**全部提示为中文面向用户文案，不抛 Python 堆栈**（对应需求 B2 / G4）。

## 5. 补测与未覆盖（诚实记录）

### 补测通过
| 格式 | 结果 | 走转码 |
|---|---|---|
| FLAC `.flac` | ✅ OK 2.70s | 否（soundfile 原生） |
| WavPack `.wv` | ✅ OK 2.70s | 是 |

### 本机 ffmpeg 无编码器，无法生成测试样本
`.ape`（Monkey's Audio）、`.mpc`（Musepack）——本机 ffmpeg 构建未编入对应
**编码器**（这只影响我造测试样本，不影响我方**解码**能力：解码器与编码器是
两套东西，主流 full_build 一般含 ape/mpc 解码器）。这两个格式仍在
`TRANSCODE_EXTS` 白名单中，首次真实遇到时验证；未验证前不作任何保证。

### 明确不支持
- **DRM 保护文件**（m4p / 加密 aac）：转码必然失败，给出中文提示。技术上
  不可绕过，属主上侧限制，非缺陷。
- **视频文件**（mp4/mkv 容器）：`.mp4` 在白名单内但仅取其音轨；若容器无音轨
  则转码失败并提示。

## 6. 附带发现：pretty_midi API 变体（本机实测）

本机 `pretty_midi 0.2.11.post0` 的 `PrettyMIDI` 对象**没有** `get_duration()` /
`get_tempo()` / `end_time`，实际可用的是：

| 惯用写法 | 本机实际可用 |
|---|---|
| `m.get_duration()` | `m.get_end_time()` |
| `m.get_tempo()` | `m.estimate_tempo()` / `m.get_tempo_changes()` |

**影响**：写验证脚本 / 读回 MIDI 时勿凭记忆用 API。已在 `engine/` 的验证工具中
按本机 API 编写。若日后换环境出现同名方法缺失，属版本差异非代码缺陷。

## 7. 内核能力实测（端到端跑通上游）

命令：
```
engine/.venv/Scripts/python.exe third_party/AutoTranscriber/main.py \
  -i third_party/AutoTranscriber/test_audio/chord_progression.wav \
  -o .tmp/probe_out.mid --n_peaks 4
```
输出：`检测到 9 个节奏起始点` → `✅ MIDI 已保存 (9 个音符)`

读回验证（pretty_midi）：
```
tempo 70.8  end_time 2.67
track0 name='' program=0 notes=9
   pitch=64 (+4st) start=0.00 end=0.79 vel=38
   pitch=67 (+7st) start=0.00 end=0.79 vel=31
   pitch=60 ( 0st) start=0.00 end=0.81 vel=42
   pitch=67 (+7st) start=0.90 end=1.70 vel=24
   pitch=55 (-5st) start=0.84 end=1.72 vel=47
```

**结论**：扒谱内核在 RTX 3080 环境下可用，音符的 pitch/start/end/velocity 均正常，
和弦音（E4/G4/C4 同时起）被正确识别为多音高。

### 上游缺陷 #3（已确认，需适配层补）
`third_party/AutoTranscriber/AutoTranscriber/midi_writer.py` 全文**无任何
`name=` 赋值**（`:13` `pretty_midi.Instrument(program=program)`、
`:330` 同）。故产出 MIDI 的**轨名恒为空字符串 `''`**，实测确认。

**影响**：MuseScore 打开时两轨都显示为默认轨名，主上无法分辨哪轨是人声、
哪轨是伴奏——直接损害需求 G6「MuseScore 可用，轨名正确」。

**我方解法（不改上游）**：适配层用 pretty_midi 读回上游产出，按轨道顺序
重新赋名（人声轨 → `人声 Voice`、伴奏轨 → `伴奏 Accompaniment`）与 program
（人声 → 52 Choir Aahs / 53 Voice Oohs，伴奏 → 0 Piano）后重写。
详见 `engine/` 的轨道命名映射表。
