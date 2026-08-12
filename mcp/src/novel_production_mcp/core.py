from __future__ import annotations

import copy
import importlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from .event_store import (
    commit_projection_event,
    init_event_store,
    list_events,
    projection_status,
    rebuild_projections,
    seed_existing_workspace,
)
from .agent_mode import AgentTaskPending, create_agent_task, current_agent_submission
from .ending import (
    apply_ending_plan as _apply_ending_plan,
    check_ending_progress as _check_ending_progress,
    ending_context_for_chapter,
    ending_progress_from_data,
    enter_final_arc as _enter_final_arc,
    finalize_novel as _finalize_novel,
    generate_ending_options as _generate_ending_options,
    make_ending_plan,
    parse_chapter_range,
    rebalance_story_budget as _rebalance_story_budget,
    set_ending_target as _set_ending_target,
    validate_outline_coverage,
    validate_volume_coverage,
)
from .models import ModelResult, ProgressCallback, WorkspacePaths
from .narrative_timing import (
    build_narrative_timing_review_context,
    normalize_narrative_timing,
)
from .observability import observability_report, record_llm_call
from .orientation import normalize_reader_orientation
from .quality import (
    apply_semantic_quality_gate,
    normalize_restricted_actions,
    normalize_rhythm_signature,
    reviewer_private_context,
    validate_outline_plan_contract,
    validate_draft_contract,
    validate_semantic_quality_audits,
    writer_safe_arc,
    writer_safe_facts,
    writer_safe_outline,
    writer_safe_profile,
)
from .parsing import extract_json_object
from .planning_terminology import (
    reader_visible_terms,
    repairer_stage_context,
    reviewer_stage_context,
    sanitize_writer_value,
    writer_stage_guidance,
)
from .prompt_templates import (
    DELTA_SCHEMA_HINT,
    EXTRACTOR_SYSTEM,
    REPAIR_SYSTEM,
    REVIEW_SCHEMA_HINT,
    REVIEWER_SYSTEM,
    STAGE_PROMPTS,
    STAGE_SYSTEM,
    WRITER_SYSTEM,
)
from .provider import call_route, generation_mode_details, list_routes, load_generation_mode, messages_hash, test_route
from .retrieval import format_retrieval_context, reindex_workspace, search_memory
from .titles import normalize_generated_chapter_outline, normalize_generated_chapter_title
from .storage import (
    atomic_write_text,
    create_checkpoint,
    get_workspace,
    read_json,
    read_text,
    read_yaml,
    restore_checkpoint,
    sha256_text,
    utc_now,
    workspace_lock,
    write_json,
    write_yaml,
)
from .validation import (
    validate_chapter_ready,
    validate_delta,
    validate_narrative_orientation_audit,
    validate_narrative_timing_audit,
    validate_reaction_logic_audit,
    validate_workspace,
)
from .continuation import (
    analyze_imported as _analyze_imported,
    apply_continuation_plan as _apply_continuation_plan,
    apply_takeover_proposal as _apply_takeover_proposal,
    collect_source_chapters,
    generate_continuation_plan as _generate_continuation_plan,
    generate_takeover_proposal as _generate_takeover_proposal,
    import_into_workspace as _import_into_workspace,
    takeover_status as _takeover_status,
)
from .writing_doctrine import (
    ensure_writing_workspace,
    format_rule_cards,
    load_writing_rules,
    select_writing_rules,
    set_rule_enabled,
)
from .writing_gates import (
    apply_writing_quality_gate,
    run_writing_gate_evals,
    targeted_repair_brief,
    validate_writing_review,
    writing_review_schema_hint,
)
from .writing_profiles import writing_quality_config
from .writing_reference import (
    analysis_prompt as writing_analysis_prompt,
    doctrine_proposal_prompt,
    import_writing_reference as _import_writing_reference,
    scan_reference_library,
    reference_batches,
    validate_doctrine_proposal,
)

def _exemplar_module() -> Any:
    return importlib.import_module("novel_production_mcp.exemplars")


STAGES = ("director", "macro", "world", "characters", "volume", "outline")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ensure_projection_clean(paths: WorkspacePaths) -> None:
    status = projection_status(paths)
    if not status["ok"]:
        preview = ", ".join(item["path"] for item in status["drift"][:8])
        raise RuntimeError(
            "Workspace projections drifted from SQLite source of truth: " + preview
            + ". Use novel_rebuild_projections to discard manual edits, or novel_adopt_file_changes to approve them."
        )


