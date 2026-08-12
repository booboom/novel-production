# 工作流速查

Skill 只做意图路由；本文件给出可执行步骤与工具顺序。门禁细节见 `quality-gates.md`、`writing-quality.md`、`ending-management.md`。

## 新书

1. `novel_create_workspace`
   - 必填：`workspace_id`（稳定 ASCII）、`title`、`genre`、`premise`、`planned_chapters`
   - v3 会把 `planned_chapters` 写成初始完结目标并生成 Story Budget；若只是粗估，创建后立刻 `novel_set_ending_target`
2. 旧工作区先 `novel_migrate_workspace`
3. 按顺序生成并**人工确认后**应用：
   1. `director`
   2. `macro`
   3. `world`
   4. `characters`
   5. `volume`
   6. `outline`
4. 每步：`novel_generate_stage_proposal` → 展示路径与摘要 → 用户确认 → `novel_apply_stage_proposal`
5. 不得跳过确认直接写入正典

### 拆章字段约定

- `title`：读者可见独立章名（如 `补交失物表`），不含章号、卷前缀、排序数字
- `mini_arc`：内部小剧情段，可跨章重复
- `chapter_task`：本章任务
- 正文标题由系统渲染为 `第N章 {title}`
- 新拆章需要：`reader_orientation`、`narrative_timing`、`rhythm_signature`；受限操作声明 `restricted_actions`
- 标题多样性：最近 5 章窗口校验；历史旧标题只作比较上下文

## 单章

默认：

```text
novel_check_ending_progress
→ novel_run_chapter(workspace_id, chapter_number, auto_commit=true)
```

完整内部顺序：

```text
Packet + 动态 Rule Cards
→ Writer
→ 独立审校 + Writing Reviewer
→ 文学门禁
→ 定点修复（仅 failed gates）
→ 完整复审
→ Delta
→ Canon 确定性校验
→ Writing Final Readiness
→ SQLite 事务提交
→ Ending Progress 更新
```

逐步控制：

1. `novel_prepare_chapter`
2. `novel_generate_chapter_draft`
3. `novel_review_chapter`
4. 失败则 `novel_repair_chapter` 后重新审校
5. `novel_extract_chapter_delta`
6. 校验通过后 `novel_commit_chapter`
7. high/critical 未解决 → 解释并停止

Writer 只读写作安全包；私密事实、未来人物弧、完整知识账本只给 Reviewer。

## 批量

- `novel_run_batch`：严格按用户指定起始章与数量
- 硬限制 20 章；首个门禁失败处停止
- 初次建议 1 章打样，常规 1–3 章
- 触碰 `max_chapter` 时写到 max 为止，不自动扩大结局

## 正典与恢复

显式登记：

- `novel_register_fact`
- `novel_register_item`
- `novel_set_travel_time`
- `novel_approve_character_ability`

运维：

- `novel_event_log`
- `novel_validate_workspace`
- `novel_rebuild_projections`
- `novel_observability_report`
- `novel_reindex_memory` / `novel_search_memory`
- `novel_run_evals` / `novel_test_writing_gates`
- `novel_checkpoint` → `novel_list_checkpoints` → 确认后 `novel_restore_checkpoint`

手工改文件造成投影漂移时：先问用户保留手改还是从 SQLite 重建。

## 写作 Doctrine

```text
导入写作书 → 分析 → 生成规则提案 → 用户确认 → 应用
```

- `novel_import_writing_reference` / `novel_scan_writing_references`
- `novel_analyze_writing_reference`
- `novel_generate_doctrine_proposal`（不自动应用）
- `novel_apply_doctrine_proposal`
- `novel_list_writing_rules` / `novel_get_writing_rule`
- `novel_enable_writing_rule` / `novel_disable_writing_rule`
- `novel_rebuild_writing_rule_index`

用户规则保存在 `~/.config/novel-production/writing-doctrine/`，不写进 Skill 发行目录。

## 审计与导出

- `novel_audit_existing_chapters`：只写 `analysis/historical-audits/`，不改正文/正典/Delta
- `novel_export_current_novel`：合并已提交章节，默认 UTF-8 TXT 到 `exports/`
