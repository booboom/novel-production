from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.storage import get_workspace, read_json, write_json
from novel_production_mcp.writing_doctrine import (
    apply_reference_rules,
    load_writing_rules,
    rebuild_writing_rule_index,
    select_writing_rules,
    set_rule_enabled,
    validate_rule_card,
    writing_doctrine_root,
)
from novel_production_mcp.writing_gates import (
    apply_writing_quality_gate,
    run_writing_gate_evals,
    targeted_repair_brief,
    validate_writing_report_for_commit,
    validate_writing_review,
)
from novel_production_mcp.writing_reference import (
    import_writing_reference,
    reference_batches,
    scan_reference_library,
    validate_doctrine_proposal,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    core.create_workspace("writing-demo", "写作门禁", "悬疑", "测试", 20)
    return get_workspace("writing-demo")


def _review(rule_ids: list[str], *, failed: str = "") -> dict[str, object]:
    gates = []
    for rule_id in rule_ids:
        is_failed = rule_id == failed
        gates.append({
            "gate_id": rule_id,
            "rule_id": rule_id,
            "passed": not is_failed,
            "severity": "high" if is_failed else "low",
            "blocking": is_failed,
            "confidence": 0.95,
            "evidence": ["正文中的明确证据。"],
            "reason": "存在具体叙事依据。" if not is_failed else "证据显示该门禁失败。",
            "repair_instruction": "补足最小因果桥梁。" if is_failed else "",
            "analysis": {
                "character": "char_001",
                "goal": "完成当前任务",
                "obstacle": "信息不足",
                "action": "采取可观察行动",
                "consequence": "状态发生变化",
                "start_state": "安静",
                "end_state": "变化后",
                "changed_dimension": "knowledge",
                "cause": "触发事件",
                "motivation": "判断异常",
                "next_pressure": "确认来源",
                "current_goal": "完成当前任务",
                "motive": "规避风险",
                "timing_reason": "时机已到",
                "action_basis": "可观察线索",
                "chapter_change": "出现异常",
                "downstream_dependency": "后续调查",
                "speaker_goal": "试探",
                "listener_goal": "隐瞒",
                "resistance_or_strategy": "回避",
                "emotion_claim": "紧张",
                "observable_evidence": "手指发白",
                "event_density": "中",
                "function_density": "中",
                "static_span": "无",
                "distribution": "均匀",
                "repeated_information": "无",
                "prior_source": "本章",
                "missing_or_present_novelty": "有新信息",
                "paragraph": "正文段落",
                "attempted_function": "推进",
                "why_no_function": "无",
                "conflict_setup": "对立已建立",
                "resolution_basis": "付出代价",
                "cost_or_pressure": "资源损耗",
                "active_goal": "本章任务",
                "state_change": "状态变化",
                "ending_pressure": "尾钩",
                "speaker_samples": "样本",
                "distinction": "声口差异",
            },
        })
    return {"approved": not failed, "writing_quality": {"score": 60 if failed else 95, "summary": "测试", "gates": gates}, "issues": []}


def test_rule_schema_and_layered_dynamic_selection(workspace) -> None:
    rules = load_writing_rules(workspace)
    ids = {rule["id"] for rule in rules}
    assert {"SCENE-001", "SCENE-002", "CAUSALITY-001", "CHARACTER-001", "ANTI-FLUFF-001", "NOVELTY-001", "DIALOGUE-001", "CONFLICT-001", "PACING-001", "CHAPTER-001"} <= ids

    outline = {"mission": "双方谈判核对证据", "participants": ["char_a", "char_b"], "scene_types": ["negotiation"]}
    selected = select_writing_rules(workspace, 1, outline, stage="writer")
    selected_ids = {rule["id"] for rule in selected}
    assert "DIALOGUE-001" in selected_ids
    assert "CONFLICT-001" in selected_ids
    assert len(selected) <= 30

    set_rule_enabled(workspace, "DIALOGUE-001", False)
    assert "DIALOGUE-001" not in {rule["id"] for rule in load_writing_rules(workspace) if rule["enabled"]}


def test_rule_schema_rejects_incomplete_rule() -> None:
    with pytest.raises(ValueError, match="missing fields"):
        validate_rule_card({"id": "BROKEN"})


def test_writing_gate_requires_specific_evidence(workspace) -> None:
    rules = [{"id": "CAUSALITY-001", "severity": "high", "blocking": True}]
    review = {"writing_quality": {"score": 70, "gates": [{"gate_id": "CAUSALITY-001", "passed": False, "severity": "high", "evidence": [], "reason": "", "repair_instruction": ""}]}}
    codes = {issue.code for issue in validate_writing_review(review, rules, {"min_score": 85})}
    assert {"writing_gate_evidence_missing", "writing_gate_reason_missing", "writing_gate_repair_missing"} <= codes


def test_format_rule_cards_exposes_analysis_schema_for_reviewer() -> None:
    from novel_production_mcp.writing_doctrine import format_rule_cards

    reviewer = format_rule_cards([{
        "id": "SCENE-001",
        "name": "场景目标",
        "severity": "high",
        "blocking": True,
        "domains": ["scene"],
        "description": "场景要有目标",
        "writer_instruction": "给角色一个可观察目标",
        "criteria": ["目标明确"],
        "evidence_required": ["changed_dimension"],
        "repair_instruction": "补目标",
    }], stage="reviewer")
    assert "SCENE-001" in reviewer
    assert "analysis 必填字段" in reviewer
    assert "character, goal, obstacle, action, consequence" in reviewer
    assert "changed_dimension" in reviewer
    assert "analysis_schema" in reviewer

    writer = format_rule_cards([{
        "id": "SCENE-001",
        "name": "场景目标",
        "severity": "high",
        "writer_instruction": "给角色一个可观察目标",
        "criteria": ["目标明确"],
    }], stage="writer")
    assert "SCENE-001" not in writer
    assert "给角色一个可观察目标" in writer


def test_analysis_incomplete_is_critical_and_blocks_commit(workspace) -> None:
    rules = [{"id": "CAUSALITY-001", "severity": "high", "blocking": True, "evidence_required": ["motivation"]}]
    review = {
        "writing_quality": {
            "score": 90,
            "gates": [{
                "gate_id": "CAUSALITY-001",
                "rule_id": "CAUSALITY-001",
                "passed": True,
                "severity": "low",
                "blocking": False,
                "confidence": 0.9,
                "evidence": ["他推开门。"],
                "reason": "有动作",
                "repair_instruction": "",
                "analysis": {"character": "陈默", "goal": "离开"},
            }],
        },
    }
    issues = validate_writing_review(review, rules, {"min_score": 85})
    incomplete = next(issue for issue in issues if issue.code == "writing_gate_analysis_incomplete")
    assert incomplete.severity == "critical"
    assert "obstacle" in incomplete.details["missing"]
    assert "motivation" in incomplete.details["missing"]

    audit = apply_writing_quality_gate(workspace, 3, {**review, "approved": True, "issues": []}, rules)
    assert audit["approved"] is False
    assert audit["writing_quality_status"] == "blocked"



def test_blocking_gate_writes_json_and_markdown_report(workspace) -> None:
    rules = [{"id": "CAUSALITY-001", "domain": "causality", "severity": "high", "blocking": True}]
    audit = apply_writing_quality_gate(workspace, 1, _review(["CAUSALITY-001"], failed="CAUSALITY-001"), rules)
    assert audit["approved"] is False
    report = read_json(workspace.root / "analysis" / "writing-quality" / "chapter-0001.json", {})
    assert report["causality_flags"][0]["gate_id"] == "CAUSALITY-001"
    assert (workspace.root / "analysis" / "writing-quality" / "chapter-0001.md").exists()
    assert validate_writing_report_for_commit(workspace, 1).ok is False
    brief = targeted_repair_brief(audit)
    assert brief["failed_gate_ids"] == ["CAUSALITY-001"]


def test_score_alone_does_not_block_commit(workspace) -> None:
    rules = [{**next(rule for rule in load_writing_rules(workspace) if rule["id"] == "PROSE-001"), "blocking": False}]
    audit = _review(["PROSE-001"])
    audit["writing_quality"]["score"] = 82
    audit = apply_writing_quality_gate(workspace, 1, audit, rules)
    write_json(workspace.chapter_file(1, "audit"), audit)
    assert audit["approved"] is True
    assert audit["writing_quality_status"] == "approved_with_warnings"
    assert validate_writing_report_for_commit(workspace, 1).ok is True


def test_insufficient_evidence_downgrades_blocking_high_to_warning(workspace) -> None:
    rules = [{"id": "CAUSALITY-001", "domain": "causality", "severity": "high", "blocking": True}]
    audit = _review(["CAUSALITY-001"], failed="CAUSALITY-001")
    gate = audit["writing_quality"]["gates"][0]
    gate["confidence"] = 0.52
    gate["evidence"] = []
    audit = apply_writing_quality_gate(workspace, 1, audit, rules)
    assert audit["approved"] is True
    assert audit["writing_quality_status"] == "approved_with_warnings"
    report = read_json(workspace.root / "analysis" / "writing-quality" / "chapter-0001.json", {})
    assert report["failed_gates"][0]["blocks_commit"] is False


def test_medium_and_low_never_block_or_aggregate(workspace) -> None:
    rules = [
        {"id": f"LOW-{index}", "domain": "prose", "severity": "low", "blocking": True}
        for index in range(10)
    ] + [
        {"id": f"MEDIUM-{index}", "domain": "dialogue", "severity": "medium", "blocking": True}
        for index in range(5)
    ]
    audit = _review([rule["id"] for rule in rules])
    for gate in audit["writing_quality"]["gates"]:
        gate.update({"passed": False, "severity": next(rule["severity"] for rule in rules if rule["id"] == gate["gate_id"]), "blocking": True, "confidence": 0.99, "repair_instruction": "定点优化。"})
    audit["approved"] = False
    audit = apply_writing_quality_gate(workspace, 1, audit, rules)
    assert audit["approved"] is True
    assert audit["writing_quality_status"] == "approved_with_warnings"


def test_repair_limit_blocks_critical_but_allows_unresolved_high_by_default(workspace) -> None:
    high_rule = [{"id": "HIGH-001", "domain": "causality", "severity": "high", "blocking": True}]
    high = _review(["HIGH-001"], failed="HIGH-001")
    high = apply_writing_quality_gate(workspace, 1, high, high_rule, repair_rounds=2)
    assert high["approved"] is True
    assert high["writing_quality_status"] == "approved_with_warnings"

    critical_rule = [{"id": "CRITICAL-001", "domain": "causality", "severity": "critical", "blocking": True}]
    critical = _review(["CRITICAL-001"], failed="CRITICAL-001")
    critical["writing_quality"]["gates"][0]["severity"] = "critical"
    critical = apply_writing_quality_gate(workspace, 2, critical, critical_rule, repair_rounds=2)
    assert critical["approved"] is False
    assert critical["writing_quality_status"] == "blocked"


def test_good_and_bad_writing_fixtures_include_false_positive_protection() -> None:
    result = run_writing_gate_evals()
    assert result["passed"] is True
    assert result["total"] == 11
    names = {item["name"] for item in result["results"]}
    assert {"valid_scene", "valid_dialogue", "valid_slow_scene"} <= names


def test_import_markdown_reference_and_validate_proposal(workspace, tmp_path: Path) -> None:
    source = tmp_path / "method.md"
    source.write_text("# 场景\n\n人物要追求具体结果。\n\n# 对话\n\n双方目标应有差异。\n", encoding="utf-8")
    imported = import_writing_reference(workspace, str(source), "方法测试")
    reference_root = Path(imported["path"])
    copied = next(reference_root.glob("source.*"))
    assert not copied.stat().st_mode & stat.S_IWUSR
    assert len(reference_batches(workspace, imported["reference_id"])) == 1

    base_rule = load_writing_rules(workspace)[0]
    proposal = validate_doctrine_proposal({"rules": [{**base_rule, "id": "REF-001"}], "conflicts": []})
    assert proposal["rules"][0]["source_refs"]


def test_global_writing_quality_toml_can_disable_profile(workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "global-config"
    config.mkdir()
    (config / "routes.toml").write_text('[writing_quality]\nenabled = false\nprofile = ""\n', encoding="utf-8")
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    assert select_writing_rules(workspace, 1, {"participants": ["a"]}, stage="writer") == []


def test_multitag_reference_index_and_precise_battle_selection(workspace) -> None:
    base = next(rule for rule in load_writing_rules(workspace) if rule["id"] == "CAUSALITY-001")
    battle = {
        **base,
        "id": "BATTLE-CAUSALITY-001",
        "description": "战斗中的关键动作必须改变位置、资源或风险。",
        "domain": "battle",
        "domains": ["battle", "causality", "character"],
        "scene_types": ["battle", "chase"],
        "techniques": ["pacing", "tension"],
        "genres": ["xianxia", "fantasy"],
        "source_refs": [{"reference_id": "BOOK-001", "section": "combat"}],
    }
    romance = {
        **base,
        "id": "ROMANCE-001",
        "description": "感情场景中的选择必须改变信任或关系距离。",
        "domain": "romance",
        "domains": ["romance", "character"],
        "scene_types": ["romance"],
        "techniques": ["emotional_payoff"],
        "genres": ["romance"],
        "source_refs": [{"reference_id": "BOOK-002", "section": "romance"}],
    }
    applied = apply_reference_rules([battle, romance])
    assert applied["rule_count"] == 2
    index = rebuild_writing_rule_index()
    assert index["by_scene_type"]["battle"] == ["BATTLE-CAUSALITY-001"]
    assert index["by_domain"]["character"] == ["BATTLE-CAUSALITY-001", "ROMANCE-001"]
    (writing_doctrine_root() / "index.yaml").unlink()
    assert {"BATTLE-CAUSALITY-001", "ROMANCE-001"} <= {
        rule["id"] for rule in load_writing_rules(workspace)
    }

    selected = select_writing_rules(
        workspace,
        1,
        {"dominant_scene": "battle", "scene_types": ["battle"], "techniques": ["tension"], "genres": ["xianxia"]},
    )
    ids = {rule["id"] for rule in selected}
    assert "BATTLE-CAUSALITY-001" in ids
    assert "ROMANCE-001" not in ids


def test_incremental_reference_scan_skips_unchanged_and_reanalyzes_modified(workspace, tmp_path: Path) -> None:
    library = tmp_path / "books"
    library.mkdir()
    source = library / "method.md"
    source.write_text("# 场景\n人物必须有即时目标。\n", encoding="utf-8")
    first = scan_reference_library(workspace, str(library))
    assert len(first["new"]) == 1
    second = scan_reference_library(workspace, str(library))
    assert second["new"] == []
    assert second["unchanged"] == first["new"]
    source.write_text("# 场景\n人物必须有即时目标，并产生状态变化。\n", encoding="utf-8")
    third = scan_reference_library(workspace, str(library))
    assert len(third["modified"]) == 1
    assert third["modified"] != first["new"]
