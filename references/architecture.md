# 架构

```text
薄 Skill（意图路由 + 硬门禁 + 人工确认）
        ↓ 按需读 references/
        ↓ MCP tools
Novel Production MCP
  ├─ 模型路由与调用追踪
  ├─ 章节状态机与语义门禁
  ├─ Ending Controller + Story Budget
  ├─ SQLite 事件事务（耐久事实源）
  ├─ YAML/Markdown 投影器
  └─ 本地混合检索与评测
        ↓
OpenAI-compatible providers 或 宿主 Agent（Agent Native）
        ↓
独立小说工作区
```

Skill 不应复述全部门禁与版本史；确定性规则以 MCP 校验与 `references/` 为准。

## 数据边界

- `SQLite events.sqlite3`：耐久事件、当前投影、模型调用、检索块。
- YAML/Markdown/JSON：便于人类查看和 Git 管理的投影。
- Writer 输出：仅草稿，不能直接改正典。
- Extractor 输出：仅 Delta，必须经过确定性校验。
- 检索结果：低优先级补充，不能覆盖结构化正典。

## 事务语义

规划应用和章节提交先在单个 SQLite `BEGIN IMMEDIATE` 事务中写入事件和全部投影内容，提交后再原子物化文件。若进程在数据库提交后、文件完成前中断，`novel_rebuild_projections` 可从数据库恢复，不会丢失已提交状态。

中间产物（packet/draft/audit/delta）不属于正式正典，可独立写入；正式状态只有通过 `chapter.committed` 事件后生效。

## Continuation Bootstrap（已有小说接管）

```text
用户旧正文
  ↓ 只读复制 + 章节识别
source/original + chapters/
  ↓ Map：每批约5章证据提取
analysis/plan.json + analysis/chunks/*.json
  ↓ Reduce：全局归并、去重、冲突检测
analysis/reports/takeover-proposal.json
  ↓ 人工确认
SQLite takeover.applied 事务
  ↓
角色/世界/时间线/道具/伏笔/知识边界投影
  + continuation-boundary.yaml
  ↓ 未来规划（只能 next_chapter 之后）
  ↓ 人工确认
标准章节生产流水线
```

接管模式复用 `extractor` 路由做证据抽取，复用 `director` 路由做全局归并和未来规划，因此不要求额外供应商配置。

## Ending Controller（v2.3）

```text
Ending Target (min / ideal / max)
        ↓
Story Budget 1..ideal
        ↓
macro → volume → full outline
        ↓
chapter execution
        ↓ 每章更新
ending-progress
        ↓
final arc gates
        ↓
max chapter completion gate
        ↓
finalize audit → completed
```

新书的 `volume` 和完整 `outline` 在应用时做确定性覆盖校验。旧书续写只允许从 continuation boundary 之后规划，并受 ending max 限制。终局阶段的新长期剧情线/新伏笔由 Delta 校验器阻断，而不是只靠 Prompt。
