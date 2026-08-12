from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from novel_production_mcp import core
from novel_production_mcp.event_store import commit_projection_event
from novel_production_mcp.models import ModelResult
from novel_production_mcp.storage import get_workspace, read_yaml, write_yaml
from novel_production_mcp.validation import validate_delta


def _reader_orientation() -> dict[str, str]:
    return {
        "task_origin": "上一章留下未完成主线",
        "task_object": "当前主线目标",
        "why_now": "本章必须继续推进",
        "stakes": "延误会破坏章节预算",
        "method_reason": "按既定计划处理目标",
        "establish_before": "第一次主线行动之前",
    }


def _narrative_timing(chapter: int) -> dict[str, object]:
    return {
        "established_before_chapter": [],
        "introduce_this_chapter": [{
            "element_id": f"element_chapter_{chapter:04d}",
            "description": "本章首次推进的主线信息",
            "establish_before": "第一次使用该信息之前",
            "allow_early_hint": False,
        }],
    }


def _rhythm_signature(chapter: int) -> dict[str, str]:
    return {
        "opening_mode": f"承接-{chapter}",
        "core_action": f"推进-{chapter}",
        "evidence_type": f"证据-{chapter}",
        "emotional_turn": f"转折-{chapter}",
        "ending_hook_type": f"钩子-{chapter}",
    }


def _distinct_title(chapter: int) -> str:
    return f"{chr(0x4E00 + chapter)}灯照见{chr(0x5000 + chapter)}墙"


@pytest.fixture()
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir(); config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    return root


def _empty_delta(chapter: int) -> dict:
    return {
        "chapter": chapter,
        "character_updates": {},
        "utterances": [],
        "pov_observations": [],
        "relationship_updates": [],
        "timeline_events": [],
        "inventory_events": [],
        "ability_events": [],
        "plot_updates": [],
        "foreshadowing_updates": [],
        "ending_outcome_updates": [],
        "canon_change_requests": [],
        "state_coverage": {"detected_changes": [], "representations": [], "untracked_changes": []},
    }


def test_create_initializes_v3_ending_and_story_budget(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=100)
    paths = get_workspace("demo")
    project = read_yaml(paths.project, {})
    ending = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    budget = read_yaml(paths.root / "planning" / "story-budget.yaml", {})
    assert project["schema_version"] == 3
    assert project["planned_chapters"] == 100
    assert ending["target"] == {
        "mode": "fixed", "min_chapter": 100, "ideal_chapter": 100, "max_chapter": 100, "source": "workspace_create"
    }
    assert budget["stages"][0]["chapter_range"][0] == 1
    assert budget["stages"][-1]["chapter_range"][1] == 100
    # Budget stages are contiguous.
    expected = 1
    for stage in budget["stages"]:
        start, end = stage["chapter_range"]
        assert start == expected
        assert end >= start
        expected = end + 1
    assert expected == 101


def test_set_range_and_hard_validate_volume_and_outline_coverage(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=10)
    result = core.set_ending_target("demo", ideal_chapter=12, min_chapter=10, max_chapter=14)
    paths = get_workspace("demo")
    assert result["target"]["mode"] == "range"
    assert read_yaml(paths.project, {})["planned_chapters"] == 12

    good_volumes = yaml.safe_dump({"volumes": [
        {"order": 1, "title": "一", "chapter_range": [1, 4]},
        {"order": 2, "title": "二", "chapter_range": [5, 8]},
        {"order": 3, "title": "三", "chapter_range": [9, 12]},
    ]}, allow_unicode=True, sort_keys=False)
    projections, _ = core._stage_projections(paths, "volume", good_volumes)
    assert "planning/volumes/volume-003.yaml" in projections

    bad_volumes = yaml.safe_dump({"volumes": [
        {"order": 1, "chapter_range": [1, 4]},
        {"order": 2, "chapter_range": [6, 12]},
    ]})
    with pytest.raises(ValueError, match="contiguous"):
        core._stage_projections(paths, "volume", bad_volumes)

    good_outline = yaml.safe_dump({"chapters": [
        {"number": number, "title": _distinct_title(number), "reader_orientation": _reader_orientation(), "narrative_timing": _narrative_timing(number), "restricted_actions": [], "rhythm_signature": _rhythm_signature(number), "participants": ["char_placeholder"]}
        for number in range(1, 13)
    ]}, allow_unicode=True, sort_keys=False)
    projections, _ = core._stage_projections(paths, "outline", good_outline)
    assert "planning/chapter-outlines/chapter-0012.yaml" in projections

    bad_outline = yaml.safe_dump({"chapters": [{"number": n} for n in range(1, 12)]})
    with pytest.raises(ValueError, match="exactly chapters 1-12"):
        core._stage_projections(paths, "outline", bad_outline)


