#!/usr/bin/env bash
# 扒谱助手 · 开发态启动
#
# ⚠️ 必须用 `npm run tauri:dev`（或 tauri build），**不能用 cargo build/run**。
#    cargo 只编 Rust，产物仍指向 devUrl(http://localhost:1420)，
#    双击 exe 会白屏报 ERR_CONNECTION_REFUSED。
#    这是本项目真实踩过的坑（见 docs/desktop/v1/review/02-round4-user-feedback.md）。

set -e
cd "$(dirname "$0")"

MODE="${1:-dev}"
PY="engine/.venv/Scripts/python.exe"

echo "[run] 检查引擎…"
if [ ! -x "$PY" ]; then
  echo "[run] 未找到引擎 venv。先安装："
  echo "       py engine/bootstrap.py --tier full"
  exit 1
fi
"$PY" engine/bootstrap.py --status

case "$MODE" in
  dev)
    echo "[run] 启动开发态（Vite + Tauri）…"
    npm run tauri:dev
    ;;
  build)
    echo "[run] 构建 release（含 dist 嵌入）…"
    npm run tauri:build
    ;;
  installer)
    echo "[run] 打包 NSIS installer…"
    npm run tauri:build
    ls -la src-tauri/target/release/bundle/nsis/
    ;;
  run)
    echo "[run] 启动已构建的 release 版…"
    "./src-tauri/target/release/musicxml-scribe.exe"
    ;;
  *)
    echo "用法: ./run.sh [dev|build|installer|run]"
    exit 2
    ;;
esac
