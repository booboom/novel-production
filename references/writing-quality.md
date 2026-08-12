# Writing Quality 子系统

## 目录

- 分层职责
- Rule Card
- Doctrine 选择
- 章节执行顺序
- 写作参考书
- 配置
- 报告与阻断
- 用户 Doctrine 与检索索引
- 严重度、证据和收敛策略

## 分层职责

- `validation.py`：保留 Canon、Timeline、Knowledge、Inventory、Ability、Relationship、Ending 等确定性门禁。
- `writing_doctrine.py`：加载 Core / Genre / Project Doctrine，校验和动态选择 Rule Cards。
- `writing_gates.py`：验证 Reviewer 的文学质量 JSON、判定阻断、生成报告和定点修复清单。
- `writing_profiles.py`：合并默认、项目、全局 TOML 与工作区 TOML 配置。
- `writing_reference.py`：解析 PDF/EPUB/TXT/Markdown，并为分批方法论提炼提供来源定位。

## Rule Card

每张规则必须包含：

```yaml
id: SCENE-001
name: 主要场景必须存在即时目标
domain: scene
domains: [scene, causality]
scene_types: [investigation]
techniques: [suspense, information_control]
genres: [suspense]
description: 主要场景由人物主动追求一个具体结果。
stage: [writer, reviewer, repairer]
check_type: semantic
severity: high
blocking: true
criteria: []
failure_patterns: []
evidence_required: []
writer_instruction: 把规则转为场景行为，不复述规则。
review_instruction: 提取目标、阻力、行动和结果。
repair_instruction: 只修复当前场景目标缺口。
applies_when: {}
source_refs:
  - reference_id: core-doctrine
    section: scene-purpose
enabled: true
```

`source_refs` 只保存 reference_id 与页码/章节/section 等短定位。规则必须重新表达方法、概念、判断标准和流程，不能长篇复制参考书。

## Doctrine 选择

合并优先级为 Project > Genre > Reference > Core。系统根据章节的显式 scene type/tags、参与人数、任务关键词、genre 与最近三章失败 Gate 确定性排序。默认每章最多 30 张规则、每场景 12 张规则、约 6000 rule tokens；必要 Core Rules 不会被预算全部挤掉。Writer 与 Reviewer 使用同一份本章规则集合，不会接收无关的全部 Doctrine。

`web_novel` 是可关闭的 Genre Profile。它强调有效事件密度、持续目标、局势变化和章末未完成压力，不要求机械爽点、打脸或固定段落长度。

## 章节执行顺序

```text
Packet + Rule Selection
→ Writer
→ Existing Reviewer + writing_quality.gates
→ Existing Gates + Writing Gates
→ Targeted Repair（仅 failed_gate_ids）
→ Full Recheck
→ Delta
→ Existing Canon Validation
→ Writing Report Readiness
→ Commit
```

Reviewer 对每个适用 Gate 都必须提供正文证据、通过/失败原因和失败时的定点修复指令。high/critical 且 blocking 的失败会停止流程；medium/low 默认作为警告。慢场景、环境描写和内心活动只要承担明确功能并改变理解、关系、决定或压力即可通过。

## 写作参考书

自然语言流程：

```text
导入写作书 /path/book.pdf
分析这本写作书
生成写作规则
启用这套写作方法
```

对应 MCP：

- `novel_import_writing_reference`
- `novel_analyze_writing_reference`
- `novel_generate_doctrine_proposal`
- `novel_apply_doctrine_proposal`
- `novel_list_writing_rules`
- `novel_enable_writing_rule`
- `novel_disable_writing_rule`
- `novel_test_writing_gates`
- `novel_scan_writing_references`
- `novel_rebuild_writing_rule_index`
- `novel_get_writing_rule`

导入后原文件在 `writing/references/<reference_id>/` 只读保存；解析结果按页、EPUB 文档或 Markdown 标题切分。分析批次立即落盘，重新调用会复用已完成批次。提案必须先展示给用户，不自动写入 Project Doctrine。

## 配置

全局或工作区 `routes.toml`：

```toml
[writing_quality]
enabled = true
profile = "web_novel"
min_score = 85
max_repair_rounds = 2
block_high = true
block_critical = true
min_blocking_confidence = 0.75
stop_on_unresolved_critical = true
stop_on_unresolved_high = false
allow_commit_with_warnings = true
max_rules_per_chapter = 30
max_rules_per_scene = 12
max_rule_tokens = 6000
anti_fluff = true
causality = true
dialogue = true
information_novelty = true
```

工作区 TOML 优先于全局 TOML；两者优先于项目创建时的默认配置。禁用某条 Rule Card 只影响文学语义规则，不能关闭 Canon 硬门禁。

## 报告与阻断

每次审校写入：

- `analysis/writing-quality/chapter-NNNN.json`
- `analysis/writing-quality/chapter-NNNN.md`

JSON 记录 score、status、applied_rules、passed_gates、failed_gates、repair_rounds、每项 Gate assessment 和分类 flags。提交前会再次检查报告存在、规则 ID 可解析、Reviewer 覆盖完整且没有阻断失败。`min_score` 只产生质量警告，不单独拒绝提交。

## 用户 Doctrine 与检索索引

参考书规则不写进 Skill 或小说工作区，统一保存到：

```text
~/.config/novel-production/writing-doctrine/
├── core/<domain>/
├── scenes/<scene_type>/
├── techniques/<technique>/
├── genre/<genre>/
├── references/
├── proposals/
└── index.yaml
```

每条规则只保存一份；`index.yaml` 用 `by_rule_id / by_domain / by_scene_type / by_technique / by_genre / by_stage / by_severity / by_source` 建立多标签关系。索引是派生数据，可由 `novel_rebuild_writing_rule_index` 从 Rule Cards 重建。旧 Rule Card 没有 taxonomy 字段时，系统从 `domain` 和 `applies_when` 迁移默认值。

参考书目录先用 `novel_scan_writing_references` 扫描。文件哈希未变化时跳过；新增文件导入，修改文件生成新 reference fingerprint，删除文件只登记状态，不静默删除已经应用的规则。提案阶段负责分类、去重、来源合并和冲突报告；只有用户确认后才应用。

## 严重度、证据和收敛策略

- `critical + blocking + sufficient evidence`：阻断并定点修复；达到上限仍失败时默认 `blocked`。
- `high + blocking + sufficient evidence`：修复上限内为 `repair_required`；达到上限后由 `stop_on_unresolved_high` 决定阻断或带警告提交。
- `medium`：不单独阻断，可作为 repair candidate；达到上限后必须继续推进。
- `low`：只记录建议，默认不触发 Repair。

所有失败 Gate 都应提供 rule_id、severity、blocking、confidence、evidence、reason。证据为空、理由为空或 confidence 低于 `min_blocking_confidence` 时降级为 warning。多个 low/medium 不会按数量自动升级；需要整体阻断时必须有独立 aggregate Rule 和正文证据。最终状态只有 `approved / approved_with_warnings / repair_required / blocked`，且 Gate 始终优先于 Score。
