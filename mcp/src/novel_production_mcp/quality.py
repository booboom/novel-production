from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from .models import ValidationIssue, WorkspacePaths
from .planning_terminology import (
    detect_planning_term_literalization,
    reader_visible_terms,
)
from .storage import read_json, read_text, read_yaml


REQUIRED_SEMANTIC_AUDITS = (
    "knowledge_provenance_audit",
    "authorization_audit",
    "outline_fidelity_audit",
    "reasoning_chain_audit",
    "state_coverage_audit",
    "repetition_audit",
    "planning_language_audit",
)

RHYTHM_FIELDS = (
    "opening_mode",
    "core_action",
    "evidence_type",
    "emotional_turn",
    "ending_hook_type",
)

TITLE_REPETITION_MIN_FRAGMENT = 3

WRITER_OUTLINE_FIELDS = (
    "number",
    "title",
    "mini_arc",
    "chapter_task",
    "reader_orientation",
    "narrative_timing",
    "mission",
    "participants",
    "must_happen",
    "must_not_happen",
    "threads_advanced",
    "foreshadowing_actions",
    "target_chars",
    "start_time",
    "end_time",
    "location_ids",
    "pov_character",
    "pov_mode",
    "restricted_actions",
    "rhythm_signature",
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confidence(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_restricted_actions(chapter: Mapping[str, Any], chapter_number: int) -> list[dict[str, str]]:
    raw = chapter.get("restricted_actions")
    if not isinstance(raw, list):
        raise ValueError(f"Chapter {chapter_number} restricted_actions must be a list")
    normalized: list[dict[str, str]] = []
    required = ("action", "actor", "resource", "authority_basis", "establish_before")
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"Chapter {chapter_number} restricted_actions[{index}] must be a mapping")
        missing = [field for field in required if not _text(item.get(field))]
        if missing:
            raise ValueError(
                f"Chapter {chapter_number} restricted_actions[{index}] requires non-empty fields: "
                + ", ".join(missing)
            )
        normalized.append({field: _text(item[field]) for field in required})
    return normalized


def normalize_rhythm_signature(chapter: Mapping[str, Any], chapter_number: int) -> dict[str, str]:
    raw = chapter.get("rhythm_signature")
    if not isinstance(raw, Mapping):
        raise ValueError(f"Chapter {chapter_number} must contain a rhythm_signature mapping")
    missing = [field for field in RHYTHM_FIELDS if not _text(raw.get(field))]
    if missing:
        raise ValueError(
            f"Chapter {chapter_number} rhythm_signature requires non-empty fields: "
            + ", ".join(missing)
        )
    return {field: _text(raw[field]) for field in RHYTHM_FIELDS}


def _normalized_title(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _text(value).lower(), flags=re.UNICODE)


def _title_edge_fragments(value: str) -> set[str]:
    maximum = min(len(value), 12)
    fragments: set[str] = set()
    for size in range(TITLE_REPETITION_MIN_FRAGMENT, maximum + 1):
        fragments.add(value[:size])
        fragments.add(value[-size:])
    return fragments


def validate_outline_title_contract(
    chapters: Iterable[Mapping[str, Any]],
    *,
    repetition_window: int = 5,
    enforce_from_chapter: int | None = None,
) -> None:
    """Reject reader-facing title templates while allowing a repeated planning mini-arc."""
    ordered = sorted(chapters, key=lambda item: int(item.get("number", 0) or 0))
    for index, chapter in enumerate(ordered):
        number = int(chapter.get("number", 0) or 0)
        if enforce_from_chapter is not None and number < enforce_from_chapter:
            continue
        title = _normalized_title(chapter.get("title"))
        if not title:
            continue
        previous = ordered[max(0, index - repetition_window):index]
        previous_titles = [
            (int(item.get("number", 0) or 0), _normalized_title(item.get("title")), item)
            for item in previous
            if _normalized_title(item.get("title"))
        ]
        duplicate = next((prior_number for prior_number, prior_title, _ in previous_titles if prior_title == title), None)
        if duplicate is not None:
            raise ValueError(
                f"Chapter {number} repeats the reader-facing title of chapter {duplicate}; "
                "title must use a chapter-specific action, choice, conflict, result, anomaly, or image"
            )

        mini_arc = _normalized_title(chapter.get("mini_arc"))
        if len(mini_arc) >= TITLE_REPETITION_MIN_FRAGMENT and mini_arc in title:
            mini_arc_hits = [
                prior_number
                for prior_number, prior_title, prior in previous_titles
                if _normalized_title(prior.get("mini_arc")) == mini_arc and mini_arc in prior_title
            ]
            if len(mini_arc_hits) >= 2:
                raise ValueError(
                    f"Chapter {number} reuses mini_arc '{_text(chapter.get('mini_arc'))}' as a title template "
                    f"with chapters {mini_arc_hits[-2]} and {mini_arc_hits[-1]}; keep mini_arc as planning metadata"
                )

        current_fragments = _title_edge_fragments(title)
        repeated: list[tuple[str, list[int]]] = []
        for fragment in current_fragments:
            hits = [
                prior_number
                for prior_number, prior_title, _ in previous_titles
                if fragment in _title_edge_fragments(prior_title)
            ]
            if len(hits) >= 2:
                repeated.append((fragment, hits))
        if repeated:
            fragment, hits = max(repeated, key=lambda item: (len(item[0]), item[0]))
            raise ValueError(
                f"Chapter {number} repeats title edge fragment '{fragment}' with chapters "
                f"{hits[-2]} and {hits[-1]} within the {repetition_window}-chapter window"
            )


def validate_outline_plan_contract(
    chapters: Iterable[Mapping[str, Any]],
    *,
    repetition_window: int = 5,
    title_enforcement_start: int | None = None,
) -> None:
    ordered = sorted(chapters, key=lambda item: int(item.get("number", 0) or 0))
    validate_outline_title_contract(
        ordered,
        repetition_window=repetition_window,
        enforce_from_chapter=title_enforcement_start,
    )
    introduced: dict[str, int] = {}
    for chapter in ordered:
        number = int(chapter.get("number", 0) or 0)
        timing = chapter.get("narrative_timing")
        introductions = timing.get("introduce_this_chapter") if isinstance(timing, Mapping) else []
        for item in _as_list(introductions):
            if not isinstance(item, Mapping):
                continue
            element_id = _text(item.get("element_id"))
            if element_id in introduced:
                raise ValueError(
                    f"Narrative element {element_id} is introduced in both chapter "
                    f"{introduced[element_id]} and chapter {number}"
                )
            introduced[element_id] = number

    for index, chapter in enumerate(ordered):
        number = int(chapter.get("number", 0) or 0)
        signature = chapter.get("rhythm_signature")
        if not isinstance(signature, Mapping) or any(not _text(signature.get(field)) for field in RHYTHM_FIELDS):
            continue
        previous = ordered[max(0, index - repetition_window):index]
        for prior in previous:
            prior_signature = prior.get("rhythm_signature")
            if not isinstance(prior_signature, Mapping) or any(
                not _text(prior_signature.get(field)) for field in RHYTHM_FIELDS
            ):
                continue
            identical = [
                field for field in RHYTHM_FIELDS
                if _text(signature.get(field)) == _text(prior_signature.get(field))
            ]
            if len(identical) == len(RHYTHM_FIELDS):
                raise ValueError(
                    f"Chapter {number} repeats the complete rhythm signature of chapter "
                    f"{int(prior.get('number', 0) or 0)}"
                )
            if "ending_hook_type" in identical and "core_action" in identical and "evidence_type" in identical:
                raise ValueError(
                    f"Chapter {number} repeats core_action/evidence_type/ending_hook_type from chapter "
                    f"{int(prior.get('number', 0) or 0)} within the {repetition_window}-chapter window"
                )


def writer_safe_outline(outline: Mapping[str, Any]) -> dict[str, Any]:
    """Remove planner-only/future scaffolding before the outline reaches Writer."""
    return {field: outline[field] for field in WRITER_OUTLINE_FIELDS if field in outline}


def writer_safe_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Expose behavior and approved abilities, not untagged private biography."""
    public = profile.get("public_profile")
    if isinstance(public, Mapping):
        result = dict(public)
        result.setdefault("id", profile.get("id"))
        result.setdefault("name", profile.get("name"))
        result.setdefault("role", profile.get("role"))
    else:
        result = {
            field: profile[field]
            for field in ("id", "name", "role", "personality", "speech", "abilities")
            if field in profile
        }
    return result


def writer_safe_facts(
    paths: WorkspacePaths,
    participants: Iterable[str],
    *,
    ledger_name: str = "facts.yaml",
) -> dict[str, Any]:
    facts = read_yaml(paths.root / "canon" / ledger_name, {"facts": []})
    entries = _as_list(facts.get("facts") if isinstance(facts, Mapping) else [])
    boundaries = read_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {}})
    allowed: set[str] = set()
    by_character = boundaries.get("characters", {}) if isinstance(boundaries, Mapping) else {}
    for character_id in participants:
        boundary = by_character.get(character_id, {}) if isinstance(by_character, Mapping) else {}
        if isinstance(boundary, Mapping):
            allowed.update(_text(value) for value in _as_list(boundary.get("knows")))
            allowed.update(_text(value) for value in _as_list(boundary.get("suspects")))
    safe: list[Any] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        visibility = _text(item.get("visibility")).lower()
        if bool(item.get("public")) or visibility in {"public", "world", "common"} or _text(item.get("id")) in allowed:
            safe.append(dict(item))
    return {"facts": safe}


def writer_safe_arc(arc: Mapping[str, Any]) -> dict[str, Any]:
    """Keep achieved/current arc state while withholding future milestones."""
    safe_fields = (
        "character_id",
        "current_phase",
        "current_stage",
        "completed_milestones",
        "history",
        "trajectory_so_far",
    )
    return {field: arc[field] for field in safe_fields if field in arc}


def reviewer_private_context(paths: WorkspacePaths, participants: Iterable[str]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for character_id in participants:
        profiles[character_id] = read_yaml(
            paths.root / "characters" / "profiles" / f"{character_id}.yaml",
            {},
        )
    return {
        "full_profiles": profiles,
        "full_fact_ledger": read_yaml(paths.root / "canon" / "facts.yaml", {"facts": []}),
        "full_immutable_fact_ledger": read_yaml(paths.root / "canon" / "immutable-facts.yaml", {"facts": []}),
        "knowledge_boundaries": read_yaml(
            paths.root / "characters" / "knowledge-boundaries.yaml",
            {"characters": {}},
        ),
    }


def _require_audit_object(audit: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any] | None, list[ValidationIssue]]:
    value = audit.get(name)
    if not isinstance(value, Mapping):
        return None, [ValidationIssue(f"{name}_missing", f"Chapter audit must contain {name}", "critical")]
    if not isinstance(value.get("summary"), str) or not _text(value.get("summary")):
        return None, [ValidationIssue(f"{name}_malformed", f"{name} requires a non-empty summary", "critical")]
    return value, []


def validate_semantic_quality_audits(audit: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    values: dict[str, Mapping[str, Any]] = {}
    for name in REQUIRED_SEMANTIC_AUDITS:
        value, malformed = _require_audit_object(audit, name)
        issues.extend(malformed)
        if value is not None:
            values[name] = value

    knowledge = values.get("knowledge_provenance_audit")
    if knowledge is not None:
        claims = knowledge.get("claims")
        if not isinstance(knowledge.get("complete"), bool) or not isinstance(claims, list):
            issues.append(ValidationIssue("knowledge_provenance_audit_malformed", "knowledge_provenance_audit requires boolean complete and claims[]", "critical"))
        else:
            if not knowledge.get("complete"):
                issues.append(ValidationIssue("knowledge_provenance_incomplete", "Reviewer did not complete knowledge provenance coverage", "critical"))
            for claim in claims:
                if not isinstance(claim, Mapping):
                    issues.append(ValidationIssue("knowledge_provenance_audit_malformed", "Knowledge claim must be an object", "critical"))
                    continue
                authorized = claim.get("authorized")
                sensitive = bool(claim.get("sensitive", False))
                source_id = _text(claim.get("source_id"))
                if not isinstance(authorized, bool):
                    issues.append(ValidationIssue("knowledge_provenance_audit_malformed", "Knowledge claim requires boolean authorized", "critical"))
                elif not authorized or (sensitive and not source_id):
                    issues.append(ValidationIssue(
                        "knowledge_source_missing",
                        "A character uses private or unsupported knowledge without a traceable source",
                        "high",
                        _text(claim.get("location")) or None,
                        dict(claim),
                    ))

    authorization = values.get("authorization_audit")
    if authorization is not None:
        actions = authorization.get("actions")
        if not isinstance(authorization.get("complete"), bool) or not isinstance(actions, list):
            issues.append(ValidationIssue("authorization_audit_malformed", "authorization_audit requires boolean complete and actions[]", "critical"))
        else:
            if not authorization.get("complete"):
                issues.append(ValidationIssue("authorization_audit_incomplete", "Reviewer did not complete restricted-action coverage", "critical"))
            for action in actions:
                if not isinstance(action, Mapping) or not isinstance(action.get("authorized"), bool):
                    issues.append(ValidationIssue("authorization_audit_malformed", "Authorization action requires boolean authorized", "critical"))
                elif not action.get("authorized"):
                    issues.append(ValidationIssue("authorization_gap", "Restricted action lacks established authority or access basis", "high", _text(action.get("location")) or None, dict(action)))

    fidelity = values.get("outline_fidelity_audit")
    if fidelity is not None:
        mismatches = fidelity.get("mismatches")
        if not isinstance(fidelity.get("matches"), bool) or not isinstance(mismatches, list):
            issues.append(ValidationIssue("outline_fidelity_audit_malformed", "outline_fidelity_audit requires boolean matches and mismatches[]", "critical"))
        elif not fidelity.get("matches") or mismatches:
            issues.append(ValidationIssue("outline_fidelity", "Draft does not satisfy the approved chapter outline", "high", details={"mismatches": mismatches}))

    reasoning = values.get("reasoning_chain_audit")
    if reasoning is not None:
        chains = reasoning.get("chains")
        if not isinstance(reasoning.get("valid"), bool) or not isinstance(chains, list):
            issues.append(ValidationIssue("reasoning_chain_audit_malformed", "reasoning_chain_audit requires boolean valid and chains[]", "critical"))
        else:
            invalid = [dict(item) for item in chains if isinstance(item, Mapping) and item.get("valid") is False]
            if not reasoning.get("valid") or invalid:
                issues.append(ValidationIssue("reasoning_gap", "Draft contains an unsupported or type-incompatible inference", "high", details={"chains": invalid}))

    coverage = values.get("state_coverage_audit")
    if coverage is not None:
        changes = coverage.get("changes")
        if not isinstance(coverage.get("complete"), bool) or not isinstance(changes, list):
            issues.append(ValidationIssue("state_coverage_audit_malformed", "state_coverage_audit requires boolean complete and changes[]", "critical"))
        elif not coverage.get("complete"):
            issues.append(ValidationIssue("state_coverage_incomplete", "Reviewer did not complete state-change coverage", "critical"))

    repetition = values.get("repetition_audit")
    if repetition is not None:
        repetitions = repetition.get("repetitions")
        if not isinstance(repetition.get("distinct"), bool) or not isinstance(repetitions, list):
            issues.append(ValidationIssue("repetition_audit_malformed", "repetition_audit requires boolean distinct and repetitions[]", "critical"))
        else:
            blocking = [dict(item) for item in repetitions if isinstance(item, Mapping) and not bool(item.get("intentional", False))]
            if not repetition.get("distinct") or blocking:
                issues.append(ValidationIssue("chapter_repetition", "Chapter repeats a recent scene/evidence/hook pattern without intentional escalation", "high", details={"repetitions": blocking}))

    planning_language = values.get("planning_language_audit")
    if planning_language is not None:
        occurrences = planning_language.get("occurrences")
        if not isinstance(planning_language.get("naturalized"), bool) or not isinstance(occurrences, list):
            issues.append(ValidationIssue(
                "planning_language_audit_malformed",
                "planning_language_audit requires boolean naturalized and occurrences[]",
                "critical",
            ))
        else:
            supported = [
                dict(item)
                for item in occurrences
                if isinstance(item, Mapping)
                and isinstance(item.get("locations"), list)
                and bool(item.get("locations"))
                and _confidence(item.get("confidence")) >= 0.75
            ]
            blocking = [item for item in supported if bool(item.get("blocking", False))]
            if blocking:
                issues.append(ValidationIssue(
                    "planning_term_literalization",
                    "Draft exposes planning terminology instead of naturalizing it into story action",
                    "high",
                    details={"occurrences": blocking},
                ))
            elif not planning_language.get("naturalized") or occurrences:
                issues.append(ValidationIssue(
                    "planning_term_literalization",
                    "Planning terminology may not be fully naturalized, but blocking evidence is insufficient",
                    "medium",
                    details={"occurrences": supported or occurrences},
                ))
    return issues


def apply_semantic_quality_gate(audit: dict[str, Any]) -> dict[str, Any]:
    existing = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    signatures = {(str(item.get("type", "")), str(item.get("location", ""))) for item in existing if isinstance(item, Mapping)}
    for issue in validate_semantic_quality_audits(audit):
        signature = (issue.code, str(issue.path or issue.code))
        if signature in signatures:
            continue
        existing.append({
            "type": issue.code,
            "severity": issue.severity,
            "location": issue.path or issue.code,
            "evidence": issue.message,
            "fix": str(issue.details.get(
                "repair_instruction",
                "Repair the cited gap using only established chapter and canon evidence",
            )),
            "details": issue.details,
        })
        signatures.add(signature)
    audit["issues"] = existing
    if any(item.get("severity") in {"high", "critical"} for item in existing if isinstance(item, Mapping)):
        audit["approved"] = False
    return audit


def effective_character_count(text: str) -> int:
    lines = text.lstrip("\ufeff\r\n").splitlines()
    if lines and re.match(r"^\s*(?:#\s*)?第\s*\d+\s*章\b", lines[0]):
        lines = lines[1:]
    return len(re.sub(r"\s+", "", "\n".join(lines)))


def validate_draft_contract(paths: WorkspacePaths, chapter_number: int, draft_text: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    project = read_yaml(paths.project, {})
    policy = project.get("quality_policy", {}) if isinstance(project, Mapping) else {}
    target = int(outline.get("target_chars", 0) or 0) if isinstance(outline, Mapping) else 0
    ratio = float(policy.get("min_chapter_length_ratio", 0.85) or 0.85)
    count = effective_character_count(draft_text)
    if target > 0 and count < int(target * ratio):
        issues.append(ValidationIssue(
            "chapter_underlength",
            f"Chapter has {count} effective characters; requires at least {int(target * ratio)} for target_chars={target}",
            "high",
            details={"actual": count, "target": target, "min_ratio": ratio},
        ))

    terminology_sources: list[Any] = [outline]
    for participant in _as_list(outline.get("participants") if isinstance(outline, Mapping) else []):
        profile = read_yaml(paths.root / "characters" / "profiles" / f"{participant}.yaml", {})
        terminology_sources.append(profile)
        for field in ("personality", "speech"):
            for source in _scalar_strings(profile.get(field) if isinstance(profile, Mapping) else None):
                normalized_source = _normalize_prose(source)
                if len(normalized_source) < 8:
                    continue
                for sentence in re.split(r"[。！？!?\n]", draft_text):
                    normalized_sentence = _normalize_prose(sentence)
                    if len(normalized_sentence) < 8:
                        continue
                    match = SequenceMatcher(None, normalized_source, normalized_sentence)
                    if match.ratio() >= 0.82 and match.find_longest_match().size >= 8:
                        issues.append(ValidationIssue(
                            "profile_literalization_overlap",
                            f"Draft closely reproduces {participant}.{field} instead of dramatizing it",
                            "high",
                            sentence.strip()[:160] or None,
                            {"profile_text": source[:160]},
                        ))
                        break
    issues.extend(detect_planning_term_literalization(
        draft_text,
        *terminology_sources,
        allowed_terms=reader_visible_terms(
            outline if isinstance(outline, Mapping) else {},
            read_yaml(paths.root / "canon" / "world.yaml", {}),
            read_yaml(paths.root / "canon" / "facts.yaml", {}),
            read_yaml(paths.root / "canon" / "immutable-facts.yaml", {}),
            require_established=True,
        ),
    ))
    return issues


def _scalar_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _scalar_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalar_strings(item)


def _normalize_prose(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value).lower()


def validate_delta_coverage(paths: WorkspacePaths, chapter_number: int, delta: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    audit = read_json(paths.chapter_file(chapter_number, "audit"), {})
    coverage_audit = audit.get("state_coverage_audit") if isinstance(audit, Mapping) else None
    required_ids = {
        _text(item.get("change_id"))
        for item in _as_list(coverage_audit.get("changes") if isinstance(coverage_audit, Mapping) else [])
        if isinstance(item, Mapping) and bool(item.get("requires_delta", True)) and _text(item.get("change_id"))
    }
    coverage = delta.get("state_coverage")
    if not isinstance(coverage, Mapping):
        return [ValidationIssue("delta_state_coverage_missing", "Delta must contain state_coverage", "critical")]
    detected = coverage.get("detected_changes")
    representations = coverage.get("representations")
    untracked = coverage.get("untracked_changes")
    if not isinstance(detected, list) or not isinstance(representations, list) or not isinstance(untracked, list):
        return [ValidationIssue("delta_state_coverage_malformed", "state_coverage requires detected_changes, representations and untracked_changes arrays", "critical")]
    if untracked:
        issues.append(ValidationIssue("state_change_untracked", "Extractor found state changes that are not represented in Delta", "critical", details={"changes": untracked}))

    represented_ids = {_text(item.get("change_id")) for item in representations if isinstance(item, Mapping)}
    detected_ids = {_text(item.get("change_id")) for item in detected if isinstance(item, Mapping)}
    missing = sorted((required_ids | detected_ids) - represented_ids)
    if missing:
        issues.append(ValidationIssue("state_change_unrepresented", f"State changes are missing Delta representations: {', '.join(missing)}", "critical", details={"change_ids": missing}))

    section_ids = {
        "character_updates": {_text(key) for key in (delta.get("character_updates") or {}).keys()} if isinstance(delta.get("character_updates"), Mapping) else set(),
        "timeline_events": {_text(item.get("id")) for item in _as_list(delta.get("timeline_events")) if isinstance(item, Mapping)},
        "inventory_events": {_text(item.get("id")) for item in _as_list(delta.get("inventory_events")) if isinstance(item, Mapping)},
        "relationship_updates": {_text(item.get("id")) for item in _as_list(delta.get("relationship_updates")) if isinstance(item, Mapping)},
        "ability_events": {_text(item.get("id")) for item in _as_list(delta.get("ability_events")) if isinstance(item, Mapping)},
        "plot_updates": {_text(item.get("id") or item.get("thread_id")) for item in _as_list(delta.get("plot_updates")) if isinstance(item, Mapping)},
        "foreshadowing_updates": {_text(item.get("id")) for item in _as_list(delta.get("foreshadowing_updates")) if isinstance(item, Mapping)},
    }
    for representation in representations:
        if not isinstance(representation, Mapping):
            issues.append(ValidationIssue("delta_state_coverage_malformed", "State representation must be an object", "critical"))
            continue
        section = _text(representation.get("delta_section"))
        delta_id = _text(representation.get("delta_id"))
        if section not in section_ids or not delta_id or delta_id not in section_ids[section]:
            issues.append(ValidationIssue(
                "state_representation_invalid",
                f"State coverage points to missing {section}:{delta_id}",
                "critical",
                details=dict(representation),
            ))

    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    expected_locations = {_text(value) for value in _as_list(outline.get("location_ids") if isinstance(outline, Mapping) else [])}
    actual_locations = {
        _text(item.get("location_id") or item.get("location"))
        for item in _as_list(delta.get("timeline_events"))
        if isinstance(item, Mapping) and _text(item.get("location_id") or item.get("location"))
    }
    outside = sorted(actual_locations - expected_locations) if expected_locations else []
    if outside:
        issues.append(ValidationIssue(
            "outline_location_mismatch",
            f"Delta uses locations outside approved outline: {', '.join(outside)}",
            "critical",
            details={"expected": sorted(expected_locations), "actual": sorted(actual_locations)},
        ))
    missing_outline_locations = sorted(expected_locations - actual_locations) if expected_locations else []
    if missing_outline_locations and _as_list(delta.get("timeline_events")):
        issues.append(ValidationIssue(
            "outline_location_unrepresented",
            f"Outline locations are missing from delta timeline_events: {', '.join(missing_outline_locations)}",
            "high",
            details={"expected": sorted(expected_locations), "actual": sorted(actual_locations), "missing": missing_outline_locations},
        ))
    # Surface bare timeline rows that still lack stable ids so commit/preflight
    # can report them beside state_coverage gaps.
    for index, event in enumerate(_as_list(delta.get("timeline_events"))):
        if not isinstance(event, Mapping):
            continue
        if not _text(event.get("id")):
            issues.append(ValidationIssue(
                "timeline_event_id_missing",
                f"timeline_events[{index}] is missing id",
                "critical",
            ))
        if not _text(event.get("location_id") or event.get("location")):
            issues.append(ValidationIssue(
                "timeline_location_id_missing",
                f"timeline_events[{index}] is missing location_id",
                "critical",
            ))
    return issues
