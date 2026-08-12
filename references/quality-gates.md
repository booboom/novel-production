# 质量门禁

文学质量的语义门禁、动态 Rule Cards 与写作参考书流程见 [writing-quality.md](writing-quality.md)。本文件以下内容继续定义 Canon 与连续性的既有硬门禁；v2.9 不删除、不弱化，也不把这些门禁重复实现为 Writing Gates。

## 1. 结构与 ID

- 必需目录、数据库和账本存在
- 角色 ID 唯一，且 profile/state/arc 完整
- 所有知识变化引用已登记 `fact_id`
- 时间线、关系、知识、能力和道具证据引用有效 `event_id`

## 2. 人物与视角

- 章节包、导演提案和质量门禁中的设定口号只供内部创作与审校使用；正文必须用行为、选择、冲突、拒绝、代价和后果表现，不得直接复述规则标题、检查清单、稳定 ID 或制作术语
- reviewer 发现旁白或说教式对白泄漏内部规则时必须报告 `rule_leak`；该问题一律作为 high/critical 阻断，repairer 必须改写为场景内表现而非同义改写口号
- 人物档案字段只表示跨场景倾向，不得被复述或字面化为必须出现的台词、动作、道具和习惯；只有当前场景、大纲、既有正文或道具账本提供独立因果时才可出现具体表现
- reviewer 发现为了展示档案而强行增加内容时必须报告 `profile_literalization`；该问题一律作为 high/critical 阻断，repairer 必须删除强演内容并保留人物在当前冲突中的自然选择
- 有名有姓人物首次出现或进入新场景前，必须先有事件/职责/目标/地点因果、身份或行动线索、已有角色提及三者之一；惊喜现身也要先给可回溯线索再揭示身份，连续场景中的既有人物除外
- reviewer 发现姓名与人物无预兆同时空降时必须报告 `character_entry`；该问题一律作为 high/critical 阻断，repairer 只能补最小桥梁或延后揭名，不得新增未登记背景
- Delta 不能修改 immutable/profile/core traits/speech style
- 角色发言短句必须存在于正文
- 发言中声明的 fact 必须位于角色 `knows` 或本章有证据地获得
- 第一人称/第三人称有限视角不能读取其他人物内心
- 新能力必须显式审批；使用能力必须已拥有
- 对角色而言的实质异常刺激必须有符合人物与处境的反应桥梁；可以是察觉、警惕、迟疑、试探、回避、冻结、寻找来源或有依据地压下反应，不机械要求追问
- reviewer 必须输出 `abnormal_events`；没有实质异常时也必须输出空数组。字段缺失、条目格式错误或任一条 `plausible=false` 都阻断提交

## 3. 时间、地点和因果资源

- 时间不得倒退
- 同一人物同一时刻不能出现在不同地点
- 地点迁移必须满足 `travel_minutes`
- 道具通过 `inventory_events` 记账：acquire/consume/transfer/drop/destroy/damage/repair
- 消耗和转移不能造成负余额
- 关系大幅跳变必须有事件证据
- Writer 只接收写作安全档案、已发生成长线和角色可用事实；完整私密档案与全部事实账本仅供 reviewer 追溯知识来源
- reviewer 必须输出 `knowledge_provenance_audit` 和 `authorization_audit`。敏感主张没有 fact/event/正文来源、或受限资源操作没有职责/授权/取得访问权事件时阻断
- reviewer 必须输出 `outline_fidelity_audit` 和 `reasoning_chain_audit`。大纲地点/任务漂移、证据类型错配、相关冒充因果、怀疑升级为确认时阻断
- reviewer 的 `state_coverage_audit` 为每个应入账变化分配 `change_id`；Extractor 必须用 `state_coverage.representations` 映射到真实 Delta 条目，映射缺失或指向不存在条目时阻断
- 道具 `current_holder` 不得写入静态元数据；持有人只能由 balances/events 推导

## 4. 剧情与伏笔

- 剧情线/伏笔更新必须关联真实事件
- 到 `deadline_chapter` 的伏笔必须 payoff/resolve/extend
- 新拆章必须提供 `rhythm_signature.opening_mode/core_action/evidence_type/emotional_turn/ending_hook_type`；最近五章重复同一核心动作、证据和结尾钩子组合时拒绝应用

