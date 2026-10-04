# 扒谱助手 · 三种用法

同一个引擎核心，三种接法，行为完全一致。

| 接法 | 入口 | 适用 |
|---|---|---|
| **图形界面** | 应用主程序 | 日常使用，拖拽操作 |
| **命令行** | `engine/cli.py` | 批处理、写脚本、CI |
| **MCP** | `engine/mcp_server.py` | 让 AI 助手（WorkBuddy / Claude Code 等）直接扒谱 |

---

## 一、命令行（CLI）

### 快速上手

```bash
# 最简：给个音频，扒完输出到同目录同名 .mid
bapu 歌曲.mp3

# 指定模式与输出
bapu 歌曲.mp3 -m vocals -o 旋律.mid

# 只扒伴奏
bapu 歌曲.mp3 -m accompaniment

# 批量：一整个目录
bapu *.mp3 -m basic

# 已分离好的音频直接扒（不重新分离）
bapu 人声.wav -m pre_separated --extra 伴奏.wav

# 自己分好的人声 → 单轨旋律谱（小提琴等单声部乐器演奏用）
bapu 人声.wav -m basic_vocals -o 旋律.mid
```

### 八种模式

| 模式 | 说明 | 需分离 |
|---|---|---|
| `full_auto` | 全自动，人声 + 伴奏双轨 | 是 |
| `accompaniment` | 只扒伴奏 | 是 |
| `vocals` | 只扒人声旋律 | 是 |
| `basic` | 整段直扒（乐器·不分离），**默认** | 否 |
| `basic_vocals` | 单轨直扒（人声旋律·不分离），单旋律追踪 | 否 |
| `basic_accompaniment` | 单轨直扒（伴奏多音高·不分离） | 否 |
| `basic_multi` | 多轨直扒（每个文件一轨） | 否 |
| `pre_separated` | 已分离音频直入（需 `--extra`） | 否 |

> `basic_vocals` 与 `basic_accompaniment` 都输出**单轨**：前者走单旋律追踪
> （人声、独奏小提琴等单声部），后者走多音高识别（伴奏、钢琴等复音乐器）。

### 常用参数

```bash
--n-peaks N      每帧最大同时音符数（和弦 5-8，人声 1-2）
--tempo BPM      MIDI 速度（默认 120）
--onset 0~1      起始检测灵敏度（默认 0.3，越小越灵敏）
--min-note N     最小音符时长，单位帧（默认 4）
--simplify N     音符精简强度 0/2/3/5（默认 0=关闭）
--perceptual     感知模式（默认关。开启会重写音符，多声部可能丢失）
--piano          钢琴优化：更高时间分辨率 + 中值滤波
--device cuda|cpu|auto   分离设备（默认 auto）
--no-fallback    禁用中频分离降级（Demucs 失败即报错）
```

### 机器消费

```bash
bapu 歌曲.mp3 --json
```

输出结构化 JSON，含 `output` / `totalNotes` / `tracks` / `elapsed` / `warnings`。

### 环境自检

```bash
bapu --caps
```

报告 Demucs / CUDA / ffmpeg 是否就绪，以及支持的格式清单。

### 退出码契约

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 业务失败（文件损坏、扒不出音符等） |
| 2 | 参数错误 |
| 3 | 环境未就绪（ffmpeg 缺失等） |

供 shell 脚本与 CI 判定，**不必解析 stdout**：

```bash
if bapu 歌曲.mp3 --quiet; then
  echo "成功"
fi
```

### 在本项目里调用

```bash
# Windows
engine\.venv\Scripts\python.exe engine\cli.py 歌曲.mp3

# 若引擎装在用户目录（首启引导装的）
"%LOCALAPPDATA%\bapu\engine\.venv\Scripts\python.exe" ^
    "%LOCALAPPDATA%\bapu\engine\cli.py" 歌曲.mp3
```

---

## 二、MCP（让 AI 助手调用）

### 提供的工具

| 工具 | 作用 |
|---|---|
| `transcribe` | 核心扒谱，支持全部 6 种模式 |
| `list_modes` | 列出可用模式及说明（用于向用户解释该选哪个） |
| `inspect_audio` | 探测音频时长/格式，**不执行扒谱**（可在扒谱前先确认文件没问题） |
| `environment_status` | 报告引擎能力（Demucs/CUDA/ffmpeg） |

所有工具都返回结构化结果，**失败时返回 `{"ok": false, "error": "中文原因"}` 而非抛异常** ——
AI 读到的是可读的中文提示，不是协议错误。

### 配置

本仓库已提供现成配置：`mcp/bapu.json`。内容：

```json
{
  "mcpServers": {
    "bapu": {
      "command": "D:\\...\\engine\\.venv\\Scripts\\python.exe",
      "args": ["D:\\...\\engine\\mcp_server.py"],
      "env": {
        "TQDM_DISABLE": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1"
      }
    }
  }
}
```

**路径要改成你自己的**（两条路径都是从项目根算起）：
- `command` → `engine/.venv/Scripts/python.exe` 的绝对路径
- `args[0]` → `engine/mcp_server.py` 的绝对路径

若引擎是首启引导装的（不在项目目录内），路径为：
`%LOCALAPPDATA%\bapu\engine\.venv\Scripts\python.exe` 与
`%LOCALAPPDATA%\bapu\engine\mcp_server.py`

### 验证配置是否生效

```bash
cd "<REPO>/engine"
.venv/Scripts/python.exe tests/test_mcp_server.py
```

会真机跑一遍 `initialize → tools/list → tools/call` 握手，输出 10 项检查结果。

### 版本注意

本项目用的是 **mcp 2.x**。1.x 的 `FastMCP` 在 2.x 已被改名 `MCPServer`，
`engine/mcp_server.py` 里的 `_import_server()` 做了双路兼容，两个大版本都能跑。

---

## 三、图形界面（主程序）

拖入音频 → 选模式 → 开始扒谱。首次运行会引导安装引擎（三档可选）。
