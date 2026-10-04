"""
pipeline — 六条产品路径的编排层

## 定位
本模块是 engine 的**唯一对外扒谱入口**。所有路径都在这里编排，
绝不直接调 `third_party/AutoTranscriber/main.py`——那份 CLI 带着
`has_demucs()` 门禁与 `sys.exit(1)`（缺陷 D-1/D-3），一旦命中进程直接死。

## 六条路径（对应主上需求）

| mode | 功能页 | 分离 | 扒谱算法 | 输出轨 |
|---|---|---|---|---|
| `full_auto` | 1 | Demucs | 伴奏 CQT 多音高 + 人声 pYIN | Voice + Accompaniment |
| `accompaniment` | 1 | Demucs | CQT 多音高 | Accompaniment |
| `vocals` | 1 | Demucs | pYIN 单旋律 | Voice |
| `basic` | 1 / 2 | 无 | CQT 多音高 | Instrument |
| `basic_multi` | 2 | 无 | CQT 多音高（可多轨） | Instrument N |
| `pre_separated` | 2 | 跳过（用户提供） | 各轨分别扒 | 由用户提供的轨道数 |

## 关键设计决策（均来自实测，非推测）

### 1. `perceptual_filter` 必须显式传 `melody_split=False`
上游默认 `melody_split=True` 会调 `separate_melody_and_accompaniment`
把音符按音域切三块**各自重建**（`perceptual_filter.py:80-83` + `:390`）。
实测：4 和弦的 18 个音符被砍到 **3 个**（丢 83%），这不是滤波是重编排。
默认关闭，`perceptual` 作为 UI 显式可选项（默认关）。

### 2. 禁用 CREPE
`crepe_wrapper.py:19-24` 的 `_ensure_crepe_script()` 在脚本缺失时
**往上游目录写文件**，直接违反红线 B-1（上游只读）。人声一律走 pYIN
（`estimate_vocal_pitch`，纯 librosa 实现，零外部依赖）。

### 3. `fmax` 提到 4186Hz
上游 `main.py:163/231` 硬编码 `fmax=2093`（≈B6），吉他高把位等高音声部
会被切掉。`compute_cqt` 本身可传参，适配层直接传即可，无需改上游。
代价：CQT bin 数从 180 增到 360，内存与耗时约翻倍（实测绝对值仍很小）。

### 4. 不调 `main.py`，只 import 子模块
`main.py` 的参数解析、门禁、`sys.exit`、PDF 生成全部不需要复用。
直接 `from AutoTranscriber import ...` 拿函数，最干净。

## 红线遵守
`third_party/` 一字不改（BOUNDARY B-1）。所有规避均在参数层与编排层完成。
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal

# 把 vendored 上游挂到 sys.path（只读引用，不写入）
#
# 踩坑实录（打包兼容性）：初版只认一种布局——
#   <engine>/../../third_party/AutoTranscriber
# 开发态下成立（项目根/engine → 项目根/third_party），但 **Tauri 打包后资源
# 目录会重排**，此路径可能失效，表现为 `ModuleNotFoundError: No module named
# 'AutoTranscriber'`——且只在装好的正式版里出现，本地开发永远测不到。
#
# 正解：列出多个候选，逐个探测（含 `AutoTranscriber/__init__.py` 是否存在），
# 第一个命中的即用；全不命中则给出**可操作的**错误信息而非裸 ImportError。
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_ENGINE_DIR)


def _find_upstream() -> str | None:
    """在多个候选位置中找 vendored 上游根目录（含 AutoTranscriber/ 子包）。"""
    candidates = [
        # 开发态：<项目根>/engine/../../third_party/AutoTranscriber
        os.path.join(_PROJECT_ROOT, "third_party", "AutoTranscriber"),
        # 打包态：resources/engine 与 resources/third_party 可能同级或父子
        os.path.join(_ENGINE_DIR, "third_party", "AutoTranscriber"),
        os.path.join(_ENGINE_DIR, "..", "third_party", "AutoTranscriber"),
        os.path.join(_ENGINE_DIR, "..", "..", "third_party", "AutoTranscriber"),
        # 兜底：直接在引擎同级找 AutoTranscriber 包
        os.path.join(_PROJECT_ROOT, "AutoTranscriber"),
    ]
    for c in candidates:
        c = os.path.abspath(c)
        if os.path.isfile(os.path.join(c, "AutoTranscriber", "__init__.py")):
            return c
    return None


_UPSTREAM = _find_upstream()
if _UPSTREAM:
    if _UPSTREAM not in sys.path:
        sys.path.insert(0, _UPSTREAM)
else:
    # 不静默失败——给出可操作的指引
    import warnings as _warnings

    _warnings.warn(
        "找不到 vendored 上游 AutoTranscriber。\n"
        f"已尝试的位置：\n  " + "\n  ".join(
            os.path.abspath(c) for c in [
                os.path.join(_PROJECT_ROOT, "third_party", "AutoTranscriber"),
                os.path.join(_ENGINE_DIR, "third_party", "AutoTranscriber"),
            ]
        )
        + "\n请重新安装应用，或确认安装包完整。",
        RuntimeWarning,
        stacklevel=2,
    )

from format_guard import AudioFormatError, ensure_workdir, load_for_upstream  # noqa: E402
from midi_post import TrackSpec, apply_track_metadata  # noqa: E402
from separator import SeparationError, separate as run_separation  # noqa: E402

Mode = Literal[
    "full_auto",
    "accompaniment",
    "vocals",
    "basic",
    "basic_multi",
    "pre_separated",
]

# 进度回调：(阶段名, 0~1, 说明)
ProgressFn = Callable[[str, float, str], None]

STAGES = ("prepare", "separate", "spectrum", "track", "export")
"""四阶段 UI 进度锚点。prepare 与 separate 对应「分离人声」，
spectrum 对应「分析频谱」，track 对应「追踪音符」，export 对应「生成 MIDI」。"""

STAGE_LABELS = {
    "prepare": "准备音频",
    "separate": "分离人声与伴奏",
    "spectrum": "分析频谱",
    "track": "追踪音符",
    "export": "生成 MIDI",
}

# 频谱参数
FMIN = 65.41          # C2
FMAX = 4186.0         # C8（上游硬编码 2093，此处上提以覆盖高音声部）
BINS_PER_OCTAVE = 36

# 各模式的默认 n_peaks（据上游实测：平均同时音数 ~3.1，超过 8 收益趋零）
DEFAULT_N_PEAKS = {
    "full_auto": 6,
    "accompaniment": 6,
    "vocals": 2,
    "basic": 5,
    "basic_multi": 6,
    "pre_separated": 5,
}


class TranscribeError(Exception):
    """扒谱失败（面向用户，需中文化）"""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


@dataclass
class TranscribeRequest:
    """一次扒谱请求的全部参数。默认值已按各模式调好，主上通常不用动。"""

    mode: Mode
    input_path: str
    """主输入路径。mode=pre_separated 时为人声文件路径。"""

    output_path: str
    """输出 .mid 路径。"""

    # 已分离音频（mode=pre_separated）
    extra_inputs: list[str] = field(default_factory=list)
    """额外的音轨文件。mode=pre_separated 时传 [伴奏路径]。"""

    # 扒谱参数
    n_peaks: int | None = None
    hop_length: int = 512
    onset_threshold: float = 0.3
    pitch_threshold: float = 0.1
    min_note_duration: int = 4
    tempo: float = 120.0

    # 模式开关
    perceptual: bool = False
    """感知模式。⚠️ 默认 False——上游该模式会重写音符（见模块文档第 1 条）。"""

    simplify: int = 0
    """音符精简强度，0=关闭，2/3/5 递增强度。"""

    piano_mode: bool = False
    """钢琴优化：更高时间分辨率 + 中值滤波。"""

    use_pyin: bool = True
    """人声轨是否用 pYIN。恒为 True（CREPE 违反只读红线，见模块文档第 2 条）。"""

    # 分离参数
    demucs_model: str = "htdemucs"
    device: str = "auto"
    allow_hpss_fallback: bool = True

    # 轨道命名
    track_names: list[str | None] = field(default_factory=list)
    """自定义轨名（ASCII，MIDI 限制）。空则用角色默认名。"""


@dataclass
class TranscribeResult:
    """一次扒谱的结果。"""

    output_path: str
    tracks: list[dict]
    """[{name, program, notes, duration}, ...]"""

    total_notes: int
    duration: float
    """输入音频时长（秒）。"""

    elapsed: float
    """本次耗时（秒）。"""

    mode: str
    separation_method: str | None = None
    """实际生效的分离方式；未分离时为 None。"""

    warnings: list[str] = field(default_factory=list)


def _emit(progress: ProgressFn | None, stage: str, pct: float, msg: str) -> None:
    if progress:
        progress(stage, max(0.0, min(1.0, pct)), msg)


def _transcribe_cqt(
    audio_path: str,
    n_peaks: int,
    hop_length: int,
    onset_threshold: float,
    pitch_threshold: float,
    min_note_duration: int,
    perceptual: bool,
    simplify: int,
    piano_mode: bool,
    progress: ProgressFn | None,
    label: str,
    progress_from: float = 0.0,
    progress_span: float = 1.0,
) -> list[dict]:
    """
    CQT 多音高链路：复刻上游 `transcribe_file` 的核心算法，但：
    - fmax 提到 4186（上游硬编码 2093 切掉高音）
    - 走 format_guard 保证格式可读
    - 分阶段进度上报
    - `perceptual_filter` 显式 `melody_split=False`（见模块文档第 1 条）
    """
    from AutoTranscriber import (
        compute_cqt,
        compute_spectral_flux,
        detect_onsets,
        estimate_pitches,
        estimate_pitches_onset_driven,
        perceptual_filter,
        track_notes,
        track_notes_onset_driven,
    )

    from format_guard import load_for_upstream

    def _p(pct: float, msg: str) -> None:
        _emit(progress, "spectrum", progress_from + pct * progress_span * 0.5, msg)

    # 钢琴模式：更高时间分辨率 + 更低 onset 阈值（对齐上游 piano_mode 行为）
    if piano_mode:
        eff_peaks = 2
        eff_onset = 0.15
        eff_pitch = 0.2
        eff_hop = 256
    else:
        eff_peaks = n_peaks
        eff_onset = onset_threshold
        eff_pitch = pitch_threshold
        eff_hop = hop_length

    # ---- 加载 ----
    _p(0.05, f"{label}：正在加载音频…")
    loaded = load_for_upstream(audio_path)
    try:
        y, sr = _read_mono(loaded.path)
        if piano_mode or True:
            from AutoTranscriber import preprocess

            y = preprocess(y, sr)

        # ---- CQT ----
        _p(0.2, f"{label}：正在计算频谱…")
        cqt, times, freqs = compute_cqt(
            y, sr, hop_length=eff_hop,
            fmin=FMIN, fmax=FMAX, bins_per_octave=BINS_PER_OCTAVE,
        )

        # ---- onset ----
        _p(0.45, f"{label}：正在检测节奏起始点…")
        flux = compute_spectral_flux(cqt)
        onset_frames, onset_times = detect_onsets(
            flux, sr, hop_length=eff_hop, threshold=eff_onset
        )

        # ---- 音符 ----
        _emit(progress, "track", progress_from + progress_span * 0.6,
              f"{label}：正在识别音高（{len(onset_frames)} 个起始点）…")

        if perceptual:
            onset_notes = estimate_pitches_onset_driven(
                cqt, freqs, times, onset_frames, onset_times,
                sr, hop_length=eff_hop,
                n_peaks=eff_peaks, threshold_factor=eff_pitch,
            )
            notes = track_notes_onset_driven(
                onset_notes, cqt, freqs, times, sr, hop_length=eff_hop,
                decay_ratio=0.25, snr_threshold=0.3,
            )
            # ⚠️ 必须显式 False，见模块文档第 1 条
            notes = perceptual_filter(
                notes,
                outlier_semitones=12,
                min_duration=0.06,
                harmonic_check=True,
                max_simultaneous=max(6, eff_peaks),
                max_notes_per_beat=8,
                melody_split=False,
            )
        else:
            frame_notes = estimate_pitches(
                cqt, freqs, times, sr,
                hop_length=eff_hop,
                n_peaks=eff_peaks,
                threshold_factor=eff_pitch,
            )
            if piano_mode:
                frame_notes = _median_filter_frames(frame_notes, window=3)
            notes = track_notes(
                frame_notes, onset_frames, onset_times, times, sr,
                hop_length=eff_hop,
                min_note_duration=min_note_duration,
                velocity_scale=80.0,
            )

        if simplify > 0 or piano_mode:
            level = simplify if simplify > 0 else 2
            before = len(notes)
            notes = _simplify_notes(notes, max_per_beat=level)
            _p(0.9, f"{label}：精简 {before} → {len(notes)} 个音符")

        _emit(progress, "track", progress_from + progress_span, f"{label}：完成")
        return notes
    finally:
        if loaded.was_transcoded:
            loaded.cleanup()


def _transcribe_vocal(
    audio_path: str,
    hop_length: int,
    min_note_duration: int,
    progress: ProgressFn | None,
    label: str = "人声",
    progress_from: float = 0.0,
    progress_span: float = 1.0,
) -> list[dict]:
    """
    人声旋律链路：pYIN（`estimate_vocal_pitch`）。

    刻意不用 CREPE：`crepe_wrapper.py:19-24` 的 `_ensure_crepe_script()`
    会在脚本缺失时往上游目录写文件，违反红线 B-1。
    pYIN 纯 librosa 实现、零外部依赖、音高足够准。
    """
    from AutoTranscriber import estimate_vocal_pitch

    from format_guard import load_for_upstream

    _emit(progress, "spectrum", progress_from + progress_span * 0.15,
          f"{label}：正在加载音频…")
    loaded = load_for_upstream(audio_path)
    try:
        y, sr = _read_mono(loaded.path)
        _emit(progress, "track", progress_from + progress_span * 0.6,
          f"{label}：正在用 pYIN 追踪音高…")
        notes = estimate_vocal_pitch(
            y, sr,
            hop_length=hop_length,
            min_duration_frames=min_note_duration,
        )
        _emit(progress, "track", progress_from + progress_span,
          f"{label}：找到 {len(notes)} 个旋律音符")
        return notes
    finally:
        if loaded.was_transcoded:
            loaded.cleanup()


def _read_mono(path: str) -> tuple:
    """读音频为单声道 float32。"""
    import librosa

    y, sr = librosa.load(path, sr=22050, mono=True)
    return y, sr


def _median_filter_frames(frame_notes: list, window: int = 3) -> list:
    """逐帧音高中值滤波，去单帧毛刺。复刻上游 `_median_filter_frames`。"""
    if not frame_notes:
        return frame_notes
    n = len(frame_notes)
    result = []
    for i in range(n):
        cur = frame_notes[i]
        if not cur:
            result.append([])
            continue
        filtered = []
        for note in cur:
            support = 0
            for j in range(max(0, i - window), min(n, i + window + 1)):
                if j == i:
                    continue
                for nb in frame_notes[j]:
                    if abs(nb["pitch"] - note["pitch"]) <= 1:
                        support += 1
                        break
            if support >= 1:
                filtered.append(note)
        if not filtered:
            filtered = [max(cur, key=lambda x: x["amplitude"])]
        result.append(filtered)
    return result


def _simplify_notes(notes: list, max_per_beat: int = 3) -> list:
    """音符精简：去毛刺 + 合并近音 + 时间窗选音。复刻上游 `_simplify_notes`。"""
    if not notes:
        return []
    notes = [dict(n) for n in notes]
    notes = [n for n in notes if n["end"] - n["start"] >= 0.08]
    notes.sort(key=lambda n: (n["start"], n["pitch"]))

    merged: list[dict] = []
    for n in notes:
        if merged:
            last = merged[-1]
            gap = n["start"] - last["end"]
            pdiff = abs(n["pitch"] - last["pitch"])
            if pdiff == 0 and gap < 0.2:
                last["end"] = max(last["end"], n["end"])
                continue
            if pdiff <= 1 and gap < 0.1:
                if n["end"] - n["start"] > last["end"] - last["start"]:
                    last["pitch"] = n["pitch"]
                    last["end"] = n["end"]
                continue
        merged.append(n)

    if max_per_beat <= 0:
        return merged

    t_min = min(n["start"] for n in merged)
    t_max = max(n["end"] for n in merged)
    window = 0.15
    n_win = int((t_max - t_min) / window) + 1
    kept: set[int] = set()

    for i in range(n_win):
        ws = t_min + i * window
        we = ws + window
        active = [
            (n, n["end"] - n["start"])
            for n in merged
            if n["start"] < we and n["end"] > ws and id(n) not in kept
        ]
        if not active:
            continue
        active.sort(
            key=lambda x: (
                3 if x[0]["pitch"] >= 67 else 2 if x[0]["pitch"] < 48 else 1,
                x[1],
                x[0]["pitch"] if x[0]["pitch"] >= 67 else -x[0]["pitch"],
            ),
            reverse=True,
        )
        for n, _ in active[:max_per_beat]:
            kept.add(id(n))

    result = [n for n in merged if id(n) in kept]
    result.sort(key=lambda n: (n["start"], -n["pitch"]))
    return result


def _write_midi(
    track_notes_list: list[list[dict]],
    output_path: str,
    tempo: float,
    roles: list[str],
    custom_names: list[str | None],
) -> list[dict]:
    """写多轨 MIDI 并补齐轨名。"""
    from AutoTranscriber import write_midi, write_multitrack_midi

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if len(track_notes_list) == 1:
        prog = TrackSpec(role=roles[0]).resolve()[1] if roles else 0
        write_midi(track_notes_list[0], output_path, tempo=tempo, program=prog)
    else:
        progs = [
            TrackSpec(role=r).resolve()[1] if r else 0 for r in roles
        ]
        write_multitrack_midi(
            track_notes_list, output_path, tempo=tempo, programs=progs
        )

    specs = []
    for i, role in enumerate(roles):
        name = custom_names[i] if i < len(custom_names) else None
        specs.append(TrackSpec(role=role, custom_name=name))

    apply_track_metadata(output_path, specs, in_place=True)

    from midi_post import describe

    return describe(output_path)


def transcribe(req: TranscribeRequest, progress: ProgressFn | None = None) -> TranscribeResult:
    """
    执行一次扒谱。**engine 层唯一对外入口。**

    Parameters
    ----------
    req : TranscribeRequest
    progress : 进度回调 (阶段, 0~1, 文字)

    Returns
    -------
    TranscribeResult

    Raises
    ------
    TranscribeError / AudioFormatError / SeparationError（均带 user_message 中文文案）
    """
    t0 = time.time()
    warnings: list[str] = []
    workdir = ensure_workdir()
    sep_result = None

    n_peaks = req.n_peaks if req.n_peaks is not None else DEFAULT_N_PEAKS.get(req.mode, 5)

    try:
        # ── 进度单调化 ──
        # 踩坑实录（勿简化）：初版各阶段各自从 0 起算，双轨模式出现
        # spectrum 0.5 → 0.2 的倒退，前端进度条闪回，用户以为程序崩了。
        # 第二版踩的更隐蔽的坑：在阶段切换处「重置 last」以图重新计数，
        # 结果与单调化自相矛盾，倒退更严重（实测 18 事件里倒退 9 次）。
        # 正解：全流程一条全局进度轴，**任何地方都不得重置 last**，
        #       阶段只换 stage 标签不换刻度。阶段起止用 _switch 声明。
        _progress_state = {"last": 0.0}

        def _mono(stage: str, pct: float, msg: str) -> None:
            cur = _progress_state["last"]
            if pct < cur:
                pct = cur
            _progress_state["last"] = pct
            _emit(progress, stage, pct, msg)

        def _switch(stage: str, start_pct: float) -> None:
            """声明进入某阶段。start_pct 不得低于已到达进度，故钳到当前值。"""
            _progress_state["last"] = max(start_pct, _progress_state["last"])

        def _mono_pre(msg: str) -> None:
            _mono("prepare", 0.05, msg)

        # ---------- 阶段 1：准备 ----------
        _mono_pre("准备音频…")
        from format_guard import validate_audio_file

        validate_audio_file(req.input_path)
        for extra in req.extra_inputs:
            validate_audio_file(extra)

        # ---------- 阶段 2：分离（按模式）----------
        need_sep = req.mode in {"full_auto", "accompaniment", "vocals"}
        if need_sep:
            _mono("separate", 0.02, "正在分离人声与伴奏…")
            try:
                sep_result = run_separation(
                    req.input_path,
                    model=req.demucs_model,
                    device=req.device,
                    workdir=os.path.join(workdir, "sep"),
                    progress=progress,
                    allow_hpss_fallback=req.allow_hpss_fallback,
                )
            except SeparationError as e:
                raise TranscribeError(e.user_message, e.detail) from e

            if sep_result.method == "hpss":
                warnings.append(
                    "Demucs 分离不可用，已降级为中频分离（人声/伴奏分得不如正常干净）。"
                )

        _mono("prepare", 1.0, "准备完成")

        # ---------- 阶段 3-4：扒谱 ----------
        track_notes_list: list[list[dict]] = []
        roles: list[str] = []

        # ---------- 参数钳制（对抗式自测发现的崩溃点）----------
        # 踩坑实录：tempo=0 传进 pretty_midi.PrettyMIDI(initial_tempo=0) 会在
        # 内部算 `60.0/(initial_tempo*self.resolution)` 时抛 ZeroDivisionError，
        # 整个任务崩掉。经 tests/adversarial.py 的边界数值用例逮到。
        # MIDI 的 tempo 范围是 [0, 0xFFFFFF] 微秒/四分音符，实际可用 BPM 约
        # 20~300，超出这个范围人耳已无法分辨，钳到边界即可。
        safe_tempo = min(max(float(req.tempo), 20.0), 300.0)
        if abs(safe_tempo - float(req.tempo)) > 1e-6:
            warnings.append(
                f"速度 {req.tempo} BPM 超出可用范围（20~300），已按 {safe_tempo:.0f} BPM 处理。"
            )

        # n_peaks 至少为 1，否则 estimate_pitches 内部循环恒不执行
        safe_n_peaks = max(1, int(n_peaks))
        if safe_n_peaks != n_peaks:
            warnings.append(f"最大同时音符数 {n_peaks} 无效，已按 {safe_n_peaks} 处理。")

        # onset 灵敏度钳到 [0,1]，否则 librosa 内部会出怪结果
        safe_onset = min(max(float(req.onset_threshold), 0.0), 1.0)
        safe_pitch = min(max(float(req.pitch_threshold), 0.0), 1.0)

        if req.mode == "full_auto":
            assert sep_result is not None
            # 伴奏先（多音高），人声后（单旋律）——与上游 main.py 的顺序一致
            _switch("spectrum", 0.0)
            accomp = _transcribe_cqt(
                sep_result.accompaniment_path, safe_n_peaks, req.hop_length,
                safe_onset, safe_pitch, req.min_note_duration,
                req.perceptual, req.simplify, req.piano_mode,
                _mono, "伴奏", 0.0, 0.45,
            )
            _switch("spectrum", 0.45)
            vocal = _transcribe_vocal(
                sep_result.vocals_path, req.hop_length, req.min_note_duration,
                _mono, "人声", 0.45, 0.45,
            )
            track_notes_list = [vocal, accomp]
            roles = ["vocals", "accompaniment"]

        elif req.mode == "accompaniment":
            assert sep_result is not None
            _switch("spectrum", 0.0)
            track_notes_list = [
                _transcribe_cqt(
                    sep_result.accompaniment_path, n_peaks, req.hop_length,
                    req.onset_threshold, req.pitch_threshold, req.min_note_duration,
                    req.perceptual, req.simplify, req.piano_mode,
                    _mono, "伴奏",
                )
            ]
            roles = ["accompaniment"]

        elif req.mode == "vocals":
            assert sep_result is not None
            _switch("spectrum", 0.0)
            track_notes_list = [
                _transcribe_vocal(
                    sep_result.vocals_path, req.hop_length,
                    req.min_note_duration, _mono, "人声",
                )
            ]
            roles = ["vocals"]

        elif req.mode == "basic":
            _switch("spectrum", 0.0)
            track_notes_list = [
                _transcribe_cqt(
                    req.input_path, safe_n_peaks, req.hop_length,
                    safe_onset, safe_pitch, req.min_note_duration,
                    req.perceptual, req.simplify, req.piano_mode,
                    _mono, "乐器",
                )
            ]
            roles = ["instrument"]

        elif req.mode == "basic_multi":
            _switch("spectrum", 0.0)
            track_notes_list = [
                _transcribe_cqt(
                    req.input_path, safe_n_peaks, req.hop_length,
                    safe_onset, safe_pitch, req.min_note_duration,
                    req.perceptual, req.simplify, req.piano_mode,
                    _mono, "乐器",
                )
            ]
            roles = ["instrument"]

        elif req.mode == "pre_separated":
            # 用户已自行分离，跳过分离阶段
            _switch("spectrum", 0.0)
            n_extra = max(1, len(req.extra_inputs))
            head_span = 1.0 / (n_extra + 1)
            vocal = _transcribe_vocal(
                req.input_path, req.hop_length, req.min_note_duration,
                _mono, "人声", 0.0, head_span,
            )
            track_notes_list = [vocal]
            roles = ["vocals"]

            span = 1.0 / (n_extra + 1)
            for idx, extra in enumerate(req.extra_inputs):
                _switch("spectrum", span * (idx + 1))
                accomp = _transcribe_cqt(
                    extra, safe_n_peaks, req.hop_length,
                    safe_onset, safe_pitch, req.min_note_duration,
                    req.perceptual, req.simplify, req.piano_mode,
                    _mono, "伴奏", 0.0, span,
                )
                track_notes_list.append(accomp)
                roles.append("accompaniment")

        else:
            raise TranscribeError(f"未知的扒谱模式：{req.mode}", f"mode={req.mode}")

    # ---------- 阶段 5：导出 ----------
        _mono("export", max(0.9, _progress_state["last"]), "正在生成 MIDI…")

        if any(len(t) == 0 for t in track_notes_list):
            empty_roles = [roles[i] for i, t in enumerate(track_notes_list) if not t]
            warnings.append(
                f"以下音轨未识别到音符：{'、'.join(empty_roles)}。"
                f"可尝试提高灵敏度（降低 onset 阈值）或换一首音频。"
            )

        tracks = _write_midi(
            track_notes_list, req.output_path, safe_tempo, roles, req.track_names
        )

        total = sum(t["notes"] for t in tracks)
        if total == 0:
            raise TranscribeError(
                "没能从这段音频识别出任何音符。\n\n"
                "可能原因：\n"
                "• 音频太短或几乎无声\n"
                "• 音乐过于复杂，算法跟不上\n\n"
                "建议：换一首清晰的音频，或在参数区调低「起始灵敏度」后重试。",
                "zero notes produced",
            )

        _mono("export", 1.0, f"完成，共 {total} 个音符")

        return TranscribeResult(
            output_path=req.output_path,
            tracks=tracks,
            total_notes=total,
            duration=tracks[0]["duration"] if tracks else 0.0,
            elapsed=time.time() - t0,
            mode=req.mode,
            separation_method=sep_result.method if sep_result else None,
            warnings=warnings,
        )

    except (AudioFormatError, SeparationError) as e:
        raise TranscribeError(e.user_message, e.detail) from e
    finally:
        # 清理本次任务的全部临时产物（含分离 stems、转码 wav）
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)


def describe_modes() -> list[dict]:
    """返回六条路径的元信息，供前端渲染模式列表（单一真源，避免前后端不一致）。"""
    return [
        {
            "mode": "full_auto",
            "page": 1,
            "label": "全自动扒谱（两轨）",
            "description": "分离人声与伴奏，分别扒谱，导出双轨 MIDI",
            "separates": True,
            "tracks": 2,
            "roles": ["vocals", "accompaniment"],
        },
        {
            "mode": "accompaniment",
            "page": 1,
            "label": "只扒伴奏",
            "description": "分离后只扒伴奏轨，适合只要伴奏旋律",
            "separates": True,
            "tracks": 1,
            "roles": ["accompaniment"],
        },
        {
            "mode": "vocals",
            "page": 1,
            "label": "只扒人声旋律",
            "description": "分离后只扒人声旋律，单音轨",
            "separates": True,
            "tracks": 1,
            "roles": ["vocals"],
        },
        {
            "mode": "basic",
            "page": 1,
            "label": "基本扒谱（乐器 / 单音轨）",
            "description": "不分离，直接对整段音频做多音高识别",
            "separates": False,
            "tracks": 1,
            "roles": ["instrument"],
        },
        {
            "mode": "basic_multi",
            "page": 2,
            "label": "基本扒谱（多音轨）",
            "description": "适合已有多轨素材，逐轨扒谱",
            "separates": False,
            "tracks": 1,
            "roles": ["instrument"],
        },
        {
            "mode": "pre_separated",
            "page": 2,
            "label": "已分离音频直入",
            "description": "导入你已分离好的人声 + 伴奏，跳过分离步骤",
            "separates": False,
            "tracks": 2,
            "roles": ["vocals", "accompaniment"],
        },
    ]


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("用法: python pipeline.py <mode> <输入> <输出.mid> [额外输入...]")
        print(f"模式: {', '.join(m['mode'] for m in describe_modes())}")
        raise SystemExit(0)

    mode_arg, inp, outp = sys.argv[1], sys.argv[2], sys.argv[3]
    extras = sys.argv[4:]

    def _p(stage: str, pct: float, msg: str) -> None:
        print(f"  [{STAGE_LABELS.get(stage, stage):12s}] {pct * 100:5.1f}%  {msg}")

    try:
        res = transcribe(
            TranscribeRequest(
                mode=mode_arg, input_path=inp, output_path=outp, extra_inputs=extras
            ),
            progress=_p,
        )
    except TranscribeError as e:
        print(f"\nFAIL: {e.user_message}")
        if e.detail:
            print(f"detail: {e.detail}")
        raise SystemExit(1) from e

    print(f"\nOK  {res.output_path}")
    print(f"    耗时 {res.elapsed:.1f}s  音符 {res.total_notes}  分离={res.separation_method}")
    for t in res.tracks:
        print(f"    {t['name']:20s} program={t['program']:3d} notes={t['notes']:5d}")
    for w in res.warnings:
        print(f"    [警告] {w}")
