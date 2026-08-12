# Novel Production Skill + MCP

本地优先的长篇小说生产系统。Agent Skill 负责流程决策与人工确认，MCP/CLI 负责确定性工具、状态门禁与事务；模型生成可使用 Ollama/oMLX/云模型，也可切换为当前宿主 Agent。每本小说使用独立工作区保存正典、人物、动态状态、时间线、道具、剧情线、伏笔、全书章节预算和完结状态。

`SKILL.md` 是意图路由器：会话启动、硬不变量、快捷意图与最小流程。安装、接管、完结、门禁、写作质量、场景模拟等长说明由 `references/` 按需加载。版本变更见 `CHANGELOG.md`。

## 安装

最简单安装：

```bash
cd /path/to/novel-production
./install.sh
```

安装器会准备 uv/Python/MCP 依赖、创建配置和小说目录；检测到 pi 时自动安装 `pi-mcp-extension` 并合并 `~/.pi/agent/mcp.json`。默认 NexaPortAI Base URL 为 `https://api.nexaportai.com/v1`，cloud profile 的路由使用 `nexaport / gpt-5.6-sol`。NexaPortAI Key 可静默保存到 `~/.config/novel-production/.env`（0600），MCP 会自动读取，无需手动 `export` / `source`。

```bash
./install.sh --check    # 检查
./install.sh --repair   # 修复
```

Skill 也可直接解压/链接到 Agent 的 skills 目录。第一次调用仍会运行环境门禁；全部通过后写本地标记，配置未变化时后续缓存命中。

完整安装与使用见 `references/installation.md`。

## 生成模式

默认由 MCP 调用 `providers.toml` / `routes.toml` 中的外部模型。若希望所有生成路由使用当前 Codex、pi 或其他宿主 Agent 的模型和额度，在 `~/.config/novel-production/routes.toml` 中设置：

```toml
[generation]
mode = "agent"
```

也可使用 `NOVEL_GENERATION_MODE=agent` 作为进程级覆盖。安装器默认把 Codex 注册为 `agent`、pi/Hermes 注册为 `external`；进程级 Agent 模式不会被工作区 `routes.toml` 的 external 覆盖。

Agent 模式下生成工具返回 `agent_task`：当前 Agent 读取任务消息，调用 `novel_submit_agent_task` 提交原始生成结果，MCP 再继续校验、落盘和记录事件。该模式不让 MCP 反向调用宿主 Agent，因此不会自动产生第二个模型额度消耗。

Provider 可在 `providers.toml` 中关闭流式：

```toml
stream = false
```

每个供应商可独立设置协议：

```toml
api_mode = "responses"         # POST <base_url>/responses
api_mode = "chat_completions"  # POST <base_url>/chat/completions
```

新安装默认 NexaPortAI 使用 `responses`，Ollama/oMLX 使用 `chat_completions`。旧配置没有 `api_mode` 时仍默认 Chat Completions。要把已有 NexaPort 配置切到 Responses：

```bash
./install.sh --repair --reconfigure --api-mode responses
```

## 已有小说接管续写

```text
接管 /path/to/novel
```

默认执行：

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

接管旧书和完结管理不新增模型路由：证据提取复用 `extractor`，全局归并/未来规划/结局方案复用 `director`。

## 完结管理 + Story Budget

`planned_chapters` 是正式终点，不是仅供参考的规划上下文：

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

相关工具：

- `novel_set_ending_target`
- `novel_generate_ending_options`
- `novel_apply_ending_plan`
- `novel_rebalance_story_budget`
- `novel_check_ending_progress`
- `novel_enter_final_arc`
- `novel_finalize_novel`

快捷意图：

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

章节生产默认链路：

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

## Writing Quality

在 Canon、时间线、知识边界、道具、能力、关系和终局门禁之外，Writing Quality 独立控制场景目标、因果链、人物动机、对白、信息新鲜度、节奏、废话和章节价值。

默认启用 `web_novel` Profile，每章只注入当前最相关的 Rule Cards。配置位于 `routes.toml`：

```toml
[writing_quality]
enabled = true
profile = "web_novel"
min_score = 85
max_repair_rounds = 2
block_high = true
block_critical = true
```

用户从参考书提炼的 Rule Cards 保存在 `~/.config/novel-production/writing-doctrine/`，通过可重建 `index.yaml` 按场景、技巧、题材、阶段、严重度和来源检索。支持把 PDF、EPUB、TXT、Markdown 写作教程一次性导入，分批提炼为带来源的 Rule Cards；规则提案必须经用户确认后才进入 Project Doctrine。

Writing Gate 以「规则 blocking + severity + confidence + 正文证据」共同决定是否阻断。分数低、medium/low、非阻断 high 或证据不足只产生 warning/repair candidate，不会因为主观小问题无限 Repair。

详见 `references/writing-quality.md`。

## 核心质量能力

- 稳定 `fact_id/location_id/item_id/ability_id/event_id`
- 人物永久档案、成长线、动态状态、知识边界分离
- Writer 使用按人物知识边界过滤的安全章节包；完整私密正典只交给 Reviewer 做泄漏检查
- 角色发言归属、知识越界、有限视角内心访问校验
- 地点图与旅行时间矩阵
- 道具双向事件账本，阻止负库存和道具复活
- 能力使用/学习事件校验；受限操作授权与节奏指纹
- 伏笔期限门禁；章节标题多样性门禁
- Ending target / story budget / final arc / finalization 门禁
- Delta 必须覆盖正文状态变化并与大纲地点一致，否则禁止写入 SQLite 正典
- SQLite 事件存储，多文件状态事务，文件可重建
- 规划术语自然化：Writer 只收自然语言执行要求，内部规划标签不进正文
- 人物意图模拟、Belief/ToM 分离、场景导演、Exemplar 关联选择（均可关闭）
- token、成本、失败、repair round、质量趋势和模型对比
- 本地混合检索
- 固定故障回归评测
- `novel_audit_existing_chapters`：对历史已提交章节生成只读报告

## 稳定性与运维要点

- Provider 默认 SSE 流式调用 Responses API 和 Chat Completions；普通 JSON 响应也兼容
- 524/429/408/5xx、网络错误和协议错误分类处理；429 遵守 `Retry-After`，不可恢复的 4xx 不盲目重试
- 接管分析支持断点恢复：批次计划与状态持久化，源 hash 一致时从未完成批次继续
- 续写规划支持分批生成与断点续跑，不覆盖已有逐章大纲
- MCP 工具统一进度执行器：阶段进度 + 心跳；完成响应返回状态摘要，长结果落盘压缩

