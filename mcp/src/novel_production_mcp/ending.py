from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from .event_store import commit_projection_event
from .models import ModelResult, ProgressCallback, WorkspacePaths
from .parsing import extract_json_object
from .retrieval import reindex_workspace
from .storage import create_checkpoint, read_json, read_yaml, utc_now, workspace_lock, write_json

ENDING_OPTIONS_SYSTEM = """你是长篇小说终局设计师。必须服从已有正典、人物成长、知识边界、活跃剧情线、伏笔和章节预算。
你的任务是设计可执行的结局，不得为了结局方便而改写已经发生的事实。每个方案都必须说明必须回收的主线/伏笔、人物最终落点、终局阶段和风险。
must_resolve_threads / optional_unresolved_threads 只能引用当前 plot ledger 已存在的 thread id；must_resolve_foreshadowing / optional_unresolved_foreshadowing 只能引用当前 foreshadowing ledger 已存在的 clue id。若当前没有对应 ID，则返回空数组，不得编造。required_outcomes / relationship_outcomes 可以为本次结局规划创建新的稳定 outcome id。
只输出一个 JSON 对象，不要 Markdown。"""


def _report_progress(
    callback: ProgressCallback | None,
    message: str,
    progress: float | None = None,
    total: float | None = 100.0,
) -> None:
    if callback is not None:
        callback(message, progress, total)


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def parse_chapter_range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        try:
            return int(value.get("start")), int(value.get("end"))
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+)\s*[-–—~至]\s*(\d+)\s*", value)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _stage_ranges(ideal: int, final_arc_start: int | None = None) -> list[dict[str, Any]]:
    if ideal < 1:
        raise ValueError("ideal chapter must be >= 1")
    if ideal <= 7:
        return [{"id": "final_arc", "title": "完整短篇弧", "chapter_range": [1, ideal]}]
    final_start = int(final_arc_start or default_final_arc_start(ideal, 0))
    final_start = min(max(2, final_start), ideal)
    pre_end = final_start - 1
    pre_labels = [
        ("opening_hook", "开篇钩子", 0.08),
        ("setup", "核心建立", 0.23),
        ("expansion", "世界与冲突展开", 0.51),
        ("midpoint", "中段升级/转折", 0.68),
        ("truth_convergence", "真相逼近与主线收束", 0.88),
        ("finale_preparation", "终局准备", 1.00),
    ]
    result: list[dict[str, Any]] = []
    start = 1
    previous_end = 0
    for stage_id, title, fraction in pre_labels:
        if start > pre_end:
            break
        end = max(start, min(pre_end, int(round(pre_end * fraction))))
        end = max(end, previous_end + 1)
        end = min(end, pre_end)
        result.append({"id": stage_id, "title": title, "chapter_range": [start, end]})
        previous_end = end
        start = end + 1
    # Reserve a short tail for resolution when room exists; otherwise final arc owns the remaining chapters.
    tail = max(1, int(round(ideal * 0.03)))
    resolution_start = max(final_start + 1, ideal - tail + 1)
    if resolution_start <= ideal and resolution_start > final_start:
        result.append({"id": "final_arc", "title": "最终篇章", "chapter_range": [final_start, resolution_start - 1]})
        result.append({"id": "resolution", "title": "收束与尾声", "chapter_range": [resolution_start, ideal]})
    else:
        result.append({"id": "final_arc", "title": "最终篇章与收束", "chapter_range": [final_start, ideal]})
    return result


def default_final_arc_start(ideal: int, last_committed: int = 0) -> int:
    start = max(1, int(math.floor(ideal * 0.88)) + 1)
    if last_committed >= start:
        start = last_committed + 1
    return min(max(1, start), ideal)


