# Changelog

## 2.11.2 - 2026-08-11

- Skill 变薄：`SKILL.md` 改为意图路由 + 会话启动 + 硬不变量 + 快捷表；长流程、门禁与 Agent 模式细节下沉到 `references/`。
- 新增 `references/workflows.md`、`references/scene-simulation.md`；扩充 `provider-routing.md`（Agent Native）、`continuation-bootstrap.md`（完结目标步骤）、`architecture.md`（薄 Skill 分层）。
- 行为与 MCP 工具面不变；仅降低每次触发注入的流程手册体积。

## 2.11.1 - 2026-08-09

- Agent 提交加固：`novel_submit_agent_task` 在 resume 前校验 content 体积（reviewer/extractor ≤100KB，writer/repairer ≤500KB）与 JSON 语法；JSON 路由 `json.dumps(ensure_ascii=False)` 归一化；repairer 固定 `plain_text`。
- Writing Gate 分析字段强制化：每个 Rule Card 绑定 `analysis_schema` / 必填五元组（character/goal/obstacle/action/consequence）+ `evidence_required`；`writing_gate_analysis_incomplete` 升为 critical 并阻断 commit。
- Delta 验证加强：`state_coverage` 必须完整三数组；timeline 缺 `id`/`location_id` 为 critical；utterance 缺 speaker/excerpt/`event_ids` 或未知 event 为 critical；outline 地点与 timeline 可对比缺失。
- Commit 预检：`commit_chapter` 先跑 `validate_workspace`，投影漂移时直接拒绝提交。
- 修复 `format_rule_cards` 在 criteria 缺失时的 UnboundLocalError。

## 2.11.0 - 2026-08-08

- 新增 Character Simulation Layer：目标分层、Belief、Theory of Mind、Perception Filter 与隐私隔离；角色 Intent 不读取其他角色私有意图或 hidden world truth。
- 新增 Scene Director 与轻量 World Reaction：多角色意图碰撞生成 Scene Execution Plan，可审计写入 `analysis/character-simulation/`。
- 新增 Exemplar Cards：参考资料同次提炼 Rule + Exemplar，建立 Rule↔Exemplar 索引与场景级有界选择；`short_excerpt` 最长 280 字，Writer 只收 abstracted_pattern。
- 新增可选 Scene-Level Generation 与 Scene Checkpoint：场景草稿可断点续写，全部场景完成后再组装章节进入既有 Writing/Canon 门禁。
- 新增 Arc Reflection：仅接受带 event 证据的 belief/goal/strategy/fear 更新，拒绝无证据 personality rewrite。
- Provider 层统一 route>provider stream 开关；Chat Completions 与 Responses 均走 `_effective_stream`。
- 配置新增可关闭段：`[character_simulation]`、`[scene_generation]`、`[writing_exemplars]`、`[arc_reflection]`；旧工作区无需迁移。
- 新增 MCP 工具：`novel_list_exemplars`、`novel_select_scene_exemplars`、`novel_simulate_character_intents`、`novel_direct_scene`、`novel_prepare_scene`、`novel_save_scene_checkpoint`、`novel_load_scene_checkpoints`、`novel_assemble_chapter_from_scenes`、`novel_propose_arc_reflection`、`novel_apply_belief_updates`；工具总数 70。

## 2.10.3 - 2026-08-03

- 新增单一规划术语表 `assets/planning-terminology.yaml`，集中维护术语别名、正文策略、Writer 自然化措辞、Reviewer 判断依据和 Repairer 修复方向。
- Chapter Packet 改为分阶段渲染：Writer 不再接收 Rule Card ID、名称、严重度或原始规划标签；旧大纲、角色档案、状态和检索上下文会在内存中自然化，原文件保持不变。
- Reviewer/Repairer 获得专属完整术语上下文；Reviewer 新增必填 `planning_language_audit`，证据或置信度不足时只产生 warning，不阻断提交。
- 新增提交前确定性规划术语扫描：内部术语首次出现即阻断；上下文术语按重复阈值警告或阻断，并附带正文证据与定点修复说明。
- 大纲新增可选 `reader_visible_terms` 放行列表，只允许已由正典建立的世界内正式名称；旧大纲无需迁移。
- 新增术语自然化、阶段隔离、放行规则、证据阈值和阻断阈值回归测试。

## 2.10.2 - 2026-08-02

