"""
separator — 音源分离适配层（人声 / 伴奏）

## 为什么存在（致命缺陷 #1）
上游 `AutoTranscriber/separator.py:16` 硬编码了原作者本机路径：

    DEMUCS_PYTHON = r"C:/Users/<upstream-author>\\miniconda3\\envs\\AutoTranscriber\\python.exe"

`separate_audio()` 用它拼 `subprocess` 命令调 demucs（`separator.py:147-155`）。
该路径只存在于原作者电脑上。后果链：

1. `has_demucs()` 在任何其他机器上恒为 `False`
2. `main.py:445` 命中 `if not has_demucs(): sys.exit(1)` 直接退出
3. 连 `separator.py:190-195` 的 HPSS 兜底分支都**走不到**（exit 在前）
4. **路径 1/2/3（双轨 / 只扒伴奏 / 只扒人声旋律）在本机 100% 不可用**

实测确认（本机 venv 已装 demucs 4.1.0）：
    has_demucs() = False
    main.py --separate → "❌ Demucs 不可用，无法分离。"

这是典型的「作者本机自嗨代码」：subprocess 调绝对路径，从未在第二台机器上验证。

## 我方解法（守住红线 B-1，不改上游一字）
**不用 subprocess，直接调 demucs 的 Python API。** 好处：
- 无硬编码路径，venv 装在哪都work（本项目用 `engine/.venv`）
- 可直接走 CUDA，省去子进程的环境变量传递
- 异常可捕获并中文化，而非读子进程 stderr
- 首次运行自动下载模型权重（demucs 官方行为），进度可见

## 模型来源（墙内可达性治理）
demucs 4.1.0 的 `get_model()` 把 HuggingFace Hub 放在第一位，墙内直连会
TCP 超时并重试 5 次才回落官方源 —— 首次分离要白等数分钟。
本模块把加载顺序改为「本地 → 官方直链 → 国内镜像」三跳，
详见 `_load_separator_model()` 上方的注释与实测数据。

## 降级链
    demucs (htdemucs, CUDA)
      ↓ 失败（无 GPU / 显存不足 / 模型下载失败）
    demucs (htdemucs, CPU)
      ↓ 失败
    HPSS 中频分离（librosa，纯 CPU，秒级，质量明显更差）
      ↓ 失败
    抛错，UI 给中文提示并建议改用「已分离音频直入」路径
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 进度回调签名：(阶段名, 0~1 进度, 说明文字)
ProgressFn = Callable[[str, float, str], None]


class SeparationError(Exception):
    """分离失败（面向用户，需中文化）"""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


@dataclass
class SeparationResult:
    """分离产物。路径为本任务专属临时目录下的 WAV，调用方负责 cleanup。"""

    vocals_path: str
    """人声轨 WAV 路径。"""

    accompaniment_path: str
    """伴奏轨 WAV 路径。"""

    method: str
    """实际生效的分离方式：demucs-cuda / demucs-cpu / hpss。"""

    workdir: str
    """本次任务的临时工作目录。"""

    def cleanup(self) -> None:
        """删除整个工作目录。**绝不触碰用户原文件**（它不在 workdir 内）。"""
        if self.workdir and os.path.isdir(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)


def has_demucs() -> bool:
    """
    本机是否可用 demucs。与上游同名函数语义一致，但判据完全不同。

    上游判据是「那个硬编码路径的 python 是否存在」，
    本函数判据是「当前解释器能否 import demucs 且 torch 可用」——后者才正确。
    """
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def _ensure_wav(src: str, workdir: str) -> str:
    """
    保证输入是 44.1kHz WAV。demucs 原生吃 wav/flac，其他格式先经 ffmpeg 转。

    复用 `format_guard.transcode_to_wav`，与扒谱路径共用同一套格式逻辑，
    避免两处格式白名单漂移。
    """
    ext = os.path.splitext(src)[1].lower()
    if ext in {".wav", ".flac"}:
        return src

    from format_guard import transcode_to_wav

    return transcode_to_wav(src, workdir)


# ── 模型权重来源治理（墙内可达性）──────────────────────────────────────────
#
# 踩坑实录（2026-10-05，主上老公的机器）：首次分离卡在
#     '[WinError 10060] 由于连接方在一段时间后没有正确答复…' thrown while
#     requesting HEAD https://huggingface.co/adefossez/HTDemucs/resolve/main/htdemucs.yaml
#     Retrying in 1s [Retry 1/5].
# 一直重试到 5/5 后彻底失败。
#
# 根因：demucs 4.1.0（PyPI 2026-07-11 发布，`pip install demucs` 的默认版本）
# 把 **HuggingFace Hub 放在第一位**（`demucs/pretrained.py:73-82` 调
# `get_hf_model()`），`dl.fbaipublicfiles.com` 只是 `except` 分支里的兜底。
# 本机实测（黑龙江）：
#     https://huggingface.co/…          连接超时（curl exit 28）
#     https://dl.fbaipublicfiles.com/…  200 OK，ttfb 0.64s
#     https://hf-mirror.com/…           307 → 200，0.77s
# 于是墙内每次「首次分离」都要先等 HF 的 TCP 超时 × 5 次重试（数分钟），
# 之后才（或根本没）落到兜底源。
#
# 为什么本机不复现：`~/.cache/huggingface/hub/models--adefossez--HTDemucs`
# 早有缓存，HF 分支在本地命中即返回，网络代码路径**从未被真正触发**。
#
# 解法：把「HF 优先」改成「本地 → 官方直链 → 国内镜像」三跳，全程不改
# demucs 与 huggingface_hub 一行（依赖只读，同 BOUNDARY B-1 的红线精神）。

_MODEL_DIR_NAME = "models"
"""本地权重目录名，落点 `<engine>/models`。放对文件即可完全离线使用。"""

_OFFICIAL_TH_NAME = "955717e8-8726e21a.th"
"""`htdemucs` 这个 bag 唯一成员的权重文件名。