def make_ending_plan(
    *,
    min_chapter: int,
    ideal_chapter: int,
    max_chapter: int,
    target_source: str,
    creative_brief: str = "",
    last_committed: int = 0,
    status: str = "target_only",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not (1 <= min_chapter <= ideal_chapter <= max_chapter):
        raise ValueError("ending target must satisfy 1 <= min <= ideal <= max")
    if max_chapter < last_committed:
        raise ValueError("ending max chapter cannot be before the last committed chapter")
    final_start = default_final_arc_start(ideal_chapter, last_committed)
    plan = {
        "version": 1,
        "status": status,
        "target": {
            "mode": "fixed" if min_chapter == max_chapter else "range",
            "min_chapter": min_chapter,
            "ideal_chapter": ideal_chapter,
            "max_chapter": max_chapter,
            "source": target_source,
        },
        "creative_brief": creative_brief.strip(),
        "required_outcomes": [],
        "must_resolve_threads": [],
        "optional_unresolved_threads": [],
        "must_resolve_foreshadowing": [],
        "optional_unresolved_foreshadowing": [],
        "relationship_outcomes": [],
        "final_arc": {
            "start_chapter": final_start,
            "entered": False,
            "entered_at_chapter": 0,
            "mode": "auto_at_threshold",
        },
        "policy": {
            "block_chapters_after_max": True,
            "block_new_plot_threads_in_final_arc": True,
            "block_new_foreshadowing_in_final_arc": True,
            "require_all_nonoptional_threads_resolved_to_finalize": True,
            "require_all_nonoptional_foreshadowing_resolved_to_finalize": True,
            "require_required_outcomes_complete_to_finalize": True,
        },
        "updated_at": utc_now(),
    }
    budget = {
        "version": 1,
        "target": copy.deepcopy(plan["target"]),
        "stages": _stage_ranges(ideal_chapter, final_start),
        "rules": {
            "volume_ranges_must_be_contiguous": True,
            "volume_ranges_must_cover_1_to_ideal": True,
            "full_outline_must_cover_1_to_ideal": True,
            "continuation_may_not_exceed_max": True,
            "ending_progress_refresh_after_each_commit": True,
        },
        "updated_at": utc_now(),
    }
    progress = ending_progress_from_data(
        {"last_committed_chapter": last_committed}, plan, {"threads": []}, {"items": []}
    )
    return plan, budget, progress


def ending_progress_from_data(
    project: dict[str, Any],
    plan: dict[str, Any],
    plot: dict[str, Any],
    foreshadowing: dict[str, Any],
) -> dict[str, Any]:
    target = plan.get("target", {}) if isinstance(plan, dict) else {}
    ideal = int(target.get("ideal_chapter", project.get("planned_chapters", 0)) or 0)
    minimum = int(target.get("min_chapter", ideal) or ideal)
    maximum = int(target.get("max_chapter", ideal) or ideal)
    last = int(project.get("last_committed_chapter", 0) or 0)
    final_arc = plan.get("final_arc", {}) if isinstance(plan, dict) else {}
    final_start = int(final_arc.get("start_chapter", ideal) or ideal) if ideal else 0
    effective_final_arc = bool(final_arc.get("entered")) or (final_start > 0 and last + 1 >= final_start)

    optional_threads = {str(v) for v in plan.get("optional_unresolved_threads", []) if v}
    required_threads = {str(v) for v in plan.get("must_resolve_threads", []) if v}
    unresolved_threads: list[str] = []
    resolved_threads: set[str] = set()
    for item in plot.get("threads", []) if isinstance(plot, dict) and isinstance(plot.get("threads"), list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        thread_id = str(item["id"])
        status = str(item.get("status", "active")).lower()
        if status in {"resolved", "closed", "completed", "cancelled"}:
            resolved_threads.add(thread_id)
        elif thread_id not in optional_threads:
            unresolved_threads.append(thread_id)
    for thread_id in sorted(required_threads - resolved_threads):
        if thread_id not in unresolved_threads:
            unresolved_threads.append(thread_id)

    optional_clues = {str(v) for v in plan.get("optional_unresolved_foreshadowing", []) if v}
    required_clues = {str(v) for v in plan.get("must_resolve_foreshadowing", []) if v}
    unresolved_clues: list[str] = []
    resolved_clues: set[str] = set()
    for item in foreshadowing.get("items", []) if isinstance(foreshadowing, dict) and isinstance(foreshadowing.get("items"), list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        clue_id = str(item["id"])
        status = str(item.get("status", item.get("action", "active"))).lower()
        if status in {"resolved", "paid_off", "payoff", "closed", "cancelled"} or str(item.get("action", "")).lower() in {"payoff", "resolve"}:
            resolved_clues.add(clue_id)
        elif clue_id not in optional_clues:
            unresolved_clues.append(clue_id)
    for clue_id in sorted(required_clues - resolved_clues):
        if clue_id not in unresolved_clues:
            unresolved_clues.append(clue_id)

    required_outcomes = plan.get("required_outcomes", []) if isinstance(plan, dict) else []
    pending_outcomes: list[str] = []
    for item in required_outcomes if isinstance(required_outcomes, list) else []:
        if isinstance(item, str):
            pending_outcomes.append(item)
        elif isinstance(item, dict):
            if str(item.get("status", "pending")).lower() not in {"complete", "completed", "resolved", "done"}:
                pending_outcomes.append(str(item.get("id") or item.get("description") or "unnamed_outcome"))
    relationship_outcomes = plan.get("relationship_outcomes", []) if isinstance(plan, dict) else []
    pending_relationships: list[str] = []
    for item in relationship_outcomes if isinstance(relationship_outcomes, list) else []:
        if isinstance(item, str):
            pending_relationships.append(item)
        elif isinstance(item, dict) and str(item.get("status", "pending")).lower() not in {"complete", "completed", "resolved", "done"}:
            pending_relationships.append(str(item.get("id") or item.get("description") or "unnamed_relationship_outcome"))

    remaining_to_ideal = max(0, ideal - last) if ideal else 0
    remaining_to_max = max(0, maximum - last) if maximum else 0
    blockers = []
    if unresolved_threads:
        blockers.append("unresolved_threads")
    if unresolved_clues:
        blockers.append("unresolved_foreshadowing")
    if pending_outcomes:
        blockers.append("pending_required_outcomes")
    if pending_relationships:
        blockers.append("pending_relationship_outcomes")
    if last < minimum:
        blockers.append("minimum_length_not_reached")
    return {
        "updated_at": utc_now(),
        "status": plan.get("status", "unset") if isinstance(plan, dict) else "unset",
        "last_committed_chapter": last,
        "target": {"min": minimum, "ideal": ideal, "max": maximum},
        "remaining_to_ideal": remaining_to_ideal,
        "remaining_to_max": remaining_to_max,
        "final_arc_start": final_start,
        "effective_final_arc": effective_final_arc,
        "unresolved_threads": sorted(set(unresolved_threads)),
        "unresolved_foreshadowing": sorted(set(unresolved_clues)),
        "pending_required_outcomes": pending_outcomes,
        "pending_relationship_outcomes": pending_relationships,
        "blockers": blockers,
        "ready_to_finalize": not blockers and minimum <= last <= maximum,
    }


def ending_context_for_chapter(paths: WorkspacePaths, chapter_number: int) -> dict[str, Any]:
    plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    if not plan or str(plan.get("status", "unset")) == "unset":
        return {"configured": False, "chapter": chapter_number}
    project = read_yaml(paths.project, {})
    target = plan.get("target", {})
    maximum = int(target.get("max_chapter", 0) or 0)
    ideal = int(target.get("ideal_chapter", 0) or 0)
    final_start = int((plan.get("final_arc") or {}).get("start_chapter", ideal) or ideal)
    if maximum and chapter_number > maximum:
        raise ValueError(f"Chapter {chapter_number} exceeds ending max chapter {maximum}")
    final_arc = chapter_number >= final_start if final_start else False
    return {
        "configured": True,
        "chapter": chapter_number,
        "target": target,
        "final_arc_start": final_start,
        "is_final_arc": final_arc,
        "is_ideal_final_chapter": bool(ideal and chapter_number == ideal),
        "is_max_final_chapter": bool(maximum and chapter_number == maximum),
        "required_outcomes": plan.get("required_outcomes", []),
        "must_resolve_threads": plan.get("must_resolve_threads", []),
        "must_resolve_foreshadowing": plan.get("must_resolve_foreshadowing", []),
        "policy": plan.get("policy", {}),
    }


def set_ending_target(
    paths: WorkspacePaths,
    ideal_chapter: int,
    min_chapter: int = 0,
    max_chapter: int = 0,
    creative_brief: str = "",
) -> dict[str, Any]:
    project = copy.deepcopy(read_yaml(paths.project, {}))
    last = int(project.get("last_committed_chapter", 0) or 0)
    ideal = int(ideal_chapter)
    minimum = int(min_chapter or ideal)
    maximum = int(max_chapter or ideal)
    if ideal < last:
        raise ValueError(f"ideal ending chapter {ideal} cannot be before last committed chapter {last}")
    plan, budget, progress = make_ending_plan(
        min_chapter=minimum,
        ideal_chapter=ideal,
        max_chapter=maximum,
        target_source="user",
        creative_brief=creative_brief,
        last_committed=last,
        status="target_only" if not creative_brief.strip() else "planned",
    )
    project["planned_chapters"] = ideal
    project["length_plan"] = copy.deepcopy(plan["target"])
    project["ending_status"] = plan["status"]
    project["updated_at"] = utc_now()
    projections = {
        "project.yaml": _yaml(project),
        "planning/ending/ending-plan.yaml": _yaml(plan),
        "planning/story-budget.yaml": _yaml(budget),
        "state/ending-progress.yaml": _yaml(progress),
    }
    with workspace_lock(paths, "ending-target"):
        checkpoint = create_checkpoint(paths, "before-ending-target")
        event = commit_projection_event(
            paths,
            event_type="ending.target_set",
            payload={"target": plan["target"], "checkpoint": checkpoint.name},
            projections=projections,
        )
    reindex_workspace(paths)
    return {"target": plan["target"], "final_arc_start": plan["final_arc"]["start_chapter"], "story_budget": budget, "checkpoint": checkpoint.name, "event": event}


def rebalance_story_budget(paths: WorkspacePaths, final_arc_start: int = 0) -> dict[str, Any]:
    plan = copy.deepcopy(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}))
    if not plan or str(plan.get("status", "unset")) == "unset":
        raise RuntimeError("Set an ending target first")
    target = plan.get("target", {})
    ideal = int(target.get("ideal_chapter", 0) or 0)
    if ideal < 1:
        raise RuntimeError("Ending target has no ideal_chapter")
    last = int(read_yaml(paths.project, {}).get("last_committed_chapter", 0) or 0)
    if final_arc_start:
        if not (last + 1 <= final_arc_start <= int(target.get("max_chapter", ideal))):
            raise ValueError("final_arc_start must be after committed chapters and within ending max")
        plan.setdefault("final_arc", {})["start_chapter"] = int(final_arc_start)
    start = int((plan.get("final_arc") or {}).get("start_chapter", default_final_arc_start(ideal, last)))
    budget = {
        "version": 1,
        "target": copy.deepcopy(target),
        "stages": _stage_ranges(ideal, start),
        "rules": {
            "volume_ranges_must_be_contiguous": True,
            "volume_ranges_must_cover_1_to_ideal": True,
            "full_outline_must_cover_1_to_ideal": True,
            "continuation_may_not_exceed_max": True,
            "ending_progress_refresh_after_each_commit": True,
        },
        "updated_at": utc_now(),
    }
    plan["updated_at"] = utc_now()
    with workspace_lock(paths, "story-budget"):
        checkpoint = create_checkpoint(paths, "before-story-budget-rebalance")
        event = commit_projection_event(
            paths,
            event_type="ending.story_budget_rebalanced",
            payload={"target": target, "final_arc_start": start, "checkpoint": checkpoint.name},
            projections={"planning/ending/ending-plan.yaml": _yaml(plan), "planning/story-budget.yaml": _yaml(budget)},
        )
    return {"story_budget": budget, "checkpoint": checkpoint.name, "event": event}


def _ending_options_prompt(paths: WorkspacePaths, instruction: str) -> str:
    data = {
        "project": read_yaml(paths.project, {}),
        "ending_plan": read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}),
        "story_budget": read_yaml(paths.root / "planning" / "story-budget.yaml", {}),
        "macro": (paths.root / "planning" / "macro.md").read_text(encoding="utf-8") if (paths.root / "planning" / "macro.md").exists() else "",
        "characters": read_yaml(paths.root / "characters" / "index.yaml", {}),
        "relationships": read_yaml(paths.root / "state" / "relationship-state.yaml", {}),
        "plot": read_yaml(paths.root / "state" / "plot-ledger.yaml", {}),
        "foreshadowing": read_yaml(paths.root / "state" / "foreshadowing.yaml", {}),
        "continuation_boundary": read_yaml(paths.root / "state" / "continuation-boundary.yaml", {}),
    }
    schema = {
        "options": [
            {
                "id": "A",
                "title": "",
                "ending_summary": "",
                "required_outcomes": [{"id": "outcome_x", "description": "", "status": "pending"}],
                "must_resolve_threads": [],
                "optional_unresolved_threads": [],
                "must_resolve_foreshadowing": [],
                "optional_unresolved_foreshadowing": [],
                "relationship_outcomes": [],
                "final_arc": {"start_chapter": 0, "strategy": ""},
                "risks": [],
            }
        ]
    }
    return (
        "基于当前小说生成 3 个彼此明显不同但都遵守正典与章节预算的结局方案。"
        + (f"\n用户要求：{instruction.strip()}" if instruction.strip() else "")
        + "\n\n当前状态：\n" + json.dumps(data, ensure_ascii=False)
        + "\n\n输出结构：\n" + json.dumps(schema, ensure_ascii=False, indent=2)
    )


