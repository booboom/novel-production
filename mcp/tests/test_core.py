from __future__ import annotations

from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.event_store import list_events, projection_status, seed_existing_workspace
from novel_production_mcp.prompt_templates import REPAIR_SYSTEM, REVIEWER_SYSTEM, WRITER_SYSTEM
from novel_production_mcp.retrieval import reindex_workspace, search_memory
from novel_production_mcp.storage import get_workspace, read_json, read_yaml, write_json, write_yaml
from novel_production_mcp.validation import (
    validate_delta,
    validate_narrative_orientation_audit,
    validate_narrative_timing_audit,
    validate_reaction_logic_audit,
)
from novel_production_mcp.writing_doctrine import format_rule_cards, select_writing_rules
from novel_production_mcp.writing_gates import apply_writing_quality_gate, required_analysis_fields


@pytest.fixture()
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    return root


def seed_character_and_outline(workspace_id: str) -> None:
    paths = get_workspace(workspace_id)
    write_yaml(paths.root / "canon" / "facts.yaml", {"facts": [
        {"id": "fact_phone_messages", "statement": "手机会自动收消息"},
        {"id": "fact_secret_x", "statement": "秘密组织的真实名称"},
    ]})
    write_yaml(paths.root / "canon" / "locations.yaml", {
        "locations": [{"id": "loc_dorm", "name": "宿舍"}, {"id": "loc_gate", "name": "校门"}],
        "travel_minutes": {"loc_dorm": {"loc_gate": 10}},
    })
    write_yaml(paths.root / "characters" / "index.yaml", {"characters": [{"id": "char_001", "name": "陈默", "role": "protagonist"}]})
    write_yaml(paths.root / "characters" / "profiles" / "char_001.yaml", {
        "id": "char_001", "name": "陈默", "immutable": {"core_traits": ["警惕"]},
        "speech": {"style": "短句"}, "abilities": {"ability_photography": 4},
    })
    write_yaml(paths.root / "state" / "characters" / "char_001.yaml", {
        "character_id": "char_001",
        "location": {"location_id": "loc_dorm", "place": "宿舍", "time": "2026-01-01T20:00:00+08:00"},
        "knowledge": {"confirmed": [], "suspected": []},
    })
    write_yaml(paths.root / "characters" / "arcs" / "char_001.yaml", {"character_id": "char_001", "stage": "起点"})
    write_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {
        "char_001": {"knows": [], "must_not_know": ["fact_secret_x"]}
    }})
    write_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}, "events": []})
    write_yaml(paths.chapter_file(1, "outline"), {
        "number": 1, "title": "异常手机", "mission": "主角捡到手机", "participants": ["char_001"],
        "must_happen": ["收到陌生消息"], "must_not_happen": ["揭示幕后组织"], "target_chars": 10,
        "pov_character": "char_001", "pov_mode": "third_limited",
    })
    seed_existing_workspace(paths)


def empty_delta() -> dict:
    return {
        "chapter": 1, "character_updates": {}, "utterances": [], "pov_observations": [],
        "relationship_updates": [], "timeline_events": [], "inventory_events": [], "ability_events": [],
        "plot_updates": [], "foreshadowing_updates": [], "canon_change_requests": [],
        "state_coverage": {"detected_changes": [], "representations": [], "untracked_changes": []},
    }


def valid_orientation_audit() -> dict:
    return {
        "task_origin": "辅导员要求陈默核对异常手机",
        "task_object": "异常手机及其消息来源",
        "why_now": "下一条消息将在今晚到达",
        "stakes": "错过后无法确认发送者",
        "method_reason": "检查手机记录可以保存时间证据",
        "established_before_consequential_action": True,
        "sufficient": True,
        "location": "开篇任务说明",
        "severity": "low",
        "fix": "",
    }


def valid_narrative_timing_audit() -> dict:
    return {
        "sequence_valid": True,
        "premature_references": [],
        "summary": "未发现提前引用",
    }