签名与校验和取自 `demucs/remote/files.txt`（`955717e8-8726e21a.th`），
bag 定义 `demucs/remote/htdemucs.yaml` 的内容就是 `models: ['955717e8']`。
"""

_OFFICIAL_TH_URL = (
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/" + _OFFICIAL_TH_NAME
)
"""官方直链。国内实测可达（AWS CDN）。"""

_HF_ENDPOINTS_DEFAULT = (
    "https://hf-mirror.com",
    "https://aifasthub.com",
)
"""HF 国内镜像候选，**按序探活、取第一个可达者**。

实测（2026-10-05，黑龙江）：
    https://hf-mirror.com                     307 → 200，0.61s   ✓
    https://aifasthub.com                     200，0.90s         ✓
    https://hf-api.gitee.com                  404（无该路径）
    https://mirror.sjtu.edu.cn/hugging-face   404
    https://huggingface.co                    连接超时

用户可用环境变量 `BAPU_HF_ENDPOINTS`（逗号分隔）覆盖，例如配了代理想走官方：
    BAPU_HF_ENDPOINTS=https://huggingface.co
"""

_PROBE_TIMEOUT = 4.0
"""单次探活超时（秒）。候选逐个探，最坏耗时 = 该值 × 候选数。"""

_PROBE_UA = "bapu-engine/0.1 (demucs model fetch)"


def models_dir() -> Path:
    """本地权重目录（`<engine>/models`）。目录不存在不代表出错，仅表示需联网下载。"""
    return Path(__file__).resolve().parent / _MODEL_DIR_NAME


def _ensure_local_bag_yaml(root: Path, model: str) -> None:
    """
    本地目录缺 `<model>.yaml` 时，从 demucs 自带的 `remote/` 复制一份。

    用户手动兜底时只需下载 `.th` 本身，bag 定义由我们补齐，少一个出错点。
    """
    target = root / f"{model}.yaml"
    if target.is_file():
        return
    try:
        import demucs

        src = Path(demucs.__file__).resolve().parent / "remote" / f"{model}.yaml"
        if src.is_file():
            root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    except Exception:  # noqa: BLE001 — 补齐失败不致命，交给加载器逐跳报错
        pass


def _set_hf_offline(offline: bool) -> None:
    """
    切换 HuggingFace 离线模式。

    `huggingface_hub.constants` 在 import 时就把环境变量固化成了模块常量，
    因此**只改 `os.environ` 对已 import 的进程无效**，必须同时改常量。
    （demucs 的 `hf.py` 是函数内 `from huggingface_hub import hf_hub_download`，
    所以「首次 import 前设 env」这条也有效——两条都做，互为保险。）
    """
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
    try:
        from huggingface_hub import constants

        constants.HF_HUB_OFFLINE = offline
    except Exception:  # noqa: BLE001 — 未装 huggingface_hub 时只需 env
        pass


def _set_hf_endpoint(endpoint: str) -> None:
    """同 `_set_hf_offline`：env 与已 import 的常量都要改。"""
    os.environ["HF_ENDPOINT"] = endpoint
    try:
        from huggingface_hub import constants

        constants.ENDPOINT = endpoint
    except Exception:  # noqa: BLE001
        pass


def _hf_endpoints() -> tuple[str, ...]:
    """当前生效的镜像候选。`BAPU_HF_ENDPOINTS`（逗号分隔）可整体覆盖。"""
    raw = (os.environ.get("BAPU_HF_ENDPOINTS") or "").strip()
    if not raw:
        return _HF_ENDPOINTS_DEFAULT
    items = tuple(x.strip().rstrip("/") for x in raw.split(",") if x.strip())
    return items or _HF_ENDPOINTS_DEFAULT


def _probe_url(url: str) -> float | None:
    """
    HEAD 探测 URL 是否可达，返回耗时（秒）；不可达返回 None。

    为什么非探不可：直接让 demucs / torch 去试，一次连不上要等**系统级 TCP
    超时**（Windows 约 21s），再叠加各自的重试 —— 用户看到的就是「卡死」。
    这里 4s 判死并顺势换源，把不确定性压到最小。
    """
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _PROBE_UA})
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            if 200 <= resp.status < 400:
                return time.monotonic() - started
    except Exception:  # noqa: BLE001 — 探测失败一律视作不可达
        return None
    return None


def _hf_probe_url(endpoint: str, model: str) -> str:
    """
    构造探活用的 HF 原始文件 URL：`<endpoint>/<ns>/<repo>/resolve/main/<model>.yaml`。

    仓库名映射**复用 demucs 自己的 `hf.py`**，不另写一份，避免两边漂移
    （`htdemucs` → `HTDemucs`，`htdemucs_ft` → `HTDemucs-ft`，其余 → `Demucs-<name>`）。
    demucs 4.0.1 没有 `hf.py`（那时还不走 HF），此时返回空串，调用方自然跳过镜像跳。
    """
    try:
        from demucs.hf import DEFAULT_NAMESPACE, hf_repo_name

        namespace, name = DEFAULT_NAMESPACE, model
        if "/" in model:
            namespace, name = model.split("/", 1)
        return f"{endpoint}/{namespace}/{hf_repo_name(name)}/resolve/main/{name}.yaml"
    except Exception:  # noqa: BLE001
        return f"{endpoint}/adefossez/HTDemucs/resolve/main/{model}.yaml"


def _pick_mirror(model: str) -> str | None:
    """按序探活镜像候选，返回第一个可达者；全不可达返回 None。"""
    for endpoint in _hf_endpoints():
        url = _hf_probe_url(endpoint, model)
        if url and _probe_url(url) is not None:
            return endpoint
    return None


def _model_error_message(model: str, errors: list[str]) -> str:
    """把三跳的失败原因汇总成可操作的中文提示（不写「请检查网络」这种废话）。"""
    lines = [
        f"分离模型「{model}」下载失败。已依次尝试：本地权重目录 → 官方源 → 国内镜像。",
        "",
    ]
    if errors:
        lines.append("失败原因：")
        lines += [f"  · {e}" for e in errors]
        lines.append("")
    lines += [
        "可以这样解决（任选其一）：",
        "  1. 检查网络后重试。若使用了代理，可设环境变量 "
        "BAPU_HF_ENDPOINTS=https://huggingface.co 直接走官方站点；"
        "也可用逗号分隔填多个镜像，程序会按序探活取第一个可用的。",
        "  2. 手动下载权重放到下面这个目录，再重试（此后永久离线可用）：",
        f"     {models_dir()}",
        f"     需要文件：{_OFFICIAL_TH_NAME}",
        f"     下载地址：{_OFFICIAL_TH_URL}",
        "  3. 暂时无法下载时，可改用「基本扒谱」路径：不做分离，"
        "直接对整段音频做多音高识别。",
    ]
    return "\n".join(lines)


def _load_separator_model(model: str, progress: ProgressFn | None):
    """
    按「墙内可达优先」的顺序加载 demucs 模型，返回已 eval 的模型对象。

    跳 1 本地权重目录   `get_model(model, repo=<engine>/models)` —— 零网络
    跳 2 官方直链       探活通过后强制离线，让 demucs 直接落到 dl.fbaipublicfiles.com
    跳 3 HF 国内镜像    按序探活候选镜像，用第一个可达的 endpoint 再试一次

    跳 1 的意义不只是「快」：它是**离线可用**的唯一保障，也是用户手动兜底的
    落点（错误提示里就把这个目录告诉用户）。

    跳 2/跳 3 都**先探活再动手**：直接让 demucs/torch 去试的话，一次连不上
    要等系统 TCP 超时（Windows 约 21s）再叠加自身重试，用户看到的就是「卡死」。
    """
    from demucs.pretrained import get_model

    errors: list[str] = []

    # ── 跳 1：本地权重目录 ──
    root = models_dir()
    if root.is_dir() and any(root.glob("*.th")):
        _ensure_local_bag_yaml(root, model)
        try:
            net = get_model(model, repo=root)
            if progress:
                progress("separate", 0.12, "已从本地读取分离模型")
            return net
        except Exception as e:  # noqa: BLE001
            errors.append(f"本地权重目录：{type(e).__name__}: {str(e)[:150]}")

    # ── 跳 2：官方直链（AWS CDN，国内多数情况可达）──
    if _probe_url(_OFFICIAL_TH_URL) is not None:
        if progress:
            progress(
                "separate", 0.06,
                "正在下载分离模型权重（首次约 80MB，之后离线可用）…",
            )
        _set_hf_offline(True)  # 强制离线 → demucs 只能落到 dl.fbaipublicfiles.com
        try:
            net = get_model(model)
            if progress:
                progress("separate", 0.12, "分离模型已就绪")
            return net
        except Exception as e:  # noqa: BLE001
            errors.append(f"官方源：{type(e).__name__}: {str(e)[:150]}")
    else:
        errors.append("官方源 dl.fbaipublicfiles.com：探测不可达，已跳过")

    # ── 跳 3：HF 国内镜像（探活择优）──
    candidates = _hf_endpoints()
    endpoint = _pick_mirror(model)
    if endpoint is None:
        errors.append("HF 镜像：" + "、".join(candidates) + " 均探测不可达")
    else:
        if progress:
            progress("separate", 0.06, f"正在从镜像源下载分离模型（{endpoint}）…")
        _set_hf_offline(False)
        _set_hf_endpoint(endpoint)
        try:
            net = get_model(model)
            if progress:
                progress("separate", 0.12, "分离模型已就绪")
            return net
        except Exception as e:  # noqa: BLE001
            errors.append(f"镜像源 {endpoint}：{type(e).__name__}: {str(e)[:150]}")

    raise SeparationError(_model_error_message(model, errors), "all model sources failed")


def _run_demucs_api(
    wav_path: str,
    workdir: str,
    model: str,
    device: str,
    progress: ProgressFn | None,
) -> tuple[str, str] | None:
    """
    用 demucs Python API 分离，返回 (vocals_path, no_vocals_path)；失败返回 None。

    demucs 4.x 的 API 形态：
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
        from demucs.audio import AudioFile, save_audio
    """
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile, save_audio

    if progress:
        progress("separate", 0.05, "正在加载分离模型…")

    # 模型来源按「本地 → 官方直链 → 国内镜像」三跳加载。
    # 失败原因已在 `_load_separator_model` 里汇总成可操作的中文提示，
    # 此处**不再二次包装**，否则会把详细信息覆盖成泛泛的「请检查网络」。
    net = _load_separator_model(model, progress)

    if progress:
        progress("separate", 0.15, "正在读取音频…")

    try:
        wav = AudioFile(wav_path).read(
            streams=0, samplerate=net.samplerate, channels=net.audio_channels
        )
    except Exception as e:  # noqa: BLE001
        raise SeparationError(
            f"分离时读取音频失败：{type(e).__name__}: {str(e)[:200]}",
            f"AudioFile.read failed on {wav_path}",
        ) from e

    # ⚠️ demucs 4.1.0 的 apply_model 签名中**没有 ref 参数**（那是 3.x 旧签名）。
    # 实测：传 ref 会抛 `TypeError: apply_model() got an unexpected keyword argument 'ref'`。
    # 正确做法是直接迁移模型与张量到目标设备，归一化由 apply_model 内部处理。
    if device == "cuda":
        net.cuda()
        wav = wav.cuda()
    else:
        net.cpu()

    if progress:
        progress("separate", 0.3, f"正在分离（{device}，首次可能较慢）…")

    try:
        with torch.no_grad():
            out = apply_model(
                net,
                wav[None],
                device=device,
                shifts=0,
                split=True,
                overlap=0.25,
                progress=False,
                num_workers=0,
            )[0]
        out = out.cpu()
    except torch.cuda.OutOfMemoryError as e:
        raise SeparationError(
            "显存不足，无法用 GPU 分离。\n"
            "请关闭其他占用显存的程序后重试，或改用「基本扒谱」路径（不需分离）。",
            f"CUDA OOM: {e}",
        ) from e
    except Exception as e:  # noqa: BLE001
        raise SeparationError(
            f"音源分离失败：{type(e).__name__}: {str(e)[:300]}",
            f"apply_model failed on {device}",
        ) from e

    if progress:
        progress("separate", 0.9, "正在写出分离结果…")

    stem_dir = os.path.join(workdir, "stems")
    os.makedirs(stem_dir, exist_ok=True)

    # demucs 源顺序：drums / bass / other / vocals
    # --two-stems vocals 的语义 = vocals 与 (其余全部相加)
    vocals = out[3]
    others = out[:3].sum(0)

    vocals_path = os.path.join(stem_dir, "vocals.wav")
    accomp_path = os.path.join(stem_dir, "no_vocals.wav")

    # 踩坑实录：demucs 4.1.0 的签名是 save_audio(wav, path, samplerate)，
    # 波形在前路径在后。误写成 (path, tensor) 会报
    # `AttributeError: 'str' object has no attribute 'dtype'`（内部在 wav.dtype 上断言）。
    save_audio(vocals, vocals_path, samplerate=net.samplerate)
    save_audio(others, accomp_path, samplerate=net.samplerate)

    for p in (vocals_path, accomp_path):
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            raise SeparationError(
                "分离完成但产物为空，请换一首音频或改用其他路径。", f"empty stem: {p}"
            )

    return vocals_path, accomp_path


def _run_hpss(
    wav_path: str,
    workdir: str,
    progress: ProgressFn | None,
) -> tuple[str, str]:
    """
    HPSS 中频分离兜底（librosa，纯 CPU，秒级）。

    质量明显不如 demucs（只按中频/人声频段做掩蔽），但**总比完全不可用好**。
    仅在 demucs 完全失败时启用，且 UI 须明确提示用户当前用的是降级方案。
    """
    import librosa
    import numpy as np
    import soundfile as sf

    if progress:
        progress("separate", 0.2, "Demucs 不可用，正在用中频分离降级处理…")

    try:
        y, sr = librosa.load(wav_path, sr=22050, mono=True)
        y_harmonic, y_percussive = librosa.effects.hpss(y)
    except Exception as e:  # noqa: BLE001
        raise SeparationError(
            f"中频分离也失败了：{type(e).__name__}: {str(e)[:200]}",
            "hpss failed",
        ) from e

    # 人声主导中频（300Hz-4kHz），构造频段掩蔽
    try:
        S = np.abs(librosa.stft(y))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        band = (freqs >= 300) & (freqs <= 4000)
        vocal_mask = np.zeros_like(S)
        vocal_mask[band, :] = S[band, :]
        vocal = librosa.istft(vocal_mask, hop_length=512)
        accomp = y - vocal
    except Exception:  # noqa: BLE001 — 频段法失败则退回谐波/打击乐二分
        vocal = y_harmonic
        accomp = y_percussive

    stem_dir = os.path.join(workdir, "stems")
    os.makedirs(stem_dir, exist_ok=True)
    vocals_path = os.path.join(stem_dir, "vocals_hpss.wav")
    accomp_path = os.path.join(stem_dir, "no_vocals_hpss.wav")

    sf.write(vocals_path, vocal, sr)
    sf.write(accomp_path, accomp, sr)

    return vocals_path, accomp_path


def separate(
    input_path: str,
    model: str = "htdemucs",
    device: str = "auto",
    workdir: str | None = None,
    progress: ProgressFn | None = None,
    allow_hpss_fallback: bool = True,
) -> SeparationResult:
    """
    把歌曲分离为人声与伴奏。

    Parameters
    ----------
    input_path : 任意支持格式的音频路径（经 format_guard 校验）
    model : demucs 模型名（htdemucs / htdemucs_ft / mdx_extra / mdx_q）
    device : "auto"（有 CUDA 用 CUDA）/ "cuda" / "cpu"
    workdir : 临时工作目录，默认在系统 temp 下新建
    progress : 进度回调 (阶段, 0~1, 文字)
    allow_hpss_fallback : demucs 全失败时是否降级到 HPSS

    Returns
    -------
    SeparationResult

    Raises
    ------
    SeparationError : 分离彻底失败（全链路走完仍无产物）
    """
    from format_guard import validate_audio_file

    validate_audio_file(input_path)

    if workdir is None:
        workdir = os.path.join(
            tempfile.gettempdir(), f"musicxml_sep_{uuid.uuid4().hex[:10]}"
        )
    os.makedirs(workdir, exist_ok=True)

    wav_path = _ensure_wav(input_path, workdir)

    if device == "auto":
        device = "cuda" if _torch_cuda_available() else "cpu"

    if not has_demucs():
        if not allow_hpss_fallback:
            raise SeparationError(
                "本机未安装 Demucs，无法进行人声/伴奏分离。\n\n"
                "可选方案：\n"
                "1. 改用「基本扒谱」路径（不需分离）\n"
                "2. 用外部工具分离好后，用「已分离音频直入」路径导入",
                "demucs not importable",
            )
        vocals, accomp = _run_hpss(wav_path, workdir, progress)
        return SeparationResult(
            vocals_path=vocals,
            accompaniment_path=accomp,
            method="hpss",
            workdir=workdir,
        )

    # 首选路径：按 device 尝试 demucs
    # 踩坑实录（勿再犯）：本函数早期版本把 demucs 的真实异常直接吞掉降级到 HPSS，
    # 导致「分离质量差」的表象掩盖了「代码调错 API」的真因，排查多绕了一轮。
    # 现做法：**完整保留失败链**，既有降级能力，也能在日志/UI 暴露真因。
    demucs_error: SeparationError | None = None
    try:
        result = _run_demucs_api(wav_path, workdir, model, device, progress)
    except SeparationError as e:
        demucs_error = e
        result = None
        # CUDA 专属失败（显存/驱动）可退回 CPU 重试一次
        retriable = "显存" in e.user_message or "CUDA" in str(e.detail or "")
        if device == "cuda" and retriable:
            if progress:
                progress("separate", 0.1, "GPU 分离失败，正在改用 CPU 重试…")
            try:
                result = _run_demucs_api(wav_path, workdir, model, "cpu", progress)
                demucs_error = None
            except SeparationError as cpu_err:
                demucs_error = cpu_err
                result = None
        if result is None and not allow_hpss_fallback:
            raise demucs_error or SeparationError("音源分离失败。", "unknown")

    if result is not None:
        vocals, accomp = result
        method = "demucs-cuda" if device == "cuda" else "demucs-cpu"
        if progress:
            progress("separate", 1.0, "分离完成")
        return SeparationResult(
            vocals_path=vocals,
            accompaniment_path=accomp,
            method=method,
            workdir=workdir,
        )

    # 全部 demucs 路径失败 → HPSS 兜底
    reason = ""
    if demucs_error is not None:
        reason = f"Demucs 失败原因：{demucs_error.user_message.splitlines()[0]}"
        if progress:
            progress("separate", 0.15, f"{reason}，改用中频分离降级处理…")

    if not allow_hpss_fallback:
        raise SeparationError(
            "音源分离失败，已禁用降级方案。\n"
            f"{reason}\n\n"
            "可改用「已分离音频直入」路径：先用外部工具分离好，再导入。",
            demucs_error.detail if demucs_error else "demucs failed",
        )

    vocals, accomp = _run_hpss(wav_path, workdir, progress)
    if progress:
        progress("separate", 1.0, "分离完成（降级方案，质量有限）")
    return SeparationResult(
        vocals_path=vocals,
        accompaniment_path=accomp,
        method="hpss",
        workdir=workdir,
    )


def capabilities() -> dict:
    """
    能力自检，供 UI 决定是否显示降级警告、以及启动时的环境诊断。

    Returns
    -------
    {"demucs": bool, "cuda": bool, "device": str, "ffmpeg": bool, "notes": [...]}
    """
    notes = []
    demucs_ok = has_demucs()
    cuda_ok = _torch_cuda_available()

    if not demucs_ok:
        notes.append("未安装 Demucs，人声/伴奏分离将降级为中频分离（质量有限）。")
    if not cuda_ok:
        notes.append("未检测到 CUDA，分离将在 CPU 上运行，3 分钟歌曲可能需数分钟。")

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    if not ffmpeg_ok:
        notes.append("未找到 ffmpeg，部分音频格式无法读取。")

    return {
        "demucs": demucs_ok,
        "cuda": cuda_ok,
        "device": "cuda" if cuda_ok else "cpu",
        "ffmpeg": ffmpeg_ok,
        "notes": notes,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "--probe"}:
        caps = capabilities()
        print("=== 分离能力自检 ===")
        print(json.dumps(caps, ensure_ascii=False, indent=2))
        if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
            print("\n用法: python separator.py <音频路径>")
            raise SystemExit(0)
        raise SystemExit(0)

    target = sys.argv[1]

    def _p(stage: str, pct: float, msg: str) -> None:
        print(f"  [{pct * 100:5.1f}%] {msg}")

    try:
        res = separate(target, progress=_p)
    except SeparationError as e:
        print(f"FAIL: {e.user_message}")
        raise SystemExit(1) from e

    print(f"OK  方式={res.method}")
    print(f"    人声: {res.vocals_path}")
    print(f"    伴奏: {res.accompaniment_path}")
    res.cleanup()
