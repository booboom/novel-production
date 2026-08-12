from __future__ import annotations

"""Deterministic scene intent collision, environment reaction, and checkpoints."""

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .storage import atomic_write_text, read_json, utc_now, write_json


def direct_scene(scene: Mapping[str, Any], intents: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = [dict(intent) for intent in intents if isinstance(intent, Mapping)]
    if not ordered:
        return {
            "opening_state": dict(scene.get("opening_state") or {}),
            "initiative_order": [],
            "actions": [],
            "reactions": [],
            "strategy_changes": [],
            "reveals": [],
            "lies": [],
            "misunderstandings": [],
            "conflict_escalation": "stable",
            "turning_point": str(scene.get("turning_point", "")),
            "consequence": list(scene.get("external_events") or []),
            "ending_state": dict(scene.get("environment_state") or {}),
            "unresolved_pressure": list(scene.get("unresolved_pressure") or []),
        }
    actions = []
    reactions = []
    reveals: list[dict[str, Any]] = []
    lies: list[dict[str, Any]] = []
    strategy_changes: list[dict[str, Any]] = []
    for index, intent in enumerate(ordered):
        actor = str(intent.get("character_id", ""))
        action = {
            "actor": actor,
            "action": intent.get("planned_action", ""),
            "goal": intent.get("goal", ""),
            "strategy": intent.get("strategy", ""),
            "reaction_to": intent.get("reaction_to", ordered[index - 1].get("character_id", "") if index else ""),
        }
        actions.append(action)
        if index > 0:
            reactions.append(
                {
                    "actor": actor,
                    "to": ordered[index - 1].get("character_id", ""),
                    "response": intent.get("planned_action", ""),
                    "fallback": intent.get("fallback_action", ""),
                }
            )
        reveals.extend({"actor": actor, "item": value} for value in intent.get("what_to_reveal", []) or [])
        lies.extend({"actor": actor, "item": value} for value in intent.get("planned_lie", []) or [])
        if intent.get("trigger_to_change_strategy") and intent.get("fallback_action"):
            strategy_changes.append(
                {
                    "actor": actor,
                    "from": intent.get("strategy", ""),
                    "to": intent.get("fallback_action", ""),
                    "trigger": intent.get("trigger_to_change_strategy", ""),
                }
            )
    goals = {str(item.get("goal", "")) for item in ordered if item.get("goal")}
    conflict = len(goals) > 1
    misunderstandings = []
    if conflict:
        misunderstandings = [
            {
                "actor": item.get("character_id", ""),
                "assumption": item.get("assumptions_about_others", {}),
            }
            for item in ordered
            if item.get("assumptions_about_others")
        ]
    environment = dict(scene.get("environment_state") or {})
    consequence = list(scene.get("external_events") or [])
    for action in actions:
        if action["action"]:
            consequence.append(
                {
                    "source": action["actor"],
                    "type": "environment_pressure",
                    "description": action["action"],
                }
            )
    return {
        "opening_state": dict(scene.get("opening_state") or {}),
        "initiative_order": [item.get("character_id", "") for item in ordered],
        "actions": actions,
        "reactions": reactions,
        "strategy_changes": strategy_changes,
        "reveals": reveals,
        "lies": lies,
        "misunderstandings": misunderstandings,
        "conflict_escalation": "rising" if conflict else "stable",
        "turning_point": str(scene.get("turning_point", "")),
        "consequence": consequence,
        "ending_state": environment,
        "unresolved_pressure": list(scene.get("unresolved_pressure") or []),
        "scene_goal": str(scene.get("scene_goal", "")),
        "importance": str(scene.get("importance", "normal") or "normal"),
    }


def react_to_world(environment: Mapping[str, Any], execution: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(environment)
    result.setdefault("external_events", [])
    result["external_events"] = [
        *list(result.get("external_events") or []),
        *list(execution.get("consequence") or []),
    ]
    if execution.get("conflict_escalation") == "rising":
        result["security"] = "elevated"
        if "time_pressure" not in result:
            result["time_pressure"] = "increasing"
    if execution.get("reveals"):
        result["information_leak_pressure"] = True
    return result


def scene_checkpoint_dir(root: Path, chapter_number: int) -> Path:
    return root / "drafts" / "scenes" / f"chapter-{chapter_number:04d}"


def save_scene_checkpoint(
    root: Path,
    chapter_number: int,
    scene_index: int,
    *,
    draft: str = "",
    intents: Sequence[Mapping[str, Any]] | None = None,
    execution_plan: Mapping[str, Any] | None = None,
    state_delta: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    directory = scene_checkpoint_dir(root, chapter_number)
    directory.mkdir(parents=True, exist_ok=True)
    scene_id = f"scene-{scene_index:02d}"
    payload = {
        "chapter": chapter_number,
        "scene_index": scene_index,
        "updated_at": utc_now(),
        "status": "completed",
        "intents": [dict(item) for item in intents or []],
        "execution_plan": dict(execution_plan or {}),
        "state_delta": dict(state_delta or {}),
        "metadata": dict(metadata or {}),
    }
    write_json(directory / f"{scene_id}.json", payload)
    if draft:
        atomic_write_text(directory / f"{scene_id}.md", draft)
    progress = read_json(directory / "progress.json", {"chapter": chapter_number, "completed_scenes": [], "next_scene": 1})
    completed = [int(value) for value in progress.get("completed_scenes", []) if str(value).isdigit() or isinstance(value, int)]
    if scene_index not in completed:
        completed.append(scene_index)
    completed = sorted(set(completed))
    progress = {
        "chapter": chapter_number,
        "completed_scenes": completed,
        "next_scene": (max(completed) + 1) if completed else 1,
        "updated_at": utc_now(),
    }
    write_json(directory / "progress.json", progress)
    return {"path": str(directory / f"{scene_id}.json"), "progress": progress}


def load_scene_progress(root: Path, chapter_number: int) -> dict[str, Any]:
    directory = scene_checkpoint_dir(root, chapter_number)
    progress = read_json(directory / "progress.json", {"chapter": chapter_number, "completed_scenes": [], "next_scene": 1})
    scenes = []
    for path in sorted(directory.glob("scene-*.json")):
        try:
            scenes.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return {"progress": progress, "scenes": scenes, "directory": str(directory)}


def assemble_chapter_from_scenes(root: Path, chapter_number: int) -> str:
    directory = scene_checkpoint_dir(root, chapter_number)
    parts: list[str] = []
    for path in sorted(directory.glob("scene-*.md")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip() + ("\n" if parts else "")


def scene_budget(importance: str, *, max_rules: int = 8, max_exemplars: int = 2) -> dict[str, int]:
    level = str(importance or "normal").lower()
    if level == "low":
        return {"max_rules": max(1, max_rules // 2), "max_exemplars": min(1, max_exemplars), "simulation_depth": 1}
    if level == "critical":
        return {"max_rules": max_rules, "max_exemplars": max_exemplars, "simulation_depth": 3}
    if level == "high":
        return {"max_rules": max(2, (max_rules * 3) // 4), "max_exemplars": max_exemplars, "simulation_depth": 2}
    return {"max_rules": max(2, max_rules // 2 + 1), "max_exemplars": min(max_exemplars, 2), "simulation_depth": 2}
