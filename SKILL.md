---
name: novel-production
description: 长篇小说策划、已有小说接管续写、角色/世界观/卷纲/拆章、章节生成与审修、完结预算、事实 ID、时间地点、道具能力、伏笔与状态事务。适用于写小说、接管旧书、续写、批量写章、检查逻辑；不用于普通短文或不需要项目状态的创作。
---

# Novel Production v2.11

Agent = 导演与事务协调器；MCP = 状态机、门禁与 SQLite 真源。YAML/Markdown 只是可重建投影。生成可走外部 Provider，也可走当前 Agent（Agent Native）。

细节按需读取 `references/`，不要把整本手册塞进上下文。

| 主题 | 文件 |
|---|---|
| 安装 / 环境 | `references/installation.md` |
| 架构 / 事务 | `references/architecture.md` |
| 接管续写 | `references/continuation-bootstrap.md` |
| 完结预算 | `references/ending-management.md` |
| 新书与写章流程 | `references/workflows.md` |
| Canon 门禁 | `references/quality-gates.md` |
| 文学质量 | `references/writing-quality.md` |
| 路由 / Agent 模式 | `references/provider-routing.md` |
| 工作区布局 | `references/workspace-layout.md` |
| 规划术语 | `references/planning-terminology.md` |
| 场景模拟 | `references/scene-simulation.md` |

## 会话启动（每次）

1. 在 Skill 根目录：`python3 scripts/check_environment.py --json`
2. `ready=false` → **停止**。让用户跑 `./install.sh --repair`，不要手改 MCP JSON 或把 Key 贴进聊天。
3. `ready=true` → `novel_generation_mode`，再 `novel_list_routes`。
4. 确认实际模式看 `mode` / `source` / `mode_source`，不要把 `configured_external_routes` 当成当前执行供应商。
5. 任何写作/规划前先 `novel_workspace_status`；不要猜阶段和章号。
6. `mode=agent` 时禁止 `novel_test_route`。生成工具返回 `agent_task` 后：读 `messages` → 宿主生成符合 `response_format` 的**原始内容** → `novel_submit_agent_task(task_id, content)`。不要把 task 包装对象当正文提交。Agent 任务依赖同一 MCP 进程；中途重启会丢续接。

## 硬不变量

1. Writer 只出正文；**不能**改正典/状态。正典变更走提案→人工确认→apply，或显式登记/审批工具。
2. 永久档案（`profiles`/`arcs`）与动态 `state/` 分离。
3. 可追踪事实必须有稳定 ID（`fact_id` / `location_id` / `item_id` / `ability_id` / `event_id`）。
4. 章节默认链路：Packet → Draft → Review → Repair → Delta → 确定性校验 → SQLite 事务提交 → 投影。失败即停。
5. SQLite 事件是真源；检索只是低优先级补充，冲突时服从正典。
6. 旧书 `source/original/` 只读。confirmed 必须有章号+原文证据；inferred/unknown 不当硬事实。
7. Ending plan / story budget 是硬预算：不写超过 `max_chapter`；final arc 默认禁新长期线/新伏笔；终章未收线则阻断提交与完结。
8. high/critical 未解决门禁、未批准能力、未处理 `canon_change_requests` → 停止并报告，不绕过。

## 快捷意图

| 用户说 | 动作 |
|---|---|
| `接管 <路径>` | 导入→分批分析→接管提案→**停等确认**（不自动续写） |
| `设置完结 300章` / `280-320章` | `novel_set_ending_target` |
| `设计结局` | `novel_generate_ending_options`（不自动选） |
| `选择B作为正式结局` | `novel_apply_ending_plan(..., option_id="B")` |
| `检查完结进度` | `novel_check_ending_progress` |
| `进入终局` | `novel_enter_final_arc` |
| `完结小说` | `novel_finalize_novel(force=false)`；仅用户明确要求时 `force=true` |
| `续写N章` | 查状态与完结进度 → `novel_run_batch`；不越 max |
| `导出当前正文` | `novel_export_current_novel` |
| `审计已写章节 A-B` | `novel_audit_existing_chapters`（只读报告） |
| `导入/分析写作书` / `生成写作规则` / `启用这套写作方法` | doctrine 提案流；**确认前不 apply** |
| `查看写作规则` | `novel_list_writing_rules` |
| `准备场景` / `模拟角色意图` / `导演场景` / 场景检查点 | 见 `references/scene-simulation.md` |

## 最小流程

**接管：** `import` → `analyze` → `generate_takeover_proposal` → 展示冲突与证据 → 用户确认 → `apply_takeover_proposal` → 若无真实完结目标先设置 → `generate_continuation_plan` → 确认 → `apply_continuation_plan` → 写章。详情：`references/continuation-bootstrap.md`。

**新书：** `novel_create_workspace` → 提案顺序 `director → macro → world → characters → volume → outline`，每步生成后展示，**确认后才** `novel_apply_stage_proposal`。旧工作区先 `novel_migrate_workspace`。详情：`references/workflows.md`。

**单章（默认）：** `novel_check_ending_progress` → `novel_run_chapter(..., auto_commit=true)`。逐步模式：`prepare` → `draft` → `review` → `repair` → `extract_delta` → `commit`。批量：`novel_run_batch`（硬限 20；首败即停；建议先 1 章打样）。

**正典登记：** `novel_register_fact` / `novel_register_item` / `novel_set_travel_time` / `novel_approve_character_ability`。投影漂移：用户选择保留手改或 `novel_rebuild_projections`。

## 向用户报告

- 工作区、阶段、当前/下一章
- 读者可见 `title` vs 内部 `mini_arc` / `chapter_task`（勿把 mini_arc 当章名）
- 完结目标、剩余章数、final arc、收线阻断
- 实际路由（无密钥）、审校/修复/门禁结果、是否已提交
- 阻断项与下一步

长正文留在工作区，不要整章粘贴进聊天。完整工具语义与门禁细节以 MCP 返回和 `references/` 为准。
