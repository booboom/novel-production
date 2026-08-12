from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.event_store import seed_existing_workspace
from novel_production_mcp.models import ModelResult, WorkspacePaths
from novel_production_mcp.quality import (
    validate_delta_coverage,
    validate_draft_contract,
    validate_outline_plan_contract,
    validate_outline_title_contract,
    validate_semantic_quality_audits,
    writer_safe_arc,
    writer_safe_facts,
    writer_safe_profile,
)
from novel_production_mcp.storage import get_workspace, write_json, write_yaml
from novel_production_mcp.validation import validate_workspace


def _rhythm(chapter: int) -> dict[str, str]:
    return {
        "opening_mode": f"opening-{chapter}",
        "core_action": f"action-{chapter}",
        "evidence_type": f"evidence-{chapter}",
        "emotional_turn": f"turn-{chapter}",
        "ending_hook_type": f"hook-{chapter}",
    }


def _valid_historical_audit() -> dict[str, object]:
    return {
        "approved": True,
        "score": 95,
        "abnormal_events": [],
        "orientation_audit": {
            "task_origin": "正文已建立任务",
            "task_object": "当前对象",
            "why_now": "当前触发",
            "stakes": "失败后果",
            "method_reason": "方法有依据",
            "established_before_consequential_action": True,
            "sufficient": True,
            "location": "开篇",
            "severity": "low",
            "fix": "",
        },
        "narrative_timing_audit": {
            "sequence_valid": True,
            "premature_references": [],
            "summary": "时序正常",
        },
        "knowledge_provenance_audit": {"complete": True, "claims": [], "summary": "来源正常"},
        "authorization_audit": {"complete": True, "actions": [], "summary": "无受限操作"},
        "outline_fidelity_audit": {"matches": True, "mismatches": [], "summary": "符合大纲"},
        "reasoning_chain_audit": {"valid": True, "chains": [], "summary": "推理正常"},
        "state_coverage_audit": {"complete": True, "changes": [], "summary": "无状态变化"},
        "repetition_audit": {"distinct": True, "repetitions": [], "summary": "无重复"},
        "planning_language_audit": {"naturalized": True, "occurrences": [], "summary": "规划术语已自然化"},
        "issues": [],
        "summary": "通过",
    }


def test_semantic_audits_fail_closed_on_private_knowledge_authority_and_reasoning() -> None:
    audit = _valid_historical_audit()
    audit["knowledge_provenance_audit"] = {
        "complete": True,
        "claims": [{
            "location": "你姑母参与过改造？",
            "character": "char_chenmo",
            "claim": "林知微姑母参与旧楼改造",
            "source_id": "",
            "sensitive": True,
            "authorized": True,
        }],
        "summary": "存在敏感主张",
    }
    audit["authorization_audit"] = {
        "complete": True,
        "actions": [{"location": "打印门禁导出", "authorized": False}],
        "summary": "权限不足",
    }
    audit["reasoning_chain_audit"] = {
        "valid": False,
        "chains": [{"location": "同样的间隔", "valid": False}],
        "summary": "时间间隔与文字措辞类型不相容",
    }

    codes = {issue.code for issue in validate_semantic_quality_audits(audit)}

    assert {"knowledge_source_missing", "authorization_gap", "reasoning_gap"} <= codes


def test_planning_language_audit_requires_supported_blocking_evidence() -> None:
    audit = _valid_historical_audit()
    audit["planning_language_audit"] = {
        "naturalized": False,
        "occurrences": [{
            "term": "异常证据分层",
            "locations": [],
            "count": 3,
            "blocking": True,
            "confidence": 0.52,
        }],
        "summary": "证据不足",
    }

    issues = validate_semantic_quality_audits(audit)

    assert any(issue.code == "planning_term_literalization" and issue.severity == "medium" for issue in issues)
    assert not any(issue.code == "planning_term_literalization" and issue.severity == "high" for issue in issues)


def test_planning_language_audit_blocks_with_evidence_and_confidence() -> None:
    audit = _valid_historical_audit()
    audit["planning_language_audit"] = {
        "naturalized": False,
        "occurrences": [{
            "term": "异常证据分层",
            "locations": ["普通解释一，异常解释二"],
            "count": 3,
            "blocking": True,
            "confidence": 0.91,
        }],
        "summary": "存在机械复唱",
    }

    issues = validate_semantic_quality_audits(audit)

    assert any(issue.code == "planning_term_literalization" and issue.severity == "high" for issue in issues)


