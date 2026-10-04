# worklog — win-<host> / raphael

> 本文件由 dev-flow skill 分发，逐字拷贝为 `worklog/<平台>-<机器名>-<写作者>.md` 后使用。
> 记录规则（第 0 铁律/分工/恢复流程/条目模板/双写顺序/记录纪律）见上一级目录 `../WORKLOG-PROTOCOL.md`，本文件不重复规则。
> 纪律：只写自己的文件；条目倒序（最新在顶）；开工开条目、收工更新同一条目不新开。

- 机器：<host>（Windows，<REPO>）
- 写作者：raphael（WorkBuddy / 智慧之王）
- 更名注记：本项目工作区目录原名 `MusicXML 格式`、仓库与产物原名 `musicxml-scribe`；2026-10-05 统一更名为 `audio2score`。本文件正文保留当时的原始记录，文中出现的旧名/旧路径均为历史事实，非当前值。
- 协议：`../WORKLOG-PROTOCOL.md`

---

<!-- 新条目插在此线下方、旧条目之上；条目格式见 WORKLOG-PROTOCOL.md §4 -->

## [P4-3]-raphael-20261005-0240 模型来源三跳治理 + 音频直扒页单轨化 — 2026-10-05 02:40 开始
- **执行者**: raphael（WorkBuddy / 智慧之王，机器：<host>）
- **目标**: 主上一句三合一指令，三件全做 ——
  ① 分离模型下载准备多个镜像源，哪个通走哪个（修老公机器 HF 超时）
  ② 功能页 2「基本扒谱」更名为更合适的名字
  ③ 该页支持**单轨输出**（分离好的人声出单轨做小提琴演奏谱；伴奏单轨留给别的乐器；
     **旧的别删**）
- **进展**:
  1. **自我纠正（我上一轮判错了）**：我原判「demucs 权重源是 `dl.fbaipublicfiles.com`，
     与 HuggingFace 无关」——**错误**。真因：demucs **4.1.0**（PyPI 2026-07-11）起，
     `pretrained.get_model()` 在 `repo is None` 时**先走 HuggingFace**（新增 `demucs/hf.py`），
     模块级常量 `ROOT_URL`（第 19 行）只服务于 `except` 兜底分支。
     线上报错逐字为 `HEAD huggingface.co/adefossez/HTDemucs/resolve/main/htdemucs.yaml`，
     与 `hf.py:26-34` 的名映射（`htdemucs`→`HTDemucs`）完全吻合。
     **教训：只读模块级常量会得出与真相反的结论，必须读函数体。**
  2. **三跳模型加载器**（`separator._load_separator_model`）：
     本地权重目录 → 官方直链 → HF 国内镜像（探活择优）。
     **先探活（HEAD，4s）再动手**——直接连不通的源要等系统 TCP 超时（Windows 约 21s）
     再叠 demucs 自身 5 次重试，用户看到的是「卡死数分钟」。
     镜像实测：`hf-mirror.com` 307→200 ✓ / `aifasthub.com` 200 ✓ / `hf-api.gitee.com` 404 /
     `mirror.sjtu.edu.cn/hugging-face` 404 / `hf.maizi.cc` DNS 失败 / `huggingface.co` 超时。
     两个可用镜像均验证**能真正服务权重**（HTTP 206），不是只回个 200。
  3. **版本治理**：`bootstrap.DEMUCS_PACKAGES` pin `demucs>=4.1,<4.2`，允许补丁号、挡住
     可能改变下载行为的破坏性变更。
  4. **功能页 2 更名**：「基本扒谱」→ **「音频直扒」**（与页 1「歌曲扒谱」四字对仗；
     「直扒」= 不分离、直接识别）。同步 App tab / Settings 能力项 / Onboarding 指引与
     模式表 / 引擎 `describe_modes` / CLI epilog / MCP 工具说明 / 各处注释。
  5. **单轨化（本次核心）**：**新增**两条 `page:2` 模式，**不改任何旧模式** ——
     - `basic_vocals`「单轨直扒（人声旋律）」：走 pYIN 单旋律。
       人声与独奏小提琴都是单声部，CQT 多音高会把泛音误判成和声声部，
       生成的谱面出现大量无法演奏的假声部。
     - `basic_accompaniment`「单轨直扒（伴奏多音高）」：走 CQT，留给钢琴/吉他等复音乐器。
     该页默认模式改为 `basic_vocals`（主上主场景：直播放伴奏、琴拉人声部分）。
  6. **顺手修掉两处同类「静默吞文件」缺陷**（与本次页面改动同源）：
     - `basic_multi`：初版只转写 `req.input_path`，而前端拖放区允许多选 6 个 →
       后 5 个被**静默丢弃**。改为逐轨处理；多文件时前端传 `Instrument N` 轨名
       （轨名必须纯 ASCII，`midi_post` 只允许 `\x20-\x7e`，故不能用中文文件名）。
     - 模式切换不裁剪文件：多轨丢 3 个文件后切到单轨，多余文件留在列表里被引擎忽略。
       新增 `useWorkspace.maxFiles` + `trimFilesToCapacity()`，容量**由模式统一决定**，
       而不是只把 `max` 写在 DropZone 上。
  7. 版本号 0.1.0 → **0.1.1**（仅 `tauri.conf.json` + `Cargo.toml` 两处），
     理由：安装包文件名可辨识，避免老公那边装了旧版还以为没修。