def _yaml_text(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _mkdirs(root: Path) -> None:
    for relative in [
        "canon",
        "characters/profiles",
        "characters/arcs",
        "state/characters",
        "planning/volumes",
        "planning/chapter-outlines",
        "planning/proposals",
        "planning/ending",
        "final",
        "exports",
        "chapters",
        "drafts/history",
        "packets",
        "deltas",
        "audits",
        "runs",
        "checkpoints",
        "metrics",
        ".novel",
        ".locks",
        "prompts/overrides",
        "source/original",
        "analysis/chunks",
        "analysis/reports",
        "analysis/conflicts",
        "analysis/writing-quality",
        "writing/references",
        "writing/proposals",
        "planning/reconstructed-outlines",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)


def _initial_projections(
    workspace_id: str,
    title: str,
    genre: str,
    premise: str,
    planned_chapters: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    now = utc_now()
    project = {
        "schema_version": 3,
        "workspace_id": workspace_id,
        "title": title.strip(),
        "genre": genre.strip(),
        "premise": premise.strip(),
        "planned_chapters": max(1, planned_chapters),
        "length_plan": {
            "mode": "fixed",
            "min_chapter": max(1, planned_chapters),
            "ideal_chapter": max(1, planned_chapters),
            "max_chapter": max(1, planned_chapters),
            "source": "workspace_create",
        },
        "ending_status": "target_only",
        "created_at": now,
        "updated_at": now,
        "current_stage": "director",
        "last_committed_chapter": 0,
        "context_policy": {
            "previous_chapter_tail_chars": 2500,
            "max_packet_chars": 56000,
            "retrieval_top_k": 6,
            "retrieval_max_chars": 9000,
        },
        "quality_policy": {
            "min_review_score": 85,
            "max_repair_rounds": 2,
            "max_relationship_delta_per_chapter": 15,
            "allow_unapproved_ability_additions": False,
            "require_human_approval_for_canon_changes": True,
            "require_fact_ids": True,
            "require_inventory_events": True,
            "min_chapter_length_ratio": 0.85,
        },
        "writing_quality": {
            "enabled": True,
            "profile": "web_novel",
            "min_score": 85,
            "max_repair_rounds": 2,
            "block_high": True,
            "block_critical": True,
            "min_blocking_confidence": 0.75,
            "stop_on_unresolved_critical": True,
            "stop_on_unresolved_high": False,
            "allow_commit_with_warnings": True,
            "anti_fluff": True,
            "causality": True,
            "dialogue": True,
            "information_novelty": True,
            "max_writer_rules": 8,
        },
        "character_simulation": {
            "enabled": True,
            "theory_of_mind": True,
            "max_simulated_characters_per_scene": 4,
        },
        "scene_generation": {
            "enabled": False,
            "scene_level": False,
        },
        "writing_exemplars": {
            "enabled": True,
            "max_exemplars_per_scene": 2,
            "max_exemplar_tokens": 1800,
        },
        "arc_reflection": {
            "enabled": True,
        },
    }
    ending_plan, story_budget, ending_progress = make_ending_plan(
        min_chapter=max(1, planned_chapters),
        ideal_chapter=max(1, planned_chapters),
        max_chapter=max(1, planned_chapters),
        target_source="workspace_create",
        last_committed=0,
        status="target_only",
    )
    projections = {
        "project.yaml": _yaml_text(project),
        "planning/ending/ending-plan.yaml": _yaml_text(ending_plan),
        "planning/story-budget.yaml": _yaml_text(story_budget),
        "state/ending-progress.yaml": _yaml_text(ending_progress),
        "canon/premise.md": f"# {title}\n\n{premise.strip()}\n",
        "canon/world.yaml": _yaml_text({"status": "draft", "rules": [], "locations": [], "factions": []}),
        "canon/rules.yaml": _yaml_text({"hard_rules": [], "soft_rules": []}),
        "canon/immutable-facts.yaml": _yaml_text({"facts": []}),
        "canon/facts.yaml": _yaml_text({"facts": []}),
        "canon/locations.yaml": _yaml_text({"locations": [], "travel_minutes": {}}),
        "characters/index.yaml": _yaml_text({"characters": []}),
        "characters/relationships.yaml": _yaml_text({"relationships": []}),
        "characters/knowledge-boundaries.yaml": _yaml_text({"characters": {}}),
        "state/relationship-state.yaml": _yaml_text({"relationships": []}),
        "state/timeline.yaml": _yaml_text({"current_story_time": "", "events": []}),
        "state/inventory.yaml": _yaml_text({"characters": {}}),
        "state/item-ledger.yaml": _yaml_text({"items": {}, "balances": {}, "events": []}),
        "state/plot-ledger.yaml": _yaml_text({"threads": []}),
        "state/foreshadowing.yaml": _yaml_text({"items": []}),
        "planning/macro.md": "# 故事宏观规划\n\n待生成。\n",
        "writing/project-doctrine.yaml": _yaml_text({"rules": []}),
        "writing/rule-overrides.yaml": _yaml_text({"rules": {}}),
    }
    return project, projections


def create_workspace(
    workspace_id: str,
    title: str,
    genre: str = "",
    premise: str = "",
    planned_chapters: int = 30,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    if paths.root.exists() and any(paths.root.iterdir()):
        raise FileExistsError(f"Workspace already exists: {paths.root}")
    _mkdirs(paths.root)
    project, projections = _initial_projections(workspace_id, title, genre, premise, planned_chapters)
    init_event_store(paths)
    event = commit_projection_event(
        paths,
        event_type="workspace.created",
        payload={"workspace_id": workspace_id, "title": title, "schema_version": 3},
        projections=projections,
    )
    reindex_workspace(paths)
    return {"workspace_id": workspace_id, "path": str(paths.root), "project": project, "event": event}


def workspace_status(workspace_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    project = read_yaml(paths.project, {})
    index = read_yaml(paths.root / "characters" / "index.yaml", {"characters": []})
    outlines = sorted((paths.root / "planning" / "chapter-outlines").glob("chapter-*.yaml"))
    chapters = sorted((paths.root / "chapters").glob("chapter-*.md"))
    drafts = sorted((paths.root / "drafts").glob("chapter-*.md"))
    return {
        "workspace_id": workspace_id,
        "path": str(paths.root),
        "title": project.get("title"),
        "schema_version": project.get("schema_version", 1),
        "current_stage": project.get("current_stage"),
        "last_committed_chapter": project.get("last_committed_chapter", 0),
        "characters": len(index.get("characters", [])),
        "outlines": len(outlines),
        "drafts": len(drafts),
        "chapters": len(chapters),
        "ending": _check_ending_progress(paths),
        "validation": validate_workspace(paths).to_dict(),
        "projection_status": projection_status(paths),
    }


def _workspace_context(paths: WorkspacePaths) -> str:
    project = read_yaml(paths.project, {})
    pieces = [
        "PROJECT:\n" + _yaml_text(project),
        "PREMISE:\n" + read_text(paths.root / "canon" / "premise.md"),
        "IMMUTABLE FACTS:\n" + _yaml_text(read_yaml(paths.root / "canon" / "immutable-facts.yaml", {})),
        "FACT LEDGER:\n" + _yaml_text(read_yaml(paths.root / "canon" / "facts.yaml", {})),
        "WORLD:\n" + _yaml_text(read_yaml(paths.root / "canon" / "world.yaml", {})),
        "MACRO:\n" + read_text(paths.root / "planning" / "macro.md"),
        "ENDING PLAN:\n" + _yaml_text(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})),
        "STORY BUDGET:\n" + _yaml_text(read_yaml(paths.root / "planning" / "story-budget.yaml", {})),
        "ENDING PROGRESS:\n" + _yaml_text(_check_ending_progress(paths)),
    ]
    return "\n\n".join(pieces)


def _call(
    paths: WorkspacePaths,
    route: str,
    messages: list[dict[str, str]],
    *,
    purpose: str,
    chapter: int | None = None,
    repair_round: int = 0,
) -> ModelResult:
    input_hash = messages_hash(messages)
    if load_generation_mode(paths.routes) == "agent":
        submission = current_agent_submission()
        if (
            submission
            and submission.get("route") == route
            and submission.get("purpose") == purpose
            and not submission.get("consumed")
            and (
                submission.get("input_hash") == input_hash
                or submission.get("task_id")
            )
        ):
            submission["consumed"] = True
            content = str(submission.get("content", ""))
            result = ModelResult(
                content=content,
                provider="current-agent",
                model="current-agent",
                route=route,
                latency_ms=0,
                raw_usage=None,
                input_hash=input_hash,
                output_hash=sha256_text(content),
                attempt_count=1,
                estimated_cost_usd=0.0,
            )
        else:
            raise AgentTaskPending(
                create_agent_task(
                    paths,
                    route,
                    messages,
                    purpose=purpose,
                    chapter=chapter,
                    repair_round=repair_round,
                    input_hash=input_hash,
                )
            )
    else:
        try:
            result = call_route(route, messages, paths.routes)
        except Exception as exc:
            record_llm_call(
                paths,
                purpose=purpose,
                chapter=chapter,
                result=None,
                success=False,
                repair_round=repair_round,
                error=exc,
                route_name=route,
                input_hash=input_hash,
            )
            raise
    record_llm_call(
        paths,
        purpose=purpose,
        chapter=chapter,
        result=result,
        success=True,
        repair_round=repair_round,
    )
    return result


def generate_stage_proposal(
    workspace_id: str,
    stage: str,
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
    if progress_callback:
        progress_callback(f"阶段 1/3：正在准备 {stage} 提案输入", 5, 100)
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    prompt = STAGE_PROMPTS[stage]
    if instruction.strip():
        prompt += f"\n\n用户补充要求：\n{instruction.strip()}"
    messages = [
        {"role": "system", "content": STAGE_SYSTEM},
        {"role": "user", "content": _workspace_context(paths) + "\n\nTASK:\n" + prompt},
    ]
    if progress_callback:
        progress_callback(f"阶段 2/3：正在调用 {stage} 模型", 30, 100)
    result = _call(paths, stage, messages, purpose=f"stage.{stage}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    extension = "yaml" if stage in {"world", "characters", "volume", "outline"} else "md"
    proposal = paths.root / "planning" / "proposals" / f"{stamp}-{stage}.{extension}"
    atomic_write_text(proposal, result.content.strip() + "\n")
    write_json(proposal.with_suffix(proposal.suffix + ".meta.json"), {
        "stage": stage,
        "created_at": utc_now(),
        "route": result.route,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "input_hash": result.input_hash,
        "output_hash": result.output_hash,
        "token_usage": result.raw_usage,
        "estimated_cost_usd": result.estimated_cost_usd,
    })
    if progress_callback:
        progress_callback(f"阶段 3/3：{stage} 提案已写入", 100, 100)
    return {"proposal": str(proposal), "stage": stage, "preview": result.content[:2000]}


def _clean_fenced(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^```(?:yaml|yml|markdown|md)?\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip() + "\n"


def _stage_projections(paths: WorkspacePaths, stage: str, content: str) -> tuple[dict[str, str], str]:
    projections: dict[str, str] = {}
    target = ""
    if stage == "director":
        target = "planning/director-brief.md"
        projections[target] = content
    elif stage == "macro":
        target = "planning/macro.md"
        projections[target] = content
    elif stage == "world":
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise ValueError("World proposal must parse to a YAML mapping")
        target = "canon/world.yaml"
        projections[target] = _yaml_text(parsed)
        if isinstance(parsed.get("facts"), list):
            projections["canon/facts.yaml"] = _yaml_text({"facts": parsed["facts"]})
        if isinstance(parsed.get("immutable_facts"), list):
            projections["canon/immutable-facts.yaml"] = _yaml_text({"facts": parsed["immutable_facts"]})
        if isinstance(parsed.get("locations"), list):
            projections["canon/locations.yaml"] = _yaml_text({
                "locations": parsed["locations"],
                "travel_minutes": parsed.get("travel_minutes", {}),
            })
        if isinstance(parsed.get("items"), list):
            ledger = copy.deepcopy(read_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}, "events": []}))
            item_map = ledger.setdefault("items", {})
            for item in parsed["items"]:
                if isinstance(item, dict) and item.get("id"):
                    item_map[str(item["id"])] = {
                        key: value for key, value in item.items()
                        if key not in {"id", "current_holder"}
                    }
            projections["state/item-ledger.yaml"] = _yaml_text(ledger)
    elif stage == "characters":
        parsed = yaml.safe_load(content)
        characters = parsed.get("characters", []) if isinstance(parsed, dict) else []
        if not isinstance(characters, list) or not characters:
            raise ValueError("Character proposal must contain a non-empty characters list")
        index_items: list[dict[str, Any]] = []
        knowledge = read_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {}})
        for raw in characters:
            if not isinstance(raw, dict):
                raise ValueError("Each character must be a mapping")
            char_id = str(raw.get("id", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", char_id):
                raise ValueError(f"Invalid character id: {char_id}")
            profile = {
                "id": char_id,
                "name": raw.get("name", char_id),
                "role": raw.get("role", "supporting"),
                "immutable": raw.get("immutable", {}),
                "personality": raw.get("personality", {}),
                "speech": raw.get("speech", {}),
                "abilities": raw.get("abilities", {}),
            }
            state = copy.deepcopy(raw.get("initial_state", {}) or {})
            state.update({"character_id": char_id, "updated_after_chapter": 0})
            arc = copy.deepcopy(raw.get("arc", {}) or {})
            arc.update({"character_id": char_id})
            projections[f"characters/profiles/{char_id}.yaml"] = _yaml_text(profile)
            projections[f"state/characters/{char_id}.yaml"] = _yaml_text(state)
            projections[f"characters/arcs/{char_id}.yaml"] = _yaml_text(arc)
            index_items.append({"id": char_id, "name": profile["name"], "role": profile["role"]})
            boundary = raw.get("knowledge_boundary", {}) or {}
            knowledge.setdefault("characters", {})[char_id] = {
                "knows": boundary.get("knows", []),
                "suspects": boundary.get("suspects", []),
                "must_not_know": boundary.get("must_not_know", []),
            }
        target = "characters/index.yaml"
        projections[target] = _yaml_text({"characters": index_items})
        projections["characters/knowledge-boundaries.yaml"] = _yaml_text(knowledge)
    elif stage == "volume":
        parsed = yaml.safe_load(content)
        volumes = parsed.get("volumes", []) if isinstance(parsed, dict) else []
        if not isinstance(volumes, list) or not volumes:
            raise ValueError("Volume proposal must contain volumes")
        validate_volume_coverage(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}), volumes)
        for volume in volumes:
            order = int(volume.get("order", 0))
            if order < 1:
                raise ValueError("Volume order must be >= 1")
            projections[f"planning/volumes/volume-{order:03d}.yaml"] = _yaml_text(volume)
        target = "planning/volumes"
    elif stage == "outline":
        parsed = yaml.safe_load(content)
        chapters = parsed.get("chapters", []) if isinstance(parsed, dict) else []
        if not isinstance(chapters, list) or not chapters:
            raise ValueError("Outline proposal must contain chapters")
        validate_outline_coverage(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}), chapters)
        normalized_chapters: list[dict[str, Any]] = []
        for chapter in chapters:
            number = int(chapter.get("number", 0))
            if number < 1:
                raise ValueError("Chapter number must be >= 1")
            normalized = normalize_generated_chapter_outline(chapter, number)
            normalized["reader_orientation"] = normalize_reader_orientation(normalized, number)
            normalized["narrative_timing"] = normalize_narrative_timing(normalized, number)
            normalized["restricted_actions"] = normalize_restricted_actions(normalized, number)
            normalized["rhythm_signature"] = normalize_rhythm_signature(normalized, number)
            normalized_chapters.append(normalized)
        validate_outline_plan_contract(normalized_chapters)
        for normalized in normalized_chapters:
            number = int(normalized["number"])
            projections[f"planning/chapter-outlines/chapter-{number:04d}.yaml"] = _yaml_text(normalized)
        target = "planning/chapter-outlines"
    else:
        raise ValueError(f"Unsupported stage: {stage}")
    project = read_yaml(paths.project, {})
    stage_index = STAGES.index(stage)
    project["current_stage"] = STAGES[min(stage_index + 1, len(STAGES) - 1)] if stage != "outline" else "chapter_execution"
    project["updated_at"] = utc_now()
    projections["project.yaml"] = _yaml_text(project)
    return projections, target


def apply_stage_proposal(workspace_id: str, proposal_relative_path: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    proposal = (paths.root / proposal_relative_path).resolve()
    proposal_root = (paths.root / "planning" / "proposals").resolve()
    if proposal_root not in proposal.parents or not proposal.exists():
        raise ValueError("Proposal must be an existing file under planning/proposals")
    meta = read_json(proposal.with_suffix(proposal.suffix + ".meta.json"), {})
    stage = str(meta.get("stage") or proposal.stem.split("-")[-1])
    content = _clean_fenced(read_text(proposal))
    with workspace_lock(paths, "stage"):
        checkpoint = create_checkpoint(paths, f"before-{stage}")
        projections, target = _stage_projections(paths, stage, content)
        event = commit_projection_event(
            paths,
            event_type="stage.applied",
            payload={"stage": stage, "proposal": proposal_relative_path, "checkpoint": checkpoint.name},
            projections=projections,
            input_hash=str(meta.get("output_hash", "")),
        )
    reindex_workspace(paths)
    return {"applied": True, "stage": stage, "target": target, "checkpoint": checkpoint.name, "event": event}


def _chapter_terminology_sources(
    paths: WorkspacePaths,
    outline: Mapping[str, Any],
    participants: list[str],
    writing_rules: list[Mapping[str, Any]] | None = None,
) -> list[Any]:
    profiles = {
        char_id: read_yaml(paths.root / "characters" / "profiles" / f"{char_id}.yaml", {})
        for char_id in participants
    }
    return [
        {"outline": dict(outline)},
        {"profiles": profiles},
        {"writing_rules": list(writing_rules or [])},
    ]


def _established_reader_visible_terms(
    paths: WorkspacePaths,
    outline: Mapping[str, Any],
) -> set[str]:
    return reader_visible_terms(
        outline,
        read_yaml(paths.root / "canon" / "world.yaml", {}),
        read_yaml(paths.root / "canon" / "facts.yaml", {}),
        read_yaml(paths.root / "canon" / "immutable-facts.yaml", {}),
        require_established=True,
    )


def _writer_yaml_text(value: Any, allowed_terms: set[str]) -> str:
    return _yaml_text(sanitize_writer_value(value, allowed_terms=allowed_terms))


def _writer_text(value: str, allowed_terms: set[str]) -> str:
    return str(sanitize_writer_value(value, allowed_terms=allowed_terms))


def _profile_bundle(
    paths: WorkspacePaths,
    participants: list[str],
    allowed_terms: set[str],
) -> str:
    sections: list[str] = []
    knowledge_all = read_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {}})
    item_ledger = read_yaml(paths.root / "state" / "item-ledger.yaml", {"balances": {}})
    balances = item_ledger.get("balances", {}) if isinstance(item_ledger, dict) else {}
    for char_id in participants:
        profile = read_yaml(paths.root / "characters" / "profiles" / f"{char_id}.yaml", {})
        state = read_yaml(paths.root / "state" / "characters" / f"{char_id}.yaml", {})
        arc = read_yaml(paths.root / "characters" / "arcs" / f"{char_id}.yaml", {})
        knowledge = knowledge_all.get("characters", {}).get(char_id, {}) if isinstance(knowledge_all, dict) else {}
        if not profile:
            raise FileNotFoundError(f"Missing character profile: {char_id}")
        sections.append(
            f"## {char_id} / {profile.get('name', char_id)}\n"
            f"### 写作安全档案\n```yaml\n{_writer_yaml_text(writer_safe_profile(profile), allowed_terms)}```\n"
            f"### 当前状态\n```yaml\n{_writer_yaml_text(state, allowed_terms)}```\n"
            f"### 已发生成长线\n```yaml\n{_writer_yaml_text(writer_safe_arc(arc), allowed_terms)}```\n"
            f"### 知识边界\n```yaml\n{_writer_yaml_text(knowledge, allowed_terms)}```\n"
            f"### 道具余额\n```yaml\n{_writer_yaml_text(balances.get(char_id, {}), allowed_terms)}```"
        )
    return "\n\n".join(sections)


def _fit_packet(sections: list[tuple[str, str, bool]], max_chars: int) -> str:
    rendered = "\n\n".join(f"## {title}\n{content}" for title, content, _ in sections)
    if len(rendered) <= max_chars:
        return rendered + "\n"
    mutable = list(sections)
    # Drop optional low-priority sections from the end first.
    for index in range(len(mutable) - 1, -1, -1):
        title, content, required = mutable[index]
        if required:
            continue
        mutable[index] = (title, "（因上下文预算被省略）", required)
        rendered = "\n\n".join(f"## {t}\n{c}" for t, c, _ in mutable)
        if len(rendered) <= max_chars:
            return rendered + "\n"
    # Required data is never silently dropped; fail instead of producing an unsafe packet.
    raise ValueError(f"Required chapter packet context exceeds max_packet_chars={max_chars}; increase the budget or reduce structured state")


def build_chapter_packet(workspace_id: str, chapter_number: int) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    project = read_yaml(paths.project, {})
    if str(project.get("status", "active")) in {"completed", "completed_with_exceptions"}:
        raise RuntimeError("Novel is finalized; restore a checkpoint or explicitly reopen the project before writing more chapters")
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    if not outline:
        raise FileNotFoundError(f"Missing outline for chapter {chapter_number}")
    participants = [str(value) for value in outline.get("participants", [])]
    if not participants:
        raise ValueError("Chapter outline must list participants")
    writing_rules = select_writing_rules(paths, chapter_number, outline, stage="writer")
    allowed_terms = _established_reader_visible_terms(paths, outline)
    terminology_sources = _chapter_terminology_sources(paths, outline, participants, writing_rules)
    stage_guidance = writer_stage_guidance(*terminology_sources)
    previous = read_text(paths.chapter_file(chapter_number - 1, "chapter")) if chapter_number > 1 else ""
    ending_context = ending_context_for_chapter(paths, chapter_number)
    context_policy = project.get("context_policy", {}) if isinstance(project, dict) else {}
    tail_chars = int(context_policy.get("previous_chapter_tail_chars", 2500))
    previous_tail = _writer_text(
        previous[-tail_chars:] if previous else "（第一章，无上一章）",
        allowed_terms,
    )
    query = " ".join([
        str(outline.get("title", "")),
        str(outline.get("mission", "")),
        " ".join(str(value) for value in outline.get("threads_advanced", []) or []),
        previous_tail[-800:],
    ])
    retrieval_results = search_memory(
        paths,
        query,
        top_k=int(context_policy.get("retrieval_top_k", 6)),
        exclude_chapter_gte=chapter_number,
    )
    retrieval_context = format_retrieval_context(
        retrieval_results,
        max_chars=int(context_policy.get("retrieval_max_chars", 9000)),
    )
    retrieval_context = _writer_text(retrieval_context, allowed_terms)
    recent_rhythm: list[dict[str, Any]] = []
    for number in range(max(1, chapter_number - 6), chapter_number):
        prior_outline = read_yaml(paths.chapter_file(number, "outline"), {})
        if isinstance(prior_outline, dict) and prior_outline:
            recent_rhythm.append({
                "chapter": number,
                "title": prior_outline.get("title", ""),
                "rhythm_signature": prior_outline.get("rhythm_signature", {}),
            })
    sections = [
        ("项目", f"```yaml\n{_writer_yaml_text(project, allowed_terms)}```", True),
        ("完结计划", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'planning' / 'ending' / 'ending-plan.yaml', {}), allowed_terms)}```", True),
        ("全书章节预算", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'planning' / 'story-budget.yaml', {}), allowed_terms)}```", True),
        ("完结进度", f"```yaml\n{_writer_yaml_text(_check_ending_progress(paths), allowed_terms)}```", True),
        ("本章终局约束", f"```yaml\n{_writer_yaml_text(ending_context, allowed_terms)}```", True),
        ("本章大纲", f"```yaml\n{_writer_yaml_text(writer_safe_outline(outline), allowed_terms)}```", True),
        ("本章正文执行要求（只执行，不得逐字复述）", _writer_text(format_rule_cards(writing_rules, stage="writer"), allowed_terms), True),
        ("本章补充执行要求（只执行，不得逐字复述）", "\n".join(f"- {item}" for item in stage_guidance), True),
        ("本章可用不可变事实", f"```yaml\n{_writer_yaml_text(writer_safe_facts(paths, participants, ledger_name='immutable-facts.yaml'), allowed_terms)}```", True),
        ("本章可用事实 ID", f"```yaml\n{_writer_yaml_text(writer_safe_facts(paths, participants), allowed_terms)}```", True),
        ("世界规则", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'canon' / 'world.yaml', {}), allowed_terms)}```", True),
        ("地点与旅行时间", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'canon' / 'locations.yaml', {}), allowed_terms)}```", True),
        ("出场人物", _profile_bundle(paths, participants, allowed_terms), True),
        ("关系状态", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'state' / 'relationship-state.yaml', {}), allowed_terms)}```", True),
        ("当前时间线", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'state' / 'timeline.yaml', {}), allowed_terms)}```", True),
        ("活跃剧情线", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'state' / 'plot-ledger.yaml', {}), allowed_terms)}```", True),
        ("伏笔账本", f"```yaml\n{_writer_yaml_text(read_yaml(paths.root / 'state' / 'foreshadowing.yaml', {}), allowed_terms)}```", False),
        ("近期章节节奏依据", f"```yaml\n{_writer_yaml_text(recent_rhythm, allowed_terms)}```", True),
        ("历史混合检索（低优先级）", retrieval_context, False),
        ("上一章结尾", previous_tail, True),
    ]
    packet = f"# 第 {chapter_number} 章生产包\n\n" + _fit_packet(
        sections,
        int(context_policy.get("max_packet_chars", 56000)) - 30,
    )
    path = paths.chapter_file(chapter_number, "packet")
    atomic_write_text(path, packet)
    write_json(path.with_suffix(".manifest.json"), {
        "chapter": chapter_number,
        "created_at": utc_now(),
        "participants": participants,
        "writing_rule_ids": [rule["id"] for rule in writing_rules],
        "sha256": sha256_text(packet),
        "retrieval": [
            {"source_path": item["source_path"], "chunk_id": item["chunk_id"], "score": item["score"]}
            for item in retrieval_results
        ],
    })
    return {
        "chapter": chapter_number,
        "packet": str(path),
        "characters": len(packet),
        "retrieval_hits": len(retrieval_results),
        "writing_rule_ids": [rule["id"] for rule in writing_rules],
    }