def generate_ending_options(
    paths: WorkspacePaths,
    llm_call: Callable[..., ModelResult],
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    if not plan or str(plan.get("status", "unset")) == "unset":
        raise RuntimeError("Set an ending target before designing ending options")
    _report_progress(progress_callback, "阶段 1/3：正在准备结局设计输入", 5)
    _report_progress(progress_callback, "阶段 2/3：正在调用 Director 模型设计结局方案", 30)
    result = llm_call(
        paths,
        "director",
        [{"role": "system", "content": ENDING_OPTIONS_SYSTEM}, {"role": "user", "content": _ending_options_prompt(paths, instruction)}],
        purpose="ending.options",
    )
    payload = extract_json_object(result.content)
    options = payload.get("options", []) if isinstance(payload, dict) else []
    if not isinstance(options, list) or len(options) < 1:
        raise ValueError("Ending options must contain an options list")
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Each ending option must be an object")
        option_id = str(option.get("id", "")).strip()
        if not option_id or option_id in seen:
            raise ValueError("Ending option IDs must be non-empty and unique")
        seen.add(option_id)
    _report_progress(progress_callback, "阶段 3/3：正在校验并写入结局方案", 85)
    stamp = utc_now().replace(":", "").replace("-", "")
    target = paths.root / "planning" / "proposals" / f"ending-options-{stamp}.json"
    write_json(target, payload)
    write_json(target.with_suffix(".meta.json"), {
        "type": "ending_options", "created_at": utc_now(), "route": result.route, "provider": result.provider,
        "model": result.model, "input_hash": result.input_hash, "output_hash": result.output_hash,
    })
    _report_progress(progress_callback, "结局方案已生成", 100)
    return {"proposal": str(target), "options": [{"id": item.get("id"), "title": item.get("title", ""), "summary": item.get("ending_summary", "")} for item in options]}


def apply_ending_plan(paths: WorkspacePaths, proposal_relative_path: str, option_id: str = "") -> dict[str, Any]:
    proposal_path = (paths.root / proposal_relative_path).resolve()
    proposal_root = (paths.root / "planning" / "proposals").resolve()
    if proposal_root not in proposal_path.parents or not proposal_path.exists():
        raise ValueError("Ending proposal must exist under planning/proposals")
    payload = read_json(proposal_path, {})
    options = payload.get("options") if isinstance(payload, dict) else None
    if isinstance(options, list):
        if not option_id:
            raise ValueError("option_id is required when applying an ending-options proposal")
        chosen = next((item for item in options if isinstance(item, dict) and str(item.get("id")) == option_id), None)
        if chosen is None:
            raise ValueError(f"Ending option not found: {option_id}")
    elif isinstance(payload, dict):
        chosen = payload
    else:
        raise ValueError("Invalid ending proposal")
    plan = copy.deepcopy(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}))
    if not plan:
        raise RuntimeError("Ending target is missing")
    plot = read_yaml(paths.root / "state" / "plot-ledger.yaml", {"threads": []})
    known_threads = {str(item.get("id")) for item in plot.get("threads", []) if isinstance(item, dict) and item.get("id")} if isinstance(plot, dict) else set()
    clues = read_yaml(paths.root / "state" / "foreshadowing.yaml", {"items": []})
    known_clues = {str(item.get("id")) for item in clues.get("items", []) if isinstance(item, dict) and item.get("id")} if isinstance(clues, dict) else set()
    for key, known in (("must_resolve_threads", known_threads), ("optional_unresolved_threads", known_threads), ("must_resolve_foreshadowing", known_clues), ("optional_unresolved_foreshadowing", known_clues)):
        values = [str(value) for value in (chosen.get(key) or [])]
        unknown = [value for value in values if value not in known]
        if unknown:
            raise ValueError(f"Ending option {key} references unknown IDs: {', '.join(unknown)}")
    if set(chosen.get("must_resolve_threads") or []) & set(chosen.get("optional_unresolved_threads") or []):
        raise ValueError("A plot thread cannot be both required and optional")
    if set(chosen.get("must_resolve_foreshadowing") or []) & set(chosen.get("optional_unresolved_foreshadowing") or []):
        raise ValueError("A foreshadowing item cannot be both required and optional")
    outcome_ids: set[str] = set()
    for bucket in ("required_outcomes", "relationship_outcomes"):
        for item in chosen.get(bucket, []) if isinstance(chosen.get(bucket), list) else []:
            if not isinstance(item, dict):
                raise ValueError(f"{bucket} entries must be objects with stable id")
            item_id = str(item.get("id", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", item_id) or item_id in outcome_ids:
                raise ValueError(f"Invalid or duplicate ending outcome id: {item_id}")
            outcome_ids.add(item_id)
    for key in (
        "required_outcomes", "must_resolve_threads", "optional_unresolved_threads", "must_resolve_foreshadowing",
        "optional_unresolved_foreshadowing", "relationship_outcomes",
    ):
        if key in chosen:
            plan[key] = copy.deepcopy(chosen.get(key) or [])
    if chosen.get("ending_summary"):
        plan["ending_summary"] = str(chosen["ending_summary"])
    if chosen.get("title"):
        plan["ending_title"] = str(chosen["title"])
    proposed_final = chosen.get("final_arc", {}) if isinstance(chosen.get("final_arc"), dict) else {}
    if proposed_final.get("start_chapter"):
        target = plan.get("target", {})
        start = int(proposed_final["start_chapter"])
        last = int(read_yaml(paths.project, {}).get("last_committed_chapter", 0) or 0)
        if not (last + 1 <= start <= int(target.get("max_chapter", 0) or 0)):
            raise ValueError("Ending option final_arc.start_chapter is outside allowed remaining range")
        plan.setdefault("final_arc", {})["start_chapter"] = start
    plan.setdefault("final_arc", {})["strategy"] = proposed_final.get("strategy", "")
    plan["status"] = "planned"
    plan["selected_option"] = option_id or str(chosen.get("id", "custom"))
    plan["updated_at"] = utc_now()
    project = copy.deepcopy(read_yaml(paths.project, {}))
    project["ending_status"] = "planned"
    project["updated_at"] = utc_now()
    budget = {
        "version": 1,
        "target": copy.deepcopy(plan["target"]),
        "stages": _stage_ranges(int(plan["target"]["ideal_chapter"]), int(plan["final_arc"]["start_chapter"])),
        "rules": {
            "volume_ranges_must_be_contiguous": True, "volume_ranges_must_cover_1_to_ideal": True,
            "full_outline_must_cover_1_to_ideal": True, "continuation_may_not_exceed_max": True,
            "ending_progress_refresh_after_each_commit": True,
        },
        "updated_at": utc_now(),
    }
    progress = ending_progress_from_data(project, plan, read_yaml(paths.root / "state" / "plot-ledger.yaml", {}), read_yaml(paths.root / "state" / "foreshadowing.yaml", {}))
    with workspace_lock(paths, "ending-plan"):
        checkpoint = create_checkpoint(paths, "before-ending-plan")
        event = commit_projection_event(
            paths,
            event_type="ending.plan_applied",
            payload={"proposal": proposal_relative_path, "option_id": option_id, "checkpoint": checkpoint.name},
            projections={
                "project.yaml": _yaml(project),
                "planning/ending/ending-plan.yaml": _yaml(plan),
                "planning/story-budget.yaml": _yaml(budget),
                "state/ending-progress.yaml": _yaml(progress),
            },
        )
    reindex_workspace(paths)
    return {"applied": True, "selected_option": plan["selected_option"], "final_arc_start": plan["final_arc"]["start_chapter"], "checkpoint": checkpoint.name, "event": event}


def check_ending_progress(paths: WorkspacePaths) -> dict[str, Any]:
    project = read_yaml(paths.project, {})
    plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    if not plan:
        return {"configured": False, "message": "No ending target is configured"}
    progress = ending_progress_from_data(
        project, plan,
        read_yaml(paths.root / "state" / "plot-ledger.yaml", {}),
        read_yaml(paths.root / "state" / "foreshadowing.yaml", {}),
    )
    progress["configured"] = str(plan.get("status", "unset")) != "unset"
    return progress


def enter_final_arc(paths: WorkspacePaths, start_chapter: int = 0) -> dict[str, Any]:
    project = copy.deepcopy(read_yaml(paths.project, {}))
    plan = copy.deepcopy(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}))
    if not plan or str(plan.get("status", "unset")) == "unset":
        raise RuntimeError("Set an ending target first")
    last = int(project.get("last_committed_chapter", 0) or 0)
    target = plan.get("target", {})
    maximum = int(target.get("max_chapter", 0) or 0)
    start = int(start_chapter or last + 1)
    if start <= last or (maximum and start > maximum):
        raise ValueError("Final arc must start after the last committed chapter and no later than ending max")
    final_arc = plan.setdefault("final_arc", {})
    final_arc.update({"start_chapter": start, "entered": True, "entered_at_chapter": last, "mode": "manual"})
    plan["status"] = "final_arc"
    plan["updated_at"] = utc_now()
    project["ending_status"] = "final_arc"
    project["current_stage"] = "final_arc_execution"
    project["updated_at"] = utc_now()
    budget = {
        "version": 1,
        "target": copy.deepcopy(target),
        "stages": _stage_ranges(int(target.get("ideal_chapter", maximum)), start),
        "rules": {
            "volume_ranges_must_be_contiguous": True, "volume_ranges_must_cover_1_to_ideal": True,
            "full_outline_must_cover_1_to_ideal": True, "continuation_may_not_exceed_max": True,
            "ending_progress_refresh_after_each_commit": True,
        },
        "updated_at": utc_now(),
    }
    with workspace_lock(paths, "final-arc"):
        checkpoint = create_checkpoint(paths, "before-final-arc")
        event = commit_projection_event(
            paths,
            event_type="ending.final_arc_entered",
            payload={"start_chapter": start, "checkpoint": checkpoint.name},
            projections={"project.yaml": _yaml(project), "planning/ending/ending-plan.yaml": _yaml(plan), "planning/story-budget.yaml": _yaml(budget)},
        )
    return {"entered": True, "start_chapter": start, "checkpoint": checkpoint.name, "event": event}