def valid_semantic_audits() -> dict:
    return {
        "knowledge_provenance_audit": {"complete": True, "claims": [], "summary": "无越界知识"},
        "authorization_audit": {"complete": True, "actions": [], "summary": "无受限操作"},
        "outline_fidelity_audit": {"matches": True, "mismatches": [], "summary": "符合大纲"},
        "reasoning_chain_audit": {"valid": True, "chains": [], "summary": "推理成立"},
        "state_coverage_audit": {"complete": True, "changes": [], "summary": "无状态变化"},
        "repetition_audit": {"distinct": True, "repetitions": [], "summary": "无重复"},
        "planning_language_audit": {"naturalized": True, "occurrences": [], "summary": "规划术语已自然化"},
    }


def test_internal_rules_stay_out_of_chapter_prose() -> None:
    assert "只供内部创作使用" in WRITER_SYSTEM
    assert "行为、选择、冲突、拒绝、代价和后果" in WRITER_SYSTEM
    assert "type=rule_leak" in REVIEWER_SYSTEM
    assert "修复 rule_leak" in REPAIR_SYSTEM

    audit = core._apply_rule_leak_gate({
        "approved": True,
        "issues": [{"type": "rule_leak", "severity": "low", "location": "旁白"}],
    })

    assert audit["approved"] is False
    assert audit["issues"][0]["severity"] == "high"


def test_writer_rule_rendering_hides_rule_metadata() -> None:
    rendered = format_rule_cards([{
        "id": "CAUSALITY-001",
        "name": "因果成立",
        "severity": "critical",
        "blocking": True,
        "domains": ["causality"],
        "description": "内部说明",
        "writer_instruction": "让关键行动具有可观察的动机和后果。",
        "criteria": ["行动前有动机", "行动后有后果"],
        "repair_instruction": "补足因果",
    }], stage="writer")

    assert "CAUSALITY-001" not in rendered
    assert "因果成立" not in rendered
    assert "critical" not in rendered
    assert "blocking" not in rendered
    assert "让关键行动具有可观察的动机和后果" in rendered


def test_character_profile_is_not_literalized_into_scene_props() -> None:
    assert "人物资料只用于约束跨场景行为倾向" in WRITER_SYSTEM
    assert "角色档案不得字面化" not in WRITER_SYSTEM
    assert "type=profile_literalization" in REVIEWER_SYSTEM
    assert "修复 profile_literalization" in REPAIR_SYSTEM

    audit = core._apply_profile_literalization_gate({
        "approved": True,
        "issues": [{
            "type": "profile_literalization",
            "severity": "medium",
            "location": "陈默突然掏出记事本列清单",
        }],
    })

    assert audit["approved"] is False
    assert audit["issues"][0]["severity"] == "high"


def test_named_character_requires_an_entry_bridge() -> None:
    assert "有名有姓的人物首次出现" in WRITER_SYSTEM
    assert "type=character_entry" in REVIEWER_SYSTEM
    assert "修复 character_entry" in REPAIR_SYSTEM

    audit = core._apply_character_entry_gate({
        "approved": True,
        "issues": [{
            "type": "character_entry",
            "severity": "medium",
            "location": "这时，林雪突然走了进来",
        }],
    })

    assert audit["approved"] is False
    assert audit["issues"][0]["severity"] == "high"


def test_reader_task_orientation_is_a_blocking_gate() -> None:
    assert "足以理解当前任务的必要因果" in WRITER_SYSTEM
    assert "最小因果闭环" not in WRITER_SYSTEM
    assert "orientation_audit" in REVIEWER_SYSTEM
    assert "修复 narrative_orientation" in REPAIR_SYSTEM

    audit = {
        "approved": True,
        "orientation_audit": {
            "task_origin": "人物正在查监控，但正文未说明任务来源",
            "task_object": "监控记录",
            "why_now": "正文未说明",
            "stakes": "正文未说明",
            "method_reason": "正文未说明为什么监控能解决问题",
            "established_before_consequential_action": False,
            "sufficient": False,
            "location": "开篇直接查看监控",
            "severity": "medium",
            "fix": "在查监控前补足任务来源、时限、代价和方法理由",
        },
        "issues": [],
    }

    gate_issues = validate_narrative_orientation_audit(audit)
    normalized = core._apply_narrative_orientation_gate(audit)

    assert [issue.code for issue in gate_issues] == ["narrative_orientation"]
    assert gate_issues[0].severity == "high"
    assert normalized["approved"] is False
    assert normalized["issues"][0]["type"] == "narrative_orientation"