- **验证**:
  - **新探针 `tests/manual/probe_single_track_modes.py`**：合成音频（标准库 wave，零依赖）
    跑 4 用例，**全 PASS** ——`basic_vocals` 1 轨/Voice、`basic_accompaniment` 1 轨/Accompaniment、
    `basic_multi` 3 文件出 3 轨（`Instrument 1..3`，14 音符）、`basic` 回归 1 轨；
    四者 `separationMethod=None` 且进度阶段里**无 `separate`** → 确认未调动 Demucs
  - **新探针 `tests/manual/probe_modes_frontend_sync.py`**：引擎 `describe_modes()` 与前端
    降级副本 `STUB_MODES` 逐字段比对 → 8 字段 × 8 模式 PASS；并做**负向验证**
    （故意改一个字 → 报 FAIL 且精确指出字段）。
    此前这条「单一真源」只靠注释提醒「改引擎时同步改这里」——**注释不会执行**。
  - `tests/test_progress_monotonic.py`：从 6 模式扩到 9 用例（含两个新模式与
    `basic_multi` 三文件逐轨），**总倒退 0**
  - 前端 `tsc -b` 0 错；`oxlint` **0 warnings 0 errors**（基线要求）
  - `npm run tauri:build`（工作区根目录）→ `扒谱助手_0.1.1_x64-setup.exe` 13.39 MiB
    （体积从早期 1.32MB 变为约 14MB 是 `src-tauri/runtime` 50MB 独立 CPython 入包所致，
    与本次改动无关）
- **决策与坑**:
  - **坑（我的结论被现场反证）**：读常量得「与 HF 无关」，读函数体得「优先 HF」。
    主上报的错就是我判「不可能」的那条路径。**先怀疑自己写的代码与自己的结论。**
  - **坑（本地缓存掩盖故障）**：开发机 `~/.cache/huggingface/hub/models--adefossez--HTDemucs`
    已存在，HF 分支本地命中即返回，**开发态从不复现**。探针用 `HF_HOME=<tmp>` 等效
    「一台从没下过模型的机器」，才复现出现场（S1：167.4s、6 次 HF 请求；S2/S3：0 次）
  - **镜像 JSON 与 `/simple/` 不同步**：清华 `/pypi/demucs/json` 陈旧仍报 4.0.1，
    但 `/simple/demucs/` 已含 4.1.0 whl。判断「能不能装到某版本」要看 `/simple/` 页
  - `tracks: 0` 作为「轨数随输入文件数变化」的约定值（`basic_multi`），
    前端须显示「多轨」而非「0 轨」；MCP 侧同步给 `trackCount: null` + `trackCountNote`
