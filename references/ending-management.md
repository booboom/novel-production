# 完结目标与 Story Budget（v2.3）

v2.3 把“预计总章数”升级为正式的完结控制系统。目标不是机械卡章数，而是让宏观规划、卷纲、拆章、续写、伏笔和人物成长始终知道全书还剩多少叙事容量，以及最终必须完成什么。

## 1. 快捷用法

Agent 已加载 Skill 后，用户可以直接说：

```text
设置完结 300章
```

等价于固定目标：

```yaml
target:
  mode: fixed
  min_chapter: 300
  ideal_chapter: 300
  max_chapter: 300
```

也可以：

```text
设置完结 280-320章
```

默认解释为：

```yaml
target:
  mode: range
  min_chapter: 280
  ideal_chapter: 300
  max_chapter: 320
```

如果用户明确给理想值，例如“280–320章，理想310章”，必须使用 310，不取中点。

其他快捷意图：

```text
设计结局
选择B作为正式结局
检查完结进度
进入终局
完结小说
```

## 2. 三个核心文件

### `planning/ending/ending-plan.yaml`

保存：

- min / ideal / max 章数；
- 结局创意摘要；
- required outcomes；
- must-resolve plot threads；
- must-resolve foreshadowing；
- 可允许留白的线索；
- 人物关系最终落点；
- final arc 起始章；
- 终局门禁策略。

### `planning/story-budget.yaml`

将 `1..ideal_chapter` 确定性拆成连续叙事阶段，例如：

```text
开篇钩子
→ 核心建立
→ 世界与冲突展开
→ 中段升级/转折
→ 真相逼近与主线收束
→ 终局准备
→ 最终篇章
→ 收束与尾声
```

阶段范围一定连续、无重叠、无缺口，并且最后结束在 ideal chapter。

### `state/ending-progress.yaml`

运行时进度，包括：

- 当前已提交章节；
- 距 ideal / max 还剩多少章；
- final arc 是否生效；
- 未解决剧情线；
- 未回收伏笔；
- 未完成 required outcomes；
- 未完成人物关系终点；
- 是否允许正式完结。

## 3. 新书如何参与规划

`novel_create_workspace(..., planned_chapters=300)` 会直接创建固定 300 章初始目标，而不是只把 300 写成备注。

随后：

```text
director
→ macro
→ world
→ characters
→ volume
→ outline
```

都会读取 Ending Plan 与 Story Budget。

### Volume 硬门禁

卷范围必须满足：

```text
第一卷：1-35
第二卷：36-72
...
最后一卷：xxx-300
```

以下情况拒绝应用：

- 第一卷不是从 1 开始；
- 卷之间有缺口；
- 卷之间重叠；
- 最后一卷没有结束在 ideal chapter；
- 章范围格式无效。

### Outline 硬门禁

新书完整拆章必须严格为：

```text
1, 2, 3, ... ideal_chapter
```

不能缺号、重复或越界。

## 4. 已有小说接管

如果使用：

```text
接管 /path/to/novel
```

但没有给最终章数，导入阶段的 `last_existing_chapter + 30` 只作为**临时工作范围**，Ending Plan 状态为 `unset`，绝不能当作真正结局。

接管提案应用后，在生成未来续写规划前必须先得到真实完结目标：

```text
设置完结 300章
```

或：

```text
设置完结 280-320章
```

之后 continuation plan：

- 只能从 `continuation_boundary.next_chapter` 开始；
- 不能覆盖旧章节；
- 不能超过 ending max；
- 如果用户要求规划 30 章但只剩 12 章到 max，会自动截为 12 章。
- 批量正文生成同样会自动截到 `max_chapter`，不会生成越界的下一章。

## 5. 设计结局

```text
设计结局
```

调用 `novel_generate_ending_options`，director 根据：

- 已有正典；
- 人物成长；
- 人物关系；
- 活跃剧情线；
- 未回收伏笔；
- continuation boundary；
- Story Budget；

生成多个方案。

用户选择后：

```text
选择B作为正式结局
```

才调用 `novel_apply_ending_plan`。禁止 Agent 自动选择。

正式 Ending Plan 可以包含：

```yaml
required_outcomes:
  - id: outcome_truth
    description: 揭示核心真相
    status: pending

must_resolve_threads:
  - thread_main

must_resolve_foreshadowing:
  - clue_blue_key

relationship_outcomes:
  - id: relation_main_pair
    description: 两位主角关系得到明确落点
    status: pending
```

## 6. Final Arc

默认 final arc 根据 ideal chapter 自动计算，约在全书后段开始。也可以显式：

```text
进入终局
```

或者指定：

```text
第260章进入终局
```

进入 final arc 后确定性门禁默认：

- 禁止新增未知 `thread_id`；
- 禁止 introduce 新的未知伏笔 ID；
- Writer/Reviewer 必须优先处理已有主线、伏笔和 ending outcomes；
- 不允许通过“再开一个大坑”拖延结局。

## 7. 结局条件如何完成

正文真的完成某个必达结局条件后，Extractor 在 Delta 中输出：

```json
{
  "ending_outcome_updates": [
    {
      "id": "outcome_truth",
      "status": "complete",
      "event_ids": ["event_ch298_003"]
    }
  ]
}
```

对应事件 ID 必须是本章新建的 timeline event；完成状态没有本章事件证据会被确定性阻断。这样“真相已经揭示”不是 Agent 的口头判断，而是可追踪状态。

人物关系结局也使用同一个 outcome update 机制。

## 8. 最大终章门禁

当章节号到达 `max_chapter` 时，系统模拟应用本章 Delta 后检查：

- 非可选剧情线是否全部解决；
- 非可选伏笔是否全部 payoff/resolve；
- required outcomes 是否完成；
- relationship outcomes 是否完成；
- 是否达到最小完结章数。

仍有阻断项，则本章不能提交。

也就是说，不会发生：

```text
第300章写完了
→ 还有12个坑
→ 系统照样标记完结
```

## 9. 正式完结

用户说：

```text
完结小说
```

调用：

```text
novel_finalize_novel(force=false)
```

通过后生成：

```text
final/
├── completion-report.md
├── unresolved-threads.md
├── character-end-states.yaml
├── final-timeline.yaml
└── epilogue-notes.md
```

并将：

```yaml
project:
  status: completed
```

此后正常章节生产会被锁定。

只有用户明确说“强制完结/带未解决项完结”时才允许 `force=true`，并且状态会记录为 `completed_with_exceptions`，审计报告会保留未解决事项。

## 10. 相关 MCP 工具

```text
novel_set_ending_target
novel_generate_ending_options
novel_apply_ending_plan
novel_rebalance_story_budget
novel_check_ending_progress
novel_enter_final_arc
novel_finalize_novel
```