def _review_prompt(
    packet: str,
    draft: str,
    timing_context: dict[str, Any],
    private_context: dict[str, Any] | None = None,
    writing_rules: list[Mapping[str, Any]] | None = None,
    terminology_context: dict[str, Any] | None = None,
) -> str:
    return (
        f"章节包：\n{packet}\n\n"
        f"仅供 Reviewer 使用的叙事时序依据（不得要求 Writer 提前写入未来内容）：\n"
        f"```yaml\n{_yaml_text(timing_context)}```\n\n"
        f"仅供 Reviewer 使用的完整人物/事实依据（不得要求 Writer 泄漏角色未知信息）：\n"
        f"```yaml\n{_yaml_text(private_context or {})}```\n\n"
        f"仅供 Reviewer 使用的规划术语依据（不得要求 Writer 复述术语）：\n"
        f"```yaml\n{_yaml_text(terminology_context or {'terms': []})}```\n\n"
        f"本章适用 Writing Rule Cards：\n{format_rule_cards(writing_rules or [])}\n\n"
        f"待审正文：\n{draft}\n\n按以下结构输出，并在同一个 JSON 对象中加入 Writing Quality：\n"
        f"{REVIEW_SCHEMA_HINT}\n\nWriting Quality 结构：\n{writing_review_schema_hint(writing_rules or [])}"
    )


def _apply_reaction_logic_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    existing = {
        (
            str(item.get("type", "")),
            (item.get("details") or {}).get("event_index") if isinstance(item.get("details"), dict) else None,
        )
        for item in issues
        if isinstance(item, dict)
    }
    gate_issues = validate_reaction_logic_audit(audit)
    for issue in gate_issues:
        signature = (issue.code, issue.details.get("event_index"))
        if signature in existing:
            continue
        issues.append({
            "type": issue.code,
            "severity": issue.severity,
            "location": issue.path or "abnormal_events",
            "evidence": issue.message,
            "fix": str(issue.details.get("fix", "Rerun review or add the missing reaction bridge")),
            "details": issue.details,
        })
        existing.add(signature)
    audit["issues"] = issues
    if gate_issues:
        audit["approved"] = False
    return audit


def _apply_narrative_orientation_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    existing = {
        str(item.get("type", ""))
        for item in issues
        if isinstance(item, dict)
    }
    gate_issues = validate_narrative_orientation_audit(audit)
    for issue in gate_issues:
        if issue.code in existing:
            continue
        issues.append({
            "type": issue.code,
            "severity": issue.severity,
            "location": issue.path or "orientation_audit",
            "evidence": issue.message,
            "fix": str(issue.details.get("fix", "Rerun review or establish the missing task context")),
            "details": issue.details,
        })
        existing.add(issue.code)
    audit["issues"] = issues
    if gate_issues:
        audit["approved"] = False
    return audit


def _apply_narrative_timing_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    existing = {
        (
            str(item.get("type", "")),
            str(item.get("location", "")),
        )
        for item in issues
        if isinstance(item, dict)
    }
    gate_issues = validate_narrative_timing_audit(audit)
    for issue in gate_issues:
        signature = (issue.code, str(issue.path or "narrative_timing_audit"))
        if signature in existing:
            continue
        issues.append({
            "type": issue.code,
            "severity": issue.severity,
            "location": issue.path or "narrative_timing_audit",
            "evidence": issue.message,
            "fix": str(issue.details.get("fix", "Remove or replace the premature future reference")),
            "details": issue.details,
        })
        existing.add(signature)
    audit["issues"] = issues
    if gate_issues:
        audit["approved"] = False
    return audit


def _apply_rule_leak_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    found = False
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("type") != "rule_leak":
            continue
        found = True
        if issue.get("severity") != "critical":
            issue["severity"] = "high"
    audit["issues"] = issues
    if found:
        audit["approved"] = False
    return audit


def _apply_profile_literalization_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    found = False
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("type") != "profile_literalization":
            continue
        found = True
        if issue.get("severity") != "critical":
            issue["severity"] = "high"
    audit["issues"] = issues
    if found:
        audit["approved"] = False
    return audit


def _apply_character_entry_gate(audit: dict[str, Any]) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    found = False
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("type") != "character_entry":
            continue
        found = True
        if issue.get("severity") != "critical":
            issue["severity"] = "high"
    audit["issues"] = issues
    if found:
        audit["approved"] = False
    return audit


def _apply_all_review_gates(audit: dict[str, Any]) -> dict[str, Any]:
    return apply_semantic_quality_gate(
        _apply_narrative_timing_gate(
            _apply_narrative_orientation_gate(
                _apply_character_entry_gate(
                    _apply_profile_literalization_gate(
                        _apply_rule_leak_gate(_apply_reaction_logic_gate(audit))
                    )
                )
            )
        )
    )


def _apply_draft_contract_gate(
    paths: WorkspacePaths,
    chapter_number: int,
    draft: str,
    audit: dict[str, Any],
) -> dict[str, Any]:
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    existing = {(str(item.get("type", "")), str(item.get("location", ""))) for item in issues if isinstance(item, dict)}
    for issue in validate_draft_contract(paths, chapter_number, draft):
        signature = (issue.code, str(issue.path or issue.code))
        if signature in existing:
            continue
        issues.append({
            "type": issue.code,
            "severity": issue.severity,
            "location": issue.path or issue.code,
            "evidence": issue.message,
            "fix": str(issue.details.get(
                "repair_instruction",
                "Expand the missing scene causality or remove profile-literal prose without adding new canon",
            )),
            "details": issue.details,
        })
        existing.add(signature)
    audit["issues"] = issues
    if any(item.get("severity") in {"high", "critical"} for item in issues if isinstance(item, dict)):
        audit["approved"] = False
    return audit


