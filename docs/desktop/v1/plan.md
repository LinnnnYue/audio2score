# desktop 执行计划（v1）

> 本文件由 dev-flow skill 分发，逐字拷贝使用；仅替换 `<尖括号>` 内容。
> 纪律：做一个勾一个，禁止做完补勾、禁止伪勾（未做保持 `[ ]` 并注明原因）；checkbox 四态 `[ ]`未开工 / `[~]`进行中 / `[x]`完成 / `[!]`受阻，与 worklog 状态 🔄/✅/⛔ 一一对应；本文件只管执行——论证回需求文档、过程回 worklog、评审回 review/；写完立即提交，合并先拉新。

<!-- 施工旗：本板块施工者 = raphael · 分支 = main · 开始 2026-10-04 12:01 -->

## P0 — 地基与上游契约（工具链就位 + 接口摸清）

- [x] 环境勘察：GPU / ffmpeg / MuseScore / cargo / node / 磁盘余量
      - 验收：`nvidia-smi` 有 RTX 3080；`ffmpeg -version` 5.1.2；`ls "C:/Program Files/MuseScore 4/bin/"` 有 MuseScore4.exe
- [x] vendor 上游至 `third_party/AutoTranscriber`（depth=1，只读）
      - 验收：`git -C third_party/AutoTranscriber rev-parse HEAD` == c7b7981...
- [x] 建 `engine/.venv` 并装齐依赖（torch cu124 / torchaudio / librosa / demucs / av / pretty_midi）
      - 验收：`engine/.venv/Scripts/python.exe -c "import torch,librosa,demucs,av,pretty_midi;print(torch.cuda.is_available())"`
      - 实测：torch 2.6.0+cu124 / cuda True / RTX 3080 / librosa 1.0.0 / demucs 4.1.0 / pretty_midi 0.2.11 ✅
- [x] 逐字拷贝 DISCIPLINE.md / WORKLOG-PROTOCOL.md / 模板入 `docs/desktop/v1/`
      - 验收：文件存在且内容与上游 devskill 一致
- [x] 写需求文档 `01-requirement.md`（五段齐全）
- [x] 写 `BOUNDARY.md`（B-1 上游只读 / B-2 禁品红黑 / B-3 仅 MIDI / B-4 四方向全做）
- [~] 上游 API 契约侦察报告 `03-upstream-api-contract.md`（arch-scout 承办）
      - 验收：6 条路径判定表 + 函数签名含文件:行号 + 音频格式支持矩阵（实测）+ 适配层 TODO 清单
- [ ] 判定：是否存在「不改上游就无法实现」的能力；有则登记例外台账请主上拍板
      - 验收：结论明确写进 `03-upstream-api-contract.md` §9

## P1 — 引擎层（Python，六条路径端到端可跑）

- [ ] `engine/bridge.py`：上游 API 适配 + 阶段进度上报（stdout 解析）+ 错误中文化
      - 验收：`python engine/bridge.py --probe` 打印上游能力自检表；异常路径返回中文错误码
- [ ] `engine/pipeline.py`：六条路径编排（双轨/伴奏/人声/基本单轨/单多轨/已分离直入）
      - 验收：对 `test_audio/chord_progression.wav` 六条路径各产出非空 .mid
- [ ] 轨名与 program 映射（人声→Voice、伴奏→Piano），tempo 正确写入
      - 验收：`pretty_midi` 读回，轨名与 program 与预期一致
- [ ] 已分离音频直入路径（跳过 Demucs）
      - 验收：手工分离的 vocals+instrumental 对导入后直接出双轨
- [ ] 引擎命令行协议（JSON stdin/stdout），供 Tauri sidecar 调用
      - 验收：PowerShell 喂 JSON 进去能拿到 JSON 结果 + 阶段事件流
- [ ] 引擎单元测试：空文件 / 非音频 / 0 秒 / 中文空格路径 / 取消中断
      - 验收：`engine/.venv/Scripts/python.exe -m pytest engine/tests -q` 全绿

## P2 — 桌面壳与前端（两个功能页）

- [ ] Tauri 2 工程初始化 + 无边框圆角窗口（`tauri-borderless-windows` 技能基线）
      - 验收：`cargo check` 零错；窗口无系统标题栏、圆角 + 弥散阴影、chrome 与内容无可见交界线
- [ ] 功能页 1「歌曲扒谱」：大拖放区 + 模式下拉（四选项）+ 参数区（模式联动）
      - 验收：四模式各自显示对应参数；拖入后显示文件名与时长
- [ ] 功能页 2「基本扒谱」：拖放 + 单轨/多轨选择
- [ ] 已分离音频导入入口（文件对选择）
      - 验收：可选 vocals + instrumental 两文件并跳过分离阶段
- [ ] Tauri command 桥：调 sidecar、收阶段事件、取消子进程
      - 验收：处理中取消后无孤儿 python 进程
- [ ] 进度 UI：四阶段文字 + 百分比 + 实时日志
      - 验收：3 分钟歌曲全程可见四阶段推进
- [ ] 结果卡片：文件名 / 音符数 / 轨数 / 打开目录 / 用 MuseScore 打开
- [ ] 参数区完整参数：n_peaks / hop / onset 灵敏度 / 最小音符时长 / BPM / 精简强度 / 感知模式 / 钢琴模式
      - 验收：每个参数改动真实影响产出（用同一音频不同参数对比音符数）
- [ ] 异常路径 8 类中文提示（对应 B2 验收）
- [ ] 并发防护：任务运行中禁用提交（对应 B4 验收）
- [ ] 设置持久化（参数记忆、输出目录、主题选择）

## P3 — 四方向视觉（红线 B-2 / B-4）

- [ ] 4 版可切换原型评审（`prototype/visual-directions.html`，ui-visual 承办）
      - 验收：4 版均含空态/进度态/完成态，交互可操作
- [ ] 抽取主题层：CSS 变量单一主题源，切换只换变量不重挂组件树
      - 验收：切换 200-300ms 过渡且状态不丢
- [ ] 落地 4 套主题至 Tauri 应用
      - 验收：4 版均能完整走通 A7 验收
- [ ] 审美自检：逐条核对 C1-C4 四项验收口径
      - 验收：品红/纯黑零出现；动效 11 条硬标准全过；无 AI 平庸态特征；布局无移位

## P4 — 对抗式自测与打包（G9 慎之勇者态度）

- [ ] 3 轮对抗式自测：每功能点跑正常/边界/异常三路，缺陷全闭环
      - 验收：`docs/desktop/v1/review/` 有三轮自测记录，缺陷清单全部 ✅
- [ ] 性能实测：3 分钟歌曲内存 <4GB，Demucs <2 分钟
      - 验收：实测数据记入 review
- [ ] 路径健壮性：中文 / 空格 / 全角字符路径
- [ ] MuseScore 4 实机打开验收（轨名 / program / tempo / 可编辑）
- [ ] NSIS installer 打包 + 干净机器安装验证
- [ ] 引擎分发方案落地（随包 venv 或首启引导安装）
      - 验收：全新环境双击安装后可直接用，无需手动配 Python
- [ ] `npm run build` + `cargo check` + `tsc --noEmit` 零错
- [ ] 用户验收（主上手动跑主路径）

<!-- 新阶段在末尾顺延编号追加（P2、P3…）；新需求先走需求文档评审再插入或新起 PX。 -->