def test_reader_task_orientation_audit_is_required() -> None:
    issues = validate_narrative_orientation_audit({"approved": True, "issues": []})

    assert [issue.code for issue in issues] == ["orientation_audit_missing"]
    assert issues[0].severity == "critical"


def test_future_plan_leak_is_a_blocking_gate() -> None:
    assert "信息在故事中的出现顺序" in WRITER_SYSTEM
    assert "narrative_timing" not in WRITER_SYSTEM
    assert "narrative_timing_audit" in REVIEWER_SYSTEM
    assert "修复 future_plan_leak" in REPAIR_SYSTEM
    audit = {
        "approved": True,
        "narrative_timing_audit": {
            "sequence_valid": False,
            "premature_references": [{
                "location": "回头再和门禁、监控记录对照",
                "element": "门禁与监控记录调查",
                "earliest_allowed_chapter": 2,
                "reason": "第1章尚未建立这种调查手段",
                "severity": "medium",
                "fix": "改为重新开箱核对铜镜来源",
            }],
            "summary": "存在提前引用",
        },
        "issues": [],
    }

    gate_issues = validate_narrative_timing_audit(audit)
    normalized = core._apply_narrative_timing_gate(audit)

    assert [issue.code for issue in gate_issues] == ["future_plan_leak"]
    assert gate_issues[0].severity == "high"
    assert normalized["approved"] is False
    assert normalized["issues"][0]["type"] == "future_plan_leak"


def test_generated_chapter_title_drops_arc_prefix_and_template_index(
    workspace_env: Path,
) -> None:
    core.create_workspace("title-demo", "标题测试")
    paths = get_workspace("title-demo")
    write_yaml(paths.chapter_file(1, "outline"), {
        "number": 1,
        "title": "失物登记·补交失物表1",
    })

    rendered = core._chapter_body_with_title(paths, 1, "正文。")

    assert rendered == "第1章 补交失物表\n\n正文。\n"