def finalize_novel(paths: WorkspacePaths, force: bool = False) -> dict[str, Any]:
    project = copy.deepcopy(read_yaml(paths.project, {}))
    plan = copy.deepcopy(read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {}))
    if not plan or str(plan.get("status", "unset")) == "unset":
        raise RuntimeError("No ending target is configured")
    progress = ending_progress_from_data(
        project, plan,
        read_yaml(paths.root / "state" / "plot-ledger.yaml", {}),
        read_yaml(paths.root / "state" / "foreshadowing.yaml", {}),
    )
    if not progress["ready_to_finalize"] and not force:
        raise RuntimeError("Novel is not ready to finalize: " + ", ".join(progress["blockers"]))
    final_chapter = int(project.get("last_committed_chapter", 0) or 0)
    if final_chapter < 1 or not paths.chapter_file(final_chapter, "chapter").exists():
        raise RuntimeError("The recorded final chapter does not exist as a committed chapter file")
    index = read_yaml(paths.root / "characters" / "index.yaml", {"characters": []})
    character_states: dict[str, Any] = {}
    for item in index.get("characters", []) if isinstance(index, dict) else []:
        if isinstance(item, dict) and item.get("id"):
            char_id = str(item["id"])
            character_states[char_id] = read_yaml(paths.root / "state" / "characters" / f"{char_id}.yaml", {})
    timeline = read_yaml(paths.root / "state" / "timeline.yaml", {})
    unresolved_md = "# 未解决事项\n\n"
    unresolved_md += "## 剧情线\n" + ("\n".join(f"- {x}" for x in progress["unresolved_threads"]) or "- 无") + "\n\n"
    unresolved_md += "## 伏笔\n" + ("\n".join(f"- {x}" for x in progress["unresolved_foreshadowing"]) or "- 无") + "\n\n"
    unresolved_md += "## 必达结局条件\n" + ("\n".join(f"- {x}" for x in progress["pending_required_outcomes"]) or "- 无") + "\n\n"
    unresolved_md += "## 人物关系终点\n" + ("\n".join(f"- {x}" for x in progress["pending_relationship_outcomes"]) or "- 无") + "\n"
    report_md = (
        f"# 完结审计报告\n\n"
        f"- 最终章节：{final_chapter}\n"
        f"- 目标范围：{progress['target']['min']}–{progress['target']['max']}（理想 {progress['target']['ideal']}）\n"
        f"- 强制完结：{'是' if force else '否'}\n"
        f"- 未解决剧情线：{len(progress['unresolved_threads'])}\n"
        f"- 未解决伏笔：{len(progress['unresolved_foreshadowing'])}\n"
        f"- 未完成结局条件：{len(progress['pending_required_outcomes'])}\n"
        f"- 未完成人物关系终点：{len(progress['pending_relationship_outcomes'])}\n"
        f"- 审计结果：{'带例外完结' if force and not progress['ready_to_finalize'] else '通过'}\n"
    )
    project["status"] = "completed_with_exceptions" if force and not progress["ready_to_finalize"] else "completed"
    project["ending_status"] = "completed"
    project["final_chapter"] = final_chapter
    project["completed_at"] = utc_now()
    project["updated_at"] = utc_now()
    plan["status"] = "completed"
    plan["completed_at"] = utc_now()
    progress["finalized"] = True
    progress["forced"] = force
    projections = {
        "project.yaml": _yaml(project),
        "planning/ending/ending-plan.yaml": _yaml(plan),
        "state/ending-progress.yaml": _yaml(progress),
        "final/completion-report.md": report_md,
        "final/unresolved-threads.md": unresolved_md,
        "final/character-end-states.yaml": _yaml({"characters": character_states}),
        "final/final-timeline.yaml": _yaml(timeline),
        "final/epilogue-notes.md": "# 尾声备注\n\n（可选：在此记录番外、后记或续作接口。）\n",
    }
    with workspace_lock(paths, "finalize"):
        checkpoint = create_checkpoint(paths, "before-finalize")
        event = commit_projection_event(
            paths,
            event_type="ending.novel_finalized",
            payload={"final_chapter": final_chapter, "forced": force, "checkpoint": checkpoint.name, "blockers": progress["blockers"]},
            projections=projections,
        )
    reindex_workspace(paths)
    return {"finalized": True, "status": project["status"], "final_chapter": final_chapter, "progress": progress, "checkpoint": checkpoint.name, "event": event}