def generate_chapter_draft(
    workspace_id: str,
    chapter_number: int,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if progress_callback:
        progress_callback(f"阶段 1/3：正在准备第 {chapter_number} 章章节包", 5, 100)
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    packet_path = paths.chapter_file(chapter_number, "packet")
    if not packet_path.exists():
        build_chapter_packet(workspace_id, chapter_number)
    packet = read_text(packet_path)
    if progress_callback:
        progress_callback(f"阶段 2/3：正在调用 writer 模型生成第 {chapter_number} 章", 30, 100)
    result = _call(
        paths,
        "writer",
        [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": packet}],
        purpose="chapter.writer",
        chapter=chapter_number,
    )
    draft_path = paths.chapter_file(chapter_number, "draft")
    atomic_write_text(draft_path, result.content.strip() + "\n")
    run = {
        "chapter": chapter_number,
        "phase": "drafted",
        "updated_at": utc_now(),
        "writer": {
            "route": result.route,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "attempts": result.attempt_count,
            "input_hash": result.input_hash,
            "output_hash": result.output_hash,
            "usage": result.raw_usage,
            "estimated_cost_usd": result.estimated_cost_usd,
        },
        "draft_sha256": result.output_hash,
        "repair_rounds": 0,
    }
    write_json(paths.chapter_file(chapter_number, "run"), run)
    if progress_callback:
        progress_callback(f"阶段 3/3：第 {chapter_number} 章草稿已保存", 100, 100)
    return {"chapter": chapter_number, "draft": str(draft_path), "chars": len(result.content), "model": result.model}


def review_chapter(
    workspace_id: str,
    chapter_number: int,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if progress_callback:
        progress_callback(f"阶段 1/3：正在准备第 {chapter_number} 章审校输入", 5, 100)
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    packet = read_text(paths.chapter_file(chapter_number, "packet"))
    draft = read_text(paths.chapter_file(chapter_number, "draft"))
    if not packet or not draft:
        raise FileNotFoundError("Packet and draft are required before review")
    run = read_json(paths.chapter_file(chapter_number, "run"), {})
    repair_round = int(run.get("repair_rounds", 0) or 0)
    timing_context = build_narrative_timing_review_context(paths, chapter_number)
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    participants = [str(value) for value in outline.get("participants", [])] if isinstance(outline, dict) else []
    writing_rules = select_writing_rules(
        paths,
        chapter_number,
        outline if isinstance(outline, Mapping) else {},
        stage="reviewer",
    )
    terminology_sources = _chapter_terminology_sources(paths, outline, participants, writing_rules)
    allowed_terms = _established_reader_visible_terms(paths, outline if isinstance(outline, Mapping) else {})
    if progress_callback:
        progress_callback(f"阶段 2/3：正在调用 reviewer 模型审校第 {chapter_number} 章", 30, 100)
    result = _call(
        paths,
        "reviewer",
        [{"role": "system", "content": REVIEWER_SYSTEM}, {"role": "user", "content": _review_prompt(
            packet,
            draft,
            timing_context,
            reviewer_private_context(paths, participants),
            writing_rules,
            reviewer_stage_context(*terminology_sources, allowed_terms=allowed_terms),
        )}],
        purpose="chapter.reviewer",
        chapter=chapter_number,
        repair_round=repair_round,
    )
    audit = _apply_draft_contract_gate(
        paths,
        chapter_number,
        draft,
        _apply_all_review_gates(extract_json_object(result.content)),
    )
    project = read_yaml(paths.project, {})
    min_score = int(project.get("quality_policy", {}).get("min_review_score", 85))
    issues = audit.get("issues", []) if isinstance(audit.get("issues"), list) else []
    has_blocking = any(item.get("severity") in {"high", "critical"} for item in issues if isinstance(item, dict))
    audit["approved"] = bool(audit.get("approved")) and int(audit.get("score", 0)) >= min_score and not has_blocking
    audit = apply_writing_quality_gate(
        paths,
        chapter_number,
        audit,
        writing_rules,
        repair_rounds=repair_round,
    )
    audit["review_meta"] = {
        "route": result.route,
        "provider": result.provider,
        "model": result.model,
        "reviewed_at": utc_now(),
        "latency_ms": result.latency_ms,
        "input_hash": result.input_hash,
        "output_hash": result.output_hash,
        "usage": result.raw_usage,
        "estimated_cost_usd": result.estimated_cost_usd,
        "repair_round": repair_round,
    }
    write_json(paths.chapter_file(chapter_number, "audit"), audit)
    if progress_callback:
        progress_callback(f"阶段 3/3：第 {chapter_number} 章审校结果已保存", 100, 100)
    return audit


def audit_existing_chapters(
    workspace_id: str,
    start_chapter: int,
    end_chapter: int,
    *,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Review committed chapters without changing chapters, canon, state, or active audits."""
    if start_chapter < 1 or end_chapter < start_chapter:
        raise ValueError("Historical audit range must satisfy 1 <= start_chapter <= end_chapter")
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    report_dir = paths.root / "analysis" / "historical-audits"
    results: list[dict[str, Any]] = []
    total = end_chapter - start_chapter + 1
    for offset, chapter_number in enumerate(range(start_chapter, end_chapter + 1), start=1):
        report_path = report_dir / f"chapter-{chapter_number:04d}.json"
        if report_path.exists() and not force:
            saved = read_json(report_path, {})
            results.append({
                "chapter": chapter_number,
                "status": "cached",
                "approved": bool(saved.get("approved")),
                "issues": len(saved.get("issues", []) or []),
                "report": str(report_path),
            })
            continue
        chapter_path = paths.chapter_file(chapter_number, "chapter")
        if not chapter_path.exists():
            results.append({"chapter": chapter_number, "status": "missing"})
            continue
        if progress_callback:
            progress_callback(
                f"历史只读审计 {offset}/{total}：第 {chapter_number} 章",
                5 + (offset - 1) / max(total, 1) * 90,
                100,
            )
        outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
        participants = [str(value) for value in outline.get("participants", [])] if isinstance(outline, dict) else []
        writing_rules = select_writing_rules(
            paths,
            chapter_number,
            outline if isinstance(outline, Mapping) else {},
            stage="reviewer",
        )
        terminology_sources = _chapter_terminology_sources(paths, outline, participants, writing_rules)
        allowed_terms = _established_reader_visible_terms(paths, outline if isinstance(outline, Mapping) else {})
        packet = read_text(paths.chapter_file(chapter_number, "packet"))
        if not packet:
            previous = read_text(paths.chapter_file(chapter_number - 1, "chapter")) if chapter_number > 1 else "（第一章）"
            packet = (
                f"# 第 {chapter_number} 章历史审计包\n\n"
                f"## 项目\n```yaml\n{_yaml_text(read_yaml(paths.project, {}))}```\n\n"
                f"## 本章大纲\n```yaml\n{_yaml_text(writer_safe_outline(outline if isinstance(outline, dict) else {}))}```\n\n"
                f"## 上一章结尾\n{previous[-2500:]}\n"
            )
        result = _call(
            paths,
            "reviewer",
            [
                {"role": "system", "content": REVIEWER_SYSTEM},
                {"role": "user", "content": _review_prompt(
                    packet,
                    read_text(chapter_path),
                    build_narrative_timing_review_context(paths, chapter_number),
                    reviewer_private_context(paths, participants),
                    writing_rules,
                    reviewer_stage_context(*terminology_sources, allowed_terms=allowed_terms),
                )},
            ],
            purpose="chapter.historical_reviewer",
            chapter=chapter_number,
        )
        audit = _apply_draft_contract_gate(
            paths,
            chapter_number,
            read_text(chapter_path),
            _apply_all_review_gates(extract_json_object(result.content)),
        )
        project = read_yaml(paths.project, {})
        min_score = int(project.get("quality_policy", {}).get("min_review_score", 85))
        issues = audit.get("issues", []) if isinstance(audit.get("issues"), list) else []
        has_blocking = any(item.get("severity") in {"high", "critical"} for item in issues if isinstance(item, dict))
        audit["approved"] = bool(audit.get("approved")) and int(audit.get("score", 0)) >= min_score and not has_blocking
        audit = apply_writing_quality_gate(
            paths,
            chapter_number,
            audit,
            writing_rules,
            write_report=False,
        )
        audit["historical_audit_meta"] = {
            "chapter": chapter_number,
            "read_only": True,
            "reviewed_at": utc_now(),
            "route": result.route,
            "provider": result.provider,
            "model": result.model,
            "chapter_sha256": sha256_text(read_text(chapter_path)),
        }
        write_json(report_path, audit)
        results.append({
            "chapter": chapter_number,
            "status": "audited",
            "approved": bool(audit.get("approved")),
            "issues": len(issues),
            "report": str(report_path),
        })
    if progress_callback:
        progress_callback("历史章节只读审计完成", 100, 100)
    return {
        "workspace_id": workspace_id,
        "range": [start_chapter, end_chapter],
        "read_only": True,
        "reports": results,
        "blocking_chapters": [item["chapter"] for item in results if item.get("status") != "missing" and not item.get("approved")],
    }


def repair_chapter(
    workspace_id: str,
    chapter_number: int,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if progress_callback:
        progress_callback(f"阶段 1/3：正在准备第 {chapter_number} 章修复输入", 5, 100)
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    packet = read_text(paths.chapter_file(chapter_number, "packet"))
    draft = read_text(paths.chapter_file(chapter_number, "draft"))
    audit = read_json(paths.chapter_file(chapter_number, "audit"), {})
    if not audit or audit.get("approved"):
        raise ValueError("Repair requires a non-approved audit")
    run_path = paths.chapter_file(chapter_number, "run")
    run = read_json(run_path, {})
    repair_round = int(run.get("repair_rounds", 0) or 0) + 1
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    outline_mapping = outline if isinstance(outline, Mapping) else {}
    participants = [str(value) for value in outline_mapping.get("participants", [])]
    writing_rules = select_writing_rules(
        paths,
        chapter_number,
        outline_mapping,
        stage="repairer",
    )
    terminology_context = repairer_stage_context(
        *_chapter_terminology_sources(paths, outline_mapping, participants, writing_rules),
        allowed_terms=_established_reader_visible_terms(paths, outline_mapping),
    )
    if progress_callback:
        progress_callback(f"阶段 2/3：正在调用 repairer 模型修复第 {chapter_number} 章", 30, 100)
    result = _call(
        paths,
        "repairer",
        [
            {"role": "system", "content": REPAIR_SYSTEM},
            {"role": "user", "content": (
                f"章节包：\n{packet}\n\n当前正文：\n{draft}\n\n"
                f"仅供 Repairer 使用的规划术语依据（不得把名称保留在正文）：\n"
                f"```yaml\n{_yaml_text(terminology_context)}```\n\n"
                f"仅允许修复以下失败项：\n{json.dumps(targeted_repair_brief(audit), ensure_ascii=False, indent=2)}"
            )},
        ],
        purpose="chapter.repairer",
        chapter=chapter_number,
        repair_round=repair_round,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    before = paths.root / "drafts" / "history" / f"chapter-{chapter_number:04d}-{stamp}.md"
    atomic_write_text(before, draft)
    atomic_write_text(paths.chapter_file(chapter_number, "draft"), result.content.strip() + "\n")
    run["repair_rounds"] = repair_round
    run["phase"] = "repaired"
    run["updated_at"] = utc_now()
    run.setdefault("repairs", []).append({
        "round": repair_round,
        "route": result.route,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "input_hash": result.input_hash,
        "output_hash": result.output_hash,
        "history": str(before.relative_to(paths.root)),
    })
    write_json(run_path, run)
    if progress_callback:
        progress_callback(f"阶段 3/3：第 {chapter_number} 章修复稿已保存", 100, 100)
    return {"chapter": chapter_number, "draft": str(paths.chapter_file(chapter_number, "draft")), "history": str(before), "repair_round": repair_round}


def extract_delta(
    workspace_id: str,
    chapter_number: int,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if progress_callback:
        progress_callback(f"阶段 1/3：正在准备第 {chapter_number} 章状态提取", 5, 100)
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    packet = read_text(paths.chapter_file(chapter_number, "packet"))
    draft = read_text(paths.chapter_file(chapter_number, "draft"))
    audit = read_json(paths.chapter_file(chapter_number, "audit"), {})
    if not audit.get("approved"):
        raise ValueError("Delta extraction requires an approved audit")
    if progress_callback:
        progress_callback(f"阶段 2/3：正在调用 extractor 模型提取第 {chapter_number} 章状态变化", 30, 100)
    result = _call(
        paths,
        "extractor",
        [
            {"role": "system", "content": EXTRACTOR_SYSTEM},
            {"role": "user", "content": f"章节包：\n{packet}\n\n已通过正文：\n{draft}\n\n请按结构输出：\n{DELTA_SCHEMA_HINT}"},
        ],
        purpose="chapter.extractor",
        chapter=chapter_number,
    )
    delta = extract_json_object(result.content)
    delta["chapter"] = chapter_number
    report = validate_delta(paths, chapter_number, delta, draft)
    write_json(paths.chapter_file(chapter_number, "delta"), delta)
    write_json(paths.chapter_file(chapter_number, "delta").with_suffix(".validation.json"), report.to_dict())
    if progress_callback:
        progress_callback(f"阶段 3/3：第 {chapter_number} 章状态变化已校验并保存", 100, 100)
    return {
        "delta": delta,
        "validation": report.to_dict(),
        "model": result.model,
        "input_hash": result.input_hash,
        "output_hash": result.output_hash,
    }


def _bounded(value: int | float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _chapter_title_line(paths: WorkspacePaths, chapter_number: int) -> str:
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    title = str(outline.get("title", "") or "").strip() if isinstance(outline, dict) else ""
    if isinstance(outline, dict) and outline.get("source") != "reconstructed":
        title = normalize_generated_chapter_title(title, chapter_number)
    if title and re.match(r"^第\s*\d+\s*章\b", title):
        return title
    return f"第{chapter_number}章 {title}".rstrip()


def _chapter_body_with_title(paths: WorkspacePaths, chapter_number: int, body: str) -> str:
    """Materialize a chapter with its canonical outline title in the body."""
    lines = body.lstrip("\ufeff\r\n").splitlines()
    if lines and re.match(r"^\s*(?:#\s*)?第\s*\d+\s*章\b", lines[0]):
        lines = lines[1:]
        if lines and re.fullmatch(r"\s*-{4,}\s*", lines[0]):
            lines = lines[1:]
    content = "\n".join(lines).strip()
    title = _chapter_title_line(paths, chapter_number)
    return f"{title}\n\n{content}\n"


def _build_delta_projections(paths: WorkspacePaths, chapter_number: int, delta: dict[str, Any], draft: str) -> dict[str, str]:
    projections: dict[str, str] = {
        f"chapters/chapter-{chapter_number:04d}.md": _chapter_body_with_title(paths, chapter_number, draft),
    }
    boundaries_path = paths.root / "characters" / "knowledge-boundaries.yaml"
    boundaries = copy.deepcopy(read_yaml(boundaries_path, {"characters": {}}))
    for char_id, update in (delta.get("character_updates") or {}).items():
        state_path = paths.root / "state" / "characters" / f"{char_id}.yaml"
        state = copy.deepcopy(read_yaml(state_path, {"character_id": char_id}))
        if update.get("location"):
            state["location"] = update["location"]
        for key in ("physical", "emotion"):
            if isinstance(update.get(key), dict):
                state[key] = {**(state.get(key) or {}), **update[key]}
        goals = list(state.get("current_goals") or [])
        goals = [goal for goal in goals if goal not in (update.get("goals_remove") or [])]
        for goal in update.get("goals_add") or []:
            if goal not in goals:
                goals.append(goal)
        state["current_goals"] = goals
        knowledge = state.setdefault("knowledge", {})
        for field, source in (("confirmed", "knowledge_add"), ("suspected", "suspicions_add")):
            values = list(knowledge.get(field) or [])
            for item in update.get(source) or []:
                if item not in values:
                    values.append(item)
            knowledge[field] = values
        char_boundary = boundaries.setdefault("characters", {}).setdefault(
            char_id, {"knows": [], "suspects": [], "must_not_know": []}
        )
        for fact in update.get("knowledge_add") or []:
            if fact not in char_boundary.setdefault("knows", []):
                char_boundary["knows"].append(fact)
            char_boundary["must_not_know"] = [value for value in char_boundary.get("must_not_know", []) if value != fact]
        for fact in update.get("suspicions_add") or []:
            if fact not in char_boundary.setdefault("suspects", []):
                char_boundary["suspects"].append(fact)
        state["updated_after_chapter"] = chapter_number
        projections[f"state/characters/{char_id}.yaml"] = _yaml_text(state)
    projections["characters/knowledge-boundaries.yaml"] = _yaml_text(boundaries)

    relation_state = copy.deepcopy(read_yaml(paths.root / "state" / "relationship-state.yaml", {"relationships": []}))
    relations = relation_state.setdefault("relationships", [])
    for update in delta.get("relationship_updates") or []:
        pair = next((item for item in relations if item.get("source") == update.get("source") and item.get("target") == update.get("target")), None)
        if pair is None:
            pair = {"source": update.get("source"), "target": update.get("target"), "trust": 0, "conflict": 0, "intimacy": 0, "fear": 0}
            relations.append(pair)
        for field in ("trust", "conflict", "intimacy", "fear"):
            pair[field] = _bounded(int(pair.get(field, 0)) + int(update.get(f"{field}_delta", 0) or 0))
        pair["last_reason"] = update.get("reason", "")
        pair["last_event_ids"] = update.get("event_ids", [])
        pair["updated_after_chapter"] = chapter_number
    projections["state/relationship-state.yaml"] = _yaml_text(relation_state)

    timeline = copy.deepcopy(read_yaml(paths.root / "state" / "timeline.yaml", {"events": []}))
    timeline.setdefault("events", []).extend(delta.get("timeline_events") or [])
    if delta.get("timeline_events"):
        timeline["current_story_time"] = delta["timeline_events"][-1].get("time", timeline.get("current_story_time", ""))
    projections["state/timeline.yaml"] = _yaml_text(timeline)

    plot = copy.deepcopy(read_yaml(paths.root / "state" / "plot-ledger.yaml", {"threads": []}))
    threads = plot.setdefault("threads", [])
    for update in delta.get("plot_updates") or []:
        thread = next((item for item in threads if item.get("id") == update.get("thread_id")), None)
        if thread is None:
            thread = {"id": update.get("thread_id")}
            threads.append(thread)
        thread.update({key: value for key, value in update.items() if key != "thread_id"})
        thread["updated_after_chapter"] = chapter_number
    projections["state/plot-ledger.yaml"] = _yaml_text(plot)

    clues = copy.deepcopy(read_yaml(paths.root / "state" / "foreshadowing.yaml", {"items": []}))
    items = clues.setdefault("items", [])
    for update in delta.get("foreshadowing_updates") or []:
        item = next((candidate for candidate in items if candidate.get("id") == update.get("id")), None)
        if item is None:
            item = {"id": update.get("id")}
            items.append(item)
        item.update(update)
        item["updated_after_chapter"] = chapter_number
    projections["state/foreshadowing.yaml"] = _yaml_text(clues)

    ending_plan_path = paths.root / "planning" / "ending" / "ending-plan.yaml"
    ending_plan = copy.deepcopy(read_yaml(ending_plan_path, {}))
    if ending_plan:
        outcome_buckets = [ending_plan.setdefault("required_outcomes", []), ending_plan.setdefault("relationship_outcomes", [])]
        for update in delta.get("ending_outcome_updates") or []:
            if not isinstance(update, dict) or not update.get("id"):
                continue
            for outcomes in outcome_buckets:
                outcome = next((item for item in outcomes if isinstance(item, dict) and str(item.get("id")) == str(update.get("id"))), None)
                if outcome is not None:
                    outcome["status"] = str(update.get("status", "complete"))
                    outcome["event_ids"] = list(update.get("event_ids") or [])
                    outcome["completed_after_chapter"] = chapter_number
                    break
        final_arc = ending_plan.setdefault("final_arc", {})
        final_start = int(final_arc.get("start_chapter", 0) or 0)
        if final_start and chapter_number >= final_start and not bool(final_arc.get("entered")):
            final_arc["entered"] = True
            final_arc["entered_at_chapter"] = chapter_number
            final_arc.setdefault("mode", "auto_at_threshold")
            ending_plan["status"] = "final_arc"
        ending_plan["updated_at"] = utc_now()
        projections["planning/ending/ending-plan.yaml"] = _yaml_text(ending_plan)

    item_ledger = copy.deepcopy(read_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}, "events": []}))
    for metadata in (item_ledger.get("items", {}) if isinstance(item_ledger, dict) else {}).values():
        if isinstance(metadata, dict):
            metadata.pop("current_holder", None)
    balances = item_ledger.setdefault("balances", {})
    for event in delta.get("inventory_events") or []:
        char_id = str(event.get("character_id", ""))
        item_id = str(event.get("item_id", ""))
        action = str(event.get("action", ""))
        quantity = int(event.get("quantity", 1) or 1)
        target = str(event.get("target_character_id", ""))
        char_items = balances.setdefault(char_id, {})
        current = int(char_items.get(item_id, 0) or 0)
        if action == "acquire":
            char_items[item_id] = current + quantity
        elif action in {"consume", "drop", "destroy"}:
            char_items[item_id] = current - quantity
        elif action == "transfer":
            char_items[item_id] = current - quantity
            target_items = balances.setdefault(target, {})
            target_items[item_id] = int(target_items.get(item_id, 0) or 0) + quantity
        elif action in {"damage", "repair"}:
            status = item_ledger.setdefault("status", {}).setdefault(char_id, {})
            status[item_id] = "damaged" if action == "damage" else "available"
        event_copy = copy.deepcopy(event)
        event_copy["chapter"] = chapter_number
        item_ledger.setdefault("events", []).append(event_copy)
    projections["state/item-ledger.yaml"] = _yaml_text(item_ledger)

    # Legacy inventory projection for simple consumers.
    projections["state/inventory.yaml"] = _yaml_text({"characters": balances})

    for event in delta.get("ability_events") or []:
        if event.get("action") != "learn":
            continue
        char_id = str(event.get("character_id", ""))
        state_key = f"state/characters/{char_id}.yaml"
        state = yaml.safe_load(projections.get(state_key, read_text(paths.root / state_key))) or {}
        learned = state.setdefault("learned_abilities", [])
        ability_id = str(event.get("ability_id", ""))
        if ability_id and ability_id not in learned:
            learned.append(ability_id)
        projections[state_key] = _yaml_text(state)

    project = copy.deepcopy(read_yaml(paths.project, {}))
    project["last_committed_chapter"] = max(int(project.get("last_committed_chapter", 0)), chapter_number)
    if ending_plan and str(ending_plan.get("status", "")) == "final_arc":
        project["ending_status"] = "final_arc"
        project["current_stage"] = "final_arc_execution"
    project["updated_at"] = utc_now()
    projections["project.yaml"] = _yaml_text(project)
    if ending_plan:
        progress = ending_progress_from_data(project, ending_plan, plot, clues)
        projections["state/ending-progress.yaml"] = _yaml_text(progress)

    run_path = paths.chapter_file(chapter_number, "run")
    run = copy.deepcopy(read_json(run_path, {}))
    run.update({"phase": "committed", "committed_at": utc_now()})
    projections[f"runs/chapter-{chapter_number:04d}.json"] = _json_text(run)
    return projections


def commit_chapter(workspace_id: str, chapter_number: int) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    workspace_report = validate_workspace(paths)
    if not workspace_report.ok:
        return {
            "committed": False,
            "preflight": "workspace",
            "validation": workspace_report.to_dict(),
        }
    report = validate_chapter_ready(paths, chapter_number)
    if not report.ok:
        return {"committed": False, "preflight": "chapter", "validation": report.to_dict()}
    with workspace_lock(paths, f"chapter-{chapter_number:04d}"):
        checkpoint = create_checkpoint(paths, f"before-chapter-{chapter_number:04d}")
        draft = read_text(paths.chapter_file(chapter_number, "draft"))
        delta = read_json(paths.chapter_file(chapter_number, "delta"), {})
        projections = _build_delta_projections(paths, chapter_number, delta, draft)
        # Add checkpoint name to run projection before transaction.
        run_key = f"runs/chapter-{chapter_number:04d}.json"
        run = json.loads(projections[run_key])
        run["checkpoint"] = checkpoint.name
        projections[run_key] = _json_text(run)
        event = commit_projection_event(
            paths,
            event_type="chapter.committed",
            chapter=chapter_number,
            payload={
                "chapter": chapter_number,
                "checkpoint": checkpoint.name,
                "draft_hash": sha256_text(draft),
                "delta_hash": sha256_text(_json_text(delta)),
            },
            projections=projections,
            input_hash=sha256_text(draft + _json_text(delta)),
        )
    reindex_workspace(paths)
    return {
        "committed": True,
        "chapter": chapter_number,
        "checkpoint": checkpoint.name,
        "event": event,
        "projection_warning": bool(event.get("projection_pending")),
    }


def _chapter_pipeline_has_progress(paths: WorkspacePaths, chapter_number: int) -> bool:
    return any(
        paths.chapter_file(chapter_number, kind).exists()
        for kind in ("packet", "draft", "run", "audit", "delta", "chapter")
    )


def _stored_delta_result(paths: WorkspacePaths, chapter_number: int) -> dict[str, Any] | None:
    delta_path = paths.chapter_file(chapter_number, "delta")
    validation_path = delta_path.with_suffix(".validation.json")
    if not delta_path.exists() or not validation_path.exists():
        return None
    delta = read_json(delta_path, {})
    validation = read_json(validation_path, {})
    if not isinstance(delta, dict) or not isinstance(validation, dict) or not validation.get("ok"):
        return None
    return {
        "delta": delta,
        "validation": validation,
        "model": "persisted",
        "input_hash": "",
        "output_hash": "",
    }


def _run_chapter_pipeline_resumed(
    workspace_id: str,
    chapter_number: int,
    auto_commit: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Resume an Agent Native chapter pipeline from persisted stage artifacts."""
    paths = get_workspace(workspace_id)
    packet_path = paths.chapter_file(chapter_number, "packet")
    draft_path = paths.chapter_file(chapter_number, "draft")

    if progress_callback:
        progress_callback(f"阶段恢复：检查第 {chapter_number} 章已完成阶段", 5, 100)
    if not packet_path.exists():
        if progress_callback:
            progress_callback(f"阶段 1/6：正在构建第 {chapter_number} 章生产包", 10, 100)
        build_chapter_packet(workspace_id, chapter_number)

    if not draft_path.exists():
        if progress_callback:
            progress_callback(f"阶段 2/6：正在生成第 {chapter_number} 章正文", 20, 100)
        generate_chapter_draft(workspace_id, chapter_number)

    audit_path = paths.chapter_file(chapter_number, "audit")
    audit = read_json(audit_path, {})
    audit_schema_invalid = False
    if audit:
        original_audit = json.dumps(audit, ensure_ascii=False, sort_keys=True)
        outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
        writing_rules = select_writing_rules(
            paths,
            chapter_number,
            outline if isinstance(outline, Mapping) else {},
            stage="reviewer",
        )
        reaction_gate_issues = validate_reaction_logic_audit(audit)
        orientation_gate_issues = validate_narrative_orientation_audit(audit)
        timing_gate_issues = validate_narrative_timing_audit(audit)
        semantic_gate_issues = validate_semantic_quality_audits(audit)
        writing_gate_issues = validate_writing_review(audit, writing_rules, writing_quality_config(paths))
        audit_schema_invalid = any(
            issue.code in {
                "reaction_audit_missing",
                "reaction_audit_malformed",
                "orientation_audit_missing",
                "orientation_audit_malformed",
                "narrative_timing_audit_missing",
                "narrative_timing_audit_malformed",
            }
            for issue in reaction_gate_issues + orientation_gate_issues + timing_gate_issues + semantic_gate_issues
        )
        audit_schema_invalid = audit_schema_invalid or any(
            issue.code.endswith(("_missing", "_malformed", "_incomplete"))
            for issue in semantic_gate_issues
        )
        audit_schema_invalid = audit_schema_invalid or bool(writing_gate_issues)
        if reaction_gate_issues:
            audit = _apply_reaction_logic_gate(audit)
        if orientation_gate_issues:
            audit = _apply_narrative_orientation_gate(audit)
        if timing_gate_issues:
            audit = _apply_narrative_timing_gate(audit)
        if semantic_gate_issues:
            audit = apply_semantic_quality_gate(audit)
        audit = _apply_draft_contract_gate(paths, chapter_number, read_text(draft_path), audit)
        audit = apply_writing_quality_gate(
            paths,
            chapter_number,
            audit,
            writing_rules,
            repair_rounds=int((read_json(paths.chapter_file(chapter_number, "run"), {}) or {}).get("repair_rounds", 0) or 0),
        )
        if json.dumps(audit, ensure_ascii=False, sort_keys=True) != original_audit:
            write_json(audit_path, audit)
    submission = current_agent_submission()
    if (
        submission
        and submission.get("route") == "reviewer"
        and not submission.get("consumed")
    ) or not audit or audit_schema_invalid:
        if progress_callback:
            progress_callback(f"阶段 3/6：正在审校第 {chapter_number} 章", 40, 100)
        audit = review_chapter(workspace_id, chapter_number)

    project = read_yaml(paths.project, {})
    writing_config = writing_quality_config(paths)
    max_rounds = (
        int(writing_config.get("max_repair_rounds", 2))
        if writing_config.get("enabled")
        else int(project.get("quality_policy", {}).get("max_repair_rounds", 2))
    )
    rounds = int((read_json(paths.chapter_file(chapter_number, "run"), {}) or {}).get("repair_rounds", 0) or 0)
    while not audit.get("approved") and rounds < max_rounds:
        submission = current_agent_submission()
        if (
            submission
            and submission.get("route") == "repairer"
            and not submission.get("consumed")
        ):
            if progress_callback:
                progress_callback(f"阶段 3/6：正在提交第 {rounds + 1} 轮修复", 50, 100)
            repair_chapter(workspace_id, chapter_number)
            if progress_callback:
                progress_callback(f"阶段 3/6：正在复核第 {chapter_number} 章修复稿", 55, 100)
            audit = review_chapter(workspace_id, chapter_number)
        else:
            if progress_callback:
                progress_callback(f"阶段 3/6：第 {chapter_number} 章未通过，准备第 {rounds + 1} 轮修复", 50, 100)
            repair_chapter(workspace_id, chapter_number)
        rounds = int((read_json(paths.chapter_file(chapter_number, "run"), {}) or {}).get("repair_rounds", rounds) or rounds)

    if not audit.get("approved"):
        return {"chapter": chapter_number, "status": "needs_human_review", "writing_quality_status": audit.get("writing_quality_status"), "audit": audit, "repair_rounds": rounds}

    if progress_callback:
        progress_callback(f"阶段 4/6：正在提取并校验第 {chapter_number} 章状态变化", 70, 100)
    delta_result = _stored_delta_result(paths, chapter_number) or extract_delta(workspace_id, chapter_number)
    if not delta_result["validation"]["ok"]:
        return {"chapter": chapter_number, "status": "delta_blocked", **delta_result}

    if auto_commit:
        run = read_json(paths.chapter_file(chapter_number, "run"), {})
        if run.get("phase") == "committed" and paths.chapter_file(chapter_number, "chapter").exists():
            commit = {"committed": True, "already_committed": True}
        else:
            if progress_callback:
                progress_callback(f"阶段 5/6：正在事务提交第 {chapter_number} 章", 90, 100)
            commit = commit_chapter(workspace_id, chapter_number)
        if progress_callback:
            progress_callback(f"阶段 6/6：第 {chapter_number} 章流程完成", 100, 100)
        return {
            "chapter": chapter_number,
            "status": "committed" if commit.get("committed") else "commit_blocked",
            "audit": audit,
            "writing_quality_status": audit.get("writing_quality_status"),
            "repair_rounds": rounds,
            "commit": commit,
        }
    if progress_callback:
        progress_callback(f"阶段 6/6：第 {chapter_number} 章已准备提交", 100, 100)
    return {"chapter": chapter_number, "status": "ready_to_commit", "writing_quality_status": audit.get("writing_quality_status"), "audit": audit, "repair_rounds": rounds, **delta_result}


def run_chapter_pipeline(
    workspace_id: str,
    chapter_number: int,
    auto_commit: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    if load_generation_mode(paths.routes) == "agent" and _chapter_pipeline_has_progress(paths, chapter_number):
        return _run_chapter_pipeline_resumed(
            workspace_id,
            chapter_number,
            auto_commit=auto_commit,
            progress_callback=progress_callback,
        )
    if progress_callback:
        progress_callback(f"阶段 1/6：正在构建第 {chapter_number} 章生产包", 5, 100)
    build_chapter_packet(workspace_id, chapter_number)
    if progress_callback:
        progress_callback(f"阶段 2/6：正在生成第 {chapter_number} 章正文", 20, 100)
    generate_chapter_draft(workspace_id, chapter_number)
    if progress_callback:
        progress_callback(f"阶段 3/6：正在审校第 {chapter_number} 章", 40, 100)
    audit = review_chapter(workspace_id, chapter_number)
    project = read_yaml(paths.project, {})
    writing_config = writing_quality_config(paths)
    max_rounds = (
        int(writing_config.get("max_repair_rounds", 2))
        if writing_config.get("enabled")
        else int(project.get("quality_policy", {}).get("max_repair_rounds", 2))
    )
    rounds = 0
    while not audit.get("approved") and rounds < max_rounds:
        if progress_callback:
            progress_callback(f"阶段 3/6：第 {chapter_number} 章未通过，正在执行第 {rounds + 1} 轮修复", 50, 100)
        repair_chapter(workspace_id, chapter_number)
        audit = review_chapter(workspace_id, chapter_number)
        rounds += 1
    if not audit.get("approved"):
        return {"chapter": chapter_number, "status": "needs_human_review", "writing_quality_status": audit.get("writing_quality_status"), "audit": audit, "repair_rounds": rounds}
    if progress_callback:
        progress_callback(f"阶段 4/6：正在提取并校验第 {chapter_number} 章状态变化", 70, 100)
    delta_result = extract_delta(workspace_id, chapter_number)
    if not delta_result["validation"]["ok"]:
        return {"chapter": chapter_number, "status": "delta_blocked", **delta_result}
    if auto_commit:
        if progress_callback:
            progress_callback(f"阶段 5/6：正在事务提交第 {chapter_number} 章", 90, 100)
        commit = commit_chapter(workspace_id, chapter_number)
        if progress_callback:
            progress_callback(f"阶段 6/6：第 {chapter_number} 章流程完成", 100, 100)
        return {
            "chapter": chapter_number,
            "status": "committed" if commit.get("committed") else "commit_blocked",
            "audit": audit,
            "writing_quality_status": audit.get("writing_quality_status"),
            "repair_rounds": rounds,
            "commit": commit,
        }
    if progress_callback:
        progress_callback(f"阶段 6/6：第 {chapter_number} 章已准备提交", 100, 100)
    return {"chapter": chapter_number, "status": "ready_to_commit", "writing_quality_status": audit.get("writing_quality_status"), "audit": audit, "repair_rounds": rounds, **delta_result}


def run_batch(
    workspace_id: str,
    start: int,
    count: int,
    auto_commit: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if count < 1 or count > 20:
        raise ValueError("count must be between 1 and 20")
    paths = get_workspace(workspace_id)
    ending_plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    maximum = 0
    if isinstance(ending_plan, dict) and str(ending_plan.get("status", "unset")) != "unset":
        maximum = int((ending_plan.get("target") or {}).get("max_chapter", 0) or 0)
    if maximum and start > maximum:
        raise ValueError(f"Batch start chapter {start} exceeds ending max chapter {maximum}")
    requested_end = start + count - 1
    planned_end = min(requested_end, maximum) if maximum else requested_end
    planned_count = max(0, planned_end - start + 1)
    results: list[dict[str, Any]] = []
    for index, chapter in enumerate(range(start, planned_end + 1)):
        if progress_callback:
            progress_callback(
                f"批量任务：开始处理第 {chapter} 章（{index + 1}/{planned_count}）",
                int(index * 100 / max(planned_count, 1)),
                100,
            )

        def chapter_progress(message: str, progress: float | None, total: float | None) -> None:
            if progress_callback is None:
                return
            chapter_fraction = (progress or 0.0) / (total or 100.0)
            overall = ((index + chapter_fraction) / max(planned_count, 1)) * 100
            progress_callback(f"第 {chapter} 章：{message}", min(99, overall), 100)

        if progress_callback is None:
            # Keep the existing call shape for integrations that monkeypatch or
            # wrap the pipeline with the pre-progress signature.
            result = run_chapter_pipeline(workspace_id, chapter, auto_commit=auto_commit)
        else:
            result = run_chapter_pipeline(
                workspace_id,
                chapter,
                auto_commit=auto_commit,
                progress_callback=chapter_progress,
            )
        results.append(result)
        if result.get("status") not in {"committed", "ready_to_commit"}:
            break
    if progress_callback:
        progress_callback(f"批量任务完成：处理了 {len(results)}/{planned_count} 章", 100, 100)
    return {
        "workspace_id": workspace_id,
        "start": start,
        "requested": count,
        "planned": planned_count,
        "completed": len(results),
        "clamped_to_ending_max": bool(maximum and requested_end > maximum),
        "ending_max": maximum or None,
        "results": results,
    }


def validate_workspace_by_id(workspace_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    report = validate_workspace(paths).to_dict()
    report["projection_status"] = projection_status(paths)
    return report


def list_routes_for_workspace(workspace_id: str | None = None) -> dict[str, Any]:
    routes = get_workspace(workspace_id).routes if workspace_id else None
    details = generation_mode_details(routes)
    mode = str(details["mode"])
    return {
        "generation_mode": mode,
        "effective_source": "current-agent" if mode == "agent" else "external-provider",
        "mode_source": details["source"],
        "process_override": details["process_override"],
        "workspace_routes": details["workspace_routes"],
        "configured_external_routes": list_routes(routes),
    }


def generation_mode_for_workspace(workspace_id: str | None = None) -> dict[str, Any]:
    routes = get_workspace(workspace_id).routes if workspace_id else None
    return generation_mode_details(routes)


def checkpoint_workspace(workspace_id: str, label: str = "manual") -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    path = create_checkpoint(paths, label)
    return {"checkpoint": path.name, "path": str(path)}


def restore_workspace(workspace_id: str, checkpoint_name: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    with workspace_lock(paths, "restore"):
        restore_checkpoint(paths, checkpoint_name)
        init_event_store(paths)
        seed_existing_workspace(paths)
    reindex_workspace(paths)
    return {"restored": True, "checkpoint": checkpoint_name}


def test_route_for_workspace(route_name: str, workspace_id: str | None = None) -> dict[str, Any]:
    routes = get_workspace(workspace_id).routes if workspace_id else None
    mode = load_generation_mode(routes)
    configured_routes = list_routes(routes)
    if route_name not in configured_routes:
        raise KeyError(f"Unknown route: {route_name}")
    if mode == "agent":
        return {
            "ok": True,
            "skipped": True,
            "generation_mode": "agent",
            "route": route_name,
            "provider": "current-agent",
            "model": "current-agent",
            "reason": "Agent mode skips external-provider connectivity tests",
        }
    result = test_route(route_name, routes)
    return {"generation_mode": "external", "skipped": False, **result}


def list_checkpoints(workspace_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    root = paths.root / "checkpoints"
    items = []
    if root.exists():
        for path in sorted((candidate for candidate in root.iterdir() if candidate.is_dir()), reverse=True):
            meta = read_json(path / "checkpoint.json", {})
            items.append({"name": path.name, "created_at": meta.get("created_at"), "label": meta.get("label")})
    return {"workspace_id": workspace_id, "checkpoints": items}


def rebuild_workspace_projections(workspace_id: str, prefix: str = "") -> dict[str, Any]:
    return rebuild_projections(get_workspace(workspace_id), prefix)


def workspace_event_log(workspace_id: str, limit: int = 100, event_type: str = "") -> dict[str, Any]:
    return {"workspace_id": workspace_id, "events": list_events(get_workspace(workspace_id), limit, event_type)}


def reindex_workspace_by_id(workspace_id: str) -> dict[str, Any]:
    return reindex_workspace(get_workspace(workspace_id))


def search_workspace_memory(workspace_id: str, query: str, top_k: int = 6) -> dict[str, Any]:
    return {"workspace_id": workspace_id, "query": query, "results": search_memory(get_workspace(workspace_id), query, top_k=top_k)}


def workspace_observability_report(workspace_id: str, limit: int = 5000) -> dict[str, Any]:
    return observability_report(get_workspace(workspace_id), limit)


def export_current_novel(
    workspace_id: str,
    format: str = "txt",
    output_path: str = "",
) -> dict[str, Any]:
    """Merge all currently committed chapter projections into a local export file."""
    paths = get_workspace(workspace_id)
    export_format = str(format or "txt").strip().lower().lstrip(".")
    if export_format not in {"txt", "md"}:
        raise ValueError("format must be txt or md")

    chapter_files: list[tuple[int, Path]] = []
    chapter_pattern = re.compile(r"^chapter-(\d+)\.md$")
    chapters_dir = paths.root / "chapters"
    if chapters_dir.exists():
        for path in chapters_dir.iterdir():
            match = chapter_pattern.match(path.name)
            if match and path.is_file():
                chapter_files.append((int(match.group(1)), path))
    chapter_files.sort(key=lambda item: item[0])
    if not chapter_files:
        raise ValueError("No committed chapter正文 found in chapters/")

    sections: list[str] = []
    for chapter_number, chapter_path in chapter_files:
        chapter = _chapter_body_with_title(paths, chapter_number, read_text(chapter_path))
        if export_format == "md":
            lines = chapter.splitlines()
            chapter = f"# {lines[0]}\n\n" + "\n".join(lines[1:]).lstrip()
        sections.append(chapter.strip())
    content = "\n\n\n".join(sections) + "\n"

    if output_path:
        target = (paths.root / output_path).resolve()
        if target != paths.root and paths.root not in target.parents:
            raise ValueError("output_path must stay inside the novel workspace")
        if not target.suffix:
            target = target.with_suffix(f".{export_format}")
    else:
        project = read_yaml(paths.project, {})
        title = str(project.get("title", "") or workspace_id).strip()
        safe_title = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" .") or workspace_id
        target = paths.root / "exports" / f"{safe_title}.{export_format}"

    atomic_write_text(target, content)
    return {
        "exported": True,
        "workspace_id": workspace_id,
        "output_path": str(target),
        "format": export_format,
        "chapter_count": len(chapter_files),
        "first_chapter": chapter_files[0][0],
        "last_chapter": chapter_files[-1][0],
    }


def import_writing_reference(workspace_id: str, source_path: str, title: str = "") -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _import_writing_reference(paths, source_path, title)


def scan_writing_references(workspace_id: str, source_path: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return scan_reference_library(paths, source_path)


def analyze_writing_reference(
    workspace_id: str,
    reference_id: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    root = paths.root / "writing" / "references" / reference_id
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Unknown writing reference: {reference_id}")
    batches = reference_batches(paths, reference_id)
    analyses: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, 1):
        output_path = root / f"analysis-batch-{index:04d}.json"
        cached = read_json(output_path, {})
        if isinstance(cached, dict) and isinstance(cached.get("principles"), list):
            cached.setdefault("exemplars", [])
            analyses.append(cached)
            continue
        if progress_callback:
            progress_callback(f"写作参考分析 {index}/{len(batches)}", 5 + (index - 1) / max(len(batches), 1) * 90, 100)
        result = _call(
            paths,
            "director",
            [
                {"role": "system", "content": "你是写作方法论分析器。提炼方法、判断标准与失败模式，不复制长段原文，不生成小说正文。"},
                {"role": "user", "content": writing_analysis_prompt(reference_id, batch)},
            ],
            purpose="writing_reference.analyze",
        )
        analysis = extract_json_object(result.content)
        principles = analysis.get("principles")
        if not isinstance(principles, list):
            raise ValueError("Writing reference analysis must contain principles list")
        for item in principles:
            if not isinstance(item, Mapping) or not str(item.get("principle", "")).strip() or not isinstance(item.get("source_refs"), list):
                raise ValueError("Each writing principle requires principle and source_refs")
        exemplars = analysis.get("exemplars", [])
        if not isinstance(exemplars, list):
            raise ValueError("Writing reference analysis exemplars must be a list")
        normalized_exemplars = []
        for item in exemplars:
            if isinstance(item, Mapping):
                normalized_exemplars.append(_exemplar_module().ExemplarCard.from_mapping(item).to_dict())
        analysis["exemplars"] = normalized_exemplars
        write_json(output_path, analysis)
        analyses.append(analysis)
    manifest = read_json(manifest_path, {})
    manifest.update({
        "status": "analyzed",
        "analyzed_at": utc_now(),
        "batch_count": len(batches),
        "principle_count": sum(len(item.get("principles", [])) for item in analyses),
    })
    write_json(manifest_path, manifest)
    if progress_callback:
        progress_callback("写作参考分析完成", 100, 100)
    return {
        "reference_id": reference_id,
        "status": "analyzed",
        "batch_count": len(batches),
        "principle_count": manifest["principle_count"],
        "path": str(root),
    }


def generate_doctrine_proposal(
    workspace_id: str,
    reference_id: str,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    root = paths.root / "writing" / "references" / reference_id
    analyses = [read_json(path, {}) for path in sorted(root.glob("analysis-batch-*.json"))]
    if not analyses:
        raise ValueError("Analyze the writing reference before generating a doctrine proposal")
    if progress_callback:
        progress_callback("正在归并写作方法、去重并检测冲突", 20, 100)
    result = _call(
        paths,
        "director",
        [
            {"role": "system", "content": "你是 Writing Doctrine 设计器。把方法论转为可审计 Rule Cards；不能关闭或弱化任何 Canon 硬门禁。"},
            {"role": "user", "content": doctrine_proposal_prompt(reference_id, analyses)},
        ],
        purpose="writing_reference.doctrine_proposal",
    )
    proposal = validate_doctrine_proposal(extract_json_object(result.content))
    all_exemplars = [item for analysis in analyses for item in analysis.get("exemplars", []) if isinstance(item, Mapping)]
    proposal["exemplars"] = _exemplar_module().deduplicate_exemplars(all_exemplars)
    proposal["exemplar_index"] = _exemplar_module().build_exemplar_index(proposal["exemplars"])
    proposal.update({
        "reference_id": reference_id,
        "created_at": utc_now(),
        "status": "proposed",
        "storage_scope": "reference_global",
    })
    from .writing_doctrine import writing_doctrine_root

    ensure_writing_workspace(paths)
    proposal_path = writing_doctrine_root() / "proposals" / f"{reference_id}.json"
    write_json(proposal_path, proposal)
    if progress_callback:
        progress_callback("Writing Doctrine 提案已生成，等待用户确认", 100, 100)
    return {
        "reference_id": reference_id,
        "proposal": str(proposal_path),
        "rule_count": len(proposal["rules"]),
        "exemplar_count": len(proposal.get("exemplars", [])),
        "conflicts": proposal.get("conflicts", []),
    }


def apply_doctrine_proposal(workspace_id: str, proposal_relative_path: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    from .writing_doctrine import writing_doctrine_root

    requested = Path(proposal_relative_path).expanduser()
    proposal_path = requested.resolve() if requested.is_absolute() else (paths.root / requested).resolve()
    proposal_root = (paths.root / "writing" / "proposals").resolve()
    global_proposal_root = (writing_doctrine_root() / "proposals").resolve()
    if not proposal_path.exists() or not any(root in proposal_path.parents for root in (proposal_root, global_proposal_root)):
        raise ValueError("Doctrine proposal must exist under workspace or global writing-doctrine proposals")
    proposal = validate_doctrine_proposal(read_json(proposal_path, {}))
    raw_proposal = read_json(proposal_path, {})
    if raw_proposal.get("storage_scope") == "reference_global":
        from .writing_doctrine import apply_reference_rules

        unresolved = [
            item for item in proposal.get("conflicts", [])
            if isinstance(item, Mapping)
            and str(item.get("severity", "")).lower() == "critical"
            and str(item.get("status", "unresolved")).lower() != "resolved"
        ]
        if unresolved:
            return {"applied": False, "blocked": True, "conflicts": unresolved}
        result = apply_reference_rules(proposal["rules"])
        if proposal.get("exemplars"):
            exemplar_root = writing_doctrine_root()
            existing_data = read_yaml(exemplar_root / "exemplars.yaml", {"exemplars": []})
            merged = _exemplar_module().deduplicate_exemplars([*(existing_data.get("exemplars", []) if isinstance(existing_data, Mapping) else []), *proposal["exemplars"]])
            write_yaml(exemplar_root / "exemplars.yaml", {"exemplars": merged, "updated_at": utc_now()})
            write_json(exemplar_root / "exemplar-index.json", _exemplar_module().build_exemplar_index(merged))
            result["exemplar_count"] = len(merged)
        raw_proposal.update({"status": "applied", "applied_at": utc_now()})
        write_json(proposal_path, raw_proposal)
        return {"applied": True, "scope": "reference_global", "proposal": str(proposal_path), **result}
    ensure_writing_workspace(paths)
    doctrine_path = paths.root / "writing" / "project-doctrine.yaml"
    doctrine = read_yaml(doctrine_path, {"rules": []})
    existing = {str(item.get("id")): dict(item) for item in doctrine.get("rules", []) if isinstance(item, Mapping)}
    for rule in proposal["rules"]:
        existing[rule["id"]] = rule
    doctrine_content = _yaml_text({"updated_at": utc_now(), "rules": list(existing.values())})
    commit_projection_event(
        paths,
        event_type="writing.doctrine_applied",
        payload={"proposal": str(proposal_path.relative_to(paths.root)), "rule_count": len(existing)},
        projections={"writing/project-doctrine.yaml": doctrine_content},
    )
    proposal["status"] = "applied"
    proposal["applied_at"] = utc_now()
    write_json(proposal_path, proposal)
    return {"applied": True, "proposal": str(proposal_path), "doctrine": str(doctrine_path), "rule_count": len(existing)}


def list_exemplars(workspace_id: str, enabled_only: bool = False) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    ensure_writing_workspace(paths)
    from .writing_doctrine import writing_doctrine_root
    values: list[dict[str, Any]] = []
    for path in (writing_doctrine_root() / "exemplars.yaml", paths.root / "writing" / "exemplars.yaml"):
        data = read_yaml(path, {"exemplars": []})
        values.extend([dict(item) for item in data.get("exemplars", []) if isinstance(item, Mapping)])
    cards = _exemplar_module().deduplicate_exemplars(values)
    if enabled_only:
        cards = [card for card in cards if card.get("enabled", True)]
    return {"workspace_id": workspace_id, "count": len(cards), "exemplars": cards, "index": _exemplar_module().build_exemplar_index(cards)}


def list_writing_rules(workspace_id: str, enabled_only: bool = False) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    rules = load_writing_rules(paths)
    if enabled_only:
        rules = [rule for rule in rules if rule.get("enabled")]
    return {"workspace_id": workspace_id, "config": writing_quality_config(paths), "count": len(rules), "rules": rules}


def select_scene_exemplars(
    workspace_id: str,
    scene: Mapping[str, Any],
    rules: list[Mapping[str, Any]] | None = None,
    *,
    limit: int | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    from .writing_profiles import writing_exemplars_config

    paths = get_workspace(workspace_id)
    config = writing_exemplars_config(paths)
    if not config.get("enabled", True):
        return {"workspace_id": workspace_id, "enabled": False, "selected": [], "count": 0}
    available = list_exemplars(workspace_id, enabled_only=True)["exemplars"]
    selected = _exemplar_module().select_exemplars(
        scene,
        rules or [],
        available,
        limit=config["max_exemplars_per_scene"] if limit is None else limit,
        token_budget=config["max_exemplar_tokens"] if token_budget is None else token_budget,
    )
    return {
        "workspace_id": workspace_id,
        "enabled": True,
        "selected": selected,
        "count": len(selected),
        "token_budget": config["max_exemplar_tokens"] if token_budget is None else token_budget,
    }


def simulate_character_intents(
    characters: list[Mapping[str, Any]],
    scene: Mapping[str, Any],
    *,
    workspace_id: str | None = None,
    enabled: bool | None = None,
    include_tom: bool | None = None,
    max_characters: int | None = None,
) -> dict[str, Any]:
    from .writing_profiles import character_simulation_config

    config = {
        "enabled": True,
        "theory_of_mind": True,
        "max_simulated_characters_per_scene": 4,
    }
    if workspace_id:
        config = character_simulation_config(get_workspace(workspace_id))
    use_enabled = config["enabled"] if enabled is None else enabled
    use_tom = config["theory_of_mind"] if include_tom is None else include_tom
    use_max = config["max_simulated_characters_per_scene"] if max_characters is None else max_characters
    if not use_enabled:
        return {"enabled": False, "intents": [], "max_simulated_characters": use_max}
    sim = importlib.import_module("novel_production_mcp.character_simulation")
    selected = sim.select_characters_for_simulation(characters, scene, max_characters=use_max)
    intents = []
    for value in selected:
        state = sim.CharacterSimulationState.from_mapping(value)
        intent = sim.simulate_intent(state, scene, include_tom=use_tom)
        sim.assert_no_private_leak(intent, scene)
        intents.append(intent)
    audit_dir = None
    if workspace_id and scene.get("chapter") and scene.get("scene_index"):
        paths = get_workspace(workspace_id)
        chapter = int(scene["chapter"])
        scene_index = int(scene["scene_index"])
        audit_dir = paths.root / "analysis" / "character-simulation" / f"chapter-{chapter:04d}" / f"scene-{scene_index:02d}"
        audit_dir.mkdir(parents=True, exist_ok=True)
        for intent in intents:
            write_json(audit_dir / f"{intent['character_id']}.json", {
                "input_state_summary": {
                    "character_id": intent["character_id"],
                    "goal": intent.get("goal"),
                    "belief_count": len(intent.get("relevant_beliefs") or []),
                },
                "intent": {k: v for k, v in intent.items() if k != "perception"},
                "updated_at": utc_now(),
            })
    return {
        "enabled": True,
        "intents": intents,
        "max_simulated_characters": use_max,
        "selected_character_ids": [item.get("character_id") for item in selected],
        "audit_dir": str(audit_dir) if audit_dir else None,
    }


def direct_scene_execution(
    scene: Mapping[str, Any],
    intents: list[Mapping[str, Any]],
    *,
    workspace_id: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    if not enabled:
        return {"enabled": False, "execution_plan": None}
    sim = importlib.import_module("novel_production_mcp.scene_simulation")
    plan = sim.direct_scene(scene, intents)
    world = sim.react_to_world(scene.get("environment_state", {}), plan)
    if workspace_id and scene.get("chapter") and scene.get("scene_index"):
        paths = get_workspace(workspace_id)
        chapter = int(scene["chapter"])
        scene_index = int(scene["scene_index"])
        audit_dir = paths.root / "analysis" / "character-simulation" / f"chapter-{chapter:04d}" / f"scene-{scene_index:02d}"
        audit_dir.mkdir(parents=True, exist_ok=True)
        write_json(audit_dir / "director.json", {"execution_plan": plan, "world_state": world, "updated_at": utc_now()})
    return {"enabled": True, "execution_plan": plan, "world_state": world}


def propose_arc_reflection(
    character_id: str,
    events: list[Mapping[str, Any]],
    *,
    workspace_id: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    from .writing_profiles import arc_reflection_config

    use_enabled = True if enabled is None else enabled
    if workspace_id and enabled is None:
        use_enabled = bool(arc_reflection_config(get_workspace(workspace_id)).get("enabled", True))
    if not use_enabled:
        return {"enabled": False, "reflection": None}
    reflection = importlib.import_module("novel_production_mcp.arc_reflection")
    proposal = reflection.reflect_arc(events, character_id=character_id)
    if workspace_id:
        paths = get_workspace(workspace_id)
        out = paths.root / "analysis" / "arc-reflections" / f"{character_id}-{utc_now().replace(':', '').replace('+', 'Z')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, proposal)
        return {"enabled": True, "reflection": proposal, "path": str(out)}
    return {"enabled": True, "reflection": proposal}


def apply_belief_updates_for_character(
    workspace_id: str,
    character_id: str,
    updates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply evidence-backed belief updates into state/characters/<id>.yaml simulation fields."""
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    state_path = paths.root / "state" / "characters" / f"{character_id}.yaml"
    state = read_yaml(state_path, {})
    if not isinstance(state, dict):
        state = {}
    sim = importlib.import_module("novel_production_mcp.character_simulation")
    beliefs = sim.apply_belief_updates(state.get("beliefs") or [], updates)
    state["beliefs"] = beliefs
    state["character_id"] = character_id
    state["updated_at"] = utc_now()
    content = _yaml_text(state)
    event = commit_projection_event(
        paths,
        event_type="character.beliefs_updated",
        payload={"character_id": character_id, "update_count": len(updates)},
        projections={f"state/characters/{character_id}.yaml": content},
    )
    return {"character_id": character_id, "belief_count": len(beliefs), "event": event}


def prepare_scene_context(
    workspace_id: str,
    chapter_number: int,
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    """Build scene-scoped rules, exemplars, intents, and director plan with feature flags."""
    from .writing_profiles import (
        character_simulation_config,
        scene_generation_config,
        writing_exemplars_config,
        writing_quality_config,
    )

    paths = get_workspace(workspace_id)
    outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    scene_meta = dict(scene)
    scene_meta.setdefault("chapter", chapter_number)
    scene_meta.setdefault("pov_character", outline.get("pov_character"))
    scene_meta.setdefault("participants", outline.get("participants") or [])
    scene_meta.setdefault("genre", read_yaml(paths.project, {}).get("genre", ""))
    importance = str(scene_meta.get("importance", "normal") or "normal")
    scene_mod = importlib.import_module("novel_production_mcp.scene_simulation")
    quality = writing_quality_config(paths)
    budget = scene_mod.scene_budget(
        importance,
        max_rules=int(quality.get("max_writer_rules") or quality.get("max_rules_per_scene") or 8),
        max_exemplars=int(writing_exemplars_config(paths).get("max_exemplars_per_scene", 2)),
    )
    rules = select_writing_rules(paths, chapter_number, {**outline, **scene_meta}, stage="writer")[: budget["max_rules"]]
    exemplars = select_scene_exemplars(
        workspace_id,
        scene_meta,
        rules,
        limit=budget["max_exemplars"],
        token_budget=writing_exemplars_config(paths)["max_exemplar_tokens"],
    )
    characters: list[dict[str, Any]] = []
    for character_id in scene_meta.get("participants") or []:
        state = read_yaml(paths.root / "state" / "characters" / f"{character_id}.yaml", {})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("character_id", character_id)
        characters.append(state)
    sim_cfg = character_simulation_config(paths)
    intents = simulate_character_intents(
        characters,
        scene_meta,
        workspace_id=workspace_id,
        enabled=sim_cfg["enabled"],
        include_tom=sim_cfg["theory_of_mind"],
        max_characters=sim_cfg["max_simulated_characters_per_scene"],
    )
    director = direct_scene_execution(
        scene_meta,
        intents.get("intents") or [],
        workspace_id=workspace_id,
        enabled=sim_cfg["enabled"],
    )
    scene_cfg = scene_generation_config(paths)
    return {
        "workspace_id": workspace_id,
        "chapter": chapter_number,
        "scene": scene_meta,
        "importance": importance,
        "budget": budget,
        "rules": [{"id": rule.get("id"), "name": rule.get("name")} for rule in rules],
        "exemplars": exemplars,
        "intents": intents,
        "director": director,
        "scene_generation": scene_cfg,
        "writer_safe_exemplars": _exemplar_module().writer_safe_exemplars(exemplars.get("selected") or []),
    }


def save_scene_checkpoint(
    workspace_id: str,
    chapter_number: int,
    scene_index: int,
    *,
    draft: str = "",
    intents: list[Mapping[str, Any]] | None = None,
    execution_plan: Mapping[str, Any] | None = None,
    state_delta: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    scene_mod = importlib.import_module("novel_production_mcp.scene_simulation")
    result = scene_mod.save_scene_checkpoint(
        paths.root,
        chapter_number,
        scene_index,
        draft=draft,
        intents=intents,
        execution_plan=execution_plan,
        state_delta=state_delta,
        metadata=metadata,
    )
    return {"workspace_id": workspace_id, **result}


def load_scene_checkpoints(workspace_id: str, chapter_number: int) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    scene_mod = importlib.import_module("novel_production_mcp.scene_simulation")
    return {"workspace_id": workspace_id, **scene_mod.load_scene_progress(paths.root, chapter_number)}


def assemble_chapter_from_scene_checkpoints(workspace_id: str, chapter_number: int) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    scene_mod = importlib.import_module("novel_production_mcp.scene_simulation")
    text = scene_mod.assemble_chapter_from_scenes(paths.root, chapter_number)
    if not text.strip():
        raise ValueError(f"No completed scene drafts found for chapter {chapter_number}")
    draft_path = paths.chapter_file(chapter_number, "draft")
    atomic_write_text(draft_path, text)
    return {
        "workspace_id": workspace_id,
        "chapter": chapter_number,
        "draft": str(draft_path),
        "characters": len(text),
    }

def rebuild_writing_index() -> dict[str, Any]:
    from .writing_doctrine import rebuild_writing_rule_index

    index = rebuild_writing_rule_index()
    return {"rebuilt": True, "rule_count": len(index["by_rule_id"]), "index": index}


def get_writing_rule(workspace_id: str, query: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    needle = query.strip().lower()
    rules = load_writing_rules(paths)
    matched = [
        rule for rule in rules
        if needle == str(rule.get("id", "")).lower()
        or needle in str(rule.get("name", "")).lower()
        or needle in {*(str(value).lower() for value in rule.get("domains", [])), *(str(value).lower() for value in rule.get("scene_types", [])), *(str(value).lower() for value in rule.get("techniques", [])), *(str(value).lower() for value in rule.get("genres", []))}
        or any(needle in str(ref.get("reference_id", "")).lower() for ref in rule.get("source_refs", []) if isinstance(ref, Mapping))
    ]
    return {"query": query, "count": len(matched), "rules": matched}


def enable_writing_rule(workspace_id: str, rule_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    result = set_rule_enabled(paths, rule_id, True)
    content = read_text(paths.root / "writing" / "rule-overrides.yaml")
    commit_projection_event(
        paths,
        event_type="writing.rule_enabled",
        payload={"rule_id": rule_id},
        projections={"writing/rule-overrides.yaml": content},
    )
    return result


def disable_writing_rule(workspace_id: str, rule_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    result = set_rule_enabled(paths, rule_id, False)
    content = read_text(paths.root / "writing" / "rule-overrides.yaml")
    commit_projection_event(
        paths,
        event_type="writing.rule_disabled",
        payload={"rule_id": rule_id},
        projections={"writing/rule-overrides.yaml": content},
    )
    return result


def test_writing_gates() -> dict[str, Any]:
    return run_writing_gate_evals()




def import_existing_novel(
    workspace_id: str,
    title: str,
    source_path: str,
    genre: str = "",
    planned_chapters: int = 0,
) -> dict[str, Any]:
    """Create a takeover workspace from an existing UTF-8 novel without modifying the source."""
    preface, chapters = collect_source_chapters(source_path)
    last = max(item.number for item in chapters)
    requested_target = _safe_int(planned_chapters or 0)
    planned = max(requested_target, last) if requested_target > 0 else last + 30
    created = create_workspace(workspace_id, title, genre, premise="Imported existing novel; canon pending evidence reconstruction.", planned_chapters=planned)
    paths = get_workspace(workspace_id)
    imported = _import_into_workspace(paths, source_path, preface, chapters, ending_target_configured=bool(planned_chapters))
    return {
        "workspace_id": workspace_id,
        "path": str(paths.root),
        "created": created["event"],
        "imported": imported,
        "next_step": "novel_analyze_imported_novel",
    }


def analyze_imported_novel(
    workspace_id: str,
    batch_size: int = 5,
    max_chars: int = 42000,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _analyze_imported(
        paths,
        _call,
        batch_size=batch_size,
        max_chars=max_chars,
        force=force,
        progress_callback=progress_callback,
    )


def generate_takeover_proposal(
    workspace_id: str,
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _generate_takeover_proposal(paths, _call, instruction, progress_callback=progress_callback)


def apply_takeover_proposal(
    workspace_id: str,
    proposal_relative_path: str = "analysis/reports/takeover-proposal.json",
    accept_unresolved_conflicts: bool = False,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _apply_takeover_proposal(paths, proposal_relative_path, accept_unresolved_conflicts=accept_unresolved_conflicts)


def takeover_status(workspace_id: str) -> dict[str, Any]:
    return _takeover_status(get_workspace(workspace_id))


def generate_continuation_plan(
    workspace_id: str,
    chapter_count: int = 30,
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
    batch_size: int = 10,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _generate_continuation_plan(
        paths,
        _call,
        chapter_count,
        instruction,
        progress_callback=progress_callback,
        batch_size=batch_size,
    )


def apply_continuation_plan(workspace_id: str, proposal_relative_path: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _apply_continuation_plan(paths, proposal_relative_path)


def set_ending_target(workspace_id: str, ideal_chapter: int, min_chapter: int = 0, max_chapter: int = 0, creative_brief: str = "") -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _set_ending_target(paths, ideal_chapter, min_chapter, max_chapter, creative_brief)


def generate_ending_options(
    workspace_id: str,
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _generate_ending_options(paths, _call, instruction, progress_callback=progress_callback)


def apply_ending_plan(workspace_id: str, proposal_relative_path: str, option_id: str = "") -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _apply_ending_plan(paths, proposal_relative_path, option_id)


def rebalance_story_budget(workspace_id: str, final_arc_start: int = 0) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _rebalance_story_budget(paths, final_arc_start)


def check_ending_progress(workspace_id: str) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _check_ending_progress(paths)


def enter_final_arc(workspace_id: str, start_chapter: int = 0) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _enter_final_arc(paths, start_chapter)


def finalize_novel(workspace_id: str, force: bool = False) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    return _finalize_novel(paths, force)


def migrate_workspace(workspace_id: str) -> dict[str, Any]:
    """Upgrade a v1/v2 workspace to the v3 event-store + ending/story-budget architecture."""
    paths = get_workspace(workspace_id)
    if not paths.root.exists() or not paths.project.exists():
        raise FileNotFoundError(f"Workspace does not exist: {paths.root}")
    with workspace_lock(paths, "migration"):
        _mkdirs(paths.root)
        project = copy.deepcopy(read_yaml(paths.project, {}))
        old_version = _safe_int(project.get("schema_version", 1) or 1, 1)
        if old_version >= 3 and paths.database.exists():
            return {"migrated": False, "schema_version": old_version, "reason": "already_current"}
        init_event_store(paths)
        seed_existing_workspace(paths)
        project["schema_version"] = 3
        project["updated_at"] = utc_now()
        context_policy = project.setdefault("context_policy", {})
        context_policy.setdefault("previous_chapter_tail_chars", 2500)
        context_policy.setdefault("max_packet_chars", 56000)
        context_policy.setdefault("retrieval_top_k", 6)
        context_policy.setdefault("retrieval_max_chars", 9000)
        quality_policy = project.setdefault("quality_policy", {})
        quality_policy.setdefault("min_review_score", 85)
        quality_policy.setdefault("max_repair_rounds", 2)
        quality_policy.setdefault("max_relationship_delta_per_chapter", 15)
        quality_policy.setdefault("allow_unapproved_ability_additions", False)
        quality_policy.setdefault("require_human_approval_for_canon_changes", True)
        quality_policy.setdefault("require_fact_ids", True)
        quality_policy.setdefault("require_inventory_events", True)
        last = _safe_int(project.get("last_committed_chapter", 0) or 0)
        ideal = max(_safe_int(project.get("planned_chapters", 30) or 30, 30), last)
        takeover_mode = str(project.get("mode", "")) == "existing_novel_takeover"
        ending_status = "unset" if takeover_mode and "ending_status" not in project else str(project.get("ending_status", "target_only"))
        ending_plan, story_budget, ending_progress = make_ending_plan(
            min_chapter=ideal, ideal_chapter=ideal, max_chapter=ideal,
            target_source="provisional_migration" if ending_status == "unset" else "migrated_planned_chapters",
            last_committed=last, status=ending_status,
        )
        project["planned_chapters"] = ideal
        project["length_plan"] = copy.deepcopy(ending_plan["target"])
        project["ending_status"] = ending_status
        defaults = {
            "canon/facts.yaml": _yaml_text({"facts": []}),
            "canon/locations.yaml": _yaml_text({"locations": [], "travel_minutes": {}}),
            "state/item-ledger.yaml": _yaml_text({"items": {}, "balances": {}, "events": []}),
            "state/inventory.yaml": _yaml_text({"characters": {}}),
            "planning/ending/ending-plan.yaml": _yaml_text(ending_plan),
            "planning/story-budget.yaml": _yaml_text(story_budget),
            "state/ending-progress.yaml": _yaml_text(ending_progress),
        }
        projections = {"project.yaml": _yaml_text(project)}
        for relative, content in defaults.items():
            if not (paths.root / relative).exists():
                projections[relative] = content
        checkpoint = create_checkpoint(paths, "before-v3-migration")
        event = commit_projection_event(
            paths,
            event_type="workspace.migrated",
            payload={"from": old_version, "to": 3, "checkpoint": checkpoint.name},
            projections=projections,
        )
    reindex_workspace(paths)
    return {"migrated": True, "from": old_version, "to": 3, "checkpoint": checkpoint.name, "event": event}


def adopt_file_changes(
    workspace_id: str,
    relative_paths: list[str],
    note: str,
) -> dict[str, Any]:
    """Explicitly approve selected manual text-file edits as new database projections."""
    if not relative_paths:
        raise ValueError("relative_paths cannot be empty")
    paths = get_workspace(workspace_id)
    allowed_roots = {"canon", "characters", "state", "planning", "project.yaml", "routes.toml"}
    projections: dict[str, str] = {}
    for raw in relative_paths:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid relative path: {raw}")
        first = relative.parts[0] if relative.parts else ""
        if first not in allowed_roots:
            raise ValueError(f"Manual adoption is not allowed for: {raw}")
        target = (paths.root / relative).resolve()
        if paths.root.resolve() not in target.parents and target != paths.root.resolve():
            raise ValueError(f"Path escaped workspace: {raw}")
        if not target.is_file():
            raise FileNotFoundError(f"File does not exist: {raw}")
        try:
            projections[relative.as_posix()] = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Only UTF-8 text files can be adopted: {raw}") from exc
    with workspace_lock(paths, "adopt"):
        checkpoint = create_checkpoint(paths, "before-adopt-manual-edits")
        event = commit_projection_event(
            paths,
            event_type="workspace.manual_edits_adopted",
            payload={"paths": sorted(projections), "note": note.strip(), "checkpoint": checkpoint.name},
            projections=projections,
            input_hash=sha256_text("".join(projections[key] for key in sorted(projections))),
        )
    reindex_workspace(paths)
    return {"adopted": sorted(projections), "checkpoint": checkpoint.name, "event": event}


def register_fact(
    workspace_id: str,
    fact_id: str,
    statement: str,
    category: str = "general",
    immutable: bool = False,
    visibility: str = "public",
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", fact_id.strip()):
        raise ValueError("fact_id must be a stable ASCII identifier")
    visibility = visibility.strip().lower()
    if visibility not in {"public", "private", "secret"}:
        raise ValueError("visibility must be public, private, or secret")
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    relative = "canon/immutable-facts.yaml" if immutable else "canon/facts.yaml"
    with workspace_lock(paths, "canon"):
        data = copy.deepcopy(read_yaml(paths.root / relative, {"facts": []}))
        facts = data.setdefault("facts", [])
        if any(str(item.get("id")) == fact_id for item in facts if isinstance(item, dict)):
            raise ValueError(f"Fact already exists: {fact_id}")
        facts.append({
            "id": fact_id,
            "statement": statement.strip(),
            "category": category,
            "visibility": visibility,
            "created_at": utc_now(),
        })
        event = commit_projection_event(
            paths,
            event_type="canon.fact_registered",
            payload={"fact_id": fact_id, "immutable": immutable, "visibility": visibility},
            projections={relative: _yaml_text(data)},
        )
    reindex_workspace(paths)
    return {"registered": True, "fact_id": fact_id, "immutable": immutable, "visibility": visibility, "event": event}


def register_item(
    workspace_id: str,
    item_id: str,
    name: str,
    description: str = "",
    initial_balances: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", item_id.strip()):
        raise ValueError("item_id must be a stable ASCII identifier")
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    with workspace_lock(paths, "canon"):
        ledger = copy.deepcopy(read_yaml(paths.root / "state" / "item-ledger.yaml", {"items": {}, "balances": {}, "events": []}))
        items = ledger.setdefault("items", {})
        if item_id in items:
            raise ValueError(f"Item already exists: {item_id}")
        items[item_id] = {"name": name.strip(), "description": description.strip(), "created_at": utc_now()}
        balances = ledger.setdefault("balances", {})
        index = read_yaml(paths.root / "characters" / "index.yaml", {"characters": []})
        valid_chars = {
            str(item.get("id")) for item in index.get("characters", [])
            if isinstance(item, dict) and item.get("id")
        }
        for char_id, quantity in (initial_balances or {}).items():
            if char_id not in valid_chars:
                raise ValueError(f"Unknown character for initial balance: {char_id}")
            value = _safe_int(quantity)
            if str(quantity).strip() and value == 0 and quantity not in (0, "0"):
                raise ValueError("Initial balance must be an integer")
            if value < 0:
                raise ValueError("Initial balance cannot be negative")
            balances.setdefault(char_id, {})[item_id] = value
        projections = {
            "state/item-ledger.yaml": _yaml_text(ledger),
            "state/inventory.yaml": _yaml_text({"characters": balances}),
        }
        event = commit_projection_event(
            paths,
            event_type="canon.item_registered",
            payload={"item_id": item_id, "initial_balances": dict(initial_balances or {})},
            projections=projections,
        )
    return {"registered": True, "item_id": item_id, "event": event}


def set_travel_time(workspace_id: str, source_location_id: str, target_location_id: str, minutes: int) -> dict[str, Any]:
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    if minutes < 0:
        raise ValueError("minutes cannot be negative")
    with workspace_lock(paths, "canon"):
        data = copy.deepcopy(read_yaml(paths.root / "canon" / "locations.yaml", {"locations": [], "travel_minutes": {}}))
        known = {str(item.get("id")) for item in data.get("locations", []) if isinstance(item, dict) and item.get("id")}
        if source_location_id not in known or target_location_id not in known:
            raise ValueError("Both locations must already exist in canon/locations.yaml")
        matrix = data.setdefault("travel_minutes", {})
        matrix.setdefault(source_location_id, {})[target_location_id] = _safe_int(minutes)
        matrix.setdefault(target_location_id, {})[source_location_id] = _safe_int(minutes)
        event = commit_projection_event(
            paths,
            event_type="canon.travel_time_set",
            payload={"source": source_location_id, "target": target_location_id, "minutes": minutes},
            projections={"canon/locations.yaml": _yaml_text(data)},
        )
    return {"updated": True, "source": source_location_id, "target": target_location_id, "minutes": minutes, "event": event}


def approve_character_ability(
    workspace_id: str,
    character_id: str,
    ability_id: str,
    level: int = 1,
    reason: str = "",
) -> dict[str, Any]:
    """Explicit human approval path for a canon-changing ability addition."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", ability_id.strip()):
        raise ValueError("ability_id must be a stable ASCII identifier")
    paths = get_workspace(workspace_id)
    _ensure_projection_clean(paths)
    with workspace_lock(paths, "canon"):
        profile_path = paths.root / "characters" / "profiles" / f"{character_id}.yaml"
        profile = copy.deepcopy(read_yaml(profile_path, {}))
        if not profile:
            raise FileNotFoundError(f"Unknown character: {character_id}")
        abilities = profile.setdefault("abilities", {})
        if isinstance(abilities, list):
            abilities = {str(value): 1 for value in abilities}
            profile["abilities"] = abilities
        abilities[ability_id] = _safe_int(level)
        approvals_path = paths.root / "canon" / "approved-changes.yaml"
        approvals = copy.deepcopy(read_yaml(approvals_path, {"changes": []}))
        approvals.setdefault("changes", []).append({
            "type": "character_ability",
            "character_id": character_id,
            "ability_id": ability_id,
            "level": _safe_int(level),
            "reason": reason.strip(),
            "approved_at": utc_now(),
        })
        event = commit_projection_event(
            paths,
            event_type="canon.ability_approved",
            payload={"character_id": character_id, "ability_id": ability_id, "level": level, "reason": reason},
            projections={
                f"characters/profiles/{character_id}.yaml": _yaml_text(profile),
                "canon/approved-changes.yaml": _yaml_text(approvals),
            },
        )
    reindex_workspace(paths)
    return {"approved": True, "character_id": character_id, "ability_id": ability_id, "event": event}
