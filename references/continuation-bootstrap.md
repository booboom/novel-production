# 已有小说接管 / Continuation Bootstrap

## 目标

把已经存在的小说正文转成 Novel Production 可持续维护的工作区，再从最后已有章节之后继续写。接管阶段不修改原文，也不把模型推测冒充成作者正典。

## 接受的输入

- 单个 UTF-8 `.txt` / `.md` / `.markdown` 文件；支持 `第N章`、`第N回`、`Chapter N` 标题切章。
- 一个包含上述文本文件的目录；优先从文件名识别章号，没有章号则按自然排序编号。

不直接解析 DOCX/PDF/EPUB；先转换为 UTF-8 文本或 Markdown 后导入。

## 正式流程

1. `novel_import_existing_novel`
   - 创建独立工作区。
   - 保存只读 `source/original/` 副本和 `source/import-manifest.yaml`。
   - 已有章节进入 `chapters/`，并设置 `last_committed_chapter`。
2. `novel_analyze_imported_novel`
   - 默认每批 5 章，受 `max_chars` 限制。
   - 分批计划和父子拆分状态写入 `analysis/plan.json`，分批输出放在 `analysis/chunks/`。
   - 分析带源文本哈希，可断点续跑；源内容没变时跳过已完成批次，子批次失败时不会重新提交父批次。
3. `novel_generate_takeover_proposal`
   - 全局归并所有批次。
   - 汇总前排除 `*.meta.json`，并校验每个结果文件、元数据和源文本哈希；校验失败时停止。
   - 输出 `analysis/reports/takeover-proposal.json`。
   - 不写入正式正典。
4. 人工审核
   - 查看人物、世界、时间线、知识边界、关系、道具、剧情线、伏笔、历史卷纲/拆章、continuation boundary 和 conflicts。
   - high/critical unresolved conflicts 默认禁止应用。
5. `novel_apply_takeover_proposal`
   - 创建检查点。
   - 通过 SQLite 事件事务写入正式投影。
6. 检查 `ending_status`。若 `status=unset`，**先**让用户设置真实完结目标（`novel_set_ending_target`），例如固定 `300` 或范围 `280-320`。导入时的 `last_existing_chapter + 30` 只是临时工作范围，不是结局。
7. `novel_generate_continuation_plan`
   - 从 `continuation_boundary.next_chapter` 或当前首个未规划章节开始规划。
   - `chapter_count` 表示整个未来范围，规划器默认按每批 10 章生成，不再有单次 100 章上限。
   - 每批提案保存到 `planning/proposals/continuation-XXXX-YYYY-batch.json`，进度保存到 `.novel/continuation-plan-progress.json`；失败或意外退出后重复调用会跳过已完成批次。
   - 不得超过 `ending_plan.target.max_chapter`；越界请求截到 max。
   - 不允许覆盖已存在的逐章大纲；不要修改未完成会话的 `chapter_count` / `batch_size` / instruction。
8. 人工审核未来规划后 `novel_apply_continuation_plan`，进入 `chapter_execution`。
9. 使用标准 `novel_run_chapter` / `novel_run_batch` 续写，并持续服从完结预算。

## 证据等级

### confirmed

正文明确给出。必须包含：

```yaml
confidence: confirmed
evidence:
  - chapter: 17
    excerpt: "不超过100字的原文短证据"
```

### inferred

可由多个现象合理推断，但作者没有直接确认。可以作为规划参考，不能当作不可违反的硬事实。

### unknown

正文没有足够信息。必须保持未知，不能为了续写方便自动补齐。

## 接管输出

正式应用后会生成或补全：

- `canon/facts.yaml`
- `canon/immutable-facts.yaml`
- `canon/world.yaml`
- `canon/locations.yaml`
- `characters/profiles/*.yaml`
- `characters/arcs/*.yaml`
- `characters/knowledge-boundaries.yaml`
- `state/characters/*.yaml`
- `state/relationship-state.yaml`
- `state/timeline.yaml`
- `state/item-ledger.yaml`
- `state/plot-ledger.yaml`
- `state/foreshadowing.yaml`
- `planning/volumes/*.yaml`
- `planning/reconstructed-outlines/*.yaml`
- `planning/chapter-outlines/*.yaml`（已有章节的回顾性拆章）
- `state/continuation-boundary.yaml`
- `analysis/reports/takeover-report.yaml`

## Continuation Boundary

这是旧书与新续写之间的硬边界，至少记录：

- 最后已有章与下一章
- 当前故事时间
- 当前 POV
- 当前地点
- 活跃人物
- 活跃剧情线
- 紧急伏笔
- 当前悬念
- 未解问题

续写规划只能从 `next_chapter` 开始。

## 冲突处理

模型发现旧正文自相矛盾时不得偷偷修复。必须写入 `conflicts`：

```json
{
  "id": "conflict_birthdate",
  "severity": "high",
  "status": "unresolved",
  "description": "角色出生日期在两处不同",
  "evidence": [
    {"chapter": 8, "excerpt": "..."},
    {"chapter": 44, "excerpt": "..."}
  ]
}
```

High/Critical unresolved 冲突默认阻断 `novel_apply_takeover_proposal`。用户明确决定采用哪一处，或明确接受旧作矛盾后，才能继续。

## 模型路由

不增加新的必需路由：

- 批次证据提取：`extractor`
- 全局接管归并：`director`
- 未来续写规划：`director`
- 正文：`writer`
- 审校：`reviewer`
- 修复：`repairer`

因此已有 v2.2 模型配置可直接升级使用。接管应用后，如果没有显式完结目标，v2.3 会要求先设置真实完结目标，再生成后续规划。
