"""
mcp_server — 扒谱助手的 MCP 服务端

## 定位
让 AI 助手（如 WorkBuddy / Claude Code / 任何 MCP 客户端）直接调用扒谱能力。
与 GUI、CLI 共用同一个 `pipeline.transcribe`，故三者行为完全一致。

## ⚠️ 版本契约（mcp 2.x，不是 1.x）
实测本机 `mcp 2.3.0`：
- 1.x 的 `from mcp.server.fastmcp import FastMCP` → **不存在**
- 2.x 改名 `MCPServer`：`from mcp.server.mcpserver import MCPServer`
- 装饰器是 `@mcp.tool()`，`run(transport="stdio")`

若照 1.x 记忆写会 `ModuleNotFoundError`。若需兼容 1.x，
下方 `_import_server()` 做了双路探测。

## 提供的工具（4 个）
1. `transcribe`   —— 核心扒谱（支持全部 6 种模式）
2. `list_modes`   —— 列出可用模式及说明
3. `inspect_audio`—— 探测音频（时长/格式）而不扒谱
4. `environment_status` —— 报告引擎能力（Demucs/CUDA/ffmpeg）

## 接入方式
在 MCP 客户端配置里加：
```json
{
  "mcpServers": {
    "bapu": {
      "command": "<REPO>/engine/.venv/Scripts/python.exe",
      "args": ["<REPO>/engine/mcp_server.py"]
    }
  }
}
```
本仓库已提供现成配置：`mcp/bapu.json`。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)


def _import_server():
    """
    兼容 mcp 1.x（FastMCP）与 2.x（MCPServer）。

    踩坑实录：初版直接 `from mcp.server.fastmcp import FastMCP`，
    在装 mcp 2.3.0 的机器上抛 `ModuleNotFoundError: No module named
    'mcp.server.fastmcp'`——2.x 已把 FastMCP 改名 MCPServer。
    正解：双路探测，优先 2.x。
    """
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2.0

        return MCPServer, "mcpserver"
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        return FastMCP, "fastmcp"
    except ImportError as e:
        raise ImportError(
            "未安装 mcp SDK。请执行：\n"
            "  pip install mcp\n"
            "或用 engine/.venv/Scripts/python.exe -m pip install mcp"
        ) from e


ServerClass, _API_STYLE = _import_server()

mcp = ServerClass("bapu")


# ─────────────────────────────────────────────────────────────
# 工具实现
# ─────────────────────────────────────────────────────────────

def _validate_output(output_path: str) -> str | None:
    """
    输出路径护栏。返回错误文案，None 表示通过。

    与 cli.py 的同名逻辑同源：**禁止写进 vendored 只读树与引擎自身目录**。
    原因见 cli.py 的踩坑实录——默认输出曾落在 third_party 里，违反红线 B-1。
    """
    engine_dir = os.path.abspath(_ENGINE_DIR)
    project_root = os.path.dirname(engine_dir)
    out_abs = os.path.abspath(output_path)

    for forbidden, label in (
        (engine_dir, "引擎目录"),
        (os.path.join(project_root, "third_party"), "上游只读目录"),
    ):
        f = os.path.abspath(forbidden)
        if out_abs == f or out_abs.startswith(f + os.sep):
            return f"拒绝把输出写进{label}：{out_abs}。请换一个输出位置。"
    return None


def _run_transcribe(
    input_path: str,
    mode: str = "basic",
    output_path: str | None = None,
    extra_inputs: list[str] | None = None,
    n_peaks: int | None = None,
    tempo: float = 120.0,
    onset_threshold: float = 0.3,
    min_note_duration: int = 4,
    simplify: int = 0,
    perceptual: bool = False,
    piano_mode: bool = False,
    device: str = "auto",
    allow_hpss_fallback: bool = True,
) -> dict[str, Any]:
    """
    核心扒谱逻辑。被 MCP 工具与自检共用。

    Returns
    -------
    成功时 {"ok": True, ...}；失败时 {"ok": False, "error": "中文原因"}
    —— **永不抛异常给调用方**。MCP 工具抛异常会让客户端收到协议级错误，
    对 AI 而言不可读；返回结构化失败更友好。
    """
    from pipeline import TranscribeError, TranscribeRequest, transcribe

    try:
        if not os.path.exists(input_path):
            return {"ok": False, "error": f"文件不存在：{input_path}"}

        extras = extra_inputs or []
        for e in extras:
            if not os.path.exists(e):
                return {"ok": False, "error": f"附加音轨文件不存在：{e}"}

        if not output_path:
            base = os.path.splitext(os.path.abspath(input_path))[0]
            output_path = base + ".mid"
            # 输入在只读位置时，输出改落 cwd
            for f in (
                _ENGINE_DIR,
                os.path.join(os.path.dirname(_ENGINE_DIR), "third_party"),
            ):
                f = os.path.abspath(f)
                if os.path.abspath(input_path).startswith(f + os.sep):
                    output_path = os.path.abspath(
                        os.path.splitext(os.path.basename(input_path))[0] + ".mid"
                    )
                    break

        err = _validate_output(output_path)
        if err:
            return {"ok": False, "error": err}

        if mode == "pre_separated" and not extras:
            return {
                "ok": False,
                "error": "「已分离音频直入」需要 extra_inputs 传入已分离好的音轨路径。",
            }

        req = TranscribeRequest(
            mode=mode,
            input_path=os.path.abspath(input_path),
            output_path=os.path.abspath(output_path),
            extra_inputs=[os.path.abspath(e) for e in extras],
            n_peaks=n_peaks,
            tempo=tempo,
            onset_threshold=onset_threshold,
            min_note_duration=min_note_duration,
            simplify=simplify,
            perceptual=perceptual,
            piano_mode=piano_mode,
            device=device,
            allow_hpss_fallback=allow_hpss_fallback,
        )

        res = transcribe(req, progress=None)

        from midi_post import TRACK_DISPLAY_NAMES

        return {
            "ok": True,
            "output": res.output_path,
            "mode": res.mode,
            "totalNotes": res.total_notes,
            "durationSeconds": round(res.duration, 2),
            "elapsedSeconds": round(res.elapsed, 2),
            "separationMethod": res.separation_method,
            "tracks": [
                {
                    "name": t["name"],
                    "nameCn": TRACK_DISPLAY_NAMES.get(t["name"], t["name"]),
                    "program": t["program"],
                    "notes": t["notes"],
                    "duration": t["duration"],
                }
                for t in res.tracks
            ],
            "warnings": res.warnings,
        }
    except TranscribeError as e:
        return {"ok": False, "error": e.user_message, "detail": e.detail}
    except Exception as e:  # noqa: BLE001
        # 兜底：绝不把堆栈抛给 MCP 客户端
        return {
            "ok": False,
            "error": f"扒谱过程出错：{type(e).__name__}: {str(e)[:200]}",
            "detail": traceback.format_exc()[:500],
        }


# ─────────────────────────────────────────────────────────────
# MCP 工具注册
# ─────────────────────────────────────────────────────────────

@mcp.tool(
    name="transcribe",
    title="音频扒谱",
    description=(
        "把音频转成 MIDI 乐谱（MuseScore 可直接打开）。\n\n"
        "六种模式：\n"
        "  full_auto      全自动（分离人声+伴奏，导出双轨）\n"
        "  accompaniment  只扒伴奏\n"
        "  vocals         只扒人声旋律\n"
        "  basic          基本扒谱（不分离，默认）\n"
        "  basic_multi    基本扒谱（多音轨）\n"
        "  pre_separated  已分离音频直入（需同时传 extra_inputs）\n\n"
        "支持格式：wav/mp3/flac/m4a/aac/ogg/opus/wma/aiff/wv 等。\n"
        "分离类模式需 Demucs，首次运行会下载模型权重，3 分钟歌曲约 20-60 秒。"
    ),
)
def transcribe_tool(
    input_path: str,
    mode: str = "basic",
    output_path: str | None = None,
    extra_inputs: list[str] | None = None,
    n_peaks: int | None = None,
    tempo: float = 120.0,
    onset_threshold: float = 0.3,
    min_note_duration: int = 4,
    simplify: int = 0,
    perceptual: bool = False,
    piano_mode: bool = False,
    device: str = "auto",
) -> dict[str, Any]:
    return _run_transcribe(
        input_path=input_path,
        mode=mode,
        output_path=output_path,
        extra_inputs=extra_inputs,
        n_peaks=n_peaks,
        tempo=tempo,
        onset_threshold=onset_threshold,
        min_note_duration=min_note_duration,
        simplify=simplify,
        perceptual=perceptual,
        piano_mode=piano_mode,
        device=device,
    )


@mcp.tool(
    name="list_modes",
    title="列出扒谱模式",
    description="列出所有可用的扒谱模式及其说明，用于向用户解释该选哪个。",
)
def list_modes_tool() -> dict[str, Any]:
    from pipeline import describe_modes

    return {
        "ok": True,
        "modes": [
            {
                "mode": m["mode"],
                "label": m["label"],
                "description": m["description"],
                "needsSeparation": m["separates"],
                "trackCount": m["tracks"],
                "page": m["page"],
            }
            for m in describe_modes()
        ],
    }


@mcp.tool(
    name="inspect_audio",
    title="探测音频文件",
    description=(
        "查看音频文件的基本信息（时长、格式、是否可读），不执行扒谱。\n"
        "用于在扒谱前先确认文件没问题——能提前发现 0 字节、损坏、"
        "伪装成音频的文本文件等情况。"
    ),
)
def inspect_audio_tool(input_path: str) -> dict[str, Any]:
    from format_guard import AudioFormatError, probe_duration, validate_audio_file

    try:
        validate_audio_file(input_path)
        dur = probe_duration(input_path)
        return {
            "ok": True,
            "name": os.path.basename(input_path),
            "path": os.path.abspath(input_path),
            "durationSeconds": round(dur, 2),
            "sizeBytes": os.path.getsize(input_path),
            "extension": os.path.splitext(input_path)[1].lower(),
        }
    except AudioFormatError as e:
        return {"ok": False, "error": e.user_message, "detail": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


@mcp.tool(
    name="environment_status",
    title="引擎能力自检",
    description=(
        "报告扒谱引擎的运行环境能力：Demucs 是否可用、GPU 加速是否可用、"
        "ffmpeg 是否就绪。用于在建议用户选择扒谱模式前先摸清环境。"
    ),
)
def environment_status_tool() -> dict[str, Any]:
    from format_guard import SUPPORTED_EXTS, find_ffmpeg
    from separator import capabilities

    caps = capabilities()
    return {
        "ok": True,
        "separation": {
            "demucsAvailable": caps["demucs"],
            "cudaAvailable": caps["cuda"],
            "device": caps["device"],
        },
        "ffmpegAvailable": caps["ffmpeg"],
        "ffmpegPath": find_ffmpeg(),
        "supportedFormats": SUPPORTED_EXTS,
        "notes": caps["notes"],
    }


def main() -> None:
    """
    stdio 传输启动。

    ### 踩坑实录（自造的坑，且症状极具误导性）
    初版在 `mcp.run("stdio")` **之前**做了：
        _real = sys.stdout
        sys.stdout = sys.stderr      # 「防止第三方库 print 污染协议流」
    结果 **initialize 永远超时**。用 strace 式排查（抓子进程全部输出）才看到真相：
    server 其实**正确回应了** `{"jsonrpc":"2.0","id":1,"result":{...,"serverInfo":{"name":"bapu"}}}`
    —— 但这条 JSON-RPC 应答被我的重定向推到了 stderr，客户端在 stdout 上干等。

    根因认知偏差：**MCP SDK 自己就管 stdout**（它用 `sys.__stdout__` 或底层
    文件描述符写帧），第三方库那点 print 干扰不到它。我防护的是不存在的问题，
    反而把唯一的通信通道堵死了。

    正解：**不碰 sys.stdout**。若真需要压制第三方库输出，用环境变量
    （TQDM_DISABLE / HF_HUB_DISABLE_PROGRESS_BARS）而不是改 stdout。

    这条与 `bridge.py` 的教训同源：**stdout 重定向必须先确认谁在用它**——
    bridge 里 emit 确实用 sys.stdout（所以那里该重定向并用 _real_stdout 兜住），
    mcp 这里 SDK 不走 sys.stdout（所以绝对不能重定向）。
    """
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

    # ⚠️ 故意不改 sys.stdout —— 见上方踩坑实录
    mcp.run("stdio")


if __name__ == "__main__":
    main()
