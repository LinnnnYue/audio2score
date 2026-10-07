<div align="center">

<img src="src-tauri/icons/icon.png" width="134" alt="扒谱助手图标">

# 扒谱助手 · audio2score

**把音频变成能编辑、能打印的乐谱。**
拖进去 → 选模式 → 点按钮，得到 **MuseScore 可直接打开的 MIDI**。

[![版本](https://img.shields.io/github/v/release/LinnnnYue/audio2score?label=version&color=2ea043)](https://github.com/LinnnnYue/audio2score/releases)
![平台](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011%20x64-lightgrey)
![许可](https://img.shields.io/badge/license-%E4%BB%85%E4%BE%9B%E5%AD%A6%E4%B9%A0%E7%A0%94%E7%A9%B6-orange)

[官网](https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd) · [说明文档](https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd#docs) · [下载](https://github.com/LinnnnYue/audio2score/releases/latest/download/audio2score-setup.exe)

</div>

---

## 这是什么

上游扒谱内核（[AutoTranscriber](https://github.com/GuyueHermit/AutoTranscriber)）本身已能扒谱，但**只有命令行入口**：要手敲十多个开关（`--n_peaks`、`--hop_length`、`--onset_threshold`、`--demucs_model` …），模式之间还有参数耦合（人声要 `n_peaks=2`、和弦要 5–8），普通用户根本用不起来；长任务又毫无进度反馈，不知道是不是卡死了。

**扒谱助手**把这些封进一个桌面应用：不碰命令行，三步出 MIDI。

- 拖入音频 → 选模式 → 点按钮
- 自动分离人声 / 伴奏，分别扒成独立音轨
- 输出标准 MIDI，轨名、乐器、速度都已处理好，MuseScore 4 打开即可继续编辑

---

## 快速开始

1. 直接下载 **[audio2score-setup.exe](https://github.com/LinnnnYue/audio2score/releases/latest/download/audio2score-setup.exe)**（永远指向最新版），或到 **[Releases](https://github.com/LinnnnYue/audio2score/releases)** 自行挑选版本（版本名资产形如 `audio2score_x.x.x_x64-setup.exe`）。
2. 安装并首次启动。应用会引导你**联网安装扒谱引擎**（引擎体积大，不随安装包分发）。
3. 选择安装档位（见 [引擎安装档位](#引擎安装档位)），等待完成。
4. 拖入音频 → 选模式 → 点「开始」→ 得到 `.mid`。

> 想先试试水，装 `basic` 档就能跑；确认要分离人声/伴奏，再升级到 `full`。

---

## 扒完之后怎么看谱？—— 配一个 MuseScore

**这是新手最容易卡住的一步，所以放在前面说。**

扒谱助手输出的是 **MIDI（`.mid`）**。它是一份「音符清单」——只记录音高、时长、力度，
里面**没有五线谱排版，也不能直接打印**。要"看谱、改谱、导出 PDF"，需要一个免费的制谱软件：
**[MuseScore](https://musescore.org/zh-hans/download)**（开源免费，Windows / macOS / Linux 都有）。

<div align="center">

### ➡️ [下载 MuseScore 4（官方 · 免费）](https://musescore.org/zh-hans/download)

</div>

四步走完，你就能拿到一份 PDF 谱子：

| 步骤 | 做什么 | 怎么做 |
|---|---|---|
| **1** | 装好 MuseScore 4 | 从上面的传送门下载安装，一路默认即可 |
| **2** | 打开扒出的 `.mid` | 扒完在结果卡里点<kbd>用 MuseScore 打开</kbd>；或先记下文件位置，手动在 MuseScore 里 <kbd>文件</kbd> → <kbd>打开</kbd> 选它 |
| **3** | 先看一眼，再动手改 | 确认 **BPM 是真实速度**（不对的话小节线会整体错位）；哪个音符不合适就在谱面上直接改 |
| **4** | 导出 PDF | <kbd>文件</kbd> → <kbd>导出</kbd> → 格式选 **PDF** |

> 📖 **更细的分步图解**（谱面怎么读、每个按钮在哪、常见报错怎么办）见 **[说明文档 · §07 拿到 MIDI 之后](https://workbuddy.link/p/6gCdf83meMdb9Zoc2dZJkd#s7)**。

### 扒出来的文件放在哪？

默认与源音频放在**同一目录**——音频在哪，谱就在哪。

想让它固定落到某个文件夹（比如专门的「曲谱」盘）：

**设置 → 输出位置 → 选择目录** —— 设一次，之后每首的 `.mid` 都会放进那里，文件名与源音频一致。

单个任务里也可以点底部的「另存为」临时改到别处，不会改动上面的默认设置。

---

## 功能

| 能力 | 说明 |
|---|---|
| **零命令操作** | 拖入音频 + 选模式 + 点按钮，三步得到 MIDI，全程不接触命令行 |
| **人声 / 伴奏分离** | 基于 Demucs（htdemucs）AI 音源分离，人声与伴奏分轨输出 |
| **六条扒谱路径** | 全自动双轨 · 只扒伴奏 · 只扒人声旋律 · 基本扒谱（单/多音轨）· 已分离音频直入 |
| **主流格式通吃** | mp3 / wav / flac / ogg / opus / aiff 原生支持；m4a / aac / wma 等经 ffmpeg 转码兜底，失败给明确中文原因而非堆栈 |
| **过程可见** | 分离 / 频谱分析 / 音符追踪 / 生成 MIDI 四阶段进度 + 百分比 + 实时日志 |
| **输出位置可设** | 默认与源音频同目录；也可指定固定目录，之后每首都落在那里 |
| **MuseScore 可用** | 导出的 `.mid` 轨名正确（人声 / 伴奏 / 乐器名）、program 合理、tempo 正确 |
| **多入口同源** | GUI / CLI / MCP 三种接法共用同一套引擎核心，行为完全一致 |
| **四套视觉** | 霜蓝玻璃 / 暗房琥珀 / 深海声谱 / 素纸墨青，运行时热切换 |

---

## 引擎安装档位

引擎不随安装包分发，首次启动时联网安装。三档可选：

| 档位 | 内容 | 体积 | 耗时 | 适用 |
|---|---|---|---|---|
| `basic` | librosa / scipy / pretty_midi / soundfile / av | 约 200 MB | 1–2 分钟 | 基本扒谱（**不做人声伴奏分离**） |
| `full`（默认，推荐） | basic + torch(cu124) + demucs | 约 5.2 GB | 10–25 分钟 | 完整功能（分离人声 / 伴奏） |
| `mcp` | full + MCP SDK | 约 5.2 GB | +10 秒 | 额外提供 MCP 服务端 |

> 下载走国内镜像择优（镜像与版本约束在安装前会校验），无需自备网络工具。
> 引擎默认装在 `%LOCALAPPDATA%`，若 C 盘吃紧，可在设置页把它整体迁到别的盘。

---

## 从源码构建

### 环境要求

- **Windows 10 / 11 x64**
- **Node.js 18+**
- **Rust**（stable 工具链，含 `cargo`）
- **Python 3.10 – 3.13**（3.14 尚无 torch wheel，勿用）

### 步骤

```bash
git clone https://github.com/LinnnnYue/audio2score.git
cd audio2score

# 1) 前端依赖
npm install
npm --prefix src install

# 2) 拉取随包分发的独立 CPython 运行时（约 50MB，不入库）
python engine/tools/fetch_runtime.py

# 3) 打包（⚠️ 必须在仓库根目录执行）
npm run tauri:build
```

产物位于 `src-tauri/target/release/bundle/`。

> **不要用 `cargo build`** —— 它跳过前端构建，打出的包会白屏。
> 交付一律走 `npm run tauri:build`。

### 发布（Releases）

发版时**除版本名资产外，必须再上传一份固定名资产** `audio2score-setup.exe`：

```bash
V=0.1.4
gh release create "v$V" \
  "src-tauri/target/release/bundle/nsis/扒谱助手_${V}_x64-setup.exe" \
  --title "扒谱助手 $V" --notes-file RELEASE_NOTES.md

# 固定名副本：内容与上完全一致，只是改了名字
cp "src-tauri/target/release/bundle/nsis/扒谱助手_${V}_x64-setup.exe" /tmp/audio2score-setup.exe
gh release upload "v$V" /tmp/audio2score-setup.exe
```

官网、README、软件内的下载按钮统一指向：

```
https://github.com/LinnnnYue/audio2score/releases/latest/download/audio2score-setup.exe
```

这是 GitHub 的**固定路由**，永远解析到最新一次发布的同名资产，**不经过 API**。

> **为什么不用「JS 查 API 取最新版号」**：GitHub 匿名 API 限流按**出口 IP** 计
> （60 次/小时）。共享出口 IP 的环境（公司网络 / 代理 / 部分运营商）下会长期 403，
> 消费者端只剩回退文案。固定名直链不吃这个亏。
>
> 代价只有一条：**每次发版多传一份固定名资产**。传完，官网与 README 一处都不用改。

### 图标

图标由 `src-tauri/icons/source/*.svg` 栅格化而来（六边形 + 双八分音符，青 `#6fe3ff` → 紫 `#a99cff`）。
改图标只动 SVG，然后：

```bash
python engine/tools/make_icons.py          # 重新生成全套 PNG / ICO
python engine/tools/make_icon_preview.py   # 顺带刷新 docs/icon-preview.png
```

小尺寸（16 / 24 / 32px）走单独的加粗版 `icon-small.svg`——母版的细线缩到 16px 会糊。

### 可选：构建期注入诊断上报凭证

应用内置的故障诊断上报是**可选**能力。若你在自己构建时需要它，在构建前设置环境变量即可，源码中不含任何凭证：

```powershell
$env:BAPU_REPORT_TOKEN = "你的 PushPlus token"
npm run tauri:build
```

未注入时该功能静默降级，主流程完全不受影响。

### 上游内核

`third_party/AutoTranscriber` 是上游的**只读快照**，不随仓库分发，需自行放置：

```bash
git clone https://github.com/GuyueHermit/AutoTranscriber third_party/AutoTranscriber
```

本项目对上游**一字不改**，所有适配都在 `engine/` 层完成。

---

## 命令行（CLI）

不想开 GUI 时，可以直达引擎：

```bash
# 最简：输出同目录同名 .mid
python engine/cli.py 歌曲.mp3

# 指定模式与输出
python engine/cli.py 歌曲.mp3 -m vocals -o 旋律.mid

# 批量
python engine/cli.py *.mp3 -m accompaniment

# 已分离好的音频直入（跳过 Demucs）
python engine/cli.py 人声.wav -m pre_separated --extra 伴奏.wav

# 结构化输出（供脚本消费）
python engine/cli.py 歌曲.mp3 --json

# 查看能力（依赖是否装齐、GPU 是否可用）
python engine/cli.py --caps
```

**退出码即契约**：`0` 成功 · `1` 业务失败 · `2` 参数错误 · `3` 环境未就绪。

---

## MCP 服务

引擎可作为 MCP（Model Context Protocol）服务端接入支持 MCP 的客户端：

```bash
python engine/mcp_server.py
```

配置模板见 [`mcp/bapu.json`](mcp/bapu.json)（把其中的 `<REPO>` 替换为你的仓库绝对路径）。

---

## 项目结构

```
├─ src/                 Tauri 2 + React + TypeScript + Tailwind（前端）
│  ├─ 功能页 + 四方向主题层（CSS 变量驱动热切换）
│  └─ Tauri command / event 通道
├─ src-tauri/           Rust 壳：无边框窗口、文件对话框、目录揭示、sidecar 生命周期
│  └─ icons/source/     图标真源 SVG（生成脚本消费它）
├─ engine/              Python 引擎
│  ├─ bridge.py         适配层：参数映射 / 阶段进度上报 / 轨道编排 / 错误中文化
│  ├─ pipeline.py       六条产品路径的编排实现
│  ├─ bootstrap.py      首启引导安装（三档）
│  ├─ cli.py            命令行入口
│  └─ mcp_server.py     MCP 服务端
├─ mcp/                 MCP 配置模板
├─ docs/                设计文档与图标预览
└─ third_party/         上游 AutoTranscriber 只读快照（需自行放置，不入库）
```

---

## 技术栈

- **桌面壳**：Tauri 2（Rust）+ React + TypeScript + Tailwind CSS
- **引擎**：Python（librosa 路线；完整档加 PyTorch + Demucs）
- **通信**：stdin / stdout JSON 行协议（`engine/bridge.py`）
- **产物**：约 15 MB 的应用壳 + 首启按需安装的引擎

---

## 已知取舍与限制

- **引擎不随包分发**。完整档引擎实测约 5.1 GB（torch 一家占 4.4 GB），打进安装包不现实，故改为首次运行联网引导安装。
- **输出为 MIDI，不是 MusicXML**。MuseScore 打开 `.mid` 会落在导入总谱视图，不如原生 `.musicxml` 的分谱表体验精细 —— 这是已知的取舍。
- **进度百分比为估算值**。上游无回调机制，进度由 stdout 逐行解析 + 阶段推断得到，非精确值。
- **人声音高检测**优先用 CREPE（深度学习，更准），不可用时降级到 pYIN，界面会提示当前所用算法。
- **仅 Windows x64** 提供预编译包。

---

## 许可与免责声明

**本软件仅供个人学习与研究使用，保留所有权利。**

- 请勿用于任何商业用途，请勿二次分发或再发布。
- 请勿用于抓取、复制、传播任何受版权保护的音乐作品。使用者须自行确保对所用音频拥有合法权利，由此产生的一切后果由使用者自负。
- 本软件按「现状」提供，不附带任何明示或暗示的担保。作者不对使用本软件造成的任何损失承担责任。
- 完整条款见 [LICENSE](LICENSE)。

---

## 致谢

- **[AutoTranscriber](https://github.com/GuyueHermit/AutoTranscriber)**（作者 GuyueHermit）—— 本项目的扒谱内核，人声分离、多音高估计与 MIDI 导出能力均来自上游。
- **[MuseScore](https://musescore.org/)** —— 免费开源的制谱软件，也是本项目的推荐下游工具。
- 以及 [librosa](https://librosa.org/)、[Demucs](https://github.com/facebookresearch/demucs)、[pretty_midi](https://github.com/craffel/pretty-midi)、[Tauri](https://tauri.app/) 等开源项目。

---

<div align="center">

如果这个工具对你有帮助，欢迎通过 [爱发电](https://afdian.com/a/LinnYue) 支持作者 ☕

</div>
