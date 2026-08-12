from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .ending import ending_context_for_chapter, ending_progress_from_data
from .models import ValidationIssue, ValidationReport, WorkspacePaths
from .quality import (
    validate_delta_coverage,
    validate_draft_contract,
    validate_semantic_quality_audits,
)
from .storage import read_json, read_text, read_yaml
from .writing_gates import validate_writing_report_for_commit

REQUIRED_DIRS = [
    "canon",
    "characters/profiles",
    "characters/arcs",
    "state/characters",
    "planning/chapter-outlines",
    "planning/ending",
    "final",
    "chapters",
    "drafts",
    "packets",
    "deltas",
    "audits",
    "runs",
    "checkpoints",
    "metrics",
    ".novel",
]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_reaction_logic_audit(audit: dict[str, Any]) -> list[ValidationIssue]:
    """Fail closed when the reviewer omits or rejects the abnormal-response audit."""
    events = audit.get("abnormal_events")
    if not isinstance(events, list):
        return [ValidationIssue(
            "reaction_audit_missing",
            "Chapter audit must contain an abnormal_events array, including an empty array when no material anomaly exists",
            "critical",
            details={"fix": "Rerun the reviewer with the required abnormal-response audit schema"},
        )]

    issues: list[ValidationIssue] = []
    required = {
        "location",
        "stimulus",
        "character",
        "prior_basis",
        "actual_response",
        "reaction_bridge",
        "plausible",
        "severity",
        "fix",
    }
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(ValidationIssue(
                "reaction_audit_malformed",
                f"abnormal_events[{index}] must be an object",
                "critical",
                details={"event_index": index, "fix": "Rerun the reviewer with the required abnormal-response audit schema"},
            ))
            continue
        missing = sorted(required.difference(event))
        if missing or not isinstance(event.get("plausible"), bool):
            issues.append(ValidationIssue(
                "reaction_audit_malformed",
                f"abnormal_events[{index}] is missing required fields or has a non-boolean plausible value",
                "critical",
                details={
                    "event_index": index,
                    "missing_fields": missing,
                    "plausible": event.get("plausible"),
                    "fix": "Rerun the reviewer with every required abnormal-response field",
                },
            ))
            continue
        if event["plausible"]:
            continue
        severity = "critical" if str(event.get("severity", "")).lower() == "critical" else "high"
        stimulus = str(event.get("stimulus", "")).strip() or f"abnormal_events[{index}]"
        character = str(event.get("character", "")).strip() or "unknown character"
        issues.append(ValidationIssue(
            "reaction_logic",
            f"{character} lacks a plausible response bridge for material anomaly: {stimulus}",
            severity,
            str(event.get("location", "")).strip() or None,
            details={
                "event_index": index,
                "prior_basis": event.get("prior_basis", ""),
                "actual_response": event.get("actual_response", ""),
                "reaction_bridge": event.get("reaction_bridge", ""),
                "fix": event.get("fix", ""),
            },
        ))
    return issues


def validate_narrative_orientation_audit(audit: dict[str, Any]) -> list[ValidationIssue]:
    """Fail closed when the reviewer omits or rejects reader task orientation."""
    orientation = audit.get("orientation_audit")
    if not isinstance(orientation, dict):
        return [ValidationIssue(
            "orientation_audit_missing",
            "Chapter audit must contain an orientation_audit object",
            "critical",
            details={"fix": "Rerun the reviewer with the required reader-orientation audit schema"},
        )]

    required_text = {
        "task_origin",
        "task_object",
        "why_now",
        "stakes",
        "method_reason",
        "location",
        "severity",
        "fix",
    }
    required_bool = {"established_before_consequential_action", "sufficient"}
    missing = sorted((required_text | required_bool).difference(orientation))
    invalid_text = sorted(
        field
        for field in required_text - {"fix"}
        if field in orientation and (not isinstance(orientation.get(field), str) or not str(orientation.get(field)).strip())
    )
    invalid_bool = sorted(
        field
        for field in required_bool
        if field in orientation and not isinstance(orientation.get(field), bool)
    )
    if missing or invalid_text or invalid_bool:
        return [ValidationIssue(
            "orientation_audit_malformed",
            "orientation_audit is missing required fields or contains invalid values",
            "critical",
            details={
                "missing_fields": missing,
                "invalid_text_fields": invalid_text,
                "invalid_boolean_fields": invalid_bool,
                "fix": "Rerun the reviewer with every required reader-orientation field",
            },
        )]

    if orientation["sufficient"] and orientation["established_before_consequential_action"]:
        return []
    severity = "critical" if str(orientation.get("severity", "")).lower() == "critical" else "high"
    return [ValidationIssue(
        "narrative_orientation",
        "Reader lacks a sufficient task-origin, urgency, stakes, or method bridge before consequential action",
        severity,
        str(orientation.get("location", "")).strip() or None,
        details={
            "task_origin": orientation.get("task_origin", ""),
            "task_object": orientation.get("task_object", ""),
            "why_now": orientation.get("why_now", ""),
            "stakes": orientation.get("stakes", ""),
            "method_reason": orientation.get("method_reason", ""),
            "fix": orientation.get("fix", ""),
        },
    )]


