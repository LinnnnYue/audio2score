"""
format_guard — 音频格式兜底加载层

## 为什么存在
上游 `AutoTranscriber.load_audio()` 直接调 `librosa.load()`。librosa 1.0 默认走
soundfile（libsndfile）后端，**不经过 ffmpeg**，导致其 docstring 声称支持的
m4a / aac / wma 实际全部读取失败。

实测（本机 librosa 1.0.0 + soundfile）：
    OK    wav mp3 flac ogg opus aiff
    FAIL  m4a aac wma        → LibsndfileError: Format not recognised

m4a 是网易云 / Apple Music 下载缓存的常见格式，属主上高频场景，不可缺失。

## 红线遵守
`third_party/AutoTranscriber/` 为只读 vendored 树（BOUNDARY B-1），**一字不改**。
解法全部落在此适配层：先探测，不在可靠白名单内的格式用 ffmpeg 转码为临时
44.1kHz 16-bit PCM WAV，再交给上游。ffmpeg 已随本机提供（5.1.2），亦为
Demucs / librosa 的常见依赖，缺失时给出明确中文提示。

## 可靠性白名单
只有经实测确认能被 soundfile 直读的格式才走零拷贝路径；其余一律转码。
宁可多一次转码（耗时 <1s），不可让主上拿到"格式不支持"的惊喜。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

# 经实测可被 soundfile 直读的格式（零拷贝路径）
NATIVE_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".aiff", ".aif", ".aifc", ".au", ".snd"}

# 需要 ffmpeg 转码的格式（上游声称支持但实测失败）
TRANSCODE_EXTS = {".m4a", ".mp4", ".aac", ".wma", ".wav.aac", ".ape", ".alac", ".m4b", ".mpc", ".tta", ".wv", ".aifc"}

# 界面上可以展示给用户的扩展名清单
SUPPORTED_EXTS = sorted(NATIVE_EXTS | TRANSCODE_EXTS | {".mp4", ".m4b"})

# ffmpeg 转码目标参数：44.1kHz 立体声 16-bit PCM WAV
# 理由：demucs 与 librosa 都以 44.1k 为原生训练采样率，最高保真，避免二次重采样损失
#
# 踩坑记录：曾写 `pcm_s16bit`，在本机 ffmpeg 5.1.2 (gyan full_build) 下报
# "Unknown encoder 'pcm_s16bit'"。该构建只提供 pcm_s16le / pcm_s16be，
# WAV 封装器默认按小端解释，故改用 pcm_s16le。
FFMPEG_TARGET = ["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-f", "wav"]

# 转码超时（秒）。3 分钟歌曲转 PCM WAV 通常 <2s，留足余量
FFMPEG_TIMEOUT = 120


class AudioFormatError(Exception):
    """音频无法加载 / 格式不受支持（面向用户，需中文化）"""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


@dataclass
class LoadedAudio:
    """加载结果。path 指向真实可被上游读取的文件（可能是临时转码产物）。"""

    path: str
    """交给上游的路径。若发生转码，这是临时 WAV 的路径。"""

    original_path: str
    """用户原始文件路径。"""

    sr: int
    duration: float
    was_transcoded: bool
    """True 表示原格式不被支持，已转码。"""

    temp_path: str | None = None
    """转码产生的临时文件路径，调用方 cleanup 时需删除。"""

    def cleanup(self) -> None:
        """删除临时转码文件。原文件永不触碰。"""
        if self.temp_path:
            try:
                os.unlink(self.temp_path)
            except OSError:
                pass


def find_ffmpeg() -> str | None:
    """定位 ffmpeg 可执行文件。优先 PATH，其次常见安装位置。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for candidate in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def is_supported(path: str) -> bool:
    """扩展名是否在支持清单内（不验证内容，仅查表）。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in NATIVE_EXTS or ext in TRANSCODE_EXTS


def needs_transcode(path: str) -> bool:
    """是否需要走 ffmpeg 转码。"""
    return os.path.splitext(path)[1].lower() not in NATIVE_EXTS


def validate_audio_file(path: str) -> None:
    """
    加载前的廉价校验，把三类常见坏输入挡在引擎之外，给出可读中文原因。
    对应需求 B2「异常路径全覆盖」。
    """
    if not os.path.exists(path):
        raise AudioFormatError("文件不存在，可能已被移动或删除。", f"path={path}")

    if os.path.isdir(path):
        raise AudioFormatError("这是一个文件夹，不是音频文件。", f"path={path}")

    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise AudioFormatError("无法读取该文件，可能没有访问权限。", str(e)) from e

    if size == 0:
        raise AudioFormatError("文件是空的（0 字节），请换一个音频文件。", f"path={path}")

    if size < 1024:
        raise AudioFormatError(
            f"文件只有 {size} 字节，太小，不可能是有效的音频。", f"path={path}"
        )

    if not is_supported(path):
        ext = os.path.splitext(path)[1] or "(无扩展名)"
        raise AudioFormatError(
            f"暂不支持 {ext} 格式。已支持：{'、'.join(SUPPORTED_EXTS)}",
            f"path={path}",
        )


def transcode_to_wav(src: str, workdir: str | None = None) -> str:
    """
    用 ffmpeg 把任意音频转成 44.1kHz 16-bit PCM WAV。

    Returns
    -------
    临时 wav 的绝对路径（调用方负责删除）
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise AudioFormatError(
            "该格式需要 ffmpeg 转码，但本机未找到 ffmpeg。\n"
            "请安装 ffmpeg 后重试：winget install Gyan.FFmpeg",
            "ffmpeg not found in PATH or common locations",
        )

    if workdir is None:
        workdir = tempfile.gettempdir()
    os.makedirs(workdir, exist_ok=True)

    out_path = os.path.join(workdir, f"fg_{uuid.uuid4().hex[:12]}.wav")

    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", src,
        *FFMPEG_TARGET,
        out_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as e:
        raise AudioFormatError(
            f"转码超时（超过 {FFMPEG_TIMEOUT} 秒），文件可能已损坏。", str(e)
        ) from e
    except FileNotFoundError as e:
        raise AudioFormatError("ffmpeg 无法启动，请检查安装是否完整。", str(e)) from e

    if proc.returncode != 0 or not os.path.isfile(out_path):
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise AudioFormatError(
            "音频解码失败，文件可能已损坏或不是有效的音频。\n"
            f"ffmpeg 报告：{stderr[:200] if stderr else '（无输出）'}",
            f"ffmpeg rc={proc.returncode}",
        )

    if os.path.getsize(out_path) == 0:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        raise AudioFormatError("转码结果为空，文件可能已损坏。", "empty output")

    return out_path