def test_create_and_packet(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", premise="普通学生卷入异常事件")
    seed_character_and_outline("demo")
    paths = get_workspace("demo")
    assert not paths.routes.exists()
    write_yaml(paths.chapter_file(2, "outline"), {
        "number": 2,
        "title": "核对门禁记录",
        "chapter_task": "调取门禁和监控记录",
        "mission": "确认谁在断电期间接近旧楼",
    })
    result = core.build_chapter_packet("demo", 1)
    packet = Path(result["packet"]).read_text(encoding="utf-8")
    assert "陈默" in packet
    assert "must_not_happen" in packet
    assert "本章可用事实 ID" in packet
    assert "地点与旅行时间" in packet
    assert "调取门禁和监控记录" not in packet

    timing_context = core.build_narrative_timing_review_context(paths, 1)
    review_prompt = core._review_prompt(packet, "待审正文", timing_context)
    assert "调取门禁和监控记录" in review_prompt


def test_delta_rejects_semantic_violations(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    seed_character_and_outline("demo")
    paths = get_workspace("demo")
    delta = empty_delta()
    delta.update({
        "character_updates": {"char_001": {"immutable": {"core_traits": ["健谈"]}, "abilities_add": ["ability_sword"]}},
        "utterances": [{"speaker": "char_001", "text_excerpt": "秘密组织", "asserted_fact_ids": ["fact_secret_x"], "event_ids": ["event_ch001_001"]}],
        "relationship_updates": [{"source": "char_001", "target": "char_001", "trust_delta": 50, "event_ids": []}],
    })
    report = validate_delta(paths, 1, delta, "陈默说出了秘密组织。")
    codes = {issue.code for issue in report.issues}
    assert not report.ok
    assert "immutable_mutation" in codes
    assert "ability_added_without_approval" in codes
    assert "relationship_jump" in codes
    assert "utterance_knowledge_violation" in codes


def test_reaction_logic_audit_blocks_unjustified_abnormal_response() -> None:
    audit = {
        "approved": True,
        "score": 95,
        "abnormal_events": [{
            "location": "手机里传来陌生声音",
            "stimulus": "来源不明的声音主动与主角交谈",
            "character": "char_001",
            "prior_basis": "",
            "actual_response": "主角没有确认来源便自然交谈",
            "reaction_bridge": "",
            "plausible": False,
            "severity": "medium",
            "fix": "补入主角的警惕、确认或有依据的克制反应",
        }],
        "issues": [],
    }

    gate_issues = validate_reaction_logic_audit(audit)
    normalized = core._apply_reaction_logic_gate(audit)

    assert [issue.code for issue in gate_issues] == ["reaction_logic"]
    assert gate_issues[0].severity == "high"
    assert normalized["approved"] is False
    assert normalized["issues"][0]["type"] == "reaction_logic"
    assert len(core._apply_reaction_logic_gate(normalized)["issues"]) == 1


def test_reaction_logic_audit_requires_explicit_empty_list() -> None:
    gate_issues = validate_reaction_logic_audit({"approved": True, "score": 95, "issues": []})

    assert [issue.code for issue in gate_issues] == ["reaction_audit_missing"]
    assert gate_issues[0].severity == "critical"


def test_agent_resume_restarts_review_for_legacy_audit(
    workspace_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core.create_workspace("demo", "测试小说")
    paths = get_workspace("demo")
    paths.chapter_file(1, "packet").write_text("旧章节包\n", encoding="utf-8")
    paths.chapter_file(1, "draft").write_text("旧草稿\n", encoding="utf-8")
    write_json(paths.chapter_file(1, "audit"), {"approved": True, "score": 95, "issues": []})
    restarted: list[tuple[str, int]] = []

    def restart_review(workspace_id: str, chapter_number: int) -> dict:
        restarted.append((workspace_id, chapter_number))
        raise RuntimeError("review restarted")

    monkeypatch.setattr(core, "review_chapter", restart_review)

    with pytest.raises(RuntimeError, match="review restarted"):
        core._run_chapter_pipeline_resumed("demo", 1)

    normalized = read_json(paths.chapter_file(1, "audit"), {})
    assert restarted == [("demo", 1)]
    assert normalized["approved"] is False
    assert normalized["issues"][0]["type"] == "reaction_audit_missing"


def test_commit_rechecks_reaction_logic_gate(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    seed_character_and_outline("demo")
    paths = get_workspace("demo")
    core.build_chapter_packet("demo", 1)
    paths.chapter_file(1, "draft").write_text("手机里传来陌生声音，陈默立刻和它聊了起来。\n", encoding="utf-8")
    write_json(paths.chapter_file(1, "audit"), {
        "approved": True,
        "score": 95,
        "abnormal_events": [{
            "location": "陈默立刻和它聊了起来",
            "stimulus": "来源不明的声音主动与主角交谈",
            "character": "char_001",
            "prior_basis": "",
            "actual_response": "直接交谈",
            "reaction_bridge": "",
            "plausible": False,
            "severity": "low",
            "fix": "补入警惕、确认或有依据的克制反应",
        }],
        "issues": [],
    })
    write_json(paths.chapter_file(1, "delta"), empty_delta())

    result = core.commit_chapter("demo", 1)
    codes = {issue["code"] for issue in result["validation"]["issues"]}

    assert result["committed"] is False
    assert "reaction_logic" in codes
    assert not paths.chapter_file(1, "chapter").exists()


def test_commit_applies_state_transactionally(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    seed_character_and_outline("demo")
    paths = get_workspace("demo")
    core.build_chapter_packet("demo", 1)
    paths.chapter_file(1, "draft").write_text("陈默捡到一部异常手机。\n", encoding="utf-8")
    writing_rules = select_writing_rules(paths, 1, read_yaml(paths.chapter_file(1, "outline"), {}), stage="reviewer")
    audit = {
        "approved": True,
        "score": 92,
        "abnormal_events": [],
        "orientation_audit": valid_orientation_audit(),
        "narrative_timing_audit": valid_narrative_timing_audit(),
        **valid_semantic_audits(),
        "writing_quality": {
            "score": 92,
            "summary": "测试章节通过文学门禁",
            "gates": [{
                "gate_id": rule["id"],
                "rule_id": rule["id"],
                "passed": True,
                "severity": "low",
                "blocking": False,
                "confidence": 0.95,
                "evidence": ["陈默捡到一部异常手机。"],
                "reason": "本测试只验证事务提交，正文事件形成明确状态变化。",
                "repair_instruction": "",
                "analysis": {name: "测试证据" for name in required_analysis_fields(rule)},
            } for rule in writing_rules],
        },
        "issues": [],
    }
    write_json(paths.chapter_file(1, "audit"), audit)
    apply_writing_quality_gate(paths, 1, audit, writing_rules)
    write_json(paths.chapter_file(1, "audit"), audit)
    delta = empty_delta()
    delta.update({
        "character_updates": {"char_001": {
            "location": {"location_id": "loc_gate", "place": "校门", "time": "2026-01-01T20:10:00+08:00"},
            "knowledge_add": ["fact_phone_messages"],
            "knowledge_evidence": {"fact_phone_messages": ["event_ch001_001"]},
        }},
        "timeline_events": [{
            "id": "event_ch001_001", "time": "2026-01-01T20:10:00+08:00", "location_id": "loc_gate",
            "participants": ["char_001"], "summary": "捡到手机", "fact_ids": ["fact_phone_messages"],
        }],
        "state_coverage": {
            "detected_changes": [{"change_id": "change_ch001_location"}],
            "representations": [{
                "change_id": "change_ch001_location",
                "delta_section": "timeline_events",
                "delta_id": "event_ch001_001",
            }],
            "untracked_changes": [],
        },
    })
    write_json(paths.chapter_file(1, "delta"), delta)
    result = core.commit_chapter("demo", 1)
    assert result["committed"] is True
    state = read_yaml(paths.root / "state" / "characters" / "char_001.yaml", {})
    assert state["location"]["place"] == "校门"
    assert "fact_phone_messages" in state["knowledge"]["confirmed"]
    assert paths.chapter_file(1, "chapter").exists()
    assert paths.chapter_file(1, "chapter").read_text(encoding="utf-8").startswith("第1章 异常手机\n\n")
    assert any(event["event_type"] == "chapter.committed" for event in list_events(paths))
    assert projection_status(paths)["ok"] is True


def test_export_current_novel_merges_committed_chapters(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    paths = get_workspace("demo")
    write_yaml(paths.chapter_file(1, "outline"), {"number": 1, "title": "开端"})
    write_yaml(paths.chapter_file(2, "outline"), {"number": 2, "title": "回声"})
    paths.chapter_file(1, "chapter").write_text("第1章 开端\n------------------------------------------------\n第一章正文。\n", encoding="utf-8")
    paths.chapter_file(2, "chapter").write_text("第二章正文。\n", encoding="utf-8")

    result = core.export_current_novel("demo")

    exported = Path(result["output_path"])
    content = exported.read_text(encoding="utf-8")
    assert result["format"] == "txt"
    assert result["chapter_count"] == 2
    assert content.count("第1章 开端") == 1
    assert content.count("第2章 回声") == 1
    assert content.index("第一章正文") < content.index("第二章正文")


def test_rebuild_projection_and_retrieval(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    paths = get_workspace("demo")
    # Commit a standalone projection event through the public chapter path is covered above; seed and reindex here.
    chapter = paths.root / "chapters" / "chapter-0001.md"
    chapter.write_text("雨夜里，异常手机再次响起。", encoding="utf-8")
    reindex_workspace(paths)
    results = search_memory(paths, "异常手机 雨夜", top_k=3)
    assert results
    assert "异常手机" in results[0]["text"]


def test_migrate_v1_workspace(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说")
    paths = get_workspace("demo")
    project = read_yaml(paths.project, {})
    project["schema_version"] = 1
    write_yaml(paths.project, project)
    paths.database.unlink(missing_ok=True)
    result = core.migrate_workspace("demo")
    assert result["migrated"] is True
    assert read_yaml(paths.project, {})["schema_version"] == 3
    assert paths.database.exists()
    assert (paths.root / "canon" / "locations.yaml").exists()