def validate_narrative_timing_audit(audit: dict[str, Any]) -> list[ValidationIssue]:
    """Fail closed when the reviewer omits or rejects narrative sequence timing."""
    timing = audit.get("narrative_timing_audit")
    if not isinstance(timing, dict):
        return [ValidationIssue(
            "narrative_timing_audit_missing",
            "Chapter audit must contain a narrative_timing_audit object",
            "critical",
            details={"fix": "Rerun the reviewer with the required narrative-timing audit schema"},
        )]

    if (
        not isinstance(timing.get("sequence_valid"), bool)
        or not isinstance(timing.get("premature_references"), list)
        or not isinstance(timing.get("summary"), str)
        or not str(timing.get("summary", "")).strip()
    ):
        return [ValidationIssue(
            "narrative_timing_audit_malformed",
            "narrative_timing_audit requires boolean sequence_valid, an array of premature_references, and a non-empty summary",
            "critical",
            details={"fix": "Rerun the reviewer with every required narrative-timing field"},
        )]

    issues: list[ValidationIssue] = []
    required = {"location", "element", "earliest_allowed_chapter", "reason", "severity", "fix"}
    references = timing["premature_references"]
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            issues.append(ValidationIssue(
                "narrative_timing_audit_malformed",
                f"premature_references[{index}] must be an object",
                "critical",
                details={"reference_index": index, "fix": "Rerun the reviewer with the required premature-reference fields"},
            ))
            continue
        missing = sorted(required.difference(reference))
        invalid_text = sorted(
            field
            for field in required - {"earliest_allowed_chapter"}
            if field in reference and (not isinstance(reference.get(field), str) or not str(reference.get(field)).strip())
        )
        earliest = reference.get("earliest_allowed_chapter")
        if missing or invalid_text or not isinstance(earliest, int) or isinstance(earliest, bool) or earliest < 1:
            issues.append(ValidationIssue(
                "narrative_timing_audit_malformed",
                f"premature_references[{index}] is missing required fields or contains invalid values",
                "critical",
                details={
                    "reference_index": index,
                    "missing_fields": missing,
                    "invalid_text_fields": invalid_text,
                    "earliest_allowed_chapter": earliest,
                    "fix": "Rerun the reviewer with every required premature-reference field",
                },
            ))
            continue
        severity = "critical" if str(reference.get("severity", "")).lower() == "critical" else "high"
        issues.append(ValidationIssue(
            "future_plan_leak",
            f"Narrative element appears before chapter {earliest}: {str(reference.get('element', '')).strip()}",
            severity,
            str(reference.get("location", "")).strip() or None,
            details={
                "reference_index": index,
                "element": reference.get("element", ""),
                "earliest_allowed_chapter": earliest,
                "reason": reference.get("reason", ""),
                "fix": reference.get("fix", ""),
            },
        ))

    if not timing["sequence_valid"] and not references:
        issues.append(ValidationIssue(
            "narrative_timing_audit_malformed",
            "sequence_valid is false but premature_references is empty",
            "critical",
            details={"fix": "List each premature narrative reference or mark sequence_valid true"},
        ))
    if timing["sequence_valid"] and references:
        issues.append(ValidationIssue(
            "narrative_timing_audit_malformed",
            "sequence_valid is true but premature_references is not empty",
            "critical",
            details={"fix": "Mark sequence_valid false when premature references exist"},
        ))
    return issues


def _fact_ids(paths: WorkspacePaths) -> set[str]:
    result: set[str] = set()
    for name in ("facts.yaml", "immutable-facts.yaml"):
        data = read_yaml(paths.root / "canon" / name, {"facts": []})
        for item in _as_list(data.get("facts") if isinstance(data, dict) else []):
            if isinstance(item, dict) and item.get("id"):
                result.add(str(item["id"]))
    return result