- 新增章节标题多样性确定性门禁：拒绝最近 5 章内完全重复标题、连续把 `mini_arc` 用作标题模板，以及三个标题复用同一长前缀或后缀。
- 完整 Outline、续写规划批次和跨批应用统一执行标题校验；续写时读取近期既有标题、`mini_arc` 与 `chapter_task` 作为比较上下文。
- 历史旧标题只作比较上下文，不会被新规则追溯阻断；新规划仍必须避免延续已有模板。
- Planner 指令明确要求标题来自本章独有行动、选择、冲突、结果、异常或意象，并禁止“不同修饰语 + 同一名词”的批量命名。
- Skill 输出规范要求严格区分读者可见 `title`、内部 `mini_arc` 和 `chapter_task`，避免把小剧情段误报成章节标题。

## 2.10.1 - 2026-08-02

- 修复多 Agent 共用 `routes.toml` 时工作区 `generation.mode=external` 覆盖 Codex Agent Native 模式、导致 Codex 实际请求 NexaPortAI/Ollama 的缺陷。
- 安装器现在默认给 Codex MCP 注入进程级 `NOVEL_GENERATION_MODE=agent`，给 pi/Hermes 注入 `external`；全选安装也能保持每个 Agent 的独立生成模式。
- 增加 `--codex-mode`、`--pi-mode`、`--hermes-mode`，允许显式覆盖每个 Agent 的模式。
- `novel_generation_mode(workspace_id)` 与 `novel_list_routes(workspace_id)` 现在返回 `mode_source`/`source`、`process_override` 和工作区配置路径，能够直接发现覆盖来源。
- 增加进程级 Agent 模式不可被工作区 external 配置覆盖的回归测试。
- 新建工作区不再自动生成 `routes.toml`，默认只使用 `~/.config/novel-production/routes.toml`；仍兼容用户手工创建的单书覆盖配置。

## 2.10.0 - 2026-08-02

- 将参考书提炼规则移到 `~/.config/novel-production/writing-doctrine/`，增加可扩展 Taxonomy、多标签 Rule Card 和可重建 `index.yaml` 检索索引。
- Chapter Rule Selector 支持 scene / technique / genre metadata、规则数量与 token 预算，并按 Project > Genre > Reference > Core、阻断性、严重度和相关性排序。
- 增加参考书目录增量扫描：区分 new / modified / unchanged / deleted，未变化文件不重复导入。
- 增加 `novel_scan_writing_references`、`novel_rebuild_writing_rule_index`、`novel_get_writing_rule` 及对应 CLI 入口；MCP 工具总数为 60。
- Writing Gate 改为 severity + blocking + confidence + evidence 联合判定；证据不足不能阻断，medium/low 不阻断，Score 不再单独拒绝 Commit。
- 增加 `approved / approved_with_warnings / repair_required / blocked` 状态和修复上限策略；默认 unresolved critical 停止、unresolved high 可带警告提交。
- 保留现有 Provider、Agent Native、Reviewer、Repairer、Canon/Timeline/Knowledge/Inventory/Ability/Relationship/Ending 门禁和旧工作区兼容性。

## 2.9.0 - 2026-08-02

### Writing Doctrine 与动态 Rule Cards

- 新增独立 Writing Quality 子系统：`writing_doctrine.py`、`writing_gates.py`、`writing_profiles.py`、`writing_reference.py`；现有 Canon、Timeline、Knowledge、Inventory、Ability、Relationship、Ending 确定性门禁保持原样。
- 定义正式 Rule Card schema，包含稳定 ID、领域、适用阶段、检查类型、严重度、阻断属性、判断标准、证据要求、修复指令、适用条件、来源追踪和启用状态。
- 新增 Core → Genre → Project 三层 Doctrine 合并；项目规则优先于类型和核心规则，但只能控制文学质量，不能关闭 Canon 硬门禁。
- Writer 不再接收整套方法论；系统根据场景类型、参与人数、genre、章节任务和近期失败历史动态选择最多 8 张本章 Rule Cards。
- 新增可关闭的 `web_novel` Profile，控制有效事件密度、持续目标、局势变化、章末压力、重复说明和静态章节，不硬编码“必须爽/必须打脸”。

### 首批文学质量门禁