## 4.1 章节标题

- 拆章 `title` 只使用独立、面向读者的章名
- `mini_arc` 单独保存小剧情段，`chapter_task` 单独保存本章任务，不得依赖标题承载规划结构
- 禁止把章号、剧情段/卷名前缀或末尾排序数字写入 `title`
- 旧格式 `失物登记·补交失物表1` 必须拆分为 `title: 补交失物表`、`mini_arc: 失物登记`、`chapter_task: 补交失物表`，正文标题由系统渲染为 `第1章 补交失物表`

## 4.2 读者任务定向

- 新拆章必须在 `reader_orientation` 中登记 `task_origin`、`task_object`、`why_now`、`stakes`、`method_reason`、`establish_before`
- 第一项关键行动、调查、冲突或异常发生前，正文必须形成最小因果闭环：任务来源 → 对象 → 当前触发原因 → 失败代价 → 方法理由
- 连续任务可引用前章已建立事实，但必须保留可识别衔接；人物自己知道不等于读者已经知道，后置解释不能替代前置因果
- reviewer 必须输出完整 `orientation_audit`；缺失或字段异常为 critical，`sufficient=false` 或未在关键行动前建立则产生 `narrative_orientation` high/critical
- repairer 只能用章节包、上一章和既有正典补最小桥梁，优先使用人物选择、通知、物件、程序障碍、对话或即时后果，不得编造背景或集中倾倒设定
- Writer 不得擅自提出或应用正典变化

## 4.3 叙事时序与未来计划泄漏

- 新拆章必须包含 `narrative_timing.established_before_chapter` 和 `narrative_timing.introduce_this_chapter`
- `introduce_this_chapter` 每项使用稳定 `element_id`，并登记 `description`、`establish_before`、`allow_early_hint`
- Writer 只能使用上一章/正典已经建立的信息与本章允许引入的信息；常识上的合理性不能替代叙事中的首次建立
- 未来首次引入表和兼容旧拆章的后续摘要只提供给 reviewer，不写入 Writer 章节包
- reviewer 必须输出 `narrative_timing_audit.sequence_valid/premature_references/summary`；缺失或格式错误为 critical
- 任一提前出现的事实、方法、证据来源、嫌疑方向、关系、制度或解法产生 `future_plan_leak` high/critical 并阻断提交
- repairer 必须删除或替换提前信息，不得通过补写未来背景来为原句寻找理由

## 5. 完结与章节预算

- Ending target 必须满足 `1 <= min <= ideal <= max`
- 新书卷范围必须连续覆盖 `1..ideal`
- 新书完整拆章必须恰好为 `1..ideal`
- 续写规划不能越过 `max`
- final arc 禁止新增未知长期剧情线和 introduce 新伏笔
- `ending_outcome_updates` 必须引用已登记 outcome 和有效事件证据
- 到 `max` 章节时，非可选剧情线/伏笔/required outcomes/relationship outcomes 必须全部完成，否则阻断提交
- 正式完结前再次运行确定性审计；完成后项目锁定

## 6. 独立模型审校

独立 reviewer 检查任务完成度、人物、知识、权限、发言、视角、时间、旅行、因果、推理链、异常刺激反应、叙事时序、状态覆盖、重复模式、道具、能力、关系、剧情、伏笔和文风。达到最低分，且 `abnormal_events`、`orientation_audit`、`narrative_timing_audit`、`knowledge_provenance_audit`、`authorization_audit`、`outline_fidelity_audit`、`reasoning_chain_audit`、`state_coverage_audit`、`repetition_audit` 结构完整并且无 high/critical 问题时才批准。

正文有效字符还必须达到 `target_chars × min_chapter_length_ratio`（默认 0.85）；篇幅门禁不能通过复述设定或字面化人物档案规避。

对已提交章节使用 `novel_audit_existing_chapters` 生成 `analysis/historical-audits/` 下的只读报告；该工具不能替换正文、正典、Delta、状态或活动审校。

## 7. 事务提交

- 提交前创建检查点
- 事件与所有正式状态投影在同一 SQLite 事务中提交
- 文件物化失败不会撤销数据库事实；通过投影重建恢复