def test_final_arc_blocks_new_lines_and_max_requires_resolution(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=5)
    paths = get_workspace("demo")
    write_yaml(paths.root / "state" / "plot-ledger.yaml", {"threads": [{"id": "thread_main", "status": "active"}]})
    write_yaml(paths.root / "state" / "foreshadowing.yaml", {"items": [{"id": "clue_main", "status": "active"}]})

    delta = _empty_delta(5)
    delta["plot_updates"] = [{"thread_id": "thread_new", "status": "active", "event_ids": []}]
    delta["foreshadowing_updates"] = [{"id": "clue_new", "action": "introduce", "event_ids": []}]
    report = validate_delta(paths, 5, delta, "终局正文")
    codes = {item.code for item in report.issues}
    assert "ending_new_thread_forbidden" in codes
    assert "ending_new_foreshadowing_forbidden" in codes
    assert "ending_max_chapter_unresolved" in codes

    resolved = _empty_delta(5)
    resolved["plot_updates"] = [{"thread_id": "thread_main", "status": "resolved", "event_ids": []}]
    resolved["foreshadowing_updates"] = [{"id": "clue_main", "action": "payoff", "event_ids": []}]
    report = validate_delta(paths, 5, resolved, "终局正文")
    assert "ending_max_chapter_unresolved" not in {item.code for item in report.issues}


def test_generate_and_apply_ending_option(workspace_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=30)
    paths = get_workspace("demo")
    # Seed formal IDs through a projection event so ending option references can be verified.
    commit_projection_event(
        paths, event_type="test.ending_ledgers", payload={},
        projections={
            "state/plot-ledger.yaml": yaml.safe_dump({"threads": [{"id": "thread_main", "status": "active"}]}, allow_unicode=True, sort_keys=False),
            "state/foreshadowing.yaml": yaml.safe_dump({"items": [{"id": "clue_main", "status": "active"}]}, allow_unicode=True, sort_keys=False),
        },
    )

    def fake_call(paths, route, messages, *, purpose, chapter=None, repair_round=0):
        assert purpose == "ending.options"
        payload = {"options": [
            {
                "id": "A", "title": "闭环", "ending_summary": "主线闭环",
                "required_outcomes": [{"id": "outcome_truth", "description": "揭示真相", "status": "pending"}],
                "must_resolve_threads": ["thread_main"], "optional_unresolved_threads": [],
                "must_resolve_foreshadowing": ["clue_main"], "optional_unresolved_foreshadowing": [],
                "relationship_outcomes": [{"id": "rel_end", "description": "主角关系落点", "status": "pending"}],
                "final_arc": {"start_chapter": 27, "strategy": "集中收线"}, "risks": []
            },
            {"id": "B", "title": "开放", "ending_summary": "开放式", "final_arc": {"start_chapter": 27}},
            {"id": "C", "title": "苦涩", "ending_summary": "苦涩式", "final_arc": {"start_chapter": 27}},
        ]}
        return ModelResult(
            content=json.dumps(payload, ensure_ascii=False), provider="mock", model="mock", route=route,
            latency_ms=1, input_hash="in", output_hash="out"
        )

    monkeypatch.setattr(core, "_call", fake_call)
    generated = core.generate_ending_options("demo")
    assert len(generated["options"]) == 3
    proposal = Path(generated["proposal"])
    relative = proposal.relative_to(get_workspace("demo").root).as_posix()
    applied = core.apply_ending_plan("demo", relative, "A")
    assert applied["selected_option"] == "A"
    ending = read_yaml(get_workspace("demo").root / "planning" / "ending" / "ending-plan.yaml", {})
    assert ending["required_outcomes"][0]["id"] == "outcome_truth"
    assert ending["relationship_outcomes"][0]["id"] == "rel_end"
    assert ending["final_arc"]["start_chapter"] == 27


