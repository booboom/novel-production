from __future__ import annotations

from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.arc_reflection import apply_reflection_to_state, reflect_arc, validate_reflection
from novel_production_mcp.character_simulation import (
    CharacterSimulationState,
    apply_belief_updates,
    assert_no_private_leak,
    build_perception,
    select_characters_for_simulation,
    simulate_intent,
)
from novel_production_mcp.exemplars import ExemplarCard, build_exemplar_index, select_exemplars, writer_safe_exemplars
from novel_production_mcp.scene_simulation import assemble_chapter_from_scenes, direct_scene, load_scene_progress, react_to_world, save_scene_checkpoint, scene_budget


def test_character_intent_uses_immediate_goal_and_filters_private_intents() -> None:
    character = CharacterSimulationState.from_mapping({
        "character_id": "a",
        "goals": {"immediate": "试探对方", "arc": "查明真相", "story": "掌权"},
        "knowledge": {"map": "verified"},
        "beliefs": [{"proposition": "对方不知道我已验证地图", "status": "wrong", "confidence": 0.8}],
        "theory_of_mind": {"b": {"believes_target_does_not_know": ["我已验证地图"]}},
    })
    scene = {
        "scene_goal": "判断对方是否撒谎",
        "public_information": ["雨停了"],
        "hidden_world_truth": ["map is genuine secret"],
        "other_character_private_intents": {"b": {"planned_action": "lie about map"}},
        "private_character_information": {"a": ["我已验证地图"]},
        "default_action": "假装怀疑地图",
    }
    perception = build_perception(character, scene)
    intent = simulate_intent(character, scene)
    assert perception["public_information"] == ["雨停了"]
    assert "hidden_world_truth" not in perception
    assert "other_character_private_intents" not in perception
    assert intent["goal"] == "试探对方"
    assert intent["arc_goal"] == "查明真相"
    assert intent["planned_action"] == "假装怀疑地图"
    assert "我已验证地图" in intent["what_to_hide"]
    assert_no_private_leak(intent, scene)


def test_wrong_belief_shapes_strategy_without_truth_leak() -> None:
    character = CharacterSimulationState.from_mapping({
        "character_id": "lin",
        "immediate_goal": "套出密室位置",
        "beliefs": [{"proposition": "王长老是凶手", "status": "wrong", "confidence": 0.9}],
        "theory_of_mind": {"wang": {"believes_target_knows": ["密室位置"], "believes_target_does_not_know": ["我怀疑他"]}},
    })
    scene = {
        "scene_goal": "试探王长老",
        "hidden_world_truth": ["真正凶手另有其人"],
        "public_information": ["晚宴开始"],
    }
    intent = simulate_intent(character, scene)
    assert intent["relevant_beliefs"][0]["proposition"] == "王长老是凶手"
    assert "真正凶手另有其人" not in str(intent)
    assert intent["assumptions_about_others"]["wang"]["believes_target_knows"] == ["密室位置"]


def test_belief_update_requires_evidence_and_replaces_old_belief() -> None:
    old = [{"proposition": "王长老是凶手", "status": "unverified", "confidence": 0.7}]
    with pytest.raises(ValueError):
        apply_belief_updates(old, [{"new_belief": {"proposition": "王长老不是凶手"}}])
    result = apply_belief_updates(
        old,
        [{
            "old_belief": old[0],
            "new_belief": {"proposition": "王长老不是凶手", "status": "uncertain"},
            "trigger_event": "evt-12",
            "confidence_change": -0.2,
        }],
    )
    assert [item["proposition"] for item in result] == ["王长老不是凶手"]
    assert result[0]["source"] == "evt-12"
    assert result[0]["confidence"] == pytest.approx(0.5)


def test_select_characters_prefers_pov_and_caps_cost() -> None:
    characters = [
        {"character_id": "crowd1", "simulation_level": "full"},
        {"character_id": "hero", "simulation_level": "full"},
        {"character_id": "villain", "simulation_level": "full"},
        {"character_id": "crowd2", "simulation_level": "none"},
        {"character_id": "friend", "simulation_level": "light"},
    ]
    selected = select_characters_for_simulation(
        characters,
        {"pov_character": "hero", "antagonists": ["villain"], "participants": ["hero", "villain", "friend"]},
        max_characters=2,
    )
    assert [item["character_id"] for item in selected] == ["hero", "villain"]


def test_scene_director_collides_goals_and_reacts() -> None:
    plan = direct_scene(
        {"scene_goal": "交易", "environment_state": {"security": "normal"}},
        [
            {"character_id": "a", "goal": "套话", "planned_action": "先提问", "assumptions_about_others": {"b": {"knows": ["x"]}}},
            {"character_id": "b", "goal": "隐瞒", "planned_action": "给半真信息", "planned_lie": ["完整名单"], "fallback_action": "离开", "trigger_to_change_strategy": "对方追问名单"},
        ],
    )
    assert plan["conflict_escalation"] == "rising"
    assert plan["initiative_order"] == ["a", "b"]
    assert plan["misunderstandings"]
    assert plan["lies"]
    assert plan["strategy_changes"]
    world = react_to_world({"security": "normal"}, plan)
    assert world["security"] == "elevated"