def validate_volume_coverage(plan: dict[str, Any], volumes: list[dict[str, Any]]) -> None:
    if not plan or str(plan.get("status", "unset")) == "unset":
        return
    ideal = int((plan.get("target") or {}).get("ideal_chapter", 0) or 0)
    if ideal < 1:
        return
    normalized: list[tuple[int, int, int]] = []
    orders: set[int] = set()
    for volume in volumes:
        if not isinstance(volume, dict):
            raise ValueError("Each volume must be a mapping")
        order = int(volume.get("order", 0) or 0)
        if order < 1 or order in orders:
            raise ValueError("Volume order must be positive and unique")
        orders.add(order)
        parsed = parse_chapter_range(volume.get("chapter_range"))
        if parsed is None:
            raise ValueError(f"Volume {order} chapter_range must be [start,end] or 'start-end'")
        start, end = parsed
        if start < 1 or end < start:
            raise ValueError(f"Invalid volume range for volume {order}: {start}-{end}")
        normalized.append((start, end, order))
    normalized.sort()
    if not normalized or normalized[0][0] != 1:
        raise ValueError("Volume ranges must start at chapter 1")
    expected = 1
    for start, end, order in normalized:
        if start != expected:
            raise ValueError(f"Volume ranges must be contiguous: expected chapter {expected}, volume {order} starts at {start}")
        expected = end + 1
    if normalized[-1][1] != ideal:
        raise ValueError(f"Volume ranges must end exactly at ideal ending chapter {ideal}; got {normalized[-1][1]}")


def validate_outline_coverage(plan: dict[str, Any], chapters: list[dict[str, Any]]) -> None:
    if not plan or str(plan.get("status", "unset")) == "unset":
        return
    ideal = int((plan.get("target") or {}).get("ideal_chapter", 0) or 0)
    if ideal < 1:
        return
    actual: list[int] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Each chapter outline must be a mapping")
        actual.append(int(chapter.get("number", 0) or 0))
    expected = list(range(1, ideal + 1))
    if actual != expected:
        if len(actual) > 12:
            detail = f"got {actual[0]}-{actual[-1]} ({len(actual)} chapters)"
        else:
            detail = f"got {actual}"
        raise ValueError(f"Full outline must contain exactly chapters 1-{ideal} in order; {detail}")
