# Novel Production Skill + MCP v2.11.2

## v2.11.2 薄 Skill

`SKILL.md` 压缩为意图路由器：会话启动、硬不变量、快捷意图与最小流程。安装、接管、完结、门禁、写作质量、场景模拟等长说明改由 `references/` 按需加载。MCP 引擎与工具语义不变。

## v2.11.1 Agent 提交 / Writing Gate / Delta 加固

基于 ch3 实操：Agent 提交体积与 JSON 预校验、Writing Gate analysis 五字段强制与 critical 阻断、Delta state_coverage / utterance event_ids / timeline location 完整性、commit 前 workspace 预检。兼容 v2.11 工作区，无迁移。

## v2.11.0 Character Simulation + Exemplar + Scene Director

在现有 Canon / Writing Doctrine / Rule Cards 之上增量加入人物意图模拟、Belief/ToM 分离、场景导演、Exemplar 关联选择、可选场景级生成与检查点，以及 Provider 流式路由开关统一。默认兼容旧工作区；各新模块均可关闭。

## v2.10.3 规划术语自然化

新增统一规划术语表和分阶段渲染：Planner、Reviewer、Repairer 共用同一份定义，Writer 只接收去除规则名称、字段名和分类标签后的自然语言执行要求。旧大纲和角色档案中的规划措辞会在构建 Chapter Packet 时自动转换，不修改原始规划文件。

Reviewer 新增 `planning_language_audit`，Repairer 获得定点术语修复依据；提交前还有确定性正文扫描。只有大纲 `reader_visible_terms` 明确列出的世界内正式名称可以保留，普通规划标签不能进入旁白或对白。

## v2.10.2 章节标题多样性门禁

完整拆章与分批续写规划在应用前会校验最近 5 章标题：拒绝完全重复、连续把 `mini_arc` 当标题模板，以及三个标题重复相同长前缀或后缀。`mini_arc` 仍可作为内部小剧情段跨章复用；历史旧标题只提供比较上下文，不会追溯阻断当前项目。

## v2.10 精准规则检索与可收敛 Writing Gate

用户从参考书提炼的 Rule Cards 独立保存在 `~/.config/novel-production/writing-doctrine/`，通过可重建 `index.yaml` 按场景、技巧、题材、阶段、严重度和来源检索。Chapter Packet 可选提供 `chapter_type / scene_types / dominant_scene / techniques / genres`；旧 Packet 无这些字段仍可运行。

Writing Gate 现在以“规则 blocking + severity + confidence + 正文证据”共同决定是否阻断。分数低、medium/low、非阻断 high 或证据不足只产生 warning/repair candidate；不会再因为主观小问题或低于 85 分而无限 Repair。

## v2.9 Writing Doctrine + 文学质量门禁

v2.9 在原有 Canon、时间线、知识边界、道具、能力、关系和终局门禁之外，新增独立 Writing Quality 子系统。它不替代原有确定性校验，而是在 Writer → Reviewer → Repairer 之间控制场景目标、因果链、人物动机、对白、信息新鲜度、节奏、废话和章节价值。

```text
Chapter Packet
→ 动态选择本章 Rule Cards
→ Writer + Writing Doctrine
→ Reviewer JSON + 正文证据
→ Writing Gates
→ 只修失败 Gate
→ 完整复审
→ Delta
→ Canon Validation
→ Writing Final Readiness
→ Commit
```

默认启用 `web_novel` Profile，每章只注入当前最相关的规则。配置位于 `routes.toml`：

```toml
[writing_quality]
enabled = true
profile = "web_novel"
min_score = 85
max_repair_rounds = 2
block_high = true
block_critical = true
```

支持把 PDF、EPUB、TXT、Markdown 写作教程一次性导入，分批提炼为带来源的 Rule Cards。系统不会在每章写作时重新发送整本书；规则提案必须经用户确认后才进入 Project Doctrine。详见 `references/writing-quality.md`。

## v2.8 质量闭环升级

