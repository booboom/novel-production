from __future__ import annotations

"""Data-driven character cognition and intent simulation.

No LLM calls live here. The module builds privacy-filtered context for optional
model generation and provides deterministic fallbacks. World truth is never
copied into character perception; only validators may compare truth offline.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


BELIEF_STATUSES = frozenset({"correct", "wrong", "uncertain", "unverified", "active", "revised"})


@dataclass
class Belief:
    proposition: str
    confidence: float = 0.5
    source: str = ""
    formed_at: str = ""
    last_updated: str = ""
    status: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposition": self.proposition,
            "confidence": self.confidence,
            "source": self.source,
            "formed_at": self.formed_at,
            "last_updated": self.last_updated,
            "status": self.status,
        }


@dataclass
class TheoryOfMindEntry:
    target_character_id: str
    believes_target_knows: list[str] = field(default_factory=list)
    believes_target_does_not_know: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_character_id": self.target_character_id,
            "believes_target_knows": list(self.believes_target_knows),
            "believes_target_does_not_know": list(self.believes_target_does_not_know),
            "uncertain": list(self.uncertain),
        }


@dataclass
class CharacterSimulationState:
    character_id: str
    goals: dict[str, str] = field(default_factory=dict)
    desires: list[str] = field(default_factory=list)
    fears: list[str] = field(default_factory=list)
    stakes: list[str] = field(default_factory=list)
    beliefs: list[Belief | dict[str, Any]] = field(default_factory=list)
    risk_tolerance: float = 0.5
    current_strategy: str = ""
    emotional_state: str = ""
    relationships: dict[str, Any] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)
    location: str = ""
    resources: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    theory_of_mind: dict[str, Any] = field(default_factory=dict)
    simulation_level: str = "full"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], character_id: str = "") -> "CharacterSimulationState":
        raw = dict(value)
        cid = str(raw.get("character_id") or character_id).strip()
        if not cid:
            raise ValueError("character_id is required")
        goals = dict(raw.get("goals") or {})
        for old, new in (("immediate_goal", "immediate"), ("arc_goal", "arc"), ("story_goal", "story")):
            if raw.get(old) and not goals.get(new):
                goals[new] = str(raw[old])
        beliefs: list[Belief | dict[str, Any]] = []
        for item in raw.get("beliefs", []) or []:
            if isinstance(item, Belief):
                beliefs.append(item)
            elif isinstance(item, Mapping):
                beliefs.append(normalize_belief(item))
        try:
            risk_tolerance = float(raw.get("risk_tolerance", 0.5))
        except (TypeError, ValueError):
            risk_tolerance = 0.5
        risk_tolerance = max(0.0, min(1.0, risk_tolerance))
        level = str(raw.get("simulation_level", "full") or "full").strip().lower()
        if level not in {"none", "light", "full"}:
            level = "full"
        return cls(
            character_id=cid,
            goals=goals,
            desires=_string_list(raw.get("desires")),
            fears=_string_list(raw.get("fears")),
            stakes=_string_list(raw.get("stakes")),
            beliefs=beliefs,
            risk_tolerance=risk_tolerance,
            current_strategy=str(raw.get("current_strategy", "")),
            emotional_state=str(raw.get("emotional_state", "")),
            relationships=dict(raw.get("relationships") or {}),
            knowledge=_normalize_knowledge(raw.get("knowledge")),
            location=str(raw.get("location", "")),
            resources=dict(raw.get("resources") or {}),
            constraints=_string_list(raw.get("constraints")),
            theory_of_mind=_normalize_tom(raw.get("theory_of_mind") or raw.get("tom") or {}),
            simulation_level=level,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "goals": dict(self.goals),
            "desires": list(self.desires),
            "fears": list(self.fears),
            "stakes": list(self.stakes),
            "beliefs": self.relevant_beliefs(),
            "risk_tolerance": self.risk_tolerance,
            "current_strategy": self.current_strategy,
            "emotional_state": self.emotional_state,
            "relationships": dict(self.relationships),
            "knowledge": dict(self.knowledge),
            "location": self.location,
            "resources": dict(self.resources),
            "constraints": list(self.constraints),
            "theory_of_mind": dict(self.theory_of_mind),
            "simulation_level": self.simulation_level,
        }

    def relevant_beliefs(self, propositions: list[str] | None = None) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for item in self.beliefs:
            belief = item.to_dict() if isinstance(item, Belief) else normalize_belief(item)
            if not propositions or any(p.lower() in str(belief.get("proposition", "")).lower() for p in propositions):
                values.append(belief)
        return values

    def immediate_goal(self) -> str:
        return str(self.goals.get("immediate") or self.goals.get("arc") or self.goals.get("story") or "")


def normalize_belief(value: Mapping[str, Any]) -> dict[str, Any]:
    proposition = str(value.get("proposition", "")).strip()
    if not proposition:
        raise ValueError("belief requires proposition")
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    status = str(value.get("status", "unverified") or "unverified").strip().lower()
    if status not in BELIEF_STATUSES:
        status = "unverified"
    return {
        "proposition": proposition,
        "confidence": confidence,
        "source": str(value.get("source", "")),
        "formed_at": str(value.get("formed_at", "")),
        "last_updated": str(value.get("last_updated", "")),
        "status": status,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_knowledge(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            result[str(key)] = dict(item)
        else:
            result[str(key)] = item
    return result


def _normalize_tom(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for target, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
        entry = TheoryOfMindEntry(
            target_character_id=str(raw.get("target_character_id") or target),
            believes_target_knows=_string_list(raw.get("believes_target_knows") or raw.get("knows")),
            believes_target_does_not_know=_string_list(
                raw.get("believes_target_does_not_know") or raw.get("does_not_know") or raw.get("not_knows")
            ),
            uncertain=_string_list(raw.get("uncertain")),
        )
        result[str(target)] = entry.to_dict()
    return result


def build_perception(
    character: CharacterSimulationState,
    scene: Mapping[str, Any],
    *,
    include_tom: bool = True,
) -> dict[str, Any]:
    """Return only information the character may use; private intent is excluded."""
    private_map = scene.get("private_character_information") or {}
    private_for_self = list(private_map.get(character.character_id, [])) if isinstance(private_map, Mapping) else []
    tom = dict(character.theory_of_mind)
    scene_tom = scene.get("theory_of_mind") or {}
    if isinstance(scene_tom, Mapping):
        own = scene_tom.get(character.character_id)
        if isinstance(own, Mapping):
            tom = {**tom, **_normalize_tom(own)}
    visible = {
        "character_id": character.character_id,
        "public_information": list(scene.get("public_information") or []),
        "observable_information": list(scene.get("observable_information") or []),
        "private_character_information": private_for_self,
        "knowledge": character.knowledge.copy(),
        "beliefs": character.relevant_beliefs(),
        "goals": dict(character.goals),
        "fears": list(character.fears),
        "stakes": list(character.stakes),
        "location": character.location or str(scene.get("location", "")),
        "environment": dict(scene.get("environment_state") or {}),
        "constraints": list(character.constraints),
        "relationships": dict(character.relationships),
    }
    if include_tom:
        visible["theory_of_mind"] = tom
    # Deliberately omit hidden_world_truth and other_character_private_intents.
    return visible


def _strategy_from_state(character: CharacterSimulationState, perception: Mapping[str, Any], scene: Mapping[str, Any]) -> str:
    if character.current_strategy:
        return character.current_strategy
    beliefs = perception.get("beliefs") or []
    wrongish = [item for item in beliefs if str(item.get("status", "")).lower() in {"wrong", "unverified", "uncertain"}]
    tom = perception.get("theory_of_mind") or {}
    if wrongish and tom:
        return "act on current beliefs while testing whether others share the same information"
    if character.risk_tolerance < 0.35:
        return "pursue the immediate goal while minimizing exposure and cost"
    if character.risk_tolerance > 0.7:
        return "press the immediate goal aggressively within hard constraints"
    return str(scene.get("default_strategy") or "pursue the immediate goal while limiting risk")


def _planned_action(character: CharacterSimulationState, scene: Mapping[str, Any], perception: Mapping[str, Any]) -> str:
    planned_actions = scene.get("planned_actions")
    explicit = ""
    if isinstance(planned_actions, Mapping):
        explicit = str(planned_actions.get(character.character_id, "") or "")
    if not explicit:
        explicit = str(scene.get("default_action") or "")
    if explicit:
        return explicit
    goal = character.immediate_goal()
    beliefs = perception.get("beliefs") or []
    if any("假装" in str(item.get("proposition", "")) or "试探" in goal for item in beliefs) or "试探" in goal:
        return f"围绕目标“{goal or '当前任务'}”进行有限信息试探"
    if goal:
        return f"推进目标：{goal}"
    return "观察并等待可验证的信息"


def simulate_intent(
    character: CharacterSimulationState,
    scene: Mapping[str, Any],
    *,
    include_tom: bool = True,
    level: str | None = None,
) -> dict[str, Any]:
    effective_level = (level or character.simulation_level or "full").lower()
    if effective_level == "none":
        return {
            "character_id": character.character_id,
            "simulation_level": "none",
            "goal": character.immediate_goal(),
            "planned_action": "",
            "strategy": "",
        }
    perception = build_perception(character, scene, include_tom=include_tom and effective_level == "full")
    immediate = character.immediate_goal()
    assumptions = perception.get("theory_of_mind", {}) if include_tom and effective_level == "full" else {}
    hide = list(scene.get("hide_by_default", []))
    reveal = list(scene.get("reveal_by_default", []))
    hide_map = scene.get("hide_by_character") if isinstance(scene.get("hide_by_character"), Mapping) else {}
    reveal_map = scene.get("reveal_by_character") if isinstance(scene.get("reveal_by_character"), Mapping) else {}
    hide = list(dict.fromkeys([*hide, *list(hide_map.get(character.character_id, []) if isinstance(hide_map, Mapping) else [])]))
    reveal = list(dict.fromkeys([*reveal, *list(reveal_map.get(character.character_id, []) if isinstance(reveal_map, Mapping) else [])]))
    # Private information owned by this character defaults to hidden unless explicitly revealed.
    for item in perception.get("private_character_information") or []:
        if item not in reveal and item not in hide:
            hide.append(item)
    intent = {
        "character_id": character.character_id,
        "simulation_level": effective_level,
        "goal": immediate,
        "arc_goal": str(character.goals.get("arc", "")),
        "story_goal": str(character.goals.get("story", "")),
        "desired_outcome": str(scene.get("scene_goal", "")),
        "known_information": perception["knowledge"],
        "relevant_beliefs": perception["beliefs"],
        "assumptions_about_others": assumptions,
        "strategy": _strategy_from_state(character, perception, scene),
        "what_to_hide": hide,
        "what_to_reveal": reveal,
        "acceptable_cost": scene.get("acceptable_cost", ""),
        "unacceptable_cost": scene.get("unacceptable_cost", ""),
        "planned_action": _planned_action(character, scene, perception),
        "fallback_action": str(
            (scene.get("fallback_actions") or {}).get(character.character_id, "")
            if isinstance(scene.get("fallback_actions"), Mapping)
            else scene.get("fallback_action", "")
        ),
        "trigger_to_change_strategy": str(scene.get("strategy_change_trigger", "")),
        "constraints": list(character.constraints),
        "risk_tolerance": character.risk_tolerance,
    }
    if effective_level == "full":
        intent["perception"] = perception
    return intent


def select_characters_for_simulation(
    characters: Sequence[Mapping[str, Any]],
    scene: Mapping[str, Any],
    *,
    max_characters: int = 4,
) -> list[dict[str, Any]]:
    """Prefer POV, antagonists, and relationship cores; cap cost."""
    ranked: list[tuple[int, dict[str, Any]]] = []
    pov = str(scene.get("pov_character") or "")
    core = {str(item) for item in scene.get("core_characters") or scene.get("participants") or []}
    antagonists = {str(item) for item in scene.get("antagonists") or []}
    for raw in characters:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        cid = str(item.get("character_id", ""))
        level = str(item.get("simulation_level", "full") or "full").lower()
        if level == "none":
            continue
        score = 0
        if cid and cid == pov:
            score += 100
        if cid in antagonists:
            score += 50
        if cid in core:
            score += 20
        if level == "full":
            score += 5
        ranked.append((score, item))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("character_id", ""))))
    return [item for _, item in ranked[: max(0, max_characters)]]


def apply_belief_updates(
    beliefs: Sequence[Belief | Mapping[str, Any]],
    updates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply only evidence-backed updates; return normalized beliefs."""
    result = [b.to_dict() if isinstance(b, Belief) else normalize_belief(b) for b in beliefs]
    for update in updates:
        evidence = update.get("trigger_event") or update.get("evidence")
        if not evidence:
            raise ValueError("belief update requires trigger_event or evidence")
        new = dict(update.get("new_belief") or {})
        proposition = str(new.get("proposition", "")).strip()
        if not proposition:
            raise ValueError("belief update requires new_belief.proposition")
        old = str((update.get("old_belief") or {}).get("proposition", ""))
        if old:
            result = [item for item in result if str(item.get("proposition", "")) != old]
        new.setdefault("source", str(evidence))
        new.setdefault("status", "unverified")
        if "confidence_change" in update and "confidence" not in new:
            try:
                base = float((update.get("old_belief") or {}).get("confidence", 0.5))
            except (TypeError, ValueError):
                base = 0.5
            try:
                new["confidence"] = max(0.0, min(1.0, base + float(update["confidence_change"])))
            except (TypeError, ValueError):
                new["confidence"] = base
        result.append(normalize_belief(new))
    return result


def assert_no_private_leak(intent: Mapping[str, Any], scene: Mapping[str, Any]) -> None:
    """Deterministic guard used by tests and optional validation."""
    blob = str(intent)
    if "hidden_world_truth" in blob:
        raise ValueError("intent leaked hidden_world_truth key")
    hidden = scene.get("hidden_world_truth") or []
    for item in hidden:
        text = str(item)
        if text and text in blob and text not in str(intent.get("known_information", {})):
            # Allow only if already in the character's own knowledge.
            knowledge = intent.get("known_information") or {}
            if text not in str(knowledge):
                raise ValueError("intent leaked hidden world truth content")
    others = scene.get("other_character_private_intents") or {}
    if isinstance(others, Mapping):
        for other_id, private in others.items():
            if other_id == intent.get("character_id"):
                continue
            private_text = str(private)
            if private_text and private_text in blob:
                raise ValueError(f"intent leaked private intent of {other_id}")