- **代码状态**: 仅本地未 push（仓库无 remote）
- **状态**: ✅完成
- **交付数据**:`npm run tauri:build`（工作区根）3m13s → release 编译 2m29s →
  `扒谱助手_0.1.1_x64-setup.exe` **14,048,346 字节（13.40 MiB）**，
  MD5 `030419ab14e0dc1b5cf0adbf58ed392f`；
  以 `7z l` 对账：**1667 files**，`engine\*.py` 8 个 + `engine\tools\*.py` 4 个全在
  （pipeline/bootstrap/cli/mcp_server/separator 时间戳均为本次，证明新代码入包），
  `runtime\python\python.exe`（91648 B）在包内 → 无 Python 机器仍可首启引导安装。
  已归档 `<DOCS>\扒谱助手\`，MD5 与构建产物逐字节一致；
  0.1.0 版原地保留（版本号不同故无需 `.bak` 覆盖）。
- **下一步**: 老公机器实测三件事 ——① 首次分离不再卡在 huggingface.co
  （应走官方直链，或 hf-mirror/aifasthub）② 音频直扒页能看到「单轨直扒（人声旋律）」
  且默认选中 ③ 出单轨 MIDI 可直接在 MuseScore 打开演奏

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

## [P1]-raphael-20261004-1226 引擎六路径端到端打通 — 2026-10-04 12:26 开始
- **执行者**: raphael（机器：<host>）
- **目标**: 让六条产品路径全部端到端产出 MuseScore 可用的 MIDI（需求 A1 + A5）
- **上下文**: 依赖 P0（arch-scout 侦察报告）与已修的三个致命缺陷
- **进展**:
  - 写 `engine/pipeline.py`：六模式编排（full_auto / accompaniment / vocals / basic / basic_multi / pre_separated），四阶段进度上报
  - 规避上游 D-6：`perceptual_filter` 显式传 `melody_split=False`（上游默认把 4 和弦 18 音符砍到 3）
  - 规避上游 D-11：`compute_cqt(fmax=4186.0)`（上游硬编码 2093 切掉高音声部）
  - 规避上游 D-15：**禁用 CREPE**（`_ensure_crepe_script()` 会往上游只读目录写文件），人声一律走 pYIN
  - **六路径实测全部通过**（chord_progression.wav 为输入）
  - **MuseScore 4 headless 实机验证 5/5 全 PASS**
- **验证**:
  - `cd engine && ../engine/.venv/Scripts/python.exe pipeline.py full_auto <wav> <out.mid>`
  - MuseScore 验证：`Voice(1音) + Accompaniment(9音) / Instrument(9音) / Voice(1音) / Accompaniment(9音) / Voice+Accompaniment`
- **决策与坑**:
  - **坑（自造假警报，已纠正）**：验证脚本在项目根目录跑却用 `../.tmp/` 相对路径，指向不存在的 `<TMP>`，导致 5 个文件全报 rc=1320，一度被误判为「MIDI 全坏」。**判据：验证失败时先确认验证脚本自己没错，再怀疑产物。** 已在 `midi_post.verify_musescore_opens` 内改用 `os.path.abspath` 并把该教训写进 docstring
  - **坑（吞错反噬）**：`separator.py` 早期版本把 demucs 真实异常吞掉直接降级 HPSS，表现为「分离质量差」，掩盖了真因（apply_model 传了 3.x 旧参数 `ref`）。**教训：降级不能吞掉失败原因**，现已完整保留失败链并在 UI/日志暴露
  - demucs 4.1.0 API 三处坑：`apply_model()` 无 `ref` 参数 / `save_audio(wav, path, sr)` 波形在前 / `num_workers` 在 Windows 有并发风险改 0
  - 禁用 CREPE 是**红线 B-1 的硬性要求**，非偏好选择
- **代码状态**: 仅本地未 push（仓库无 remote）
- **状态**: ✅完成
- **下一步**: 派 engine-dev 分身写 Tauri command 桥与两个功能页前端；ui-visual 原型评审后落主题

## [P4]-raphael-20261004-1305 应用端到端打通 + 三轮自测收官 — 2026-10-04 13:05 开始
- **执行者**: raphael（机器：<host>）
- **目标**: 让 Tauri 应用真正跑起来，并完成三轮对抗式自测（需求 G9 慎之勇者态度）
- **进展**:
  - 工作区根建 package.json（tauri CLI 必须在含 tauri.conf.json 的项目根跑，`src/` 下跑会 panic）
  - `npx tauri build --no-bundle` 完整构建：vite build + Rust release 全通
  - release 二进制 3.4MB，常驻内存 30.6MB
  - **真机启动验证通过**：界面完整渲染（两功能页/霜蓝主题/无边框窗口/参数面板/状态卡）
  - 三轮自测全部收官
- **验证**:
  - `npm run tauri:build -- --no-bundle` → Built application at src-tauri/target/release/musicxml-scribe.exe
  - `engine/tests/test_progress_monotonic.py` → 6/6 OK
  - `engine/tests/adversarial.py` → 23/23 PASS
  - 格式矩阵 9/9、MuseScore headless 5/5、异常输入 7/7
- **决策与坑**:
  - **坑（启动方式错）**：`cargo build --release` 出的二进制仍指向 devUrl（localhost:1420），
    首启白屏报 `ERR_CONNECTION_REFUSED`。**正解是 `tauri build`**——它先跑
    vite build 再把 dist 嵌入，cargo build 不会。`beforeBuildCommand` 只在
    tauri build 时执行
  - **坑（我改坏了一次）**：`beforeBuildCommand` 原为 `npm --prefix ../src run build`，
    但 tauri CLI 以 src-tauri 的父目录为 cwd，`../src` 指向错误位置 → 改为 `src`
  - **第三轮自测修 3 个真缺陷**：tempo=0 崩溃 / 伪造文件放行 / probe 假 OK 中间态
  - **修 4 个测试脚本假警报**：全部源于「测试自己有 bug 却当成产品缺陷」。
    核心判据已写入 review/01-round3-adversarial.md
- **代码状态**: 仅本地未 push（仓库无 remote）
- **状态**: ✅完成
- **下一步**: ① NSIS installer 打包（plan P4）② 主上手动验收主路径
  ③ 引擎随包分发方案落地（venv 约 2.5GB，打包策略待定）

## [P4-2]-raphael-20261004-1338 三项遗留收官 — 2026-10-04 13:38 开始
- **执行者**: raphael（机器：<host>）
- **目标**: NSIS 打包 / 干净环境引导安装实测 / 冷门格式白名单治理
- **进展**:
  1. **NSIS installer 打包完成**：`扒谱助手_0.1.0_x64-setup.exe` 1.32 MiB，
     已归档 `<DOCS>\扒谱助手\`
  2. **干净环境引导安装实测通过**：basic 档 64 秒装完，状态汇报与实况一致
  3. **冷门格式白名单改为可自证**：按 ffmpeg 解码器存在性动态判定
  4. **打包兼容性修复**：上游路径从单点假设改为多候选探测
- **验证**:
  - installer 内容核对：engine 8 个 .py + tools 2 个 + third_party 11 个 = 21 个全在；
    5.4GB 的 .venv 正确排除（installer 仅 1.32MB 佐证）
  - 干净副本（无 venv）状态检测：`ready:false / reason:解释器不存在` ✓
  - basic 档实装 64 秒 → `基础依赖 ✓ · Demucs ✗（降级）· MCP ✗`
  - basic 档环境实跑扒谱 → 9 音符，MuseScore 可用格式
- **决策与坑**:
  - **坑（状态检测假成功）**：basic 档环境 `import mcp` **竟成功**，但 pip list 无
    mcp 且 `mcp.__file__ is None`——某包注册了顶层命名空间包 `mcp` 造成假成功。
    正解：要求 `__file__` 非 None 才算已装。**教训：import 成功不等于包真装了。**
  - **坑（打包后才暴露的路径脆弱）**：`pipeline.py` 只认「上两级/third_party」一种
    布局，开发态成立，但 Tauri 打包后资源目录重排就会
    `ModuleNotFoundError: No module named 'AutoTranscriber'`——**本地开发永远测不到**。
    正解：5 个候选位置逐个探测（含 `__init__.py` 存在性检查），全不命中给可操作指引
  - **坑（我自造的过度承诺）**：早期把 ape/mpc/tta 一股脑列进白名单「顺手支持」，
    但本机 ffmpeg 只有解码器无编码器，连样本都造不出来——即无法验证却不敢删。
    UI 会向主上宣称支持，用户拖进来才发现不行。
    正解：改为按 `ffmpeg -decoders` 探测结果动态决定，实测本机 205 个解码器
  - **坑（我的验证方法本身有缺陷）**：用「字节扫描 exe 找文件名」判断包内容，
    报告「pipeline.py 缺」并误以为漏打包——实际 NSIS 压缩存储扫不到明文，
    资源全在。教训：验证方法本身要先自证有效
- **代码状态**: 仅本地未 push
- **状态**: ✅完成
- **下一步**: 主上手动验收主路径（拖入真实歌曲 → 双轨扒谱 → MuseScore 打开）
