"""
bridge — 引擎 JSON 协议服务端

## 定位
Tauri 前端不能直接调 Python，只能起子进程。本模块是该子进程的入口：
读 stdin 的一行 JSON 请求，执行业务，向上层吐**结构化事件流**。

## 为什么用「一行 JSON + 事件流」而不是别的
- 纯函数式扒谱**无法中途汇报进度**（上游算法是直筒计算），所以进度只能
  在阶段边界打点。事件流比 stdout 打印更可靠：不会被第三方库的 print 污染
  （上游 `merge_tracks_to_piano` 就有 print，`midi_writer.py:131`）。
- 一行一请求让 Rust 侧实现极简：spawn → 写一行 → 逐行读事件。

## 协议

### 请求（stdin，单行 JSON）
```json
{"cmd": "transcribe", "id": "uuid", "payload": { ...TranscribeRequest 字段... }}
{"cmd": "probe",    "id": "uuid", "payload": {"path": "..."}}
{"cmd": "capabilities", "id": "uuid", "payload": {}}
{"cmd": "modes",    "id": "uuid", "payload": {}}
{"cmd": "reveal",   "id": "uuid", "payload": {"path": "..."}}
{"cmd": "open_musescore", "id": "uuid", "payload": {"path": "..."}}
```

### 响应（stdout，每行一个 JSON 对象）
```json
{"type": "accepted", "id": "uuid"}
{"type": "progress", "id": "uuid", "stage": "separate", "pct": 0.42, "message": "..."}
{"type": "result",   "id": "uuid", "data": { ...TranscribeResult 序列化... }}
{"type": "error",    "id": "uuid", "message": "面向用户的中文文案", "detail": "技术细节"}
{"type": "log",      "id": "uuid", "message": "..."}
```

**stdout 纪律**：本模块**只**往 stdout 写协议 JSON。上游与第三方库若
print 到 stdout（`merge_tracks_to_piano` 就如此），一律重定向到 stderr，
否则会污染协议流让 Rust 侧解析失败。这是踩过才知道的坑。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from typing import Any

# 保证 engine 目录内模块可互相 import
_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

# 第三方库进度条（如 demucs/torch）一律不写 stdout
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

_write_lock = threading.Lock()

# 真 stdout 通道。在 _redir_lib_stdout() 里被捕获，供 emit 独占使用。
# ⚠️ 踩坑实录：emit 最初直接写 sys.stdout，而 _redir_lib_stdout() 把
# sys.stdout 换成了 stderr —— 于是协议 JSON 全被写去 stderr，前端一个字节
# 都收不到。emit 必须写 _real_stdout，不能写 sys.stdout。
_real_stdout = None


def emit(obj: dict[str, Any]) -> None:
    """写一行协议 JSON 到真 stdout 并立即 flush。线程安全。"""
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    stream = _real_stdout if _real_stdout is not None else sys.__stdout__
    with _write_lock:
        stream.write(line + "\n")
        stream.flush()


def log(message: str, req_id: str = "") -> None:
    emit({"type": "log", "id": req_id, "message": message})


def _redir_lib_stdout() -> None:
    """
    把 sys.stdout 重定向到 stderr，只留 emit 用的原始通道。

    上游 `merge_tracks_to_piano`（midi_writer.py:131）与 main.py 都有 print，
    若不重定向会往协议流里混进非 JSON 行，Rust 侧解析直接炸。
    做法：先把真 stdout 存到 `_real_stdout` 给 emit 用，再把 sys.stdout 换成 stderr。
    """
    global _real_stdout
    _real_stdout = sys.__stdout__
    sys.stdout = sys.stderr


def _err(msg: str) -> None:
    """往 stderr 写诊断信息（不影响协议流）。"""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def handle_transcribe(req_id: str, payload: dict) -> None:
    from pipeline import TranscribeError, TranscribeRequest, transcribe

    def on_progress(stage: str, pct: float, msg: str) -> None:
        emit(
            {
                "type": "progress",
                "id": req_id,
                "stage": stage,
                "pct": round(float(pct), 4),
                "message": msg,
            }
        )

    try:
        allowed = {
            "mode", "input_path", "output_path", "extra_inputs", "n_peaks",
            "hop_length", "onset_threshold", "pitch_threshold",
            "min_note_duration", "tempo", "perceptual", "simplify",
            "piano_mode", "demucs_model", "device", "allow_hpss_fallback",
            "track_names",
        }
        kwargs = {k: v for k, v in payload.items() if k in allowed}
        unknown = set(payload) - allowed
        if unknown:
            log(f"忽略未知参数：{sorted(unknown)}", req_id)

        # output_path 由前端给；若缺省则放在输入文件同目录
        if not kwargs.get("output_path"):
            base = os.path.splitext(kwargs.get("input_path", "output"))[0]
            kwargs["output_path"] = base + ".mid"

        req = TranscribeRequest(**kwargs)
        result = transcribe(req, progress=on_progress)

        emit(
            {
                "type": "result",
                "id": req_id,
                "data": {
                    "outputPath": result.output_path,
                    "tracks": result.tracks,
                    "totalNotes": result.total_notes,
                    "duration": result.duration,
                    "elapsed": result.elapsed,
                    "mode": result.mode,
                    "separationMethod": result.separation_method,
                    "warnings": result.warnings,
                },
            }
        )
    except TranscribeError as e:
        emit(
            {
                "type": "error",
                "id": req_id,
                "message": e.user_message,
                "detail": e.detail,
            }
        )
    except KeyboardInterrupt:
        emit({"type": "error", "id": req_id, "message": "任务已被取消。", "detail": "KeyboardInterrupt"})
    except Exception as e:  # noqa: BLE001 — 兜底，绝不让子进程裸死
        _err(traceback.format_exc())
        emit(
            {
                "type": "error",
                "id": req_id,
                "message": f"引擎内部错误：{type(e).__name__}。\n详细原因见引擎日志。",
                "detail": f"{type(e).__name__}: {e}",
            }
        )


def handle_probe(req_id: str, payload: dict) -> None:
    """
    探测音频基本信息，供 UI 在用户拖入文件时立即显示。

    ### 踩坑实录（对抗式自测逮到）
    初版只做廉价校验（存在/体积/魔数/扩展名）就返回 `ok:true`。结果一个
    「RIFF 头合法但内容全 0」的伪造 wav 被判为可用，且 `duration` 返回 0.0
    ——UI 会显示「0 秒」却仍允许提交，用户白等一次失败。
    正解：**duration 拿不到就必须判失败**，不给「可用但时长 0」的中间态。
    """
    from format_guard import AudioFormatError, probe_duration, validate_audio_file

    path = payload.get("path", "")
    try:
        validate_audio_file(path)
        duration = probe_duration(path)
        if duration <= 0:
            raise AudioFormatError(
                "读不出这个音频的时长，文件可能已损坏或不是有效的音频。",
                f"probe_duration returned {duration} for {path}",
            )
        emit(
            {
                "type": "result",
                "id": req_id,
                "data": {
                    "path": path,
                    "name": os.path.basename(path),
                    "size": os.path.getsize(path),
                    "duration": duration,
                    "ok": True,
                },
            }
        )
    except AudioFormatError as e:
        emit({"type": "error", "id": req_id, "message": e.user_message, "detail": e.detail})
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "id": req_id, "message": f"无法读取该文件。", "detail": str(e)})


def handle_capabilities(req_id: str, _payload: dict) -> None:
    from separator import capabilities

    emit({"type": "result", "id": req_id, "data": capabilities()})


def handle_modes(req_id: str, _payload: dict) -> None:
    from pipeline import STAGE_LABELS, describe_modes

    emit(
        {
            "type": "result",
            "id": req_id,
            "data": {"modes": describe_modes(), "stageLabels": STAGE_LABELS},
        }
    )


def handle_reveal(req_id: str, payload: dict) -> None:
    """在文件管理器中定位文件。Windows 用 explorer /select。"""
    import subprocess

    path = os.path.abspath(payload.get("path", ""))
    if not os.path.exists(path):
        emit({"type": "error", "id": req_id, "message": "文件不存在。", "detail": path})
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        emit({"type": "result", "id": req_id, "data": {"ok": True}})
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "id": req_id, "message": "无法打开文件夹。", "detail": str(e)})


def handle_open_musescore(req_id: str, payload: dict) -> None:
    """用 MuseScore 打开 MIDI。"""
    import subprocess

    from midi_post import find_musescore

    path = os.path.abspath(payload.get("path", ""))
    if not os.path.exists(path):
        emit({"type": "error", "id": req_id, "message": "MIDI 文件不存在。", "detail": path})
        return

    mscore = find_musescore()
    if not mscore:
        emit(
            {
                "type": "error",
                "id": req_id,
                "message": "未检测到 MuseScore。\n请先安装：winget install MuseScore.MuseScore",
                "detail": "musescore not found",
            }
        )
        return

    try:
        subprocess.Popen([mscore, path])
        emit({"type": "result", "id": req_id, "data": {"ok": True}})
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "id": req_id, "message": "启动 MuseScore 失败。", "detail": str(e)})


HANDLERS = {
    "transcribe": handle_transcribe,
    "probe": handle_probe,
    "capabilities": handle_capabilities,
    "modes": handle_modes,
    "reveal": handle_reveal,
    "open_musescore": handle_open_musescore,
}


def _force_utf8_stdio() -> None:
    """
    强制 stdin/stdout/stderr 使用 UTF-8。

    见 bootstrap.py 同名函数的说明：标准流为管道时 Python 会退回系统
    locale 编码（中文 Windows = GBK）。必须在本进程内显式声明，不能依赖环境变量。

    ⚠️ 踩坑实录：先前这里**只 reconfigure 了 stdout/stderr，漏了 stdin**。
    Rust 侧 `writeln!` 写出的是 UTF-8 字节，而这边按 GBK 解码 →
    含中文的音频路径当场变乱码 → 报「文件不存在，可能已被移动或删除」。
    该故障极易漏测：ASCII 路径一律正常；且只有走到真正读 stdin 的
    常驻 sidecar（扒谱）才炸，而拖入时的探测走另一条早已配好 UTF-8 的通道，
    于是症状成为「拖入能过、一点扒谱就挂」。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    _force_utf8_stdio()
    _redir_lib_stdout()

    # Windows 下避免弹黑框（若被打包成 GUI 程序）
    if sys.platform == "win32":
        try:
            ctypes_windll = __import__("ctypes").windll
            ctypes_windll.kernel32.SetConsoleMode(
                ctypes_windll.kernel32.GetStdHandle(-11), 7
            )
        except Exception:  # noqa: BLE001
            pass

    _err("[bridge] 引擎协议服务已启动，等待请求…")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            emit({"type": "error", "id": "", "message": f"请求格式错误：{e}", "detail": line[:200]})
            continue

        cmd = req.get("cmd", "")
        req_id = req.get("id", "")
        payload = req.get("payload", {}) or {}

        handler = HANDLERS.get(cmd)
        if handler is None:
            emit(
                {
                    "type": "error",
                    "id": req_id,
                    "message": f"未知命令：{cmd}",
                    "detail": f"available: {sorted(HANDLERS)}",
                }
            )
            continue

        emit({"type": "accepted", "id": req_id})
        try:
            handler(req_id, payload)
        except Exception as e:  # noqa: BLE001 — handler 自身也要兜底
            _err(traceback.format_exc())
            emit(
                {
                    "type": "error",
                    "id": req_id,
                    "message": f"命令 {cmd} 执行失败。",
                    "detail": str(e),
                }
            )


if __name__ == "__main__":
    main()
