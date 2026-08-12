from __future__ import annotations

"""Evidence-backed arc reflection proposals. Never mutates canon directly."""

from typing import Any, Mapping, Sequence

from .character_simulation import apply_belief_updates, normalize_belief


def reflect_arc(events: list[Mapping[str, Any]], *, character_id: str) -> dict[str, Any]:
    """Create an evidence-backed reflection proposal; never mutates canon."""
    evidence = [dict(event) for event in events if event.get("event_id") or event.get("id")]
    if not evidence:
        raise ValueError("Arc reflection requires event evidence")
    return {
        "character_id": character_id,
        "evidence": evidence,
        "what_character_learned": "",
        "belief_updates": [],
        "goal_updates": [],
        "strategy_updates": [],
        "fear_updates": [],
        "relationship_interpretation": {},
        "unresolved_internal_conflict": "",
        "status": "proposal",
    }


def validate_reflection(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    evidence = result.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, Mapping) or not (item.get("event_id") or item.get("id")) for item in evidence)
    ):
        raise ValueError("Arc reflection changes require event evidence")
    for field in ("belief_updates", "goal_updates", "strategy_updates", "fear_updates"):
        if not isinstance(result.get(field, []), list):
            raise ValueError(f"{field} must be a list")
    for update in result.get("belief_updates", []):
        if not isinstance(update, Mapping):
            raise ValueError("belief_updates items must be objects")
        if not (update.get("trigger_event") or update.get("evidence")):
            raise ValueError("belief_updates require trigger_event or evidence")
        new_belief = update.get("new_belief")
        if not isinstance(new_belief, Mapping) or not str(new_belief.get("proposition", "")).strip():
            raise ValueError("belief_updates require new_belief.proposition")
        normalize_belief(new_belief)
    if result.get("personality_rewrite"):
        raise ValueError("unsupported personality rewrite rejected; use belief/goal/strategy/fear updates")
    result["status"] = "validated"
    return result


def apply_reflection_to_state(
    state: Mapping[str, Any],
    reflection: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a new character simulation state dict with validated reflection applied."""
    validated = validate_reflection(reflection)
    result = dict(state)
    beliefs = list(result.get("beliefs") or [])
    if validated.get("belief_updates"):
        result["beliefs"] = apply_belief_updates(beliefs, validated["belief_updates"])
    goals = dict(result.get("goals") or {})
    for update in validated.get("goal_updates") or []:
        if not isinstance(update, Mapping):
            continue
        layer = str(update.get("layer") or update.get("goal_layer") or "immediate")
        value = str(update.get("value") or update.get("goal") or "").strip()
        if value:
            goals[layer] = value
    result["goals"] = goals
    for update in validated.get("strategy_updates") or []:
        if isinstance(update, Mapping) and update.get("value"):
            result["current_strategy"] = str(update["value"])
            break
        if isinstance(update, str) and update.strip():
            result["current_strategy"] = update.strip()
            break
    fears = list(result.get("fears") or [])
    for update in validated.get("fear_updates") or []:
        if isinstance(update, Mapping):
            text = str(update.get("value") or update.get("fear") or "").strip()
            action = str(update.get("action", "add")).lower()
            if text and action == "remove":
                fears = [item for item in fears if item != text]
            elif text and text not in fears:
                fears.append(text)
        elif isinstance(update, str) and update.strip() and update.strip() not in fears:
            fears.append(update.strip())
    result["fears"] = fears
    if isinstance(validated.get("relationship_interpretation"), Mapping):
        relationships = dict(result.get("relationships") or {})
        relationships.update(dict(validated["relationship_interpretation"]))
        result["relationships"] = relationships
    result["last_reflection"] = {
        "what_character_learned": validated.get("what_character_learned", ""),
        "unresolved_internal_conflict": validated.get("unresolved_internal_conflict", ""),
        "evidence_ids": [
            str(item.get("event_id") or item.get("id"))
            for item in validated.get("evidence", [])
            if isinstance(item, Mapping)
        ],
    }
    return result


def reject_unsupported_personality_rewrite(value: Mapping[str, Any]) -> None:
    if value.get("personality_rewrite") or value.get("core_personality_change"):
        raise ValueError("unsupported personality rewrite rejected")