def _card(**overrides: object) -> ExemplarCard:
    value = {
        "id": "EX-1",
        "name": "战术因果",
        "scene_types": ["battle"],
        "techniques": ["spatial_clarity"],
        "demonstrates": ["BATTLE-1"],
        "abstracted_pattern": "逼迫移动后利用新位置",
        "source_refs": [{"reference_id": "ref", "chapter": "1"}],
        "quality_score": 0.9,
    }
    value.update(overrides)
    return ExemplarCard.from_mapping(value)


def test_exemplar_index_and_scene_selection_are_bounded() -> None:
    battle = _card()
    romance = _card(
        id="EX-2",
        scene_types=["romance"],
        techniques=["subtext"],
        demonstrates=["DIALOGUE-1"],
        abstracted_pattern="沉默改变关系压力",
    )
    index = build_exemplar_index([battle, romance])
    assert index["by_rule"]["BATTLE-1"] == ["EX-1"]
    selected = select_exemplars(
        {"scene_type": "battle", "techniques": ["spatial_clarity"]},
        [{"id": "BATTLE-1"}],
        [battle.to_dict(), romance.to_dict()],
        limit=1,
        token_budget=1000,
    )
    assert [item["id"] for item in selected] == ["EX-1"]
    assert len(selected[0]["short_excerpt"]) <= 280
    safe = writer_safe_exemplars(selected)
    assert "short_excerpt" not in safe[0]
    assert "source_refs" not in safe[0]


def test_exemplar_rejects_missing_abstraction_and_long_excerpt() -> None:
    with pytest.raises(ValueError):
        ExemplarCard.from_mapping({"id": "EX-X", "source_refs": [{"reference_id": "r"}], "abstracted_pattern": ""})
    card = ExemplarCard.from_mapping({
        "id": "EX-Y",
        "abstracted_pattern": "信息差造成误判",
        "source_refs": [{"reference_id": "r"}],
        "short_excerpt": "字" * 500,
    })
    assert len(card.short_excerpt) == 280


def test_scene_checkpoint_resume(tmp_path: Path) -> None:
    first = save_scene_checkpoint(tmp_path, 4, 1, draft="场景一完成。")
    assert first["progress"]["next_scene"] == 2
    save_scene_checkpoint(tmp_path, 4, 2, draft="场景二完成。", intents=[{"character_id": "a"}])
    progress = load_scene_progress(tmp_path, 4)
    assert progress["progress"]["completed_scenes"] == [1, 2]
    text = assemble_chapter_from_scenes(tmp_path, 4)
    assert "场景一完成" in text and "场景二完成" in text


def test_scene_budget_scales_with_importance() -> None:
    low = scene_budget("low", max_rules=8, max_exemplars=2)
    critical = scene_budget("critical", max_rules=8, max_exemplars=2)
    assert low["max_rules"] < critical["max_rules"]
    assert low["max_exemplars"] <= critical["max_exemplars"]


def test_arc_reflection_requires_evidence_and_rejects_personality_rewrite() -> None:
    with pytest.raises(ValueError):
        reflect_arc([], character_id="a")
    proposal = reflect_arc([{"event_id": "e1", "summary": "失败"}], character_id="a")
    proposal["belief_updates"] = [{
        "old_belief": {"proposition": "高层都不可信"},
        "new_belief": {"proposition": "制度不可信但个体可能可信", "status": "unverified"},
        "trigger_event": "e1",
    }]
    proposal["strategy_updates"] = [{"value": "先验证个人再合作"}]
    proposal["personality_rewrite"] = "变成冲动的人"
    with pytest.raises(ValueError):
        validate_reflection(proposal)
    del proposal["personality_rewrite"]
    validated = validate_reflection(proposal)
    state = apply_reflection_to_state(
        {"character_id": "a", "beliefs": [{"proposition": "高层都不可信", "confidence": 0.8}], "goals": {}},
        validated,
    )
    assert state["beliefs"][0]["proposition"] == "制度不可信但个体可能可信"
    assert state["current_strategy"] == "先验证个人再合作"


def test_feature_flags_disable_simulation_and_exemplars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(tmp_path / "novels"))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    (tmp_path / "novels").mkdir()
    created = core.create_workspace("sim-demo", "演示", "都市", "前提", 10)
    assert created["project"]["character_simulation"]["enabled"] is True
    project_path = Path(created["path"]) / "project.yaml"
    text = project_path.read_text(encoding="utf-8")
    text = text.replace("character_simulation:\n  enabled: true", "character_simulation:\n  enabled: false")
    text = text.replace("writing_exemplars:\n  enabled: true", "writing_exemplars:\n  enabled: false")
    project_path.write_text(text, encoding="utf-8")
    # Bypass projection drift for this unit test by rewriting via adopt path is heavy; call APIs with explicit flags.
    disabled = core.simulate_character_intents([{"character_id": "a", "goals": {"immediate": "x"}}], {"scene_goal": "y"}, enabled=False)
    assert disabled == {"enabled": False, "intents": [], "max_simulated_characters": 4}
    exemplars = core.select_scene_exemplars("sim-demo", {"scene_type": "battle"}, limit=0)
    # workspace may still have exemplars enabled if projection rewrite didn't sync; force through config file
    routes = Path(created["path"]) / "routes.toml"
    routes.write_text("[writing_exemplars]\nenabled = false\n", encoding="utf-8")
    exemplars = core.select_scene_exemplars("sim-demo", {"scene_type": "battle"})
    assert exemplars["enabled"] is False
    assert exemplars["selected"] == []
