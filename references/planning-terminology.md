# Planning Terminology Rendering

`assets/planning-terminology.yaml` 是规划术语的唯一事实来源。不要在 Prompt、Reviewer 规则或 Repairer 逻辑中另外维护一份同义词列表。

每个条目统一定义：

- `aliases`：Planner、旧大纲、角色档案或历史材料中可能出现的原词。
- `prose_policy`：`internal_only` 表示正文不可出现；`contextual` 表示偶尔自然使用可以成立，但机械复唱会升级。
- `writer_replacements` / `writer_instruction`：只向 Writer 提供自然语言执行要求，不包含术语名称、Rule ID、严重度或审校元数据。
- `reviewer_instruction`：Reviewer 使用完整名称和别名完成 `planning_language_audit`。
- `repair_instruction`：Repairer 定点删除标签，保留具体证据、选择、行动和后果。
- `warning_occurrences` / `blocking_occurrences`：提交前确定性扫描使用的重复阈值。

阶段边界：

1. Planner 可以在结构化规划中使用统一术语。
2. Chapter Packet 构建时递归转换 Writer 可见的字段名和值，但不修改大纲、档案或正典原文件。
3. Writer 只接收转换后的执行要求。
4. Reviewer/Repairer 获得完整术语上下文，分别审校和定点修复。
5. Commit 前再次扫描正文；确定性高置信命中可阻断，LLM 证据不足只能降级为 warning。

`reader_visible_terms` 只用于已由正典明确建立的世界内正式名称。声明项若无法在 `canon/world.yaml`、`canon/facts.yaml` 或 `canon/immutable-facts.yaml` 中找到，不会获得放行。

新增术语时只编辑 YAML 条目，并补充自然化、阶段隔离、阈值和放行测试；不要把原术语写回 Writer 系统提示。