def test_writer_context_withholds_untagged_private_profile_future_arc_and_secret_facts(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    write_yaml(paths.root / "canon" / "facts.yaml", {"facts": [
        {"id": "fact_public", "statement": "公开校规", "visibility": "public"},
        {"id": "fact_known", "statement": "陈默已知线索"},
        {"id": "fact_secret", "statement": "林知微姑母失踪"},
    ]})
    write_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {
        "char_chenmo": {"knows": ["fact_known"], "suspects": [], "must_not_know": ["fact_secret"]},
    }})
    profile = {
        "id": "char_lin",
        "name": "林知微",
        "immutable": {"family_secret": "姑母在旧楼项目中失踪"},
        "personality": {"traits": ["谨慎"]},
        "speech": {"style": "短句"},
    }
    arc = {"current_phase": "调查起点", "future_milestones": ["揭晓姑母身份"]}

    safe_profile = writer_safe_profile(profile)
    safe_facts = writer_safe_facts(paths, ["char_chenmo"])
    safe_arc = writer_safe_arc(arc)

    assert "immutable" not in safe_profile
    assert "personality" in safe_profile
    assert {item["id"] for item in safe_facts["facts"]} == {"fact_public", "fact_known"}
    assert safe_arc == {"current_phase": "调查起点"}


def test_outline_contract_rejects_duplicate_first_introduction_and_repeated_rhythm() -> None:
    chapters = [
        {"number": 1, "narrative_timing": {"introduce_this_chapter": [{"element_id": "method_access"}]}, "rhythm_signature": _rhythm(1)},
        {"number": 2, "narrative_timing": {"introduce_this_chapter": [{"element_id": "method_access"}]}, "rhythm_signature": _rhythm(2)},
    ]
    with pytest.raises(ValueError, match="introduced in both"):
        validate_outline_plan_contract(chapters)

    repeated = [
        {"number": 1, "narrative_timing": {"introduce_this_chapter": []}, "rhythm_signature": _rhythm(1)},
        {"number": 2, "narrative_timing": {"introduce_this_chapter": []}, "rhythm_signature": _rhythm(1)},
    ]
    with pytest.raises(ValueError, match="complete rhythm signature"):
        validate_outline_plan_contract(repeated)


def test_outline_title_contract_allows_shared_mini_arc_with_distinct_titles() -> None:
    chapters = [
        {"number": 1, "title": "旧水房门口的编号", "mini_arc": "失物单"},
        {"number": 2, "title": "登记表上的空白", "mini_arc": "失物单"},
        {"number": 3, "title": "门外的三分钟", "mini_arc": "失物单"},
        {"number": 4, "title": "网页亮了三次", "mini_arc": "失物单"},
        {"number": 5, "title": "没有答案的回执", "mini_arc": "失物单"},
    ]

    validate_outline_title_contract(chapters)


def test_outline_title_contract_rejects_mini_arc_as_repeated_title_template() -> None:
    chapters = [
        {"number": 9, "title": "还没回答的原始视频", "mini_arc": "原始视频"},
        {"number": 10, "title": "只剩一半的原始视频", "mini_arc": "原始视频"},
        {"number": 11, "title": "不肯签字的原始视频", "mini_arc": "原始视频"},
    ]

    with pytest.raises(ValueError, match="reuses mini_arc '原始视频'"):
        validate_outline_title_contract(chapters)


def test_outline_title_contract_rejects_repeated_edge_fragment() -> None:
    chapters = [
        {"number": 1, "title": "失物单被擦掉", "mini_arc": "旧楼核验"},
        {"number": 2, "title": "失物单临时登记", "mini_arc": "旧楼核验"},
        {"number": 3, "title": "失物单走错方向", "mini_arc": "旧楼核验"},
    ]

    with pytest.raises(ValueError, match="title edge fragment '失物单'"):
        validate_outline_title_contract(chapters)


def test_outline_title_contract_only_enforces_new_continuation_chapters() -> None:
    chapters = [
        {"number": 9, "title": "还没回答的原始视频", "mini_arc": "原始视频"},
        {"number": 10, "title": "只剩一半的原始视频", "mini_arc": "原始视频"},
        {"number": 11, "title": "不肯签字的原始视频", "mini_arc": "原始视频"},
        {"number": 12, "title": "门锁背后的停顿", "mini_arc": "原始视频"},
    ]

    validate_outline_plan_contract(chapters, title_enforcement_start=12)


def test_outline_title_contract_rejects_duplicate_reader_title() -> None:
    chapters = [
        {"number": 1, "title": "门外的三分钟", "mini_arc": "失物单"},
        {"number": 2, "title": "门外的三分钟", "mini_arc": "旧楼核验"},
    ]

    with pytest.raises(ValueError, match="repeats the reader-facing title"):
        validate_outline_title_contract(chapters)