def test_finalize_creates_reports_and_locks_project(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=1)
    paths = get_workspace("demo")
    project = read_yaml(paths.project, {})
    project["last_committed_chapter"] = 1
    ending = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    progress = read_yaml(paths.root / "state" / "ending-progress.yaml", {})
    progress["last_committed_chapter"] = 1
    commit_projection_event(
        paths,
        event_type="test.chapter_seeded",
        chapter=1,
        payload={},
        projections={
            "project.yaml": yaml.safe_dump(project, allow_unicode=True, sort_keys=False),
            "chapters/chapter-0001.md": "结局。\n",
            "planning/ending/ending-plan.yaml": yaml.safe_dump(ending, allow_unicode=True, sort_keys=False),
            "state/ending-progress.yaml": yaml.safe_dump(progress, allow_unicode=True, sort_keys=False),
        },
    )
    result = core.finalize_novel("demo")
    assert result["finalized"] is True
    assert read_yaml(paths.project, {})["status"] == "completed"
    assert (paths.root / "final" / "completion-report.md").exists()
    assert (paths.root / "final" / "character-end-states.yaml").exists()
    with pytest.raises(RuntimeError, match="finalized"):
        core.build_chapter_packet("demo", 2)

def test_completed_ending_outcome_requires_current_chapter_evidence(workspace_env: Path) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=2)
    paths = get_workspace("demo")
    ending = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    ending["required_outcomes"] = [{"id": "outcome_truth", "description": "揭示真相", "status": "pending"}]
    commit_projection_event(
        paths,
        event_type="test.ending_outcome",
        payload={},
        projections={"planning/ending/ending-plan.yaml": yaml.safe_dump(ending, allow_unicode=True, sort_keys=False)},
    )

    no_evidence = _empty_delta(2)
    no_evidence["ending_outcome_updates"] = [{"id": "outcome_truth", "status": "complete", "event_ids": []}]
    report = validate_delta(paths, 2, no_evidence, "结局正文")
    assert "ending_outcome_evidence_required" in {item.code for item in report.issues}

    with_evidence = _empty_delta(2)
    with_evidence["timeline_events"] = [{
        "id": "event_ch002_truth", "time": "2026-01-02T00:00:00+00:00", "location_id": "", "location": "",
        "participants": [], "summary": "真相被揭示", "fact_ids": [],
    }]
    with_evidence["ending_outcome_updates"] = [{"id": "outcome_truth", "status": "complete", "event_ids": ["event_ch002_truth"]}]
    report = validate_delta(paths, 2, with_evidence, "结局正文")
    assert "ending_outcome_evidence_required" not in {item.code for item in report.issues}
    assert "ending_outcome_stale_evidence" not in {item.code for item in report.issues}

def test_batch_is_clamped_to_ending_max(workspace_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core.create_workspace("demo", "测试小说", planned_chapters=5)
    calls: list[int] = []

    def fake_pipeline(workspace_id: str, chapter_number: int, auto_commit: bool = True) -> dict:
        calls.append(chapter_number)
        return {"chapter": chapter_number, "status": "committed"}

    monkeypatch.setattr(core, "run_chapter_pipeline", fake_pipeline)
    result = core.run_batch("demo", 4, 3)
    assert calls == [4, 5]
    assert result["requested"] == 3
    assert result["planned"] == 2
    assert result["clamped_to_ending_max"] is True
    assert result["ending_max"] == 5
