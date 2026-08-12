from __future__ import annotations

from novel_production_mcp.planning_terminology import (
    detect_planning_term_literalization,
    load_planning_terminology,
    reader_visible_terms,
    repairer_stage_context,
    reviewer_stage_context,
    sanitize_writer_value,
    writer_stage_guidance,
)
from novel_production_mcp.prompt_templates import WRITER_SYSTEM


def test_writer_rendering_naturalizes_legacy_planning_labels() -> None:
    source = {
        "chapter_task": "把普通解释和异常解释分开记录，再执行异常先验证后接受",
        "reader_orientation": {"task_origin": "辅导员通知"},
    }

    rendered = sanitize_writer_value(source)

    text = rendered["chapter_task"]
    assert "普通解释" not in text
    assert "异常解释" not in text
    assert "异常先验证后接受" not in text
    assert "具体可能性" in text
    assert "核对可验证证据" in text
    assert "reader_orientation" not in rendered


def test_writer_guidance_hides_names_aliases_and_rule_ids() -> None:
    source = {"chapter_task": "把普通解释和异常解释分开记录"}

    guidance = "\n".join(writer_stage_guidance(source))

    assert "异常证据分层" not in guidance
    assert "普通解释" not in guidance
    assert "异常解释" not in guidance
    assert "rule_id" not in guidance


def test_writer_system_does_not_expose_catalog_aliases() -> None:
    aliases = {
        alias
        for term in load_planning_terminology().values()
        for alias in term["aliases"]
    }

    guidance = "\n".join(writer_stage_guidance({}))

    assert not sorted(alias for alias in aliases if alias in WRITER_SYSTEM)
    assert not sorted(alias for alias in aliases if alias in guidance)


def test_reviewer_and_repairer_receive_full_terminology_context() -> None:
    source = {"chapter_task": "把普通解释和异常解释分开记录"}

    reviewer = reviewer_stage_context(source)
    repairer = repairer_stage_context(source)

    assert any("普通解释" in term["aliases"] for term in reviewer["terms"])
    assert any(term["name"] == "异常证据分层" for term in repairer["terms"])


def test_contextual_term_warns_then_blocks_only_after_repetition() -> None:
    source = {"chapter_task": "把普通解释和异常解释分开记录"}

    assert detect_planning_term_literalization("他先给出一个普通解释。", source) == []
    warning = detect_planning_term_literalization("先写普通解释，再补普通解释。", source)
    blocking = detect_planning_term_literalization("普通解释、异常解释，最后又回到普通解释。", source)

    assert [issue.severity for issue in warning] == ["medium"]
    assert [issue.severity for issue in blocking] == ["high"]
    assert blocking[0].details["evidence"]


def test_internal_term_is_blocking_on_first_literal_occurrence() -> None:
    issues = detect_planning_term_literalization("这里需要补一段反应桥梁。", {})

    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].details["occurrences"] == {"反应桥梁": 1}


def test_reader_visible_terms_explicitly_allow_world_term() -> None:
    outline = {"reader_visible_terms": ["普通解释"], "chapter_task": "记录普通解释"}
    established = reader_visible_terms(
        outline,
        {"facts": [{"statement": "校内正式使用‘普通解释’作为记录栏名称"}]},
        require_established=True,
    )

    assert reader_visible_terms(outline) == {"普通解释"}
    assert established == {"普通解释"}
    assert reader_visible_terms(outline, {}, require_established=True) == set()
    assert sanitize_writer_value("记录普通解释", allowed_terms=established) == "记录普通解释"
    assert detect_planning_term_literalization(
        "普通解释、普通解释、普通解释。",
        outline,
        allowed_terms=established,
    ) == []