def test_delta_coverage_rejects_unmapped_change_and_outline_location_drift(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    write_json(paths.chapter_file(4, "audit"), {
        "state_coverage_audit": {
            "complete": True,
            "changes": [{"change_id": "change_item_transfer", "requires_delta": True}],
            "summary": "铜片发生转移",
        },
    })
    write_yaml(paths.chapter_file(4, "outline"), {"location_ids": ["loc_old_building"]})
    delta = {
        "timeline_events": [{"id": "event_dorm", "location_id": "loc_dorm"}],
        "inventory_events": [],
        "relationship_updates": [],
        "ability_events": [],
        "plot_updates": [],
        "foreshadowing_updates": [],
        "state_coverage": {
            "detected_changes": [{"change_id": "change_item_transfer"}],
            "representations": [],
            "untracked_changes": [],
        },
    }

    codes = {issue.code for issue in validate_delta_coverage(paths, 4, delta)}

    assert "state_change_unrepresented" in codes
    assert "outline_location_mismatch" in codes


def test_delta_coverage_flags_missing_timeline_ids(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    write_json(paths.chapter_file(1, "audit"), {
        "state_coverage_audit": {"complete": True, "changes": [], "summary": ""},
    })
    write_yaml(paths.chapter_file(1, "outline"), {"location_ids": ["loc_dorm", "loc_north"]})
    delta = {
        "timeline_events": [{"summary": "到达宿舍", "location_id": "loc_dorm"}],
        "inventory_events": [],
        "relationship_updates": [],
        "ability_events": [],
        "plot_updates": [],
        "foreshadowing_updates": [],
        "state_coverage": {
            "detected_changes": [],
            "representations": [],
            "untracked_changes": [],
        },
    }
    codes = {issue.code for issue in validate_delta_coverage(paths, 1, delta)}
    assert "timeline_event_id_missing" in codes
    assert "outline_location_unrepresented" in codes



def test_draft_contract_blocks_underlength_and_profile_literalization(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    write_yaml(paths.project, {"quality_policy": {"min_chapter_length_ratio": 0.85}})
    write_yaml(paths.chapter_file(1, "outline"), {
        "target_chars": 100,
        "participants": ["char_chenmo"],
    })
    write_yaml(paths.root / "characters" / "profiles" / "char_chenmo.yaml", {
        "personality": {"pressure_response": "先确认伤者状态再寻找撤退路线"},
    })

    issues = validate_draft_contract(paths, 1, "他总是先确认伤者状态再寻找撤退路线。")
    codes = {issue.code for issue in issues}

    assert "chapter_underlength" in codes
    assert "profile_literalization_overlap" in codes


def test_draft_contract_gate_marks_audit_unapproved(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    write_yaml(paths.project, {"quality_policy": {"min_chapter_length_ratio": 0.85}})
    write_yaml(paths.chapter_file(1, "outline"), {"target_chars": 100, "participants": []})

    audit = core._apply_draft_contract_gate(
        paths,
        1,
        "正文过短。",
        _valid_historical_audit(),
    )

    assert audit["approved"] is False
    assert any(issue.get("type") == "chapter_underlength" for issue in audit["issues"])


def test_workspace_rejects_static_current_holder_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    core.create_workspace("demo", "道具持有人")
    paths = get_workspace("demo")
    write_yaml(paths.root / "state" / "item-ledger.yaml", {
        "items": {"item_mirror": {"name": "铜镜", "current_holder": "char_chenmo"}},
        "balances": {"char_lin": {"item_mirror": 1}},
        "events": [],
    })

    codes = {issue.code for issue in validate_workspace(paths).issues}

    assert "item_static_holder_forbidden" in codes


def test_historical_audit_writes_separate_report_without_touching_chapter_or_active_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    core.create_workspace("demo", "历史审计")
    paths = get_workspace("demo")
    write_yaml(paths.chapter_file(1, "outline"), {"number": 1, "title": "旧章", "participants": []})
    chapter_text = "第1章 旧章\n\n旧正文保持不变。\n"
    paths.chapter_file(1, "chapter").write_text(chapter_text, encoding="utf-8")
    seed_existing_workspace(paths)

    def fake_call(*_args: object, **_kwargs: object) -> ModelResult:
        return ModelResult(
            content=json.dumps(_valid_historical_audit(), ensure_ascii=False),
            provider="mock",
            model="mock",
            route="reviewer",
            latency_ms=1,
        )

    monkeypatch.setattr(core, "_call", fake_call)
    result = core.audit_existing_chapters("demo", 1, 1)

    assert result["read_only"] is True
    assert paths.chapter_file(1, "chapter").read_text(encoding="utf-8") == chapter_text
    assert not paths.chapter_file(1, "audit").exists()
    assert (paths.root / "analysis" / "historical-audits" / "chapter-0001.json").exists()