- Writer 使用按人物知识边界过滤的安全章节包；完整私密正典只交给 Reviewer 做泄漏检查。
- 每章规划新增受限操作授权和节奏指纹，阻止权限悬空、未来信息提前出现和相邻章节模板化重复。
- Reviewer 强制完成知识来源、权限、大纲一致性、推理链、状态覆盖和重复度六类结构化审计。
- 正文篇幅、角色档案字面化、人物出场、任务因果和叙事时序在审校阶段进入修复循环，提交前再次复核。
- Delta 必须覆盖正文产生的状态变化并与大纲地点一致，否则禁止写入 SQLite 正典。
- 新增 `novel_audit_existing_chapters`，对历史已提交章节生成独立只读报告，不修改正文或正典。

## v2.7 稳定性升级：流式请求与接管断点恢复

- Provider 默认以 SSE 流式调用 Responses API 和 Chat Completions；即使供应商返回普通 JSON 也保持兼容。
- 524/429/408/5xx、网络错误和协议错误分类处理；429 遵守 `Retry-After`，不可恢复的 4xx 不盲目重试。
- 接管分析将父子批次计划持久化到 `analysis/plan.json`，每批完成后立即写入 `analysis/status.yaml`；再次执行时会复用 source hash 一致的批次，只从未完成批次继续。
- 接管批次遇到 524/超时且包含多个章节时自动拆分；分析未完成时禁止生成全局接管提案。
- 所有 MCP 工具统一经过公共进度执行器；长任务会显示阶段进度，并每 15 秒发送一次心跳。心跳只表示请求仍在运行，不代表模型已经返回内容。
- MCP 完成响应默认返回状态摘要、路径和关键计数；长列表与长文本会自动压缩，完整结果保留在工作区文件中。Agent 待处理任务仍会返回完整消息。
- 续写规划支持大范围分批生成：默认每批 10 章，批次结果和 `.novel/continuation-plan-progress.json` 即时保存；失败或退出后重复调用会从断点继续，并从首个未规划章节扩展，不覆盖已有逐章大纲。

## 当前 Agent 生成模式

默认仍由 MCP 调用 `providers.toml` / `routes.toml` 中的外部模型。若希望所有生成路由使用当前 Codex、pi 或其他宿主 Agent 的模型和额度，可在 `~/.config/novel-production/routes.toml` 中设置：

```toml
[generation]
mode = "agent"
```

也可使用 `NOVEL_GENERATION_MODE=agent` 作为当前进程级覆盖。v2.10.1 安装器默认把 Codex 注册为 `agent`、pi/Hermes 注册为 `external`，即使工作区 `routes.toml` 写了 external，也不能覆盖 Codex 的进程级 Agent 模式。生成工具会返回 `agent_task`，当前 Agent 读取任务中的消息并调用 `novel_submit_agent_task` 提交原始生成结果，MCP 再继续校验、落盘和记录事件。该模式不让 MCP 反向调用宿主 Agent，因此不会自动产生第二个模型额度消耗。
- 生成提案前会排除 `.meta.json` 并校验每个批次的结果、元数据和源文本哈希；旧提案不会在重新分析期间显示为可用。

Provider 可在 `providers.toml` 中关闭流式：

```toml
stream = false
```

## v2.6 新增：Provider 双协议（Responses + Chat Completions）

每个供应商现在可以独立设置：

```toml
api_mode = "responses"         # POST <base_url>/responses
api_mode = "chat_completions"  # POST <base_url>/chat/completions
```

新安装默认 NexaPortAI 使用 `responses`，Ollama/oMLX 继续使用 `chat_completions`。旧配置没有 `api_mode` 时仍默认 Chat Completions，避免升级后突然改变现有供应商行为。要把已有 NexaPort 配置切到 Responses：

```bash
./install.sh --repair --reconfigure --api-mode responses
```

## v2.4 新增：一键安装 + 自动 pi MCP

最简单安装现在只有：

```bash
cd ~/.pi/agent/skills/novel-production
./install.sh
```

默认 NexaPortAI Base URL 为 `https://api.nexaportai.com/v1`，默认 cloud profile 的 10 个路由都使用 `nexaport / gpt-5.6-sol`。安装器自动准备 uv/Python/MCP 依赖、创建配置和小说目录；检测到 pi 时自动安装 `pi-mcp-extension` 并合并 `~/.pi/agent/mcp.json`。NexaPortAI Key 可静默保存到 `~/.config/novel-production/.env`（0600），MCP 会自动读取，不再需要手动 `export` / `source`。

