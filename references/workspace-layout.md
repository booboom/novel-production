# 每本小说的目录

```text
<workspace>/
├── project.yaml
├── routes.toml                     # 可选；仅需覆盖全局路由时手工创建
├── .novel/
│   ├── events.sqlite3          # 事件、投影、模型调用、检索索引
│   └── continuation-plan-progress.json  # 分批续写规划断点
├── canon/
│   ├── premise.md
│   ├── world.yaml
│   ├── rules.yaml
│   ├── immutable-facts.yaml
│   ├── facts.yaml              # 稳定 fact_id
│   ├── locations.yaml          # location_id + travel_minutes
│   └── approved-changes.yaml
├── characters/
│   ├── index.yaml
│   ├── profiles/<character_id>.yaml
│   ├── arcs/<character_id>.yaml
│   ├── relationships.yaml
│   └── knowledge-boundaries.yaml
├── source/                         # 仅已有小说接管模式
│   ├── import-manifest.yaml        # 只读导入清单
│   └── original/                   # 只读原稿副本，绝不用于回写
├── analysis/                       # 仅已有小说接管模式
│   ├── plan.json                   # 可恢复的批次计划与父子拆分状态
│   ├── chunks/                     # 分批证据提取 JSON
│   ├── reports/                    # takeover proposal / report
│   └── conflicts/                  # 互相矛盾或需人工决策的证据
├── state/
│   ├── characters/<character_id>.yaml
│   ├── relationship-state.yaml
│   ├── timeline.yaml
│   ├── item-ledger.yaml        # item 定义、余额、双向事件
│   ├── inventory.yaml          # 兼容投影
│   ├── plot-ledger.yaml
│   ├── foreshadowing.yaml
│   ├── ending-progress.yaml        # 剩余章数/收线阻断/完结就绪
│   └── continuation-boundary.yaml  # 旧书最后状态与下一章边界
├── planning/
│   ├── director-brief.md
│   ├── macro.md
│   ├── story-budget.yaml           # 1..ideal 的连续全书章节预算
│   ├── ending/
│   │   └── ending-plan.yaml        # 终点、必收线、final arc
│   ├── proposals/                  # 总续写提案与每批提案
│   ├── volumes/
│   ├── chapter-outlines/
│   └── reconstructed-outlines/     # 从旧正文反推的历史拆章
├── packets/
├── drafts/history/
├── audits/
├── deltas/
├── chapters/
├── exports/                         # 当前已提交正文的合并导出，默认 UTF-8 TXT
├── runs/
├── metrics/
│   └── llm-calls.jsonl
├── final/                          # 正式完结后生成
│   ├── completion-report.md
│   ├── unresolved-threads.md
│   ├── character-end-states.yaml
│   ├── final-timeline.yaml
│   └── epilogue-notes.md
└── checkpoints/
```

## 文件修改原则

- `profiles/`、`arcs/`、`immutable-facts.yaml`：受保护正典。
- `state/`：只能由通过校验的 Delta 事务更新。
- `planning/proposals/`：非正式提案，用户确认后才应用。
- `chapters/`：只包含已提交正文。
- 默认不创建 `routes.toml`，直接使用 `~/.config/novel-production/routes.toml`；只有明确需要单本书覆盖路由时才手工创建。
- 手工改投影会被标记为 drift；重建会以 SQLite 内容覆盖手工修改。

## 已有小说接管原则

- `source/original/` 是证据副本，导入后设为只读；用户原始目录永不修改。
- `analysis/plan.json` 与 `analysis/status.yaml` 用于接管分析恢复，不属于正式正典。
- `analysis/chunks/` 可以重新生成，不属于正式正典；`*.meta.json` 只保存缓存校验和模型诊断，不提交给 Director 汇总。
- `analysis/reports/takeover-proposal.json` 只有用户确认后才事务应用。
- `confirmed` 正典必须保留原文章号与短证据；`inferred/unknown` 不能自动升级成硬事实。
- `planning/reconstructed-outlines/` 只描述已有章节，标记 `source: reconstructed`。
- `state/continuation-boundary.yaml` 是从旧书切换到续写流程的边界快照。

## 完结文件原则

- `ending-plan.yaml` 是正式规划约束，不能被 Writer 直接修改。
- `story-budget.yaml` 的阶段必须连续覆盖 `1..ideal_chapter`。
- `ending-progress.yaml` 是运行时投影，每章提交后随剧情线/伏笔/outcome 一起更新。
- `final/` 只有正式 `novel_finalize_novel` 后才成为完结审计产物。
