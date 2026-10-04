"""
probe_modes_frontend_sync — 引擎模式元信息 与 前端桩副本 的一致性探针

## 要回答的问题
模式文案的**唯一真源**是 `engine/pipeline.py:describe_modes()`，前端通过
`get_modes` 取用。但 `src/src/lib/ipc.ts` 里有一份 `STUB_MODES` 副本，
供「无 Tauri 运行时的浏览器预览」降级使用。

这份副本过去只靠注释提醒「改引擎时同步改这里」——而**注释不会执行**。
一旦漂移，症状是：真机（Tauri）里看不到新模式，浏览器预览里却看得到，
排查时极易误判成「引擎没装好」。本探针把这条人工约定变成可执行的检查。

## 检查项
    mode / page / label / description / hint / separates / tracks / roles
九条模式逐字段比对，任何不一致即 FAIL 并指出具体字段。

## 用法
    engine/.venv/Scripts/python.exe engine/tests/manual/probe_modes_frontend_sync.py
    # 指定别的 ipc.ts（用于负向验证：故意改一个字，探针必须报 FAIL）
    ... --ipc /path/to/ipc.ts
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_DIR.parent
IPC_TS = REPO_ROOT / "src" / "src" / "lib" / "ipc.ts"

# 从 STUB_MODES 的字面量起点开始做花括号配对，避免写脆弱的正则
_EXTRACT_JS = """
import fs from 'node:fs'
const src = fs.readFileSync(process.argv[2], 'utf8')
const decl = src.indexOf('const STUB_MODES')
if (decl < 0) { console.error('未找到 const STUB_MODES'); process.exit(2) }
const start = src.indexOf('{', src.indexOf('=', decl))
let depth = 0, end = -1
for (let i = start; i < src.length; i++) {
  const c = src[i]
  if (c === '{') depth++
  else if (c === '}') { depth--; if (depth === 0) { end = i + 1; break } }
}
if (end < 0) { console.error('STUB_MODES 花括号不配对'); process.exit(2) }
const obj = new Function('return (' + src.slice(start, end) + ')')()
process.stdout.write(JSON.stringify(obj.modes))
"""

FIELDS = ("mode", "page", "label", "description", "hint", "separates", "tracks", "roles")


def _frontend_modes(ipc_path: Path) -> list[dict]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("找不到 node，无法解析 ipc.ts 的 STUB_MODES")
    with tempfile.TemporaryDirectory(prefix="bapu-sync-") as tmp:
        script = Path(tmp) / "extract.mjs"
        script.write_text(_EXTRACT_JS, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script), str(ipc_path)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"提取 STUB_MODES 失败：{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def run(ipc_path: Path) -> int:
    sys.path.insert(0, str(ENGINE_DIR))
    from pipeline import describe_modes

    engine_modes = describe_modes()
    try:
        front_modes = _frontend_modes(ipc_path)
    except RuntimeError as e:
        print(f"  跳过：{e}")
        print("  （探针无法执行，不代表一致 —— 请在有 node 的环境复跑）")
        return 2

    problems: list[str] = []

    if [m["mode"] for m in engine_modes] != [m["mode"] for m in front_modes]:
        problems.append(
            "模式集合或顺序不一致：\n"
            f"    引擎：{[m['mode'] for m in engine_modes]}\n"
            f"    前端：{[m['mode'] for m in front_modes]}"
        )

    front_by_mode = {m["mode"]: m for m in front_modes}
    for em in engine_modes:
        fm = front_by_mode.get(em["mode"])
        if fm is None:
            continue
        for key in FIELDS:
            if em.get(key) != fm.get(key):
                problems.append(
                    f"[{em['mode']}] 字段 {key} 不一致：引擎={em.get(key)!r} 前端={fm.get(key)!r}"
                )

    print(f"引擎模式数：{len(engine_modes)}  前端桩模式数：{len(front_modes)}")
    if problems:
        print("\n结果：FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"\n结果：PASS（{len(FIELDS)} 个字段 × {len(engine_modes)} 条模式逐项一致）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="引擎模式元信息与前端桩副本的一致性检查")
    ap.add_argument("--ipc", default=str(IPC_TS), help="ipc.ts 路径（默认仓库内的那份）")
    args = ap.parse_args()
    raise SystemExit(run(Path(args.ipc)))