检查：`./install.sh --check`；修复：`./install.sh --repair`。


本地优先的长篇小说生产系统。Agent Skill 负责流程决策与人工确认，MCP/CLI 负责确定性工具、状态门禁与事务；模型生成可使用 Ollama/oMLX/云模型，也可切换为当前宿主 Agent。每本小说使用独立工作区保存正典、人物、动态状态、时间线、道具、剧情线、伏笔、全书章节预算和完结状态。

## v2.3 新增：真正的完结管理 + Story Budget

过去的 `planned_chapters` 只是规划上下文。v2.3 将它升级为正式终点：

```text
Ending Target (min / ideal / max)
→ Story Budget（全书连续章节预算）
→ Macro
→ Volume（硬校验连续覆盖到 ideal）
→ Full Outline（硬校验 1..ideal）
→ 章节生产
→ Ending Progress
→ Final Arc 收线门禁
→ 最大终章审计
→ Finalize
```

新增 7 个正式能力：

- `novel_set_ending_target`
- `novel_generate_ending_options`
- `novel_apply_ending_plan`
- `novel_rebalance_story_budget`
- `novel_check_ending_progress`
- `novel_enter_final_arc`
- `novel_finalize_novel`

快捷使用：

```text
设置完结 300章
设置完结 280-320章
设计结局
选择B作为正式结局
检查完结进度
进入终局
完结小说
```

final arc 生效后，确定性校验会阻止新增未知长期剧情线和新伏笔。到 `max_chapter` 仍有非可选主线、伏笔、required outcomes 或人物关系终点未完成时，章节提交被阻断。正式完结后生成 `final/` 审计文件，并锁定继续写作。

详见 `references/ending-management.md`。

## 已有小说接管续写

可以把已经写好的 `.txt/.md/.markdown` 小说交给 Skill：

```text
接管 /path/to/novel
```

这一句默认执行：

```text
导入原稿（只读）
→ 分批证据提取
→ confirmed / inferred / unknown 分级
→ 全局归并与冲突检查
→ 逆向重建人物/世界观/知识/关系/时间线/道具/剧情线/伏笔
→ 回顾性卷纲与已有拆章
→ continuation boundary
→ 接管报告
→ 停止等待用户确认
```

如果接管时没有明确总章数，`last_existing_chapter + 30` 只作为临时工作范围，**不会被当成真实结局**。应用接管提案后必须先设置真实完结目标，再生成未来 continuation plan。

## 首次调用体验

- Skill 可直接解压到 Agent 的 skills 目录。
- 推荐先执行一次 `./install.sh`；通常只需在本机终端输入 NexaPortAI API Key。
- 安装器自动配置依赖、`.env`、pi MCP 和默认模型路由。
- 第一次调用仍会运行环境门禁；全部通过后写本地标记，配置未变化时后续缓存命中。
- 接管旧书和完结管理不新增模型路由：证据提取复用 `extractor`，全局归并/未来规划/结局方案复用 `director`。

## 核心质量能力

- 稳定 `fact_id/location_id/item_id/ability_id/event_id`
- 人物永久档案、成长线、动态状态、知识边界分离
- 角色发言归属与知识越界校验
- 有限视角内心访问校验
- 地点图与旅行时间矩阵
- 道具双向事件账本，阻止负库存和道具复活
- 能力使用/学习事件校验
- 伏笔期限门禁
- Ending target / story budget / final arc / finalization 门禁
- SQLite 事件存储，多文件状态事务，文件可重建
- token、成本、失败、repair round、质量趋势和模型对比
- 本地混合检索
- 固定故障回归评测

## 新书流程

```text
创建工作区 + 完结目标
→ AI 导演开书
→ 故事宏观规划
→ 世界观与 ID 账本
→ 角色永久档案/成长线/知识边界
→ 完整卷战略
→ 全书拆章
→ 章节包
→ 正文
→ 独立审校
→ 定点修复
→ Delta 提取
→ 确定性门禁
→ SQLite 事务提交
→ Ending Progress 更新
```

完整安装与使用见 `references/installation.md`。

## 测试

执行身份：macOS 当前普通用户，不加 `sudo`。

```bash
./scripts/run_tests.sh
```