- 新增 Scene Purpose、Causality、Character Motivation、Anti-Fluff、Information Novelty、Dialogue、Scene Change、Conflict Resolution、Pacing、Chapter Value 十类核心门禁。
- Reviewer 必须在现有审校 JSON 中返回 `writing_quality.gates`，逐项覆盖本章适用规则并引用正文证据；模糊评价、未知规则、重复规则、缺失证据或缺少定点修复指令会失败关闭。
- high/critical 且 blocking 的文学门禁阻止 Delta/Commit；medium/low 默认报告警告，不以机械字数、段落数或对话长度替代文学判断。
- Repairer 只接收 `failed_gate_ids`、具体证据、失败原因和修复动作；每轮修复后重新执行完整 Reviewer，避免修对白时破坏时间线或人物状态。
- 每章保存 `analysis/writing-quality/chapter-NNNN.json` 与 `.md`，记录分数、应用规则、通过/失败门禁、修复轮次及 fluff/causality/dialogue/novelty 分类。
- Final Readiness Gate 在现有 Canon 校验之后额外核验 Writing Quality Report；文学报告缺失、低于阈值、引用未知规则或未批准时禁止提交。

### 写作参考书导入

- 新增 PDF、EPUB、TXT、Markdown 写作参考导入；原始资料一次性复制为只读证据，按页/章节/标题切分，不会在每章写作时重新发送整本书。
- 新增分批断点分析：提炼 principle、method、decision rule、failure pattern、checklist，并保存 reference_id、页码/章节/section 来源。
- 新增 Doctrine 提案流程：方法去重、归类、冲突检测、Rule Card 转换、用户确认、项目层应用；Rule Card 只保存重新表达的方法和短来源定位，不长篇复制参考书。
- 新增 8 个 MCP 工具：import/analyze reference、generate/apply doctrine proposal、list/enable/disable rules、test writing gates。

### 配置与测试

- 新增 `[writing_quality]` 配置，默认启用 `web_novel`、最低分 85、最多修复 2 轮，并允许按领域开关语义门禁；旧配置无需修改即可运行。
- 新增 8 个坏文 fixture 与 3 个好文 fixture；好例覆盖有效场景、有效对白和有效慢场景，防止把所有慢节奏文本误杀。
- MCP 测试增至 85 项，原有 8 项确定性 Canon fixture 保留；wheel 包含 Writing Doctrine assets 与 Writing Quality fixtures。

## 2.8.0 - 2026-08-02

### Writer 安全上下文

- 章节包改为 Writer 安全投影：只提供当前参与人物可用的公开事实、已知/怀疑事实、行为档案和当前成长阶段，不再把角色私密背景、未知事实或未来成长节点直接交给正文模型。
- 世界事实新增 `visibility`；`novel_register_fact` 支持 `public / private / secret`，未授权事实不会进入 Writer 上下文。
- Reviewer 单独获得完整私密正典，用于检查正文是否发生知识泄漏，而不是让 Writer 先看到答案。

### 规划与正文质量门禁

- 每章大纲新增 `restricted_actions`，门禁、监控、数据库、医疗、财务、执法、档案等受限操作必须提前声明操作者、资源、授权依据及正文建立位置。
- 每章大纲新增 `rhythm_signature`，在相邻窗口阻止重复使用相同开场、核心行动、证据、情绪转折和结尾钩子组合。
- 规划器阻止同一叙事元素被重复登记为“首次引入”，续写应用会同时检查上一批既有大纲；缺少旧版节奏字段的历史大纲保持兼容。
- Writer、Reviewer 与 Repairer 新增任务因果、知识来源、权限依据、大纲地点/行动一致性、局部推理链、状态变化和重复度规则。
- 新增正文篇幅硬门禁，默认有效正文不得低于 `target_chars` 的 85%；审校阶段即进入修复循环，提交阶段再次确定性复核。
- 新增角色档案字面化检测，阻止把 `personality`、`pressure_response` 等内部档案措辞直接复述为旁白、台词或无因果动作。

### 审校、Delta 与正典闭环

- Reviewer 必须返回 `knowledge_provenance_audit`、`authorization_audit`、`outline_fidelity_audit`、`reasoning_chain_audit`、`state_coverage_audit`、`repetition_audit` 六类结构化审计；缺失、格式错误或未完成时失败关闭。
- Delta 必须通过 `state_coverage` 映射 Reviewer 发现的道具、关系、能力、地点、剧情线和伏笔变化；未映射变化、虚假映射或大纲地点漂移会阻断提交。
- 道具静态元数据不再允许保存 `current_holder`；持有人只由双向余额和事件账本推导，避免静态字段与事务状态冲突。
- Agent Native 阶段恢复会重新执行新增门禁并持久化结果，避免旧审校报告显示通过后跳过修复。

