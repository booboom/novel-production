# 场景模拟 / Exemplar / 场景导演（v2.11）

默认兼容旧工作区；各模块可在配置中关闭。

## 能力

1. **Character Intent**：重要场景可先模拟 POV/对手/核心关系（默认最多 4 人），再经 Scene Director 合成执行计划，最后交给 Writer。关闭 `character_simulation.enabled` 时退回 Packet→Writer。
2. **Truth / Knowledge / Belief / ToM**：Belief 更新必须有 `trigger_event` / `evidence`。Perception 构建时剥离 `hidden_world_truth` 与其他角色私有 Intent。
3. **Exemplar**：参考书提炼同时产出 Rule + Exemplar；Exemplar 只保存 `abstracted_pattern` 与短摘录（≤280 字），按场景有界选择（默认 1–2）。
4. **Scene-Level Generation**：`novel_prepare_scene` → 写场景 → `novel_save_scene_checkpoint` → 可从下一场景续；全部完成后 `novel_assemble_chapter_from_scenes`，再走既有 Writing/Canon 门禁。
5. **Arc Reflection**：只生成带证据提案，禁止无证据改写 personality；`arc_reflection.enabled=false` 可关。

## 工具

| 意图 | 工具 |
|---|---|
| 准备场景 | `novel_prepare_scene` |
| 模拟角色意图 | `novel_simulate_character_intents` |
| 导演场景 | `novel_direct_scene` |
| 保存/读取场景检查点 | `novel_save_scene_checkpoint` / `novel_load_scene_checkpoints` |
| 合并场景草稿 | `novel_assemble_chapter_from_scenes` |
| 选择 Exemplar | `novel_select_scene_exemplars` / `novel_list_exemplars` |
| 应用信念更新 | `novel_apply_belief_updates` |
| 人物弧反思提案 | `novel_propose_arc_reflection` |

可审计产物写入 `analysis/character-simulation/` 等分析目录；不直接改正典。

## 配置段

```toml
[character_simulation]
enabled = true

[scene_generation]
enabled = false

[writing_exemplars]
enabled = true

[arc_reflection]
enabled = true
```

旧配置缺省字段保持兼容。Provider stream 优先级：route > provider > default。
