# worklog — win-<host> / raphael

> 本文件由 dev-flow skill 分发，逐字拷贝为 `worklog/<平台>-<机器名>-<写作者>.md` 后使用。
> 记录规则（第 0 铁律/分工/恢复流程/条目模板/双写顺序/记录纪律）见上一级目录 `../WORKLOG-PROTOCOL.md`，本文件不重复规则。
> 纪律：只写自己的文件；条目倒序（最新在顶）；开工开条目、收工更新同一条目不新开。

- 机器：<host>（Windows，<REPO>）
- 写作者：raphael（WorkBuddy / 智慧之王）
- 协议：`../WORKLOG-PROTOCOL.md`

---

<!-- 新条目插在此线下方、旧条目之上；条目格式见 WORKLOG-PROTOCOL.md §4 -->

## [design]-raphael-20261004-1201 扒谱桌面应用立项 — 2026-10-04 12:01 开始
- **执行者**: raphael（WorkBuddy 总监，机器：<host>）
- **目标**: 完成立项解构 + 团队设计 + 需求/边界/计划三文档落地，进入 P0 引擎层
- **上下文**: 主上基于 https://github.com/GuyueHermit/AutoTranscriber 新立项；已确认裁决：Tauri 2 + React/TS + Tailwind；4 版视觉方向全做且可切换；**导出仅 MIDI**（不做 MusicXML）
- **进展**:
  - 勘察环境：RTX 3080 10GB / ffmpeg 5.1.2 / MuseScore 4 / cargo 1.97 / node 22 / D 盘余 60G / librosa 未装
  - vendor 上游至 `third_party/AutoTranscriber`（只读，depth=1，HEAD c7b7981）
  - 建 venv `engine/.venv`（Python 3.13.14），装 torch 2.6.0+cu124 / librosa 1.0.0 / demucs 4.1.0 / av / pretty_midi 0.2.11，**CUDA 实测 True**
  - 逐字拷贝 DISCIPLINE.md / WORKLOG-PROTOCOL.md 入 `docs/desktop/v1/`
  - 写 `01-requirement.md`（五段齐全）、`BOUNDARY.md`（B-1~B-4）、`plan.md`（P0~P4）
  - 派发 2 分身：arch-scout（上游 API 契约侦察）、ui-visual（4 版视觉原型）
  - **总监亲手完成 G4/A2 音频格式通吃**：
    - 实测发现上游 `load_audio` → `librosa.load()`，librosa 1.0 走 soundfile(libsndfile) 后端**不经过 ffmpeg**
    - 实测 7 格式：wav/mp3/ogg/opus/aiff 直读 OK；**m4a/aac/wma 全部 FAIL**（`LibsndfileError: Format not recognised`）
    - 上游 docstring（第 17-22 行）**明确声称支持 m4a/aac/wma** → 文档与实现不符，属上游缺陷
    - 写 `engine/format_guard.py` 适配层（守住红线 B-1，不改上游）：不在白名单的格式先经 ffmpeg 转 44.1kHz 16-bit PCM WAV 再交上游
    - 踩坑：ffmpeg 目标编码器写 `pcm_s16bit` 报 "Unknown encoder"，本机 gyan full_build 只有 `pcm_s16le`/`pcm_s16be`，WAV 封装器默认小端 → 改 `pcm_s16le` 修复
    - **复测 7/7 全通**（aac/m4a/wma 走转码路径，dur 与原 wav 一致）
    - **异常对抗测试 8/8 全拦**：不存在文件 / 目录 / 0 字节 / 500 字节 / 纯文本伪装 mp3 / 伪造 wav 头 / 不支持扩展名 / 中文空格路径
- **验证**:
  - 依赖：`cd "<REPO>" && engine/.venv/Scripts/python.exe -c "import torch,librosa,demucs,av,pretty_midi;print(torch.__version__, torch.cuda.is_available(), librosa.__version__, demucs.__version__)"`
  - 格式矩阵：`engine/.venv/Scripts/python.exe engine/format_guard.py .tmp/fmt/test.m4a`
  - 单点自检：`engine/.venv/Scripts/python.exe engine/format_guard.py third_party/AutoTranscriber/test_audio/chord_progression.wav`
- **决策与坑**:
  - 裁定：上游 `third_party/` 零改动（红线 B-1），一切适配走 `engine/`
  - 主上明确「不要品红+黑色」——记入 BOUNDARY B-2，列为审美红线
  - 主上选择「仅 MIDI」，不实现 MusicXML 导出器（大幅缩减 P3 工作量）
  - curl 在本机无 DNS，改用 WebFetch 取纪律原文
  - **坑（假警报，已排除）**：pip 安装进行中测 `import torch` 报 `OSError [WinError 126] caffe2_nvrtc.dll`。用 ctypes.CDLL 单独加载该 DLL 成功 → 判定为**安装未完成时的瞬态缺依赖**，非环境问题。安装结束后复测全绿。**教训：后台依赖安装完成前不做 import 验证，否则误判为环境损坏。**
  - **坑**：Write 工具写 plan.md 时路径写成 `docs/desktop/1..plan.md`（漏 `v1`），已 `mv` 修正为 `v1/plan.md`
  - **上游缺陷 #1（已绕开）**：`audio_loader.py:17-22` docstring 声称支持 m4a/aac/wma，实测全 FAIL。README/docstring 不可信，一切以源码+实测为准
  - **上游缺陷 #2**：librosa 1.0 移除 audioread 默认后端，上游未加 ffmpeg fallback，高价值格式（m4a 为网易云/Apple Music 常见）静默失败
- **代码状态**: 仅本地未 push（仓库刚 init，无 remote）
- **状态**: 🔄进行中
- **下一步**: 收 arch-scout 的 API 契约报告 → 写 `engine/bridge.py` 适配层 → 打通端到端单文件扒谱