### 历史审计与工程完整性

- 新增 `novel_audit_existing_chapters`，可把已提交章节按范围做只读质量审计；报告写入 `analysis/historical-audits/`，不修改正文、当前审校、Delta、正典或状态。
- MCP 完成响应继续保持摘要化；新增质量报告仍以工作区文件为准，不向聊天区回传整份审计内容。
- 新增质量门禁、历史只读审计和恢复路径回归测试；MCP 测试增至 78 项，固定确定性评测 8 项全部通过。
- wheel 构建显式包含质量模块和评测夹具，并新增发布版本一致性测试，防止 `CHANGELOG.md`、Skill、环境检查器和 Python 包版本再次漂移。

## 2.7.0 - 2026-08-01

- MCP 公共返回边界新增结果摘要化：完成后只返回状态、路径、范围、计数和校验摘要，长列表/长文本自动截断，避免把完整工作区状态刷入聊天区；Agent 待处理任务保留完整消息。
- 续写规划改为可持久化分批生成：默认每批 10 章，`chapter_count` 不再受单次 100 章限制；每批失败或意外退出后可从 `.novel/continuation-plan-progress.json` 断点继续。
- 续写规划会自动从首个未规划章节扩展，应用阶段禁止覆盖已有逐章大纲；已完成的 11–110 章可以继续规划 111 章之后。
- 新增可切换的 Agent Native 生成模式：设置 `generation.mode = "agent"` 后，所有模型生成路由都由当前宿主 Agent 提供内容，MCP 负责任务编排、结构校验、落盘、事件记录和状态推进；默认 external 行为保持不变。
- 新增 `novel_generation_mode`、`novel_submit_agent_task`、`novel_agent_task_status` 三个 MCP 工具；Agent 任务会持久化到工作区 `.novel/agent-tasks/`，同一 MCP 进程内支持阶段性恢复。
- Agent 模式新增任务恢复回归测试，覆盖提案生成、任务查询、当前 Agent 提交和恢复写盘闭环。
- Provider 默认启用流式请求，支持 Responses API 与 Chat Completions 的 SSE 增量解析，同时兼容供应商返回的普通 JSON。
- 新增 524、429、408、5xx、网络错误和协议错误分类；只对可恢复错误退避重试，并读取 `Retry-After`。
- Provider 失败现在抛出带状态码、错误类别和尝试路由的 `ProviderError`，便于上层做可靠降级。
- 接管分析每个批次完成后立即持久化 `analysis/status.yaml`，失败后可从 `next_batch` 恢复，已完成批次通过 source hash 缓存复用。
- 接管批次遇到 524/超时且包含多个章节时自动二分拆分；仍失败则停止当前批次，不生成不完整的接管提案。
- 接管批次计划持久化到 `analysis/plan.json`，拆分后的父子批次可在中断后继续，不会重复提交原始大批次。
- 提案汇总排除 `.meta.json`，并在提交 Director 前校验结果文件、元数据和源文本哈希；状态接口不再把旧提案误报为就绪。
- Provider 记录 SSE `error`/`response.failed`/`response.incomplete` 事件，并保留请求 ID、`CF-Ray` 等上游诊断信息。
- `novel_takeover_status` 返回当前批次、失败批次、恢复位置和自适应拆分记录。
- Provider、接管流程和 v2.7 稳定性行为新增回归测试。

## 2.6.0 - 2026-08-01

- Provider 新增 `api_mode`，支持 `chat_completions` 与 `responses` 两种协议。
- NexaPortAI 新安装默认走 `/v1/responses`；Ollama/oMLX 保持 `/v1/chat/completions`。
- Responses 模式适配 `input`、`max_output_tokens`、typed `output` 文本解析与 usage 统计。
- `./install.sh --api-mode ...` 与 `--reconfigure` 可更新 NexaPort API 模式。
- 旧 providers.toml 未配置 `api_mode` 时继续默认 Chat Completions，保持兼容。

## 2.4.0 - 2026-08-01