def _event_ids(paths: WorkspacePaths) -> set[str]:
    timeline = read_yaml(paths.root / "state" / "timeline.yaml", {"events": []})
    return {
        str(item.get("id"))
        for item in _as_list(timeline.get("events") if isinstance(timeline, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }


def _valid_characters(paths: WorkspacePaths) -> set[str]:
    index = read_yaml(paths.root / "characters" / "index.yaml", {"characters": []})
    return {
        str(item.get("id"))
        for item in _as_list(index.get("characters") if isinstance(index, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }


def _known_abilities(paths: WorkspacePaths, char_id: str) -> set[str]:
    profile = read_yaml(paths.root / "characters" / "profiles" / f"{char_id}.yaml", {})
    state = read_yaml(paths.root / "state" / "characters" / f"{char_id}.yaml", {})
    abilities: set[str] = set()
    raw_profile = profile.get("abilities", {}) if isinstance(profile, dict) else {}
    if isinstance(raw_profile, dict):
        abilities.update(str(key) for key in raw_profile.keys())
    elif isinstance(raw_profile, list):
        abilities.update(str(value) for value in raw_profile)
    learned = state.get("learned_abilities", []) if isinstance(state, dict) else []
    abilities.update(str(value) for value in _as_list(learned))
    return abilities


def _location_data(paths: WorkspacePaths) -> tuple[set[str], dict[tuple[str, str], int]]:
    data = read_yaml(paths.root / "canon" / "locations.yaml", {"locations": [], "travel_minutes": {}})
    locations: set[str] = set()
    if isinstance(data, dict):
        for item in _as_list(data.get("locations")):
            if isinstance(item, dict):
                if item.get("id"):
                    locations.add(str(item["id"]))
                if item.get("name"):
                    locations.add(str(item["name"]))
    matrix: dict[tuple[str, str], int] = {}
    raw = data.get("travel_minutes", {}) if isinstance(data, dict) else {}
    if isinstance(raw, dict):
        for source, targets in raw.items():
            if isinstance(targets, dict):
                for target, minutes in targets.items():
                    try:
                        value = max(0, int(minutes))
                    except (TypeError, ValueError):
                        continue
                    matrix[(str(source), str(target))] = value
                    matrix.setdefault((str(target), str(source)), value)
    return locations, matrix


def _inventory_balances(paths: WorkspacePaths) -> dict[tuple[str, str], int]:
    ledger = read_yaml(paths.root / "state" / "item-ledger.yaml", {"balances": {}})
    balances: dict[tuple[str, str], int] = {}
    raw = ledger.get("balances", {}) if isinstance(ledger, dict) else {}
    if isinstance(raw, dict):
        for char_id, items in raw.items():
            if not isinstance(items, dict):
                continue
            for item_id, quantity in items.items():
                try:
                    balances[(str(char_id), str(item_id))] = int(quantity)
                except (TypeError, ValueError):
                    pass
    return balances


def validate_workspace(paths: WorkspacePaths) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not paths.project.exists():
        issues.append(ValidationIssue("missing_project", "project.yaml is missing", "critical"))
    for relative in REQUIRED_DIRS:
        if not (paths.root / relative).is_dir():
            issues.append(ValidationIssue("missing_directory", f"Missing directory: {relative}", "high", relative))
    ids: set[str] = set()
    index = read_yaml(paths.root / "characters" / "index.yaml", {"characters": []})
    for item in _as_list(index.get("characters") if isinstance(index, dict) else []):
        if not isinstance(item, dict):
            continue
        char_id = str(item.get("id", ""))
        if not char_id:
            issues.append(ValidationIssue("character_id_missing", "Character index entry has no id", "high"))
            continue
        if char_id in ids:
            issues.append(ValidationIssue("character_id_duplicate", f"Duplicate character id: {char_id}", "critical"))
        ids.add(char_id)
        for directory, code in (("characters/profiles", "profile_missing"), ("characters/arcs", "arc_missing"), ("state/characters", "state_missing")):
            path = paths.root / directory / f"{char_id}.yaml"
            if not path.exists():
                issues.append(ValidationIssue(code, f"Missing {directory} file for {char_id}", "high", str(path)))
    if not (paths.root / "canon" / "facts.yaml").exists():
        issues.append(ValidationIssue("fact_ledger_missing", "canon/facts.yaml is missing", "high"))
    if not (paths.root / "canon" / "locations.yaml").exists():
        issues.append(ValidationIssue("location_graph_missing", "canon/locations.yaml is missing", "medium"))
    if not (paths.root / "state" / "item-ledger.yaml").exists():
        issues.append(ValidationIssue("item_ledger_missing", "state/item-ledger.yaml is missing", "high"))
    else:
        ledger = read_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}})
        item_metadata = ledger.get("items", {}) if isinstance(ledger, dict) else {}
        balances = ledger.get("balances", {}) if isinstance(ledger, dict) else {}
        if isinstance(item_metadata, dict) and isinstance(balances, dict):
            for item_id, metadata in item_metadata.items():
                if not isinstance(metadata, dict) or not metadata.get("current_holder"):
                    continue
                declared = str(metadata.get("current_holder"))
                actual: list[str] = []
                for char_id, items in balances.items():
                    if not isinstance(items, dict):
                        continue
                    try:
                        quantity = int(items.get(item_id, 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if quantity > 0:
                        actual.append(str(char_id))
                actual.sort()
                issues.append(ValidationIssue(
                    "item_static_holder_forbidden",
                    f"Item {item_id} stores current_holder={declared} in static metadata; dynamic holders must come from balances/events",
                    "high",
                    str(paths.root / "state" / "item-ledger.yaml"),
                    {"declared": declared, "balance_holders": actual},
                ))
    project = read_yaml(paths.project, {})
    if int(project.get("schema_version", 1) or 1) >= 3:
        ending_path = paths.root / "planning" / "ending" / "ending-plan.yaml"
        budget_path = paths.root / "planning" / "story-budget.yaml"
        progress_path = paths.root / "state" / "ending-progress.yaml"
        for path, code in ((ending_path, "ending_plan_missing"), (budget_path, "story_budget_missing"), (progress_path, "ending_progress_missing")):
            if not path.exists():
                issues.append(ValidationIssue(code, f"Missing {path.relative_to(paths.root)}", "high", str(path)))
        ending = read_yaml(ending_path, {})
        if ending:
            target = ending.get("target", {}) if isinstance(ending, dict) else {}
            try:
                minimum = int(target.get("min_chapter", 0) or 0)
                ideal = int(target.get("ideal_chapter", 0) or 0)
                maximum = int(target.get("max_chapter", 0) or 0)
                if not (1 <= minimum <= ideal <= maximum):
                    issues.append(ValidationIssue("ending_target_invalid", "Ending target must satisfy 1 <= min <= ideal <= max", "critical"))
                if str(ending.get("status", "unset")) != "unset" and int(project.get("planned_chapters", ideal) or ideal) != ideal:
                    issues.append(ValidationIssue("planned_chapters_ending_mismatch", "project.planned_chapters must equal ending target ideal_chapter", "high"))
            except (TypeError, ValueError):
                issues.append(ValidationIssue("ending_target_invalid", "Ending target chapters must be integers", "critical"))
    return ValidationReport(ok=not any(issue.severity in {"high", "critical"} for issue in issues), issues=issues)


def validate_delta(
    paths: WorkspacePaths,
    chapter_number: int,
    delta: dict[str, Any],
    draft_text: str | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    project = read_yaml(paths.project, {})
    policy = project.get("quality_policy", {}) if isinstance(project, dict) else {}
    max_relationship_jump = int(policy.get("max_relationship_delta_per_chapter", 15))
    allow_ability_additions = bool(policy.get("allow_unapproved_ability_additions", False))
    require_fact_ids = bool(policy.get("require_fact_ids", True))
    require_inventory_events = bool(policy.get("require_inventory_events", True))
    try:
        ending_context = ending_context_for_chapter(paths, chapter_number)
    except ValueError as exc:
        ending_context = {"configured": True, "invalid": True}
        issues.append(ValidationIssue("ending_chapter_budget_exceeded", str(exc), "critical"))

    if int(delta.get("chapter", -1)) != chapter_number:
        issues.append(ValidationIssue("chapter_mismatch", "Delta chapter number does not match", "critical"))

    valid_ids = _valid_characters(paths)
    fact_ids = _fact_ids(paths)
    existing_event_ids = _event_ids(paths)
    new_events = _as_list(delta.get("timeline_events"))
    new_event_ids = {str(event.get("id")) for event in new_events if isinstance(event, dict) and event.get("id")}
    all_event_ids = existing_event_ids | new_event_ids

    boundaries_all = read_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {}})
    knowledge_additions: dict[str, set[str]] = {}
    for char_id, update in (delta.get("character_updates") or {}).items():
        if char_id not in valid_ids:
            issues.append(ValidationIssue("unknown_character", f"Unknown character in delta: {char_id}", "critical"))
            continue
        if not isinstance(update, dict):
            issues.append(ValidationIssue("invalid_character_update", f"Update for {char_id} must be an object", "critical"))
            continue
        if any(key in update for key in ("immutable", "profile", "personality_core", "speech_style", "core_traits")):
            issues.append(ValidationIssue("immutable_mutation", f"Delta attempts to modify immutable profile for {char_id}", "critical"))
        abilities = _as_list(update.get("abilities_add"))
        if abilities and not allow_ability_additions:
            issues.append(ValidationIssue("ability_added_without_approval", f"New abilities require explicit canon approval for {char_id}: {abilities}", "critical"))
        boundary = boundaries_all.get("characters", {}).get(char_id, {}) if isinstance(boundaries_all, dict) else {}
        must_not_know = set(str(v) for v in _as_list(boundary.get("must_not_know") if isinstance(boundary, dict) else []))
        evidence = update.get("knowledge_evidence") or {}
        knowledge_additions[char_id] = {str(v) for v in _as_list(update.get("knowledge_add"))}
        for fact in knowledge_additions[char_id]:
            if require_fact_ids and fact not in fact_ids:
                issues.append(ValidationIssue("unknown_fact_id", f"{char_id} knowledge_add references unknown fact id: {fact}", "critical"))
            event_refs = _as_list(evidence.get(fact) if isinstance(evidence, dict) else [])
            if fact in must_not_know and not event_refs:
                issues.append(ValidationIssue("knowledge_reveal_without_evidence", f"{char_id} learns protected fact {fact} without evidence", "critical"))
            for event_id in event_refs:
                if str(event_id) not in all_event_ids:
                    issues.append(ValidationIssue("knowledge_unknown_evidence", f"Knowledge evidence {event_id} for {char_id}/{fact} does not exist", "high"))
        if update.get("inventory_set") and require_inventory_events:
            issues.append(ValidationIssue("inventory_set_forbidden", f"{char_id} uses legacy inventory_set; use inventory_events double-entry ledger", "high"))
        location = update.get("location") or {}
        if isinstance(location, dict):
            if location.get("time") and _parse_time(str(location.get("time"))) is None:
                issues.append(ValidationIssue("invalid_time", f"Invalid ISO time for {char_id}", "high"))
            has_location_signal = any(
                str(location.get(key, "")).strip()
                for key in ("place", "location", "time", "location_id")
            )
            if has_location_signal and not str(location.get("location_id", "")).strip():
                issues.append(ValidationIssue(
                    "character_location_id_missing",
                    f"Character update for {char_id} is missing location_id",
                    "critical",
                ))

    # Character speech attribution and fact-claim boundaries.
    for utterance in _as_list(delta.get("utterances")):
        if not isinstance(utterance, dict):
            issues.append(ValidationIssue("invalid_utterance", "Utterance entry must be an object", "high"))
            continue
        speaker = str(utterance.get("speaker", ""))
        excerpt = str(utterance.get("text_excerpt", "")).strip()
        if speaker not in valid_ids:
            issues.append(ValidationIssue("utterance_unknown_speaker", f"Unknown speaker: {speaker}", "critical"))
            continue
        if not excerpt:
            issues.append(ValidationIssue("utterance_excerpt_missing", f"Utterance for {speaker} has no excerpt", "critical"))
        elif draft_text is not None and excerpt not in draft_text:
            issues.append(ValidationIssue("utterance_excerpt_not_found", f"Attributed utterance excerpt for {speaker} is not in draft", "critical", details={"excerpt": excerpt[:160]}))
        event_refs = [str(value).strip() for value in _as_list(utterance.get("event_ids") or utterance.get("event_id")) if str(value).strip()]
        if not event_refs:
            issues.append(ValidationIssue(
                "utterance_event_id_missing",
                f"Utterance for {speaker} is missing event_ids linking it to a timeline event",
                "critical",
                details={"excerpt": excerpt[:160]},
            ))
        else:
            for event_id in event_refs:
                if event_id not in all_event_ids:
                    issues.append(ValidationIssue(
                        "utterance_unknown_event",
                        f"Utterance for {speaker} references unknown timeline event {event_id}",
                        "critical",
                        details={"excerpt": excerpt[:160]},
                    ))
        boundary = boundaries_all.get("characters", {}).get(speaker, {}) if isinstance(boundaries_all, dict) else {}
        known = set(str(v) for v in _as_list(boundary.get("knows") if isinstance(boundary, dict) else []))
        known |= knowledge_additions.get(speaker, set())
        protected = set(str(v) for v in _as_list(boundary.get("must_not_know") if isinstance(boundary, dict) else []))
        for fact_id in _as_list(utterance.get("asserted_fact_ids")):
            fact = str(fact_id)
            if require_fact_ids and fact not in fact_ids:
                issues.append(ValidationIssue("utterance_unknown_fact", f"{speaker} asserts unknown fact id {fact}", "critical"))
            if fact not in known:
                severity = "critical" if fact in protected else "high"
                issues.append(ValidationIssue("utterance_knowledge_violation", f"{speaker} asserts fact {fact} outside current knowledge boundary", severity, details={"excerpt": excerpt[:160]}))

    # Point-of-view boundary. The extractor must attribute any inner-state access.
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    expected_pov = str(outline.get("pov_character", "")) if isinstance(outline, dict) else ""
    pov_mode = str(outline.get("pov_mode", "third_limited")) if isinstance(outline, dict) else "third_limited"
    for observation in _as_list(delta.get("pov_observations")):
        if not isinstance(observation, dict):
            continue
        pov_character = str(observation.get("pov_character", expected_pov))
        inner = str(observation.get("accessed_inner_state_of", ""))
        excerpt = str(observation.get("text_excerpt", "")).strip()
        if expected_pov and pov_character and pov_character != expected_pov:
            issues.append(ValidationIssue("pov_character_drift", f"Expected POV {expected_pov}, got {pov_character}", "critical"))
        if pov_mode in {"first_person", "third_limited"} and expected_pov and inner and inner != expected_pov:
            issues.append(ValidationIssue("pov_inner_state_leak", f"Limited POV {expected_pov} accesses inner state of {inner}", "critical", details={"excerpt": excerpt[:160]}))
        if excerpt and draft_text is not None and excerpt not in draft_text:
            issues.append(ValidationIssue("pov_excerpt_not_found", "POV observation excerpt is not in draft", "high", details={"excerpt": excerpt[:160]}))

    # Relationship changes need evidence and cannot jump without it.
    for relation in _as_list(delta.get("relationship_updates")):
        if not isinstance(relation, dict):
            continue
        source, target = str(relation.get("source", "")), str(relation.get("target", ""))
        if source not in valid_ids or target not in valid_ids:
            issues.append(ValidationIssue("unknown_relationship_character", f"Unknown relation pair: {source}->{target}", "critical"))
        if source and source == target:
            issues.append(ValidationIssue("self_relationship", f"Self relationship is not allowed: {source}", "high"))
        evidence_ids = [str(v) for v in _as_list(relation.get("event_ids"))]
        for event_id in evidence_ids:
            if event_id not in all_event_ids:
                issues.append(ValidationIssue("relationship_unknown_evidence", f"Relation evidence event does not exist: {event_id}", "high"))
        for key in ("trust_delta", "conflict_delta", "intimacy_delta", "fear_delta"):
            amount = int(relation.get(key, 0) or 0)
            if abs(amount) > max_relationship_jump and not evidence_ids:
                issues.append(ValidationIssue("relationship_jump", f"{key}={amount} exceeds {max_relationship_jump} without supporting event_ids", "high"))

    # Timeline, location occupancy, and travel-time graph.
    timeline = read_yaml(paths.root / "state" / "timeline.yaml", {"events": []})
    existing_events = _as_list(timeline.get("events") if isinstance(timeline, dict) else [])
    existing_times = [_parse_time(str(event.get("time", ""))) for event in existing_events if isinstance(event, dict)]
    last_global_time = max((value for value in existing_times if value is not None), default=None)
    previous_new_time = last_global_time
    seen_event_ids = set(existing_event_ids)
    locations, travel_matrix = _location_data(paths)
    participant_last: dict[str, tuple[datetime, str]] = {}
    for char_id in valid_ids:
        state = read_yaml(paths.root / "state" / "characters" / f"{char_id}.yaml", {})
        location = state.get("location", {}) if isinstance(state, dict) else {}
        if isinstance(location, dict):
            time_value = _parse_time(str(location.get("time", "")))
            place = str(location.get("location_id") or location.get("place") or "")
            if time_value and place:
                participant_last[char_id] = (time_value, place)
    participant_slots: dict[tuple[str, str], str] = {}
    for event in new_events:
        if not isinstance(event, dict):
            issues.append(ValidationIssue("invalid_timeline_event", "Timeline event must be an object", "high"))
            continue
        event_id = str(event.get("id", ""))
        event_time = _parse_time(str(event.get("time", "")))
        location = str(event.get("location_id") or event.get("location") or "")
        if not event_id:
            issues.append(ValidationIssue("timeline_event_id_missing", "Timeline event is missing id", "critical"))
        elif event_id in seen_event_ids:
            issues.append(ValidationIssue("timeline_event_duplicate", f"Duplicate timeline event id: {event_id}", "high"))
        seen_event_ids.add(event_id)
        if event_time is None:
            issues.append(ValidationIssue("timeline_time_invalid", f"Invalid timeline time for {event_id}", "high"))
        elif previous_new_time and event_time < previous_new_time:
            issues.append(ValidationIssue("timeline_reversal", f"Timeline goes backwards at {event_id}", "critical"))
        if event_time:
            previous_new_time = event_time
        if not location:
            issues.append(ValidationIssue(
                "timeline_location_id_missing",
                f"Timeline event {event_id or '(missing id)'} is missing location_id",
                "critical",
            ))
        elif locations and location not in locations:
            issues.append(ValidationIssue("unknown_location", f"Timeline event {event_id} uses unknown location: {location}", "high"))
        for fact_id in _as_list(event.get("fact_ids")):
            if require_fact_ids and str(fact_id) not in fact_ids:
                issues.append(ValidationIssue("timeline_unknown_fact", f"Event {event_id} references unknown fact id: {fact_id}", "high"))
        for participant in _as_list(event.get("participants")):
            char_id = str(participant)
            if char_id not in valid_ids:
                issues.append(ValidationIssue("timeline_unknown_participant", f"Unknown participant {char_id}", "high"))
                continue
            slot = (char_id, str(event.get("time", "")))
            previous_location = participant_slots.get(slot)
            if previous_location and location and previous_location != location:
                issues.append(ValidationIssue("simultaneous_location_conflict", f"{char_id} appears at {previous_location} and {location} at the same time", "critical"))
            elif location:
                participant_slots[slot] = location
            if event_time and location and char_id in participant_last:
                prior_time, prior_location = participant_last[char_id]
                if prior_location != location:
                    required = travel_matrix.get((prior_location, location))
                    elapsed = (event_time - prior_time).total_seconds() / 60
                    if required is not None and elapsed < required:
                        issues.append(ValidationIssue("travel_time_violation", f"{char_id} travels {prior_location}->{location} in {elapsed:.1f} min; minimum is {required} min", "critical", details={"event_id": event_id}))
            if event_time and location:
                participant_last[char_id] = (event_time, location)

    # Ability use/learning ledger.
    for event in _as_list(delta.get("ability_events")):
        if not isinstance(event, dict):
            continue
        char_id = str(event.get("character_id", ""))
        ability_id = str(event.get("ability_id", ""))
        action = str(event.get("action", "use"))
        evidence_ids = [str(v) for v in _as_list(event.get("event_ids"))]
        if char_id not in valid_ids:
            issues.append(ValidationIssue("ability_unknown_character", f"Unknown character in ability event: {char_id}", "critical"))
            continue
        known = _known_abilities(paths, char_id)
        if action == "use" and ability_id not in known:
            issues.append(ValidationIssue("ability_use_without_ownership", f"{char_id} uses unknown ability {ability_id}", "critical"))
        elif action == "learn" and not allow_ability_additions:
            issues.append(ValidationIssue("ability_learning_requires_approval", f"Learning {ability_id} requires approved canon change", "critical"))
        for event_id in evidence_ids:
            if event_id not in all_event_ids:
                issues.append(ValidationIssue("ability_unknown_evidence", f"Ability event references unknown timeline event {event_id}", "high"))

    # Double-entry item ledger.
    item_ledger = read_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}, "events": []})
    known_items = set(str(key) for key in (item_ledger.get("items", {}) if isinstance(item_ledger, dict) else {}).keys())
    balances = _inventory_balances(paths)
    seen_inventory_events = {
        str(item.get("id")) for item in _as_list(item_ledger.get("events") if isinstance(item_ledger, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }
    for event in _as_list(delta.get("inventory_events")):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id", ""))
        item_id = str(event.get("item_id", ""))
        action = str(event.get("action", ""))
        char_id = str(event.get("character_id", ""))
        quantity = int(event.get("quantity", 1) or 1)
        target = str(event.get("target_character_id", ""))
        evidence_ids = [str(v) for v in _as_list(event.get("event_ids"))]
        if not event_id or event_id in seen_inventory_events:
            issues.append(ValidationIssue("inventory_event_duplicate", f"Missing or duplicate inventory event id: {event_id}", "high"))
        seen_inventory_events.add(event_id)
        if item_id not in known_items:
            issues.append(ValidationIssue("unknown_item_id", f"Inventory event references unknown item: {item_id}", "critical"))
        if char_id not in valid_ids:
            issues.append(ValidationIssue("inventory_unknown_character", f"Inventory event references unknown character: {char_id}", "critical"))
        if quantity < 1:
            issues.append(ValidationIssue("inventory_invalid_quantity", f"Inventory event quantity must be positive: {event_id}", "high"))
            continue
        key = (char_id, item_id)
        current = balances.get(key, 0)
        if action == "acquire":
            balances[key] = current + quantity
        elif action in {"consume", "drop", "destroy"}:
            if current < quantity:
                issues.append(ValidationIssue("inventory_negative_balance", f"{char_id} cannot {action} {quantity}x {item_id}; balance={current}", "critical"))
            balances[key] = current - quantity
        elif action == "transfer":
            if target not in valid_ids:
                issues.append(ValidationIssue("inventory_transfer_target_invalid", f"Invalid transfer target: {target}", "critical"))
            if current < quantity:
                issues.append(ValidationIssue("inventory_negative_balance", f"{char_id} cannot transfer {quantity}x {item_id}; balance={current}", "critical"))
            balances[key] = current - quantity
            balances[(target, item_id)] = balances.get((target, item_id), 0) + quantity
        elif action in {"damage", "repair"}:
            if current < 1:
                issues.append(ValidationIssue("inventory_item_not_owned", f"{char_id} cannot {action} unowned item {item_id}", "high"))
        else:
            issues.append(ValidationIssue("inventory_unknown_action", f"Unknown inventory action: {action}", "high"))
        for evidence_id in evidence_ids:
            if evidence_id not in all_event_ids:
                issues.append(ValidationIssue("inventory_unknown_evidence", f"Inventory event references unknown timeline event {evidence_id}", "high"))

    # Plot/foreshadowing evidence must point to actual events.
    for update in _as_list(delta.get("plot_updates")) + _as_list(delta.get("foreshadowing_updates")):
        if not isinstance(update, dict):
            continue
        for event_id in _as_list(update.get("event_ids")):
            if str(event_id) not in all_event_ids:
                issues.append(ValidationIssue("narrative_update_unknown_evidence", f"Narrative update references unknown event {event_id}", "high"))

    # Foreshadowing deadlines cannot silently expire.
    foreshadowing = read_yaml(paths.root / "state" / "foreshadowing.yaml", {"items": []})
    updates_by_id = {str(item.get("id")): item for item in _as_list(delta.get("foreshadowing_updates")) if isinstance(item, dict) and item.get("id")}
    for item in _as_list(foreshadowing.get("items") if isinstance(foreshadowing, dict) else []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if str(item.get("status", "active")) in {"resolved", "paid_off", "cancelled"}:
            continue
        deadline = item.get("deadline_chapter")
        try:
            deadline_number = int(deadline)
        except (TypeError, ValueError):
            continue
        if chapter_number >= deadline_number:
            update = updates_by_id.get(str(item["id"]), {})
            if str(update.get("action", "")) not in {"payoff", "resolve", "extend"}:
                issues.append(ValidationIssue("foreshadowing_deadline_missed", f"Foreshadowing {item['id']} reached chapter {deadline_number} without payoff/resolve/extend", "high"))

    # Ending/story-budget gates. Final-arc chapters may resolve existing lines but may not silently open new long lines.
    ending_plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    if ending_context.get("configured") and not ending_context.get("invalid"):
        plot_state = read_yaml(paths.root / "state" / "plot-ledger.yaml", {"threads": []})
        clue_state = read_yaml(paths.root / "state" / "foreshadowing.yaml", {"items": []})
        existing_thread_ids = {
            str(item.get("id")) for item in _as_list(plot_state.get("threads") if isinstance(plot_state, dict) else [])
            if isinstance(item, dict) and item.get("id")
        }
        existing_clue_ids = {
            str(item.get("id")) for item in _as_list(clue_state.get("items") if isinstance(clue_state, dict) else [])
            if isinstance(item, dict) and item.get("id")
        }
        if ending_context.get("is_final_arc"):
            if bool((ending_context.get("policy") or {}).get("block_new_plot_threads_in_final_arc", True)):
                for update in _as_list(delta.get("plot_updates")):
                    if isinstance(update, dict) and update.get("thread_id") and str(update.get("thread_id")) not in existing_thread_ids:
                        issues.append(ValidationIssue("ending_new_thread_forbidden", f"Final arc cannot introduce new plot thread {update.get('thread_id')}", "critical"))
            if bool((ending_context.get("policy") or {}).get("block_new_foreshadowing_in_final_arc", True)):
                for update in _as_list(delta.get("foreshadowing_updates")):
                    if not isinstance(update, dict):
                        continue
                    clue_id = str(update.get("id", ""))
                    if clue_id and clue_id not in existing_clue_ids and str(update.get("action", "introduce")) == "introduce":
                        issues.append(ValidationIssue("ending_new_foreshadowing_forbidden", f"Final arc cannot introduce new foreshadowing {clue_id}", "critical"))

        required_outcomes = {}
        for bucket in ("required_outcomes", "relationship_outcomes"):
            for item in _as_list(ending_plan.get(bucket) if isinstance(ending_plan, dict) else []):
                if isinstance(item, dict) and item.get("id"):
                    required_outcomes[str(item["id"])] = item
        for update in _as_list(delta.get("ending_outcome_updates")):
            if not isinstance(update, dict):
                issues.append(ValidationIssue("ending_outcome_update_invalid", "Ending outcome update must be an object", "high"))
                continue
            outcome_id = str(update.get("id", ""))
            if outcome_id not in required_outcomes:
                issues.append(ValidationIssue("ending_outcome_unknown", f"Unknown required ending outcome: {outcome_id}", "critical"))
            event_refs = [str(value) for value in _as_list(update.get("event_ids"))]
            status = str(update.get("status", "complete")).lower()
            if status in {"complete", "completed", "resolved", "done"} and not event_refs:
                issues.append(ValidationIssue("ending_outcome_evidence_required", f"Completed ending outcome {outcome_id} requires evidence from a timeline event in this chapter", "critical"))
            for event_id in event_refs:
                if event_id not in all_event_ids:
                    issues.append(ValidationIssue("ending_outcome_unknown_evidence", f"Ending outcome {outcome_id} references unknown event {event_id}", "high"))
                elif status in {"complete", "completed", "resolved", "done"} and event_id not in new_event_ids:
                    issues.append(ValidationIssue("ending_outcome_stale_evidence", f"Completed ending outcome {outcome_id} must reference a timeline event created in chapter {chapter_number}: {event_id}", "high"))

        # Simulate post-chapter narrative ledgers so the max chapter cannot be committed with required work still open.
        prospective_plot = {"threads": [dict(item) for item in _as_list(plot_state.get("threads") if isinstance(plot_state, dict) else []) if isinstance(item, dict)]}
        for update in _as_list(delta.get("plot_updates")):
            if not isinstance(update, dict) or not update.get("thread_id"):
                continue
            thread_id = str(update["thread_id"])
            target = next((item for item in prospective_plot["threads"] if str(item.get("id")) == thread_id), None)
            if target is None:
                target = {"id": thread_id}
                prospective_plot["threads"].append(target)
            target.update({key: value for key, value in update.items() if key != "thread_id"})
        prospective_clues = {"items": [dict(item) for item in _as_list(clue_state.get("items") if isinstance(clue_state, dict) else []) if isinstance(item, dict)]}
        for update in _as_list(delta.get("foreshadowing_updates")):
            if not isinstance(update, dict) or not update.get("id"):
                continue
            clue_id = str(update["id"])
            target = next((item for item in prospective_clues["items"] if str(item.get("id")) == clue_id), None)
            if target is None:
                target = {"id": clue_id}
                prospective_clues["items"].append(target)
            target.update(update)
        prospective_plan = dict(ending_plan) if isinstance(ending_plan, dict) else {}
        for bucket in ("required_outcomes", "relationship_outcomes"):
            prospective_plan[bucket] = [dict(item) if isinstance(item, dict) else item for item in _as_list(ending_plan.get(bucket) if isinstance(ending_plan, dict) else [])]
        for update in _as_list(delta.get("ending_outcome_updates")):
            if not isinstance(update, dict):
                continue
            for bucket in ("required_outcomes", "relationship_outcomes"):
                for item in prospective_plan[bucket]:
                    if isinstance(item, dict) and str(item.get("id")) == str(update.get("id")):
                        item["status"] = str(update.get("status", "complete"))
                        item["event_ids"] = list(_as_list(update.get("event_ids")))
        prospective_project = dict(project)
        prospective_project["last_committed_chapter"] = max(int(project.get("last_committed_chapter", 0) or 0), chapter_number)
        ending_progress = ending_progress_from_data(prospective_project, prospective_plan, prospective_plot, prospective_clues)
        maximum = int((ending_plan.get("target") or {}).get("max_chapter", 0) or 0) if isinstance(ending_plan, dict) else 0
        if maximum and chapter_number >= maximum and not ending_progress.get("ready_to_finalize"):
            issues.append(ValidationIssue(
                "ending_max_chapter_unresolved",
                f"Chapter {chapter_number} is the ending max but unresolved ending work remains: {', '.join(ending_progress.get('blockers', []))}",
                "critical",
                details=ending_progress,
            ))

    canon_requests = _as_list(delta.get("canon_change_requests"))
    if canon_requests:
        issues.append(ValidationIssue("canon_change_pending", "Delta contains canon change requests; approve them manually before commit", "critical", details={"requests": canon_requests}))

    issues.extend(validate_delta_coverage(paths, chapter_number, delta))

    return ValidationReport(ok=not any(issue.severity in {"high", "critical"} for issue in issues), issues=issues)


def validate_chapter_ready(paths: WorkspacePaths, chapter_number: int) -> ValidationReport:
    issues: list[ValidationIssue] = []
    packet = paths.chapter_file(chapter_number, "packet")
    draft = paths.chapter_file(chapter_number, "draft")
    audit_path = paths.chapter_file(chapter_number, "audit")
    delta_path = paths.chapter_file(chapter_number, "delta")
    for path, code in ((packet, "packet_missing"), (draft, "draft_missing"), (audit_path, "audit_missing"), (delta_path, "delta_missing")):
        if not path.exists():
            issues.append(ValidationIssue(code, f"Missing {path.name}", "critical", str(path)))
    if audit_path.exists():
        audit = read_json(audit_path, {})
        if not bool(audit.get("approved")):
            issues.append(ValidationIssue("audit_not_approved", "Chapter audit has not approved the draft", "critical"))
        issues.extend(validate_reaction_logic_audit(audit))
        issues.extend(validate_narrative_orientation_audit(audit))
        issues.extend(validate_narrative_timing_audit(audit))
        issues.extend(validate_semantic_quality_audits(audit))
        blocking = [
            item for item in _as_list(audit.get("issues"))
            if isinstance(item, dict)
            and (
                item.get("severity") in {"high", "critical"}
                or item.get("type") in {"rule_leak", "profile_literalization", "character_entry", "narrative_orientation", "future_plan_leak"}
            )
        ]
        if blocking:
            issues.append(ValidationIssue("blocking_audit_issues", "Audit still contains blocking issues", "critical", details={"issues": blocking}))
    if delta_path.exists():
        report = validate_delta(paths, chapter_number, read_json(delta_path, {}), read_text(draft))
        issues.extend(report.issues)
    if draft.exists():
        issues.extend(validate_draft_contract(paths, chapter_number, read_text(draft)))
    issues.extend(validate_writing_report_for_commit(paths, chapter_number).issues)
    return ValidationReport(ok=not any(issue.severity in {"high", "critical"} for issue in issues), issues=issues)
