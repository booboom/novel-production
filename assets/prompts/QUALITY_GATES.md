# Writer/Reviewer Hard Gates

Writing Quality 与 Canon Quality 分层执行：Writer 只接收本章动态 Rule Cards；Reviewer 在原有审计 JSON 中追加带正文证据的 `writing_quality.gates`；Repairer 只修失败 Gate。任何 Writing Rule 都不能关闭下列 Canon/状态硬门禁。

- Never modify immutable profiles or canon directly.
- Use stable fact/location/item/ability/event IDs.
- A character may assert only facts currently known or learned in this chapter with evidence.
- Writer receives only writing-safe profiles, achieved arc state, and facts visible to participating characters; private biography and future arc milestones are reviewer-only.
- Every private/background claim needs provenance, and every restricted data/resource operation needs established authority.
- Attribute key utterances with an exact excerpt and speaker.
- Limited POV cannot access another character's inner state.
- Respect travel time between location IDs.
- Represent every item change as an inventory event; balances may never go negative.
- Every semantic state change must map from reviewer `change_id` to an existing Delta entry; static item metadata must not store `current_holder`.
- Ability use requires ownership; learning requires explicit approval.
- Relationship jumps require event evidence.
- Outline location/task drift, unsupported reasoning, underlength drafts, and unintentional repetition of recent action/evidence/hook patterns are blocking.
- Foreshadowing deadlines require payoff, resolution, or explicit extension.
- Retrieval context is supplemental and cannot override structured canon.
- Ending target and Story Budget are hard constraints: do not write beyond max_chapter.
- In the final arc, do not introduce new long-running plot threads or foreshadowing unless the ending plan explicitly allows it.
- Completing a required ending or relationship outcome requires a timeline event from the same chapter as evidence.
- The maximum ending chapter cannot commit while nonoptional plot threads, foreshadowing, required outcomes, or relationship outcomes remain unresolved.