- Added root `install.sh` one-click installer with `--check`, `--repair`, `--reconfigure`, cloud/hybrid profiles.
- Default NexaPortAI Base URL is now `https://api.nexaportai.com/v1`.
- Default cloud route profile uses `nexaport / gpt-5.6-sol` for all ten required routes; old local-writer setup remains as `routes.hybrid.example.toml`.
- Added local secret file `~/.config/novel-production/.env` support; process environment variables still take precedence.
- Installer stores `.env` with mode 0600 and no longer requires manual `export` / `source`.
- Detects pi, installs `pi-mcp-extension`, preserves existing MCP servers, and registers Novel Production as eager stdio automatically.
- Added CLI `route-test` and `routes` commands for diagnostics.
- Added explicit API `User-Agent`/`Accept` headers and a clearer 1010/WAF diagnostic hint.
- Added installer-helper and `.env` provider tests.

## 2.3.0 - 2026-08-01

### Ending Controller / Story Budget

- `planned_chapters` 升级为正式 Ending Target；新书默认生成固定 min/ideal/max 目标。
- 新增 `planning/ending/ending-plan.yaml`、`planning/story-budget.yaml`、`state/ending-progress.yaml` 和 `final/` 完结审计目录。
- 新增 7 个 MCP 工具：set ending target / ending options / apply ending plan / rebalance budget / ending progress / enter final arc / finalize。
- `volume` 应用新增确定性门禁：章范围必须从 1 连续覆盖到 ideal chapter。
- 新书完整 `outline` 应用新增确定性门禁：必须恰好覆盖 `1..ideal_chapter`。
- 已有小说未给真实终点时，导入的 `last + 30` 仅作为 provisional horizon；续写规划前必须设置真实 Ending Target。
- continuation plan 与 batch 章节生成都会自动截断到 `max_chapter`，禁止越过完结上限。
- final arc 中阻断新增未知剧情线和新伏笔。
- Delta 新增 `ending_outcome_updates`；完成 required outcomes / relationship outcomes 必须引用本章新建 timeline event 证据。
- 最大终章仍有非可选主线、伏笔或 ending outcomes 未完成时阻断提交。
- 正式 finalize 生成 completion report、未解决项、人物终态、最终时间线和尾声备注，并锁定项目。
- 工作区 schema 升级到 v3；`novel_migrate_workspace` 支持 v1/v2 → v3。
- Skill 新增快捷意图：`接管 <路径>`、`设置完结 ...`、`设计结局`、`检查完结进度`、`进入终局`、`完结小说`。

## 2.2.0 - 2026-08-01

- 新增已有小说接管 / Continuation Bootstrap。
- 支持单个 UTF-8 小说文件或章节目录导入，原稿只读保存。
- 新增分批证据提取与断点缓存。
- 新增 confirmed / inferred / unknown 证据分级。
- 新增全局接管提案：人物、世界观、知识边界、关系、时间线、道具、能力、剧情线、伏笔、历史卷纲/拆章。
- 新增高危冲突阻断与人工确认。
- 新增 continuation boundary。
- 新增只允许规划未来章节的 continuation plan，阻止覆盖已有正文。
- 新增7个 MCP 工具和对应 CLI 命令。
- 新增接管端到端与冲突阻断测试。

## 2.1.0 - 2026-08-01

- 新增首次调用环境门禁 `scripts/check_environment.py`
- 无 MCP 依赖也可先检查 Python、uv、配置、路由、API Key 环境变量和工作区权限
- 环境就绪后使用配置指纹缓存，后续调用不重复完整检查
- 配置变化或 Skill 升级自动使初始化标记失效
- SKILL.md 明确禁止在环境未就绪时开始小说生成
- 安装流程调整为“先放入 skills，首次调用再按检查结果配置”
- 保留完整 MCP/CLI/SQLite/检索/质量门禁能力，无功能删减

## 2.0.0 - 2026-08-01

- 稳定事实、地点、道具、能力和事件 ID
- 发言归属、知识边界、视角、旅行时间、能力和道具确定性门禁
- 双向道具事件账本与伏笔期限门禁
- SQLite 事件事务和可重建文件投影
- 投影漂移检查与重建工具
- 模型输入/输出哈希、token、成本、失败、修复轮次和质量趋势
- 本地关键词＋哈希向量混合检索
- 8 个固定回归评测
- v1 工作区迁移
- 显式事实、道具、旅行时间和能力审批工具

## 1.0.0 - 2026-08-01

- 首个完整 Skill + MCP 版本
- 独立小说工作区与角色四层状态
- 规划提案审批流程
- 外部模型路由
- 章节全链路质量门禁
- 文件锁、检查点和回滚
- CLI 与自动测试