def load_for_upstream(
    path: str,
    sr: int = 22050,
    workdir: str | None = None,
) -> LoadedAudio:
    """
    把任意支持格式的音频转成「上游可读」的形式。

    这是 engine 层对上游的唯一入口：所有 pipeline 都必须先过这里，
    不得直接调 `AutoTranscriber.load_audio`。

    Parameters
    ----------
    path : 用户文件路径
    sr : 目标采样率（与上游默认一致，22050）
    workdir : 临时文件目录，默认系统 temp

    Returns
    -------
    LoadedAudio
    """
    validate_audio_file(path)

    if needs_transcode(path):
        wav = transcode_to_wav(path, workdir)
        try:
            y, real_sr = _read_via_librosa(wav, sr)
        except Exception:
            try:
                os.unlink(wav)
            except OSError:
                pass
            raise
        dur = len(y) / real_sr if real_sr else 0.0
        if dur <= 0:
            _safe_unlink(wav)
            raise AudioFormatError("音频时长为 0，无法处理。", f"path={path}")
        return LoadedAudio(
            path=wav,
            original_path=path,
            sr=real_sr,
            duration=dur,
            was_transcoded=True,
            temp_path=wav,
        )

    y, real_sr = _read_via_librosa(path, sr)
    dur = len(y) / real_sr if real_sr else 0.0
    if dur <= 0:
        raise AudioFormatError("音频时长为 0，无法处理。", f"path={path}")

    return LoadedAudio(
        path=path,
        original_path=path,
        sr=real_sr,
        duration=dur,
        was_transcoded=False,
        temp_path=None,
    )


def _read_via_librosa(path: str, sr: int) -> tuple:
    """薄封装 librosa.load，统一把底层异常翻译成 AudioFormatError。"""
    import librosa

    try:
        y, real_sr = librosa.load(path, sr=sr, mono=True)
    except Exception as e:  # noqa: BLE001 — 底层库异常种类繁杂，统一拦截中文化
        raise AudioFormatError(
            f"音频读取失败：{type(e).__name__}: {str(e)[:150]}",
            f"librosa.load failed on {path}",
        ) from e

    if y is None or len(y) == 0:
        raise AudioFormatError("音频内容为空。", f"path={path}")
    return y, real_sr


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def probe_duration(path: str) -> float:
    """
    轻量探测时长（秒），用于 UI 在用户拖入文件时立即显示信息。
    优先 soundfile，失败退到 ffmpeg，避免为了显示时长而完整解码。
    """
    validate_audio_file(path)

    try:
        import soundfile as sf

        info = sf.info(path)
        if info.samplerate:
            return float(info.frames) / float(info.samplerate)
    except Exception:  # noqa: BLE001 — 探测失败不致命，走 ffmpeg
        pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return 0.0

    try:
        proc = subprocess.run(
            [ffmpeg, "-i", path, "-f", "null", "-"],
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0

    # ffmpeg 报 duration 到 stderr，非零退出属正常
    text = proc.stderr.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = hms.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                continue
    return 0.0


def cleanup_workdir(workdir: str) -> None:
    """清理整个临时工作目录（不含用户原文件）。"""
    if not workdir or not os.path.isdir(workdir):
        return
    shutil.rmtree(workdir, ignore_errors=True)


def ensure_workdir(base: str | None = None) -> str:
    """建立本次任务的临时工作目录。"""
    base = base or tempfile.gettempdir()
    path = Path(base) / f"musicxml_{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


if __name__ == "__main__":
    # 自检：python engine/format_guard.py <音频路径>
    import sys

    if len(sys.argv) < 2:
        print("用法: python format_guard.py <音频文件路径>")
        print(f"支持格式: {'、'.join(SUPPORTED_EXTS)}")
        raise SystemExit(0)

    target = sys.argv[1]
    try:
        r = load_for_upstream(target)
        print(f"OK  {os.path.basename(target)}")
        print(f"    时长 {r.duration:.2f}s  sr={r.sr}  转码={r.was_transcoded}")
        print(f"    实际路径 {r.path}")
        r.cleanup()
    except AudioFormatError as e:
        print(f"FAIL  {e.user_message}")
        if e.detail:
            print(f"    detail: {e.detail}")
        raise SystemExit(1) from e
