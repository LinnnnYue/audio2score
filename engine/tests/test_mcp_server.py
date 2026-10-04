"""
test_mcp_server.py — MCP 服务端真机握手测试

## 为什么不用「跑起来不报错」当验收
MCP 走 stdio JSON-RPC：进程能启动 ≠ 协议通。必须真正完成
initialize → tools/list → tools/call 三步握手才算数。

本测试用 mcp SDK 自带客户端起真实子进程，测真实协议。

用法：python engine/tests/test_mcp_server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

PY = os.path.join(ENGINE, ".venv", "Scripts", "python.exe")
SERVER = os.path.join(ENGINE, "mcp_server.py")
WAV = os.path.abspath(
    os.path.join(
        ENGINE, "..", "third_party", "AutoTranscriber", "test_audio",
        "chord_progression.wav",
    )
)
OUT_DIR = os.path.abspath(os.path.join(ENGINE, "..", ".tmp", "mcp"))

results: list[tuple[str, str, str]] = []


def record(cat: str, name: str, ok: bool, note: str = "") -> None:
    results.append((cat, name, ("PASS" if ok else "FAIL") + (f" — {note}" if note else "")))


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    os.makedirs(OUT_DIR, exist_ok=True)

    if not os.path.isfile(PY):
        record("环境", "venv 解释器存在", False, PY)
        return 1

    params = StdioServerParameters(command=PY, args=[SERVER], env=None)

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                # ── 1. 握手 ──
                init = await asyncio.wait_for(session.initialize(), timeout=30)
                # 踩坑实录：mcp 2.x 的 InitializeResult 字段是 snake_case
                # （server_info），1.x 是 camelCase（serverInfo）。
                # 照 1.x 记忆写会 AttributeError。此处双路兼容。
                info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
                sname = getattr(info, "name", "?") if info else "?"
                sver = getattr(info, "version", "") if info else ""
                record("握手", "initialize", bool(sname and sname != "?"),
                       f"server={sname} v{sver}")

                # ── 2. 列工具 ──
                tools = await asyncio.wait_for(session.list_tools(), timeout=30)
                names = [t.name for t in tools.tools]
                expect = {"transcribe", "list_modes", "inspect_audio", "environment_status"}
                missing = expect - set(names)
                record("工具", "四个工具全部注册", not missing,
                       f"共 {len(names)} 个: {', '.join(names)}"
                       + (f" 缺: {missing}" if missing else ""))

                for t in tools.tools:
                    if not t.description:
                        record("工具", f"{t.name} 有描述", False, "description 为空")
                        break
                else:
                    record("工具", "全部工具均有描述", True)

                # ── 3. 调 environment_status ──
                r = await asyncio.wait_for(session.call_tool("environment_status", {}), timeout=60)
                payload = _extract(r)
                record("调用", "environment_status", bool(payload.get("ok")),
                       f"demucs={payload.get('separation', {}).get('demucsAvailable')} "
                       f"cuda={payload.get('separation', {}).get('cudaAvailable')}")

                # ── 4. 调 list_modes ──
                r = await asyncio.wait_for(session.call_tool("list_modes", {}), timeout=60)
                payload = _extract(r)
                modes = payload.get("modes", [])
                record("调用", "list_modes", len(modes) == 6, f"{len(modes)} 种模式")

                # ── 5. 调 inspect_audio ──
                r = await asyncio.wait_for(session.call_tool("inspect_audio", {"input_path": WAV}), timeout=60)
                payload = _extract(r)
                record("调用", "inspect_audio", bool(payload.get("ok")),
                       f"时长 {payload.get('durationSeconds')}s")

                # ── 6. 调 transcribe（核心）──
                out = os.path.join(OUT_DIR, "mcp_out.mid")
                r = await asyncio.wait_for(
                    session.call_tool(
                        "transcribe",
                        {"input_path": WAV, "mode": "basic", "output_path": out},
                    ),
                    timeout=300,
                )
                payload = _extract(r)
                n = payload.get("totalNotes", 0)
                record("调用", "transcribe 产出 MIDI", bool(payload.get("ok")) and n > 0,
                       f"{n} 音符 → {os.path.basename(str(payload.get('output')))}")
                record("调用", "MIDI 文件落盘", os.path.isfile(out),
                       f"{os.path.getsize(out)}B" if os.path.isfile(out) else "未生成")

                # ── 7. 错误路径不应崩 ──
                r = await asyncio.wait_for(
                    session.call_tool(
                        "transcribe", {"input_path": "不存在的文件.mp3", "mode": "basic"}
                    ),
                    timeout=60,
                )
                payload = _extract(r)
                record("错误处理", "不存在文件返回结构化失败",
                       payload.get("ok") is False and bool(payload.get("error")),
                       str(payload.get("error"))[:40])

                r = await asyncio.wait_for(
                    session.call_tool(
                        "transcribe", {"input_path": WAV, "mode": "pre_separated"}
                    ),
                    timeout=60,
                )
                payload = _extract(r)
                record("错误处理", "pre_separated 缺参数有提示",
                       payload.get("ok") is False, str(payload.get("error"))[:40])

    except Exception as e:  # noqa: BLE001
        record("握手", "MCP 会话建立", False, f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 68)
    print("MCP 服务端真机握手测试")
    print("=" * 68)
    cur = None
    npass = nfail = 0
    for cat, name, verdict in results:
        if cat != cur:
            print(f"\n[{cat}]")
            cur = cat
        print(f"  {verdict:6s} {name}")
        if verdict.startswith("PASS"):
            npass += 1
        else:
            nfail += 1
    print("\n" + "=" * 68)
    print(f"通过 {npass} / 失败 {nfail} / 共 {len(results)}")
    print("=" * 68)
    return 0 if nfail == 0 else 1


def _extract(result) -> dict:
    """从 CallToolResult 里取出结构化内容。"""
    # mcp 2.x 优先 structuredContent
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict) and sc:
        # 去掉 SDK 可能加的包装层
        if "result" in sc and isinstance(sc["result"], dict):
            return sc["result"]
        return sc
    for c in getattr(result, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
    return {}


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
