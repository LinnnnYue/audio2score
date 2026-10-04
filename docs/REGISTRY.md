# 板块登记表（REGISTRY）

> 每行 = 板块路径 + 干什么（≤100 字）。新板块在同一 commit 登记一行，漏登 = 违规。

| 板块路径 | 干什么 |
|---|---|
| `docs/desktop/v1/` | 扒谱桌面应用的需求、计划、纪律、边界、工作日志（v1 定版线） |
| `docs/desktop/v1/01-requirement.md` | 主需求：现象/目标/方案取舍/不做什么/验收口径五段 |
| `docs/desktop/v1/02-format-matrix.md` | 音频格式支持矩阵与实测证据（m4a/aac/wma 上游失败及兜底方案） |
| `docs/desktop/v1/03-upstream-api-contract.md` | 上游 API 契约、note 结构、缺陷清单、适配层 TODO |
| `docs/desktop/v1/plan.md` | P0~P4 唯一执行计划，checkbox 为进度真源 |
| `docs/desktop/v1/BOUNDARY.md` | 项目红线：上游只读 / 禁品红黑 / 仅 MIDI / 四方向全做 |
| `docs/desktop/v1/worklog/` | 按写作者分文件的工作日志，倒序 |
| `docs/desktop/v1/review/` | 阶段评审与自测记录 |
| `engine/` | Python 引擎：format_guard 格式兜底 / pipeline 八路径编排 / bridge 协议 |
| `engine/tools/` | 构建期一次性工具：图标生成（含自检）、图标预览对照图、独立 Python 运行时拉取与体检 |
| `engine/tests/` | 引擎测试；`manual/` 下为需人工判读的探针（模型下载来源、打包兼容性、前后端模式文案一致性） |
| `engine/third_party/AutoTranscriber/` | vendored 上游只读快照（BOUNDARY B-1），禁止修改 |
| `src/` | Tauri 2 + React 前端：两个功能页 + 设置页 + 四方向主题层 + 新手指引弹窗 |
| `src-tauri/` | Rust 壳：无边框窗口、文件对话框、sidecar 生命周期 |
| `src-tauri/runtime/` | 随包分发的独立 CPython（约 45MB，不入 git）。让无 Python 的机器也能一键装引擎；缺失则执行 `engine/tools/fetch_runtime.py` |
| `mcp/` | MCP 客户端配置示例（开发机自用）。分发给用户的那份由应用按本机路径动态生成 |
| `docs/icon-preview.png` | 图标验收对照图（任务栏尺寸 / 深浅底 / 透明圆角），由 tools 生成 |
| `prototype/` | 视觉方向原型（4 版可切换），评审用 |
