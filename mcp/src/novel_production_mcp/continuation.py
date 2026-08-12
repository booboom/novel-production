from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .agent_mode import AgentTaskPending
from .ending import ending_context_for_chapter, make_ending_plan
from .event_store import commit_projection_event, projection_status
from .models import ModelResult, ProgressCallback, WorkspacePaths
from .narrative_timing import normalize_narrative_timing
from .orientation import normalize_reader_orientation
from .quality import (
    normalize_restricted_actions,
    normalize_rhythm_signature,
    validate_outline_plan_contract,
)
from .parsing import extract_json_object
from .provider import ProviderError
from .retrieval import reindex_workspace
from .storage import atomic_write_text, create_checkpoint, read_json, read_text, read_yaml, sha256_file, utc_now, workspace_lock, write_json
from .titles import normalize_generated_chapter_outline

TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
CHAPTER_HEADER_RE = re.compile(
    r"(?im)^(?:\s*#{1,6}\s*)?(?:第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*[章回节]|chapter\s+([0-9]+))\s*([^\r\n]*)$"
)
NUMBER_RE = re.compile(r"(\d+)")

EXTRACTION_SYSTEM = """你是长篇小说接管系统的证据提取器。你面对的是作者已经写好的原文，不能补写、脑补或纠正原作。
每一个结论必须标记 confidence=confirmed|inferred|unknown；confirmed 必须附带 evidence，evidence 至少包含 chapter 和不超过100字的原文 excerpt。
稳定 ID 使用 ASCII snake_case；同一实体跨章节必须尽量复用同一 ID。把未确认推测保留为 inferred/unknown，绝不能升级为事实。
只输出一个 JSON 对象，不要 Markdown。"""

SYNTHESIS_SYSTEM = """你是长篇小说接管系统的总编与正典重建器。输入是分批证据提取结果，而不是允许你自由创作的素材。
你必须合并重复实体、保留证据、识别互相矛盾的描述，并严格区分 confirmed / inferred / unknown。
不要替作者解释故意保留的谜团。当前状态以最后出现且有证据的状态为准；人物永久档案只收录跨多处稳定表现或正文明确事实。
所有人物、事实、地点、道具、能力、事件、剧情线和伏笔使用稳定 ASCII ID。
只输出一个 JSON 对象，不要 Markdown。"""

CONTINUATION_PLAN_SYSTEM = """你是已有长篇小说的续写总导演。必须服从接管后的 confirmed 正典、人物当前状态、知识边界、关系、道具、时间线、活跃剧情线和未回收伏笔。
不得为了方便续写而改写旧事实。规划只针对当前规划批次指定的未来章节范围。
每章 title 只写面向读者的独立章名；mini_arc 单独保存可跨章重复的小剧情段；chapter_task 单独保存本章要完成的具体叙事任务。title 必须取自本章独有的行动、选择、冲突、结果、异常或意象，不得把 mini_arc 或同一任务对象机械用作连续标题的固定前缀、后缀或词根，也不得用“不同修饰语 + 同一名词”批量命名；生成本批前必须比较输入中的近期标题。可选 reader_visible_terms 数组只登记已由 confirmed 正典建立、允许读者和人物看到的世界内正式名称，默认必须为空，不得把规划标签和审校术语列入。reader_orientation 必须包含非空 task_origin、task_object、why_now、stakes、method_reason、establish_before，明确读者最迟应在哪项关键行动前理解任务因果；连续任务可引用前章已建立事实。narrative_timing 必须包含 established_before_chapter 和 introduce_this_chapter；后者逐项包含 ASCII snake_case element_id、description、establish_before、allow_early_hint，登记信息或手段首次允许出现的章节，不得把后续批次或后续章节才建立的具体方法、证据和答案提前泄漏。restricted_actions 必须是数组，受限资源操作逐项给出 action、actor、resource、authority_basis、establish_before；没有则为空数组。rhythm_signature 必须包含 opening_mode、core_action、evidence_type、emotional_turn、ending_hook_type，相邻批次也不得重复同一核心行动、证据和结尾钩子组合。不得把章号、剧情段/卷名前缀或末尾排序数字拼进 title。
只输出一个 JSON 对象，不要 Markdown。"""

CONTINUATION_PROGRESS_PATH = ".novel/continuation-plan-progress.json"


def _report_progress(
    callback: ProgressCallback | None,
    message: str,
    progress: float | None = None,
    total: float | None = 100.0,
) -> None:
    if callback is not None:
        callback(message, progress, total)


@dataclass(slots=True)
class SourceChapter:
    number: int
    title: str
    text: str
    source_path: str
    source_hash: str


def _cn_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not value or any(ch not in digits and ch not in units for ch in value):
        return None
    total = 0
    section = 0
    number = 0
    for ch in value:
        if ch in digits:
            number = digits[ch]
        else:
            unit = units[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
                number = 0
            else:
                if number == 0:
                    number = 1
                section += number * unit
                number = 0
    return total + section + number


def _natural_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def _chapter_number_from_name(path: Path) -> int | None:
    match = CHAPTER_HEADER_RE.search(path.stem)
    if match:
        return _cn_number(match.group(1) or match.group(2) or "")
    generic = NUMBER_RE.search(path.stem)
    return int(generic.group(1)) if generic else None


def _chapter_title(text: str, fallback: str) -> str:
    first = text.lstrip("\ufeff\n\r ").splitlines()[0] if text.strip() else ""
    match = CHAPTER_HEADER_RE.match(first.strip())
    if match:
        title = (match.group(3) or "").strip(" -—：:《》")
        return title or fallback
    return fallback


def _split_single_file(path: Path, text: str) -> tuple[str, list[SourceChapter]]:
    matches = list(CHAPTER_HEADER_RE.finditer(text))
    if not matches:
        number = _chapter_number_from_name(path) or 1
        return "", [SourceChapter(number, path.stem, text, str(path), sha256_file(path))]
    preface = text[: matches[0].start()]
    chapters: list[SourceChapter] = []
    source_hash = sha256_file(path)
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip("\n\r") + "\n"
        number = _cn_number(match.group(1) or match.group(2) or "")
        if number is None:
            number = index + 1
        title = (match.group(3) or "").strip(" -—：:《》") or f"第{number}章"
        chapters.append(SourceChapter(number, title, block, str(path), source_hash))
    return preface, chapters


def collect_source_chapters(source_path: str) -> tuple[str, list[SourceChapter]]:
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Source novel path does not exist: {source}")
    if source.is_file():
        if source.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("Existing-novel import currently accepts UTF-8 .txt/.md/.markdown files")
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Source file must be UTF-8 text") from exc
        preface, chapters = _split_single_file(source, text)
    else:
        files = sorted(
            [path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES],
            key=_natural_key,
        )
        if not files:
            raise ValueError("No UTF-8 .txt/.md/.markdown files were found under the source directory")
        preface = ""
        chapters = []
        used: set[int] = set()
        next_number = 1
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"Source file must be UTF-8: {path}") from exc
            number = _chapter_number_from_name(path)
            if number is None or number in used:
                while next_number in used:
                    next_number += 1
                number = next_number
            used.add(number)
            next_number = max(next_number, number + 1)
            chapters.append(SourceChapter(number, _chapter_title(text, path.stem), text, str(path), sha256_file(path)))
    chapters.sort(key=lambda item: item.number)
    if len({item.number for item in chapters}) != len(chapters):
        raise ValueError("Duplicate chapter numbers remained after source normalization")
    return preface, chapters


def _source_manifest(chapters: list[SourceChapter], source_path: str) -> dict[str, Any]:
    return {
        "version": 1,
        "imported_at": utc_now(),
        "source_path": str(Path(source_path).expanduser().resolve()),
        "chapter_count": len(chapters),
        "first_chapter": min(item.number for item in chapters),
        "last_chapter": max(item.number for item in chapters),
        "total_chars": sum(len(item.text) for item in chapters),
        "chapters": [
            {
                "number": item.number,
                "title": item.title,
                "source_path": item.source_path,
                "source_hash": item.source_hash,
                "content_hash": __import__("hashlib").sha256(item.text.encode("utf-8")).hexdigest(),
                "chars": len(item.text),
            }
            for item in chapters
        ],
    }


def import_into_workspace(paths: WorkspacePaths, source_path: str, preface: str, chapters: list[SourceChapter], *, ending_target_configured: bool = False) -> dict[str, Any]:
    if not chapters:
        raise ValueError("No chapters detected")
    source_dir = paths.root / "source" / "original"
    source_dir.mkdir(parents=True, exist_ok=True)
    (paths.root / "analysis" / "chunks").mkdir(parents=True, exist_ok=True)
    (paths.root / "analysis" / "reports").mkdir(parents=True, exist_ok=True)
    (paths.root / "analysis" / "conflicts").mkdir(parents=True, exist_ok=True)
    (paths.root / "planning" / "reconstructed-outlines").mkdir(parents=True, exist_ok=True)

    manifest = _source_manifest(chapters, source_path)
    if preface.strip():
        atomic_write_text(source_dir / "preface.txt", preface)
        os.chmod(source_dir / "preface.txt", 0o444)

    projections: dict[str, str] = {}
    for item in chapters:
        original = source_dir / f"chapter-{item.number:04d}.txt"
        atomic_write_text(original, item.text)
        os.chmod(original, 0o444)
        projections[f"chapters/chapter-{item.number:04d}.md"] = item.text.rstrip() + "\n"

    manifest_text = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    atomic_write_text(paths.root / "source" / "import-manifest.yaml", manifest_text)
    os.chmod(paths.root / "source" / "import-manifest.yaml", 0o444)

    project = read_yaml(paths.project, {})
    project["mode"] = "existing_novel_takeover"
    project["current_stage"] = "continuation_bootstrap_analysis"
    project["last_committed_chapter"] = manifest["last_chapter"]
    project["planned_chapters"] = max(int(project.get("planned_chapters", 1)), manifest["last_chapter"] + 30)
    ending_status = "target_only" if ending_target_configured else "unset"
    target_source = "takeover_user" if ending_target_configured else "provisional_takeover"
    ending_plan, story_budget, ending_progress = make_ending_plan(
        min_chapter=int(project["planned_chapters"]),
        ideal_chapter=int(project["planned_chapters"]),
        max_chapter=int(project["planned_chapters"]),
        target_source=target_source,
        last_committed=manifest["last_chapter"],
        status=ending_status,
    )
    project["length_plan"] = copy.deepcopy(ending_plan["target"])
    project["ending_status"] = ending_status
    project["updated_at"] = utc_now()
    project["source_import"] = {
        "manifest": "source/import-manifest.yaml",
        "chapter_count": manifest["chapter_count"],
        "last_existing_chapter": manifest["last_chapter"],
        "originals_read_only": True,
    }
    projections["project.yaml"] = yaml.safe_dump(project, allow_unicode=True, sort_keys=False)
    projections["planning/ending/ending-plan.yaml"] = yaml.safe_dump(ending_plan, allow_unicode=True, sort_keys=False)
    projections["planning/story-budget.yaml"] = yaml.safe_dump(story_budget, allow_unicode=True, sort_keys=False)
    projections["state/ending-progress.yaml"] = yaml.safe_dump(ending_progress, allow_unicode=True, sort_keys=False)
    projections["analysis/status.yaml"] = yaml.safe_dump(
        {
            "phase": "imported",
            "chapter_count": manifest["chapter_count"],
            "last_existing_chapter": manifest["last_chapter"],
            "analyzed_batches": [],
            "proposal": "",
            "applied": False,
        },
        allow_unicode=True,
        sort_keys=False,
    )
    with workspace_lock(paths, "takeover-import"):
        event = commit_projection_event(
            paths,
            event_type="takeover.source_imported",
            payload={
                "chapter_count": manifest["chapter_count"],
                "last_existing_chapter": manifest["last_chapter"],
                "manifest": "source/import-manifest.yaml",
            },
            projections=projections,
        )
    reindex_workspace(paths)
    return {"manifest": manifest, "event": event, "source_originals": str(source_dir)}


def _analysis_batches(paths: WorkspacePaths, batch_size: int, max_chars: int) -> list[list[int]]:
    manifest = read_yaml(paths.root / "source" / "import-manifest.yaml", {})
    numbers = [int(item["number"]) for item in manifest.get("chapters", [])]
    if not numbers:
        raise RuntimeError("No imported source manifest")
    batches: list[list[int]] = []
    current: list[int] = []
    current_chars = 0
    for number in numbers:
        text = read_text(paths.root / "source" / "original" / f"chapter-{number:04d}.txt")
        size = len(text)
        if current and (len(current) >= batch_size or current_chars + size > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(number)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _analysis_plan_path(paths: WorkspacePaths) -> Path:
    return paths.root / "analysis" / "plan.json"


def _analysis_batch_source_hash(paths: WorkspacePaths, numbers: list[int]) -> str:
    return hashlib.sha256(
        "".join(read_text(paths.root / "source" / "original" / f"chapter-{number:04d}.txt") for number in numbers).encode("utf-8")
    ).hexdigest()


def _analysis_plan_signature(paths: WorkspacePaths, batch_size: int, max_chars: int) -> str:
    manifest = read_yaml(paths.root / "source" / "import-manifest.yaml", {})
    stable = {
        "batch_size": batch_size,
        "max_chars": max_chars,
        "chapters": [
            {"number": item.get("number"), "source_hash": item.get("source_hash")}
            for item in manifest.get("chapters", [])
            if isinstance(item, dict)
        ],
    }
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _new_analysis_plan(paths: WorkspacePaths, batch_size: int, max_chars: int) -> dict[str, Any]:
    batches = _analysis_batches(paths, batch_size, max_chars)
    return {
        "version": 1,
        "signature": _analysis_plan_signature(paths, batch_size, max_chars),
        "batch_size": batch_size,
        "max_chars": max_chars,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "batches": [
            {"batch_id": f"{index:04d}", "chapters": numbers, "parent_batch_id": None, "status": "pending"}
            for index, numbers in enumerate(batches, start=1)
        ],
    }


def _load_analysis_plan(paths: WorkspacePaths, batch_size: int, max_chars: int, *, force: bool) -> dict[str, Any]:
    plan_path = _analysis_plan_path(paths)
    plan = read_json(plan_path, {})
    signature = _analysis_plan_signature(paths, batch_size, max_chars)
    if force or not isinstance(plan, dict) or plan.get("version") != 1 or plan.get("signature") != signature:
        plan = _new_analysis_plan(paths, batch_size, max_chars)
        write_json(plan_path, plan)
        return plan

    batches = plan.get("batches")
    if not isinstance(batches, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("batch_id"), str)
        or not isinstance(item.get("chapters"), list)
        or not item.get("chapters")
        or any(not isinstance(number, int) for number in item["chapters"])
        or item.get("status") not in {"pending", "running", "completed", "failed", "split"}
        for item in batches
    ):
        raise RuntimeError(f"Invalid analysis plan: {plan_path}")
    changed = False
    for item in batches:
        if item["status"] in {"running", "failed"}:
            item["status"] = "pending"
            changed = True
    if changed:
        plan["updated_at"] = utc_now()
        write_json(plan_path, plan)
    return plan


def _analysis_plan_leaf_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan.get("batches", []) if item.get("status") != "split"]


def _analysis_plan_pending_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _analysis_plan_leaf_records(plan) if item.get("status") != "completed"]


def _persist_analysis_plan(paths: WorkspacePaths, plan: dict[str, Any]) -> None:
    plan["updated_at"] = utc_now()
    write_json(_analysis_plan_path(paths), plan)


def _analysis_target(paths: WorkspacePaths, batch_id: str) -> Path:
    return paths.root / "analysis" / "chunks" / f"batch-{batch_id}.json"


def _analysis_chunk_files(paths: WorkspacePaths) -> list[Path]:
    chunks_root = paths.root / "analysis" / "chunks"
    if not chunks_root.exists():
        return []
    return [
        path
        for path in sorted(chunks_root.glob("batch-*.json"), key=_natural_key)
        if not path.name.endswith(".meta.json")
    ]


def _analysis_batch_integrity_errors(paths: WorkspacePaths, record: dict[str, Any]) -> list[str]:
    batch_id = str(record.get("batch_id", "unknown"))
    numbers = record.get("chapters")
    errors: list[str] = []
    if not isinstance(numbers, list) or not numbers or any(not isinstance(number, int) for number in numbers):
        return [f"batch {batch_id}: chapters metadata is missing or invalid"]
    target = _analysis_target(paths, batch_id)
    meta_path = target.with_suffix(".meta.json")
    if not target.exists():
        errors.append(f"batch {batch_id}: result file is missing ({target.name})")
    if not meta_path.exists():
        errors.append(f"batch {batch_id}: metadata file is missing ({meta_path.name})")
    if errors:
        return errors
    try:
        payload = read_json(target, {})
        meta = read_json(meta_path, {})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"batch {batch_id}: result or metadata is not valid JSON ({exc})"]
    if not isinstance(payload, dict):
        errors.append(f"batch {batch_id}: result is not a JSON object")
    if not isinstance(meta, dict):
        errors.append(f"batch {batch_id}: metadata is not a JSON object")
    if not isinstance(payload, dict) or not isinstance(meta, dict):
        return errors
    if payload.get("_batch") != _batch_marker(batch_id):
        errors.append(f"batch {batch_id}: result batch marker does not match")
    if payload.get("_chapters") != numbers:
        errors.append(f"batch {batch_id}: result chapter list does not match the analysis plan")
    if meta.get("batch_id") != batch_id:
        errors.append(f"batch {batch_id}: metadata batch marker does not match")
    expected_hash = _analysis_batch_source_hash(paths, numbers)
    if meta.get("source_hash") != expected_hash:
        errors.append(f"batch {batch_id}: source hash changed or metadata is stale")
    return errors


def _completed_analysis_records(paths: WorkspacePaths, status: dict[str, Any]) -> list[dict[str, Any]]:
    plan = read_json(_analysis_plan_path(paths), {})
    if isinstance(plan, dict) and isinstance(plan.get("batches"), list):
        return [
            {
                "batch": _batch_marker(str(item["batch_id"])),
                "batch_id": str(item["batch_id"]),
                "chapters": list(item["chapters"]),
                "path": str(_analysis_target(paths, str(item["batch_id"]))),
            }
            for item in plan["batches"]
            if isinstance(item, dict) and item.get("status") == "completed"
        ]

    completed_ids = status.get("completed_batch_ids", [])
    if isinstance(completed_ids, list) and completed_ids:
        records: list[dict[str, Any]] = []
        for raw_id in completed_ids:
            batch_id = str(raw_id)
            payload = read_json(_analysis_target(paths, batch_id), {})
            chapters = payload.get("_chapters", []) if isinstance(payload, dict) else []
            records.append({
                "batch": _batch_marker(batch_id),
                "batch_id": batch_id,
                "chapters": chapters,
                "path": str(_analysis_target(paths, batch_id)),
            })
        return records

    records = []
    for path in _analysis_chunk_files(paths):
        batch_id = path.stem.removeprefix("batch-")
        payload = read_json(path, {})
        chapters = payload.get("_chapters", []) if isinstance(payload, dict) else []
        records.append({"batch": _batch_marker(batch_id), "batch_id": batch_id, "chapters": chapters, "path": str(path)})
    return records


def _validated_analysis_records(paths: WorkspacePaths, status: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    records = _completed_analysis_records(paths, status)
    errors: list[str] = []
    for record in records:
        errors.extend(_analysis_batch_integrity_errors(paths, record))
    if not records:
        errors.append("no completed analysis batches were found")
    return records, errors


def _batch_prompt(paths: WorkspacePaths, numbers: list[int]) -> str:
    blocks: list[str] = []
    for number in numbers:
        text = read_text(paths.root / "source" / "original" / f"chapter-{number:04d}.txt")
        blocks.append(f"===== CHAPTER {number} =====\n{text}")
    schema = {
        "chapters": numbers,
        "facts": [{"id": "fact_x", "statement": "", "category": "", "confidence": "confirmed", "evidence": [{"chapter": numbers[0], "excerpt": ""}]}],
        "characters": [{
            "id": "char_x", "name": "", "role": "", "profile_observations": [], "speech_observations": [],
            "ability_observations": [], "state_observations": [], "knowledge_observations": [], "relationship_observations": [],
        }],
        "world": {"rules": [], "locations": [], "factions": [], "organizations": []},
        "items": [],
        "timeline_events": [],
        "plot_threads": [],
        "foreshadowing": [],
        "chapter_outlines": [],
        "volume_signals": [],
        "conflicts": [],
    }
    return (
        "从以下原文做证据提取。不要规划未来，不要修文。角色所知信息必须按章节记录。"
        "疑似伏笔和普通细节分开，无法确定时标记 inferred。\n\n输出结构示例：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n\n原文：\n"
        + "\n\n".join(blocks)
    )


def _analysis_status_event(
    paths: WorkspacePaths,
    status: dict[str, Any],
    *,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    status["updated_at"] = utc_now()
    return commit_projection_event(
        paths,
        event_type=event_type,
        payload=payload,
        projections={"analysis/status.yaml": yaml.safe_dump(status, allow_unicode=True, sort_keys=False)},
    )


def _batch_marker(batch_id: str) -> int | str:
    return int(batch_id) if batch_id.isdigit() else batch_id


def _split_analysis_batch(numbers: list[int]) -> list[list[int]]:
    if len(numbers) < 2:
        return []
    midpoint = max(1, len(numbers) // 2)
    return [numbers[:midpoint], numbers[midpoint:]]


def _should_adapt_analysis_batch(numbers: list[int], error: ProviderError) -> bool:
    if len(numbers) < 2:
        return False
    return 524 in error.status_codes or any(
        category in {"origin_timeout", "timeout"} for category in error.categories
    )


def analyze_imported(
    paths: WorkspacePaths,
    llm_call: Callable[..., ModelResult],
    *,
    batch_size: int = 5,
    max_chars: int = 42000,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 20:
        raise ValueError("batch_size must be 1-20")
    if max_chars < 8000:
        raise ValueError("max_chars must be >= 8000")
    plan = _load_analysis_plan(paths, batch_size, max_chars, force=force)
    for record in plan["batches"]:
        if record.get("status") == "completed" and _analysis_batch_integrity_errors(paths, record):
            record["status"] = "pending"
            record.pop("source_hash", None)
            record.pop("completed_at", None)
    _persist_analysis_plan(paths, plan)
    pending = _analysis_plan_pending_records(plan)
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    if not isinstance(status, dict):
        status = {}
    adaptations: list[dict[str, Any]] = list(status.get("adaptations", []))
    completed_records = _completed_analysis_records(paths, status)
    leaf_count = len(_analysis_plan_leaf_records(plan))
    status.update({
        "phase": "analyzing",
        "planned_batch_count": leaf_count,
        "batch_count": leaf_count,
        "analyzed_batches": [entry["batch"] for entry in completed_records if isinstance(entry["batch"], int)],
        "completed_batch_ids": [entry["batch_id"] for entry in completed_records],
        "current_batch": pending[0]["batch_id"] if pending else None,
        "failed_batch": None,
        "next_batch": pending[0]["batch_id"] if pending else None,
        "last_error": "",
    })
    _analysis_status_event(
        paths,
        status,
        event_type="takeover.analysis_started",
        payload={"planned_batch_count": leaf_count, "batch_size": batch_size, "max_chars": max_chars},
    )
    _report_progress(
        progress_callback,
        f"阶段 1/3：分析计划已建立，待处理 {len(pending)} 个批次",
        5,
    )

    while pending:
        record = pending.pop(0)
        batch_id = str(record["batch_id"])
        numbers = list(record["chapters"])
        record["status"] = "running"
        _persist_analysis_plan(paths, plan)
        target = _analysis_target(paths, batch_id)
        meta_path = target.with_suffix(".meta.json")
        source_hash = _analysis_batch_source_hash(paths, numbers)
        status.update({"current_batch": batch_id, "phase": "analyzing"})
        _analysis_status_event(
            paths,
            status,
            event_type="takeover.analysis_batch_started",
            payload={"batch_id": batch_id, "chapters": numbers},
        )
        completed_count = len(completed_records)
        _report_progress(
            progress_callback,
            f"阶段 2/3：开始分析批次 {batch_id}（章节 {numbers[0]}-{numbers[-1]}）",
            min(90, 10 + int(80 * completed_count / max(leaf_count, 1))),
        )
        try:
            if target.exists() and meta_path.exists() and not force and not _analysis_batch_integrity_errors(
                paths, {"batch_id": batch_id, "chapters": numbers}
            ):
                meta = read_json(meta_path, {})
                if meta.get("source_hash") == source_hash:
                    cached = True
                else:
                    cached = False
            else:
                cached = False
            if not cached:
                _report_progress(
                    progress_callback,
                    f"批次 {batch_id}：正在调用 extractor 模型",
                    min(90, 15 + int(75 * completed_count / max(leaf_count, 1))),
                )
                result = llm_call(
                    paths,
                    "extractor",
                    [
                        {"role": "system", "content": EXTRACTION_SYSTEM},
                        {"role": "user", "content": _batch_prompt(paths, numbers)},
                    ],
                    purpose="takeover.extract",
                )
                payload = extract_json_object(result.content)
                if not isinstance(payload, dict):
                    raise ValueError(f"Extractor batch {batch_id} did not return a JSON object")
                payload["_batch"] = _batch_marker(batch_id)
                payload["_chapters"] = numbers
                write_json(target, payload)
                write_json(meta_path, {
                    "batch_id": batch_id,
                    "source_hash": source_hash,
                    "created_at": utc_now(),
                    "route": result.route,
                    "provider": result.provider,
                    "model": result.model,
                    "input_hash": result.input_hash,
                    "output_hash": result.output_hash,
                })
            item = {
                "batch": _batch_marker(batch_id),
                "batch_id": batch_id,
                "chapters": numbers,
                "cached": cached,
                "path": str(target),
            }
            record.update({"status": "completed", "source_hash": source_hash, "completed_at": utc_now()})
            _persist_analysis_plan(paths, plan)
            completed_records = _completed_analysis_records(paths, status)
            status["analyzed_batches"] = [entry["batch"] for entry in completed_records if isinstance(entry["batch"], int)]
            status["completed_batch_ids"] = [entry["batch_id"] for entry in completed_records]
            status["batch_count"] = len(completed_records) + len(_analysis_plan_pending_records(plan))
            status["current_batch"] = pending[0]["batch_id"] if pending else None
            status["failed_batch"] = None
            status["last_error"] = ""
            _analysis_status_event(
                paths,
                status,
                event_type="takeover.analysis_batch_completed",
                payload={"batch_id": batch_id, "chapters": numbers, "cached": cached},
            )
            completed_count = len(completed_records)
            _report_progress(
                progress_callback,
                f"批次 {batch_id}：证据已保存（{completed_count}/{leaf_count}）",
                min(95, 10 + int(85 * completed_count / max(leaf_count, 1))),
            )
        except ProviderError as exc:
            children = _split_analysis_batch(numbers) if _should_adapt_analysis_batch(numbers, exc) else []
            if children:
                child_ids = [f"{batch_id}-{index:02d}" for index in range(1, len(children) + 1)]
                record["status"] = "split"
                child_records = [
                    {"batch_id": child_id, "chapters": child_numbers, "parent_batch_id": batch_id, "status": "pending"}
                    for child_id, child_numbers in zip(child_ids, children)
                ]
                record_index = plan["batches"].index(record)
                plan["batches"][record_index + 1:record_index + 1] = child_records
                _persist_analysis_plan(paths, plan)
                pending = _analysis_plan_pending_records(plan)
                adaptation = {
                    "from_batch": batch_id,
                    "from_chapters": numbers,
                    "to_batches": child_ids,
                    "reason": "origin_timeout",
                    "status_codes": list(exc.status_codes),
                    "routes": list(exc.routes),
                }
                adaptations.append(adaptation)
                status.update({
                    "phase": "degraded",
                    "current_batch": child_ids[0],
                    "planned_batch_count": len(_analysis_plan_leaf_records(plan)),
                    "batch_count": len(_analysis_plan_leaf_records(plan)),
                    "next_batch": child_ids[0],
                    "last_error": str(exc)[:2000],
                    "adaptations": adaptations,
                })
                _analysis_status_event(
                    paths,
                    status,
                    event_type="takeover.analysis_batch_degraded",
                    payload=adaptation,
                )
                _report_progress(
                    progress_callback,
                    f"批次 {batch_id} 上游超时，已拆分为 {', '.join(child_ids)} 并继续",
                    min(90, 10 + int(80 * len(completed_records) / max(leaf_count, 1))),
                )
                continue
            record["status"] = "failed"
            _persist_analysis_plan(paths, plan)
            status.update({
                "phase": "failed",
                "current_batch": batch_id,
                "failed_batch": batch_id,
                "next_batch": batch_id,
                "last_error": str(exc)[:2000],
                "adaptations": adaptations,
            })
            _analysis_status_event(
                paths,
                status,
                event_type="takeover.analysis_failed",
                payload={"batch_id": batch_id, "chapters": numbers, "error": str(exc)[:2000]},
            )
            raise
        except Exception as exc:
            record["status"] = "failed"
            _persist_analysis_plan(paths, plan)
            status.update({
                "phase": "failed",
                "current_batch": batch_id,
                "failed_batch": batch_id,
                "next_batch": batch_id,
                "last_error": str(exc)[:2000],
                "adaptations": adaptations,
            })
            _analysis_status_event(
                paths,
                status,
                event_type="takeover.analysis_failed",
                payload={"batch_id": batch_id, "chapters": numbers, "error": str(exc)[:2000]},
            )
            raise

    completed_records = _completed_analysis_records(paths, status)
    leaf_count = len(_analysis_plan_leaf_records(plan))
    status.update({
        "phase": "analyzed",
        "analyzed_batches": [entry["batch"] for entry in completed_records if isinstance(entry["batch"], int)],
        "completed_batch_ids": [entry["batch_id"] for entry in completed_records],
        "batch_count": len(completed_records),
        "planned_batch_count": leaf_count,
        "current_batch": None,
        "failed_batch": None,
        "next_batch": None,
        "last_error": "",
        "adaptations": adaptations,
    })
    _analysis_status_event(
        paths,
        status,
        event_type="takeover.analysis_completed",
        payload={"batch_count": len(completed_records), "chapters": sum(len(entry["chapters"]) for entry in completed_records), "adaptations": len(adaptations)},
    )
    _report_progress(progress_callback, "阶段 3/3：证据分析完成，状态已持久化", 100)
    return {"batch_count": len(completed_records), "planned_batch_count": leaf_count, "batches": completed_records, "adaptations": adaptations}


def _compact_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "_batch", "_chapters", "facts", "characters", "world", "items", "timeline_events",
        "plot_threads", "foreshadowing", "chapter_outlines", "volume_signals", "conflicts",
    }
    return {key: value for key, value in payload.items() if key in allowed}


def _synthesis_prompt(paths: WorkspacePaths, instruction: str = "") -> str:
    manifest = read_yaml(paths.root / "source" / "import-manifest.yaml", {})
    chunks = []
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    records, errors = _validated_analysis_records(paths, status if isinstance(status, dict) else {})
    if errors:
        raise RuntimeError("Analysis artifacts are incomplete:\n- " + "\n- ".join(errors))
    for record in records:
        path = Path(record["path"])
        chunks.append(_compact_analysis(read_json(path, {})))
    if not chunks:
        raise RuntimeError("No batch analyses exist; run imported-novel analysis first")
    schema = {
        "reconstructed_premise": "从已有正文概括出的故事前提/核心矛盾，不新增事实",
        "retrospective_macro": "对已有剧情的客观回顾，不新增事实",
        "canon": {
            "facts": [], "immutable_facts": [],
            "world": {"status": "reconstructed", "rules": [], "factions": [], "organizations": []},
            "locations": [], "travel_minutes": {}, "items": [],
        },
        "characters": [{
            "id": "char_x", "name": "", "role": "", "profile": {"immutable": {}, "personality": {}, "speech": {}, "abilities": {}},
            "arc": {}, "current_state": {}, "knowledge_boundary": {"knows": [], "suspects": [], "must_not_know": []}, "evidence": [],
        }],
        "relationships": [],
        "timeline": {"current_story_time": "", "events": []},
        "item_ledger": {"items": {}, "balances": {}, "events": []},
        "plot_ledger": {"threads": []},
        "foreshadowing": {"items": []},
        "reconstructed_volumes": [],
        "reconstructed_chapter_outlines": [],
        "continuation_boundary": {
            "last_existing_chapter": manifest.get("last_chapter"), "next_chapter": int(manifest.get("last_chapter", 0)) + 1,
            "story_time": "", "pov": "", "active_location": "", "active_characters": [], "active_threads": [],
            "urgent_foreshadowing": [], "current_cliffhanger": "", "unresolved_questions": [],
        },
        "conflicts": [{"id": "conflict_x", "severity": "low|medium|high|critical", "status": "unresolved", "description": "", "evidence": []}],
        "confidence_summary": {"confirmed": 0, "inferred": 0, "unknown": 0},
    }
    extra = f"\n用户补充要求：{instruction.strip()}\n" if instruction.strip() else ""
    return (
        f"已有小说导入清单：\n{json.dumps(manifest, ensure_ascii=False)}\n{extra}\n"
        "合并下面所有批次分析，生成一个非破坏性的接管提案。任何矛盾必须放入 conflicts，不得静默选边。"
        "已有章节拆章和卷结构若为反推，标记 source=reconstructed。\n输出结构：\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n\n批次分析：\n"
        + json.dumps(chunks, ensure_ascii=False)
    )


def generate_takeover_proposal(
    paths: WorkspacePaths,
    llm_call: Callable[..., ModelResult],
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not (paths.root / "analysis" / "chunks").exists():
        raise RuntimeError("Run analysis first")
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    if str(status.get("phase", "")) != "analyzed":
        raise RuntimeError(
            "Takeover analysis is not complete; resume the failed batch before generating a proposal "
            f"(phase={status.get('phase', 'unknown')}, next_batch={status.get('next_batch')})"
        )
    _report_progress(progress_callback, "阶段 1/4：正在校验分析状态和证据完整性", 5)
    records, integrity_errors = _validated_analysis_records(paths, status)
    if integrity_errors:
        raise RuntimeError("Analysis artifacts are incomplete:\n- " + "\n- ".join(integrity_errors))
    _report_progress(
        progress_callback,
        f"阶段 2/4：已确认 {len(records)} 个证据批次，正在构建 Director 输入",
        20,
    )
    _report_progress(progress_callback, "阶段 3/4：正在调用 Director 模型综合接管提案", 30)
    result = llm_call(
        paths,
        "director",
        [
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {"role": "user", "content": _synthesis_prompt(paths, instruction)},
        ],
        purpose="takeover.synthesize",
    )
    _report_progress(progress_callback, "阶段 4/4：Director 已返回，正在校验并写入提案", 85)
    proposal = extract_json_object(result.content)
    if not isinstance(proposal, dict):
        raise ValueError("Takeover synthesis did not return a JSON object")
    manifest = read_yaml(paths.root / "source" / "import-manifest.yaml", {})
    boundary = proposal.setdefault("continuation_boundary", {})
    boundary["last_existing_chapter"] = int(manifest.get("last_chapter", 0))
    boundary["next_chapter"] = int(manifest.get("last_chapter", 0)) + 1
    target = paths.root / "analysis" / "reports" / "takeover-proposal.json"
    write_json(target, proposal)
    write_json(target.with_suffix(".meta.json"), {
        "created_at": utc_now(), "route": result.route, "provider": result.provider, "model": result.model,
        "input_hash": result.input_hash, "output_hash": result.output_hash,
    })
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    status.update({"phase": "proposal_ready", "proposal": "analysis/reports/takeover-proposal.json", "updated_at": utc_now()})
    commit_projection_event(
        paths,
        event_type="takeover.proposal_generated",
        payload={"proposal": "analysis/reports/takeover-proposal.json", "conflicts": len(proposal.get("conflicts", []))},
        projections={"analysis/status.yaml": yaml.safe_dump(status, allow_unicode=True, sort_keys=False)},
        input_hash=result.output_hash,
    )
    _report_progress(progress_callback, "接管提案已生成，状态已更新", 100)
    return {
        "proposal": str(target),
        "conflicts": proposal.get("conflicts", []),
        "continuation_boundary": proposal.get("continuation_boundary", {}),
        "counts": {
            "characters": len(proposal.get("characters", [])),
            "facts": len(proposal.get("canon", {}).get("facts", [])),
            "volumes": len(proposal.get("reconstructed_volumes", [])),
            "outlines": len(proposal.get("reconstructed_chapter_outlines", [])),
        },
    }


def _evidence_matches_source(paths: WorkspacePaths, chapter: int, excerpt: str) -> bool:
    if chapter < 1 or not excerpt.strip():
        return False
    source = read_text(paths.root / "source" / "original" / f"chapter-{chapter:04d}.txt")
    if not source:
        return False
    normalize = lambda value: re.sub(r"\s+", "", value)
    return normalize(excerpt) in normalize(source)


def _validate_takeover_proposal(paths: WorkspacePaths, proposal: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    boundary = proposal.get("continuation_boundary")
    if not isinstance(boundary, dict) or not boundary.get("next_chapter"):
        errors.append("continuation_boundary.next_chapter is required")
        last_existing = 0
    else:
        last_existing = int(boundary.get("last_existing_chapter", 0) or 0)
        if int(boundary.get("next_chapter", 0) or 0) != last_existing + 1:
            errors.append("continuation_boundary.next_chapter must equal last_existing_chapter + 1")

    canon = proposal.get("canon", {}) if isinstance(proposal.get("canon"), dict) else {}
    facts = canon.get("facts", []) if isinstance(canon.get("facts"), list) else []
    fact_ids: set[str] = set()
    for fact in facts:
        if not isinstance(fact, dict):
            errors.append("canon.facts entries must be objects")
            continue
        fact_id = str(fact.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", fact_id):
            errors.append(f"invalid fact id: {fact_id}")
            continue
        if fact_id in fact_ids:
            errors.append(f"duplicate fact id: {fact_id}")
        fact_ids.add(fact_id)
        confidence = str(fact.get("confidence", "inferred")).lower()
        evidence = fact.get("evidence", [])
        if confidence == "confirmed" and (not isinstance(evidence, list) or not evidence):
            errors.append(f"confirmed fact {fact_id} has no evidence")
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, dict):
                errors.append(f"fact {fact_id} has malformed evidence")
                continue
            chapter = int(item.get("chapter", 0) or 0)
            excerpt = str(item.get("excerpt", "")).strip()
            if chapter < 1 or (last_existing and chapter > last_existing):
                errors.append(f"fact {fact_id} evidence chapter is outside imported range: {chapter}")
            if not excerpt:
                errors.append(f"fact {fact_id} evidence excerpt is empty")
            elif chapter >= 1 and not _evidence_matches_source(paths, chapter, excerpt):
                errors.append(f"fact {fact_id} evidence excerpt was not found in source chapter {chapter}")

    chars = proposal.get("characters", [])
    if not isinstance(chars, list) or not chars:
        errors.append("characters must be a non-empty list")
        chars = []
    ids: set[str] = set()
    for char in chars:
        char_id = str(char.get("id", "")) if isinstance(char, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", char_id):
            errors.append(f"invalid character id: {char_id}")
        elif char_id in ids:
            errors.append(f"duplicate character id: {char_id}")
        ids.add(char_id)
        if not isinstance(char, dict):
            continue
        for evidence in char.get("evidence", []) if isinstance(char.get("evidence"), list) else []:
            if isinstance(evidence, dict):
                chapter = int(evidence.get("chapter", 0) or 0)
                excerpt = str(evidence.get("excerpt", "")).strip()
                if excerpt and not _evidence_matches_source(paths, chapter, excerpt):
                    errors.append(f"character {char_id} evidence excerpt was not found in source chapter {chapter}")
        knowledge = char.get("knowledge_boundary", {}) if isinstance(char.get("knowledge_boundary"), dict) else {}
        for bucket in ("knows", "suspects", "must_not_know"):
            for fact_id in knowledge.get(bucket, []) if isinstance(knowledge.get(bucket), list) else []:
                if fact_ids and str(fact_id) not in fact_ids:
                    errors.append(f"character {char_id} {bucket} references unknown fact id: {fact_id}")

    outlines = proposal.get("reconstructed_chapter_outlines", [])
    if isinstance(outlines, list):
        seen_outline: set[int] = set()
        for outline in outlines:
            if not isinstance(outline, dict):
                continue
            number = int(outline.get("number") or outline.get("chapter") or 0)
            if number < 1 or (last_existing and number > last_existing):
                errors.append(f"reconstructed outline chapter outside imported range: {number}")
            if number in seen_outline:
                errors.append(f"duplicate reconstructed outline chapter: {number}")
            seen_outline.add(number)

    ledger = proposal.get("item_ledger", {}) if isinstance(proposal.get("item_ledger"), dict) else {}
    items = ledger.get("items", {}) if isinstance(ledger.get("items"), dict) else {}
    balances = ledger.get("balances", {}) if isinstance(ledger.get("balances"), dict) else {}
    for char_id, char_balances in balances.items():
        if ids and str(char_id) not in ids:
            errors.append(f"item ledger references unknown character: {char_id}")
        if isinstance(char_balances, dict):
            for item_id, quantity in char_balances.items():
                if items and str(item_id) not in items:
                    errors.append(f"item ledger references unknown item: {item_id}")
                try:
                    if int(quantity) < 0:
                        errors.append(f"negative reconstructed item balance: {char_id}/{item_id}")
                except (TypeError, ValueError):
                    errors.append(f"non-integer item balance: {char_id}/{item_id}")
    return errors


def _blocking_conflicts(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for conflict in proposal.get("conflicts", []) if isinstance(proposal.get("conflicts"), list) else []:
        if not isinstance(conflict, dict):
            continue
        severity = str(conflict.get("severity", "medium")).lower()
        status = str(conflict.get("status", "unresolved")).lower()
        if severity in {"high", "critical"} and status not in {"resolved", "accepted"}:
            result.append(conflict)
    return result


def _takeover_projections(paths: WorkspacePaths, proposal: dict[str, Any]) -> dict[str, str]:
    projections: dict[str, str] = {}
    canon = proposal.get("canon", {}) if isinstance(proposal.get("canon"), dict) else {}
    projections["canon/facts.yaml"] = yaml.safe_dump({"facts": canon.get("facts", [])}, allow_unicode=True, sort_keys=False)
    projections["canon/immutable-facts.yaml"] = yaml.safe_dump({"facts": canon.get("immutable_facts", [])}, allow_unicode=True, sort_keys=False)
    projections["canon/world.yaml"] = yaml.safe_dump(canon.get("world", {}), allow_unicode=True, sort_keys=False)
    projections["canon/locations.yaml"] = yaml.safe_dump(
        {"locations": canon.get("locations", []), "travel_minutes": canon.get("travel_minutes", {})},
        allow_unicode=True, sort_keys=False,
    )
    reconstructed_premise = str(proposal.get("reconstructed_premise", "")).strip()
    if reconstructed_premise:
        project_title = str(read_yaml(paths.project, {}).get("title", "小说"))
        projections["canon/premise.md"] = f"# {project_title}\n\n{reconstructed_premise}\n"
    if proposal.get("retrospective_macro"):
        projections["planning/macro.md"] = "# 已有剧情回顾\n\n" + str(proposal["retrospective_macro"]).strip() + "\n"

    index: list[dict[str, Any]] = []
    knowledge: dict[str, Any] = {"characters": {}}
    for raw in proposal.get("characters", []):
        char_id = str(raw["id"])
        profile_data = raw.get("profile", {}) if isinstance(raw.get("profile"), dict) else {}
        profile = {
            "id": char_id,
            "name": raw.get("name", char_id),
            "role": raw.get("role", "supporting"),
            "immutable": profile_data.get("immutable", {}),
            "personality": profile_data.get("personality", {}),
            "speech": profile_data.get("speech", {}),
            "abilities": profile_data.get("abilities", {}),
            "reconstruction": {"source": "existing_novel", "evidence": raw.get("evidence", [])},
        }
        state = copy.deepcopy(raw.get("current_state", {}) or {})
        state["character_id"] = char_id
        state["updated_after_chapter"] = int(proposal["continuation_boundary"]["last_existing_chapter"])
        arc = copy.deepcopy(raw.get("arc", {}) or {})
        arc["character_id"] = char_id
        arc["source"] = "reconstructed"
        boundary = raw.get("knowledge_boundary", {}) or {}
        knowledge["characters"][char_id] = {
            "knows": boundary.get("knows", []), "suspects": boundary.get("suspects", []), "must_not_know": boundary.get("must_not_know", []),
            "source": "reconstructed",
        }
        projections[f"characters/profiles/{char_id}.yaml"] = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
        projections[f"characters/arcs/{char_id}.yaml"] = yaml.safe_dump(arc, allow_unicode=True, sort_keys=False)
        projections[f"state/characters/{char_id}.yaml"] = yaml.safe_dump(state, allow_unicode=True, sort_keys=False)
        index.append({"id": char_id, "name": profile["name"], "role": profile["role"]})
    projections["characters/index.yaml"] = yaml.safe_dump({"characters": index}, allow_unicode=True, sort_keys=False)
    projections["characters/knowledge-boundaries.yaml"] = yaml.safe_dump(knowledge, allow_unicode=True, sort_keys=False)
    projections["characters/relationships.yaml"] = yaml.safe_dump({"relationships": proposal.get("relationships", [])}, allow_unicode=True, sort_keys=False)
    projections["state/relationship-state.yaml"] = yaml.safe_dump({"relationships": proposal.get("relationships", [])}, allow_unicode=True, sort_keys=False)
    projections["state/timeline.yaml"] = yaml.safe_dump(proposal.get("timeline", {"events": []}), allow_unicode=True, sort_keys=False)
    item_ledger = copy.deepcopy(proposal.get("item_ledger", {"items": {}, "balances": {}, "events": []}) or {})
    if not isinstance(item_ledger, dict):
        item_ledger = {"items": {}, "balances": {}, "events": []}
    if not item_ledger.get("items") and isinstance(canon.get("items"), list):
        item_ledger["items"] = {
            str(item.get("id")): {
                k: v for k, v in item.items()
                if k not in {"id", "current_holder"}
            }
            for item in canon.get("items", [])
            if isinstance(item, dict) and item.get("id")
        }
    for metadata in (item_ledger.get("items", {}) if isinstance(item_ledger, dict) else {}).values():
        if isinstance(metadata, dict):
            metadata.pop("current_holder", None)
    item_ledger.setdefault("balances", {})
    item_ledger.setdefault("events", [])
    projections["state/item-ledger.yaml"] = yaml.safe_dump(item_ledger, allow_unicode=True, sort_keys=False)
    projections["state/plot-ledger.yaml"] = yaml.safe_dump(proposal.get("plot_ledger", {"threads": []}), allow_unicode=True, sort_keys=False)
    projections["state/foreshadowing.yaml"] = yaml.safe_dump(proposal.get("foreshadowing", {"items": []}), allow_unicode=True, sort_keys=False)
    projections["state/continuation-boundary.yaml"] = yaml.safe_dump(proposal.get("continuation_boundary", {}), allow_unicode=True, sort_keys=False)

    for volume in proposal.get("reconstructed_volumes", []) if isinstance(proposal.get("reconstructed_volumes"), list) else []:
        if not isinstance(volume, dict):
            continue
        order = int(volume.get("order", 0) or 0)
        if order > 0:
            item = copy.deepcopy(volume)
            item["source"] = "reconstructed"
            projections[f"planning/volumes/volume-{order:03d}.yaml"] = yaml.safe_dump(item, allow_unicode=True, sort_keys=False)
    for outline in proposal.get("reconstructed_chapter_outlines", []) if isinstance(proposal.get("reconstructed_chapter_outlines"), list) else []:
        if not isinstance(outline, dict):
            continue
        number = int(outline.get("number") or outline.get("chapter") or 0)
        if number > 0:
            item = copy.deepcopy(outline)
            item["source"] = "reconstructed"
            text = yaml.safe_dump(item, allow_unicode=True, sort_keys=False)
            projections[f"planning/reconstructed-outlines/chapter-{number:04d}.yaml"] = text
            projections[f"planning/chapter-outlines/chapter-{number:04d}.yaml"] = text

    report = {
        "generated_at": utc_now(),
        "confidence_summary": proposal.get("confidence_summary", {}),
        "conflicts": proposal.get("conflicts", []),
        "continuation_boundary": proposal.get("continuation_boundary", {}),
        "counts": {
            "characters": len(index),
            "facts": len(canon.get("facts", [])),
            "plot_threads": len((proposal.get("plot_ledger", {}) or {}).get("threads", [])),
            "foreshadowing": len((proposal.get("foreshadowing", {}) or {}).get("items", [])),
        },
    }
    projections["analysis/reports/takeover-report.yaml"] = yaml.safe_dump(report, allow_unicode=True, sort_keys=False)
    projections["analysis/conflicts/current.yaml"] = yaml.safe_dump({"conflicts": proposal.get("conflicts", [])}, allow_unicode=True, sort_keys=False)
    project = read_yaml(paths.project, {})
    project["current_stage"] = "continuation_plan"
    if reconstructed_premise:
        project["premise"] = reconstructed_premise
    project["last_committed_chapter"] = int(proposal["continuation_boundary"]["last_existing_chapter"])
    project["updated_at"] = utc_now()
    project["takeover"] = {
        "applied_at": utc_now(),
        "proposal": "analysis/reports/takeover-proposal.json",
        "confidence_summary": proposal.get("confidence_summary", {}),
    }
    projections["project.yaml"] = yaml.safe_dump(project, allow_unicode=True, sort_keys=False)
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    status.update({"phase": "takeover_applied", "applied": True, "updated_at": utc_now()})
    projections["analysis/status.yaml"] = yaml.safe_dump(status, allow_unicode=True, sort_keys=False)
    return projections


def apply_takeover_proposal(paths: WorkspacePaths, proposal_relative_path: str, *, accept_unresolved_conflicts: bool = False) -> dict[str, Any]:
    proposal_path = (paths.root / proposal_relative_path).resolve()
    reports_root = (paths.root / "analysis" / "reports").resolve()
    if reports_root not in proposal_path.parents or not proposal_path.exists():
        raise ValueError("Takeover proposal must exist under analysis/reports")
    proposal = read_json(proposal_path, {})
    errors = _validate_takeover_proposal(paths, proposal)
    if errors:
        raise ValueError("Invalid takeover proposal: " + "; ".join(errors))
    blocking = _blocking_conflicts(proposal)
    if blocking and not accept_unresolved_conflicts:
        raise RuntimeError(
            f"Takeover proposal has {len(blocking)} unresolved high/critical conflicts. Review analysis/conflicts before applying, or explicitly accept them."
        )
    with workspace_lock(paths, "takeover-apply"):
        checkpoint = create_checkpoint(paths, "before-takeover-apply")
        projections = _takeover_projections(paths, proposal)
        event = commit_projection_event(
            paths,
            event_type="takeover.applied",
            payload={"proposal": proposal_relative_path, "checkpoint": checkpoint.name, "accepted_blocking_conflicts": len(blocking) if accept_unresolved_conflicts else 0},
            projections=projections,
        )
    reindex_workspace(paths)
    return {
        "applied": True,
        "checkpoint": checkpoint.name,
        "event": event,
        "blocking_conflicts_accepted": len(blocking) if accept_unresolved_conflicts else 0,
        "continuation_boundary": proposal.get("continuation_boundary", {}),
    }


def takeover_status(paths: WorkspacePaths) -> dict[str, Any]:
    manifest = read_yaml(paths.root / "source" / "import-manifest.yaml", {})
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    proposal = read_json(paths.root / "analysis" / "reports" / "takeover-proposal.json", {})
    boundary = read_yaml(paths.root / "state" / "continuation-boundary.yaml", {})
    continuation_progress = read_json(_continuation_progress_path(paths), {})
    chunks = _analysis_chunk_files(paths)
    proposal_ready = (
        str(status.get("phase", "")) in {"proposal_ready", "takeover_applied"}
        and status.get("proposal") == "analysis/reports/takeover-proposal.json"
        and bool(proposal)
    )
    integrity = {"ok": True, "errors": []}
    if str(status.get("phase", "")) in {"analyzed", "proposal_ready", "takeover_applied"}:
        _, integrity_errors = _validated_analysis_records(paths, status if isinstance(status, dict) else {})
        integrity = {"ok": not integrity_errors, "errors": integrity_errors}
    return {
        "phase": status.get("phase", "not_started"),
        "chapter_count": manifest.get("chapter_count", 0),
        "last_existing_chapter": manifest.get("last_chapter", 0),
        "analyzed_batches": len(status.get("completed_batch_ids", [])) or len(chunks),
        "progress": {
            "planned_batch_count": status.get("planned_batch_count", status.get("batch_count", 0)),
            "completed_batch_ids": status.get("completed_batch_ids", []),
            "current_batch": status.get("current_batch"),
            "failed_batch": status.get("failed_batch"),
            "next_batch": status.get("next_batch"),
            "last_error": status.get("last_error", ""),
            "adaptations": status.get("adaptations", []),
            "artifact_integrity": integrity,
        },
        "proposal_ready": proposal_ready,
        "conflicts": proposal.get("conflicts", []) if proposal_ready else [],
        "applied": bool(status.get("applied")),
        "continuation_boundary": boundary or proposal.get("continuation_boundary", {}),
        "continuation_plan": continuation_progress,
        "projection_status": projection_status(paths),
    }


def _continuation_progress_path(paths: WorkspacePaths) -> Path:
    return paths.root / CONTINUATION_PROGRESS_PATH


def _first_missing_continuation_chapter(paths: WorkspacePaths) -> int:
    boundary = read_yaml(paths.root / "state" / "continuation-boundary.yaml", {})
    start = int(boundary.get("next_chapter", 0) or 0)
    if start < 1:
        raise RuntimeError("Continuation boundary has no valid next_chapter")
    current = start
    future_outlines: list[int] = []
    for path in (paths.root / "planning" / "chapter-outlines").glob("chapter-*.yaml"):
        match = re.search(r"(\d+)", path.stem)
        if match and int(match.group(1)) >= start:
            future_outlines.append(int(match.group(1)))
    for number in sorted(set(future_outlines)):
        if number < current:
            continue
        if number != current:
            raise RuntimeError(
                f"Future chapter outlines have a gap: expected chapter {current}, found {number}"
            )
        current += 1
    return current


def _continuation_request_key(
    start: int,
    end: int,
    batch_size: int,
    instruction: str,
) -> str:
    stable = json.dumps(
        {
            "start": start,
            "end": end,
            "batch_size": batch_size,
            "instruction": instruction.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _continuation_batch_specs(start: int, end: int, batch_size: int) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    batch_number = 1
    current = start
    while current <= end:
        batch_end = min(current + batch_size - 1, end)
        specs.append({
            "batch": batch_number,
            "start": current,
            "end": batch_end,
            "status": "pending",
            "proposal": "",
        })
        current = batch_end + 1
        batch_number += 1
    return specs


def _continuation_batch_path(paths: WorkspacePaths, start: int, end: int) -> Path:
    return paths.root / "planning" / "proposals" / f"continuation-{start:04d}-{end:04d}-batch.json"


def _validate_continuation_batch(
    proposal: Any,
    start: int,
    end: int,
) -> dict[str, Any]:
    if not isinstance(proposal, dict) or not isinstance(proposal.get("chapters"), list):
        raise ValueError("Continuation plan must contain a chapters list")
    expected = list(range(start, end + 1))
    actual = [int(item.get("number", 0)) for item in proposal["chapters"] if isinstance(item, dict)]
    if actual != expected:
        raise ValueError(f"Continuation plan chapter numbers must be exactly {expected[0]}-{expected[-1]}")
    normalized_chapters: list[dict[str, Any]] = []
    for chapter in proposal["chapters"]:
        number = int(chapter["number"])
        normalized = normalize_generated_chapter_outline(chapter, number)
        normalized["reader_orientation"] = normalize_reader_orientation(normalized, number)
        normalized["narrative_timing"] = normalize_narrative_timing(normalized, number)
        normalized["restricted_actions"] = normalize_restricted_actions(normalized, number)
        normalized["rhythm_signature"] = normalize_rhythm_signature(normalized, number)
        normalized_chapters.append(normalized)
    validate_outline_plan_contract(normalized_chapters)
    proposal["chapters"] = normalized_chapters
    proposal["chapter_range"] = [start, end]
    return proposal


def _existing_outline_tail(paths: WorkspacePaths, before_chapter: int, limit: int = 12) -> list[dict[str, Any]]:
    paths_and_numbers: list[tuple[int, Path]] = []
    for path in (paths.root / "planning" / "chapter-outlines").glob("chapter-*.yaml"):
        match = re.search(r"(\d+)", path.stem)
        if match:
            number = int(match.group(1))
            if number < before_chapter:
                paths_and_numbers.append((number, path))
    result: list[dict[str, Any]] = []
    for number, path in sorted(paths_and_numbers)[-limit:]:
        outline = read_yaml(path, {})
        if isinstance(outline, dict):
            result.append({
                "number": number,
                "title": outline.get("title", ""),
                "mini_arc": outline.get("mini_arc", ""),
                "chapter_task": outline.get("chapter_task", ""),
                "mission": outline.get("mission", ""),
                "participants": outline.get("participants", []),
                "threads_advanced": outline.get("threads_advanced", []),
                "foreshadowing_actions": outline.get("foreshadowing_actions", []),
                "narrative_timing": outline.get("narrative_timing", {}),
                "rhythm_signature": outline.get("rhythm_signature", {}),
            })
    return result


def _continuation_volume_context(paths: WorkspacePaths) -> list[dict[str, Any]]:
    volumes: list[dict[str, Any]] = []
    for path in sorted((paths.root / "planning" / "volumes").glob("volume-*.yaml")):
        volume = read_yaml(path, {})
        if isinstance(volume, dict):
            volumes.append(volume)
    return volumes


def _load_or_create_continuation_progress(
    paths: WorkspacePaths,
    *,
    start: int,
    end: int,
    chapter_count: int,
    batch_size: int,
    instruction: str,
) -> tuple[dict[str, Any], bool]:
    request_key = _continuation_request_key(start, end, batch_size, instruction)
    path = _continuation_progress_path(paths)
    saved = read_json(path, {})
    if isinstance(saved, dict) and saved:
        saved_start = int(saved.get("start", 0) or 0)
        saved_key = str(saved.get("request_key", ""))
        if saved_start == start and saved_key and saved_key != request_key:
            raise RuntimeError(
                "A continuation planning session is already pending for this chapter range. "
                "Resume it with the same chapter_count, batch_size, and instruction, or apply its proposal first."
            )
        if saved_start == start and saved_key == request_key:
            saved.setdefault("batches", _continuation_batch_specs(start, end, batch_size))
            return saved, True
    progress = {
        "version": 1,
        "type": "continuation_plan_progress",
        "status": "running",
        "boundary_start": int((read_yaml(paths.root / "state" / "continuation-boundary.yaml", {}) or {}).get("next_chapter", 0) or 0),
        "start": start,
        "target_end": end,
        "requested_count": chapter_count,
        "batch_size": batch_size,
        "instruction": instruction.strip(),
        "request_key": request_key,
        "batches": _continuation_batch_specs(start, end, batch_size),
        "proposal": "",
        "updated_at": utc_now(),
    }
    write_json(path, progress)
    return progress, False


def _save_continuation_progress(paths: WorkspacePaths, progress: dict[str, Any]) -> None:
    progress["updated_at"] = utc_now()
    write_json(_continuation_progress_path(paths), progress)


def _continuation_plan_result(paths: WorkspacePaths, progress: dict[str, Any], *, resumed: bool) -> dict[str, Any]:
    target = Path(str(progress.get("proposal", "")))
    if not target.is_absolute():
        target = paths.root / target
    chapters = read_json(target, {}).get("chapters", []) if target.exists() else []
    completed_batches = sum(1 for batch in progress.get("batches", []) if batch.get("status") == "completed")
    return {
        "proposal": str(target),
        "chapter_range": [int(progress["start"]), int(progress["target_end"])],
        "requested_count": int(progress.get("requested_count", len(chapters))),
        "planned_count": len(chapters),
        "batch_size": int(progress.get("batch_size", 10)),
        "batch_count": len(progress.get("batches", [])),
        "completed_batches": completed_batches,
        "resumed": resumed,
        "checkpoint": str(_continuation_progress_path(paths)),
        "strategy": read_json(target, {}).get("strategy", "") if target.exists() else "",
        "risks": read_json(target, {}).get("risks", []) if target.exists() else [],
    }


def _continuation_plan_prompt(paths: WorkspacePaths, start: int, end: int, instruction: str) -> str:
    boundary = read_yaml(paths.root / "state" / "continuation-boundary.yaml", {})
    if not boundary:
        raise RuntimeError("Continuation boundary is missing; apply the takeover proposal first")
    ending_plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    if not ending_plan or str(ending_plan.get("status", "unset")) == "unset":
        raise RuntimeError("Set an ending target before generating a continuation plan")
    if start < 1 or end < start:
        raise RuntimeError("Continuation planning range is invalid")
    maximum = int((ending_plan.get("target") or {}).get("max_chapter", 0) or 0)
    if maximum and start > maximum:
        raise RuntimeError(f"Continuation boundary {start} is beyond ending max chapter {maximum}")
    if maximum:
        if start > maximum or end > maximum:
            raise RuntimeError(f"Continuation planning range {start}-{end} exceeds ending max chapter {maximum}")
    outline_tail = _existing_outline_tail(paths, start)
    data = {
        "project": read_yaml(paths.project, {}),
        "ending_plan": ending_plan,
        "story_budget": read_yaml(paths.root / "planning" / "story-budget.yaml", {}),
        "ending_progress": read_yaml(paths.root / "state" / "ending-progress.yaml", {}),
        "boundary": boundary,
        "characters": read_yaml(paths.root / "characters" / "index.yaml", {}),
        "relationships": read_yaml(paths.root / "state" / "relationship-state.yaml", {}),
        "plot": read_yaml(paths.root / "state" / "plot-ledger.yaml", {}),
        "foreshadowing": read_yaml(paths.root / "state" / "foreshadowing.yaml", {}),
        "timeline": read_yaml(paths.root / "state" / "timeline.yaml", {}),
        "world": read_yaml(paths.root / "canon" / "world.yaml", {}),
        "existing_outline_tail": outline_tail,
        "volume_plan": _continuation_volume_context(paths),
        "planning_range": [start, end],
    }
    schema = {
        "strategy": "",
        "chapter_range": [start, end],
        "future_volumes": [],
        "chapters": [{
            "number": start, "title": "", "mission": "", "participants": [], "must_happen": [], "must_not_happen": [],
            "threads_advanced": [], "foreshadowing_actions": [], "target_chars": 3000, "pov_character": "", "pov_mode": "third_limited",
            "start_time": "", "end_time": "", "location_ids": [],
        }],
        "risks": [],
    }
    return (
        f"请规划从第{start}章到第{end}章的续写。本批次只负责这个范围，不得输出范围外的章节。不得重写第{start-1}章以前的任何事实。必须衔接 existing_outline_tail 和 volume_plan，服从 ending_plan/story_budget；进入 final arc 后不得新增长期剧情线或长期伏笔，并优先回收必收主线、伏笔与人物结局条件。"
        + (f"\n用户要求：{instruction.strip()}" if instruction.strip() else "")
        + "\n\n当前接管状态：\n" + json.dumps(data, ensure_ascii=False)
        + "\n\n输出结构：\n" + json.dumps(schema, ensure_ascii=False, indent=2)
    )


def generate_continuation_plan(
    paths: WorkspacePaths,
    llm_call: Callable[..., ModelResult],
    chapter_count: int = 30,
    instruction: str = "",
    progress_callback: ProgressCallback | None = None,
    batch_size: int = 10,
) -> dict[str, Any]:
    if chapter_count < 1:
        raise ValueError("chapter_count must be >= 1")
    if batch_size < 1 or batch_size > 20:
        raise ValueError("batch_size must be between 1 and 20")
    start = _first_missing_continuation_chapter(paths)
    ending_plan = read_yaml(paths.root / "planning" / "ending" / "ending-plan.yaml", {})
    maximum = int((ending_plan.get("target") or {}).get("max_chapter", 0) or 0) if isinstance(ending_plan, dict) else 0
    if maximum and start > maximum:
        raise RuntimeError(f"All chapters through ending max {maximum} already have outlines")
    requested_end = start + chapter_count - 1
    actual_end = min(requested_end, maximum) if maximum else requested_end
    progress, resumed = _load_or_create_continuation_progress(
        paths,
        start=start,
        end=actual_end,
        chapter_count=chapter_count,
        batch_size=batch_size,
        instruction=instruction,
    )
    progress["status"] = "running"
    progress["last_error"] = ""
    _save_continuation_progress(paths, progress)

    batches = progress.get("batches", [])
    total_batches = len(batches)
    for index, batch in enumerate(batches):
        batch_start = int(batch["start"])
        batch_end = int(batch["end"])
        batch_path = _continuation_batch_path(paths, batch_start, batch_end)
        batch["proposal"] = str(batch_path.relative_to(paths.root))
        if batch.get("status") == "completed" and batch_path.exists():
            _validate_continuation_batch(read_json(batch_path, {}), batch_start, batch_end)
            continue
        if batch_path.exists():
            try:
                _validate_continuation_batch(read_json(batch_path, {}), batch_start, batch_end)
            except (TypeError, ValueError, KeyError):
                batch_path.unlink()
            else:
                batch["status"] = "completed"
                batch["completed_at"] = utc_now()
                progress["completed_batches"] = index + 1
                _save_continuation_progress(paths, progress)
                continue

        batch["status"] = "running"
        progress["current_batch"] = index + 1
        progress["completed_batches"] = sum(1 for item in batches if item.get("status") == "completed")
        _save_continuation_progress(paths, progress)
        overall_start = 5 + int(85 * index / max(total_batches, 1))
        _report_progress(
            progress_callback,
            f"阶段 1/3：准备续写规划批次 {index + 1}/{total_batches}（第 {batch_start}-{batch_end} 章）",
            overall_start,
        )
        try:
            _report_progress(
                progress_callback,
                f"阶段 2/3：正在调用 Director 规划第 {batch_start}-{batch_end} 章",
                min(95, overall_start + 15),
            )
            result = llm_call(
                paths,
                "director",
                [
                    {"role": "system", "content": CONTINUATION_PLAN_SYSTEM},
                    {"role": "user", "content": _continuation_plan_prompt(paths, batch_start, batch_end, instruction)},
                ],
                purpose="takeover.continuation_plan",
            )
            proposal = _validate_continuation_batch(
                extract_json_object(result.content),
                batch_start,
                batch_end,
            )
            proposal["type"] = "continuation_plan_batch"
            proposal["batch"] = index + 1
            write_json(batch_path, proposal)
            write_json(batch_path.with_suffix(".meta.json"), {
                "type": "continuation_plan_batch",
                "batch": index + 1,
                "chapter_range": [batch_start, batch_end],
                "created_at": utc_now(),
                "route": result.route,
                "provider": result.provider,
                "model": result.model,
                "input_hash": result.input_hash,
                "output_hash": result.output_hash,
            })
            batch.update({"status": "completed", "completed_at": utc_now()})
            progress["completed_batches"] = sum(1 for item in batches if item.get("status") == "completed")
            progress["last_error"] = ""
            _save_continuation_progress(paths, progress)
            _report_progress(
                progress_callback,
                f"阶段 3/3：批次 {index + 1}/{total_batches} 已持久化",
                min(99, 5 + int(90 * (index + 1) / max(total_batches, 1))),
            )
        except AgentTaskPending:
            _save_continuation_progress(paths, progress)
            raise
        except Exception as exc:
            batch["status"] = "failed"
            progress["status"] = "failed"
            progress["failed_batch"] = index + 1
            progress["last_error"] = f"{type(exc).__name__}: {str(exc)[:2000]}"
            _save_continuation_progress(paths, progress)
            raise

    all_chapters: list[dict[str, Any]] = []
    future_volumes: list[dict[str, Any]] = []
    risks: list[Any] = []
    strategy = ""
    for index, batch in enumerate(batches):
        batch_start = int(batch["start"])
        batch_end = int(batch["end"])
        batch_path = _continuation_batch_path(paths, batch_start, batch_end)
        proposal = _validate_continuation_batch(read_json(batch_path, {}), batch_start, batch_end)
        if not strategy:
            strategy = str(proposal.get("strategy", ""))
        if isinstance(proposal.get("future_volumes"), list):
            future_volumes.extend(item for item in proposal["future_volumes"] if isinstance(item, dict))
        if isinstance(proposal.get("risks"), list):
            risks.extend(proposal["risks"])
        all_chapters.extend(proposal["chapters"])

    target = paths.root / "planning" / "proposals" / f"continuation-{start:04d}-{actual_end:04d}.json"
    aggregate = {
        "type": "continuation_plan",
        "strategy": strategy,
        "chapter_range": [start, actual_end],
        "future_volumes": future_volumes,
        "chapters": all_chapters,
        "risks": risks,
        "batches": [
            {"batch": index + 1, "chapter_range": [int(item["start"]), int(item["end"])]}
            for index, item in enumerate(batches)
        ],
    }
    write_json(target, aggregate)
    write_json(target.with_suffix(".meta.json"), {
        "type": "continuation_plan",
        "created_at": utc_now(),
        "chapter_range": [start, actual_end],
        "batch_size": batch_size,
        "batch_count": total_batches,
        "completed_batches": total_batches,
    })
    progress.update({
        "status": "completed",
        "current_batch": None,
        "failed_batch": None,
        "last_error": "",
        "completed_batches": total_batches,
        "proposal": str(target.relative_to(paths.root)),
        "completed_at": utc_now(),
    })
    _save_continuation_progress(paths, progress)
    _report_progress(progress_callback, "续写规划全部批次已生成并持久化", 100)
    result = _continuation_plan_result(paths, progress, resumed=resumed)
    result["clamped_to_ending_max"] = actual_end != requested_end
    return result


def apply_continuation_plan(paths: WorkspacePaths, proposal_relative_path: str) -> dict[str, Any]:
    proposal_path = (paths.root / proposal_relative_path).resolve()
    proposal_root = (paths.root / "planning" / "proposals").resolve()
    if proposal_root not in proposal_path.parents or not proposal_path.exists():
        raise ValueError("Continuation plan must exist under planning/proposals")
    proposal = read_json(proposal_path, {})
    if proposal.get("type") == "continuation_plan_batch":
        raise ValueError("Apply the aggregated continuation plan after all planning batches finish")
    start = _first_missing_continuation_chapter(paths)
    chapters = proposal.get("chapters", []) if isinstance(proposal, dict) else []
    if not chapters or int(chapters[0].get("number", 0)) != start:
        raise ValueError(f"Continuation plan must start at first unplanned chapter {start}")
    projections: dict[str, str] = {}
    expected_number = start
    normalized_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        number = int(chapter.get("number", 0))
        if number < start:
            raise ValueError("Continuation plan may not overwrite existing chapter outlines")
        if number != expected_number:
            raise ValueError(f"Continuation plan chapters must be contiguous; expected {expected_number}, got {number}")
        if paths.chapter_file(number, "outline").exists():
            raise ValueError(f"Continuation plan may not overwrite existing chapter outline {number}")
        ending_context_for_chapter(paths, number)
        expected_number += 1
        normalized = normalize_generated_chapter_outline(chapter, number)
        normalized["reader_orientation"] = normalize_reader_orientation(normalized, number)
        normalized["narrative_timing"] = normalize_narrative_timing(normalized, number)
        normalized["restricted_actions"] = normalize_restricted_actions(normalized, number)
        normalized["rhythm_signature"] = normalize_rhythm_signature(normalized, number)
        normalized_chapters.append(normalized)
    validate_outline_plan_contract(
        _existing_outline_tail(paths, start) + normalized_chapters,
        title_enforcement_start=start,
    )
    for normalized in normalized_chapters:
        number = int(normalized["number"])
        projections[f"planning/chapter-outlines/chapter-{number:04d}.yaml"] = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    existing_orders = []
    for path in (paths.root / "planning" / "volumes").glob("volume-*.yaml"):
        match = re.search(r"(\d+)", path.stem)
        if match:
            existing_orders.append(int(match.group(1)))
    next_order = max(existing_orders, default=0) + 1
    for offset, volume in enumerate(proposal.get("future_volumes", []) if isinstance(proposal.get("future_volumes"), list) else []):
        if isinstance(volume, dict):
            item = copy.deepcopy(volume)
            order = int(item.get("order", next_order + offset) or next_order + offset)
            if order in existing_orders:
                order = next_order + offset
            item["order"] = order
            item["source"] = "continuation_plan"
            projections[f"planning/volumes/volume-{order:03d}.yaml"] = yaml.safe_dump(item, allow_unicode=True, sort_keys=False)
    project = read_yaml(paths.project, {})
    project["current_stage"] = "chapter_execution"
    project["updated_at"] = utc_now()
    projections["project.yaml"] = yaml.safe_dump(project, allow_unicode=True, sort_keys=False)
    with workspace_lock(paths, "continuation-plan"):
        checkpoint = create_checkpoint(paths, "before-continuation-plan")
        event = commit_projection_event(
            paths,
            event_type="takeover.continuation_plan_applied",
            payload={"proposal": proposal_relative_path, "checkpoint": checkpoint.name, "chapter_count": len(chapters)},
            projections=projections,
        )
    progress = read_json(_continuation_progress_path(paths), {})
    if isinstance(progress, dict) and progress.get("proposal") == proposal_path.relative_to(paths.root).as_posix():
        progress.update({"status": "applied", "applied_at": utc_now()})
        _save_continuation_progress(paths, progress)
    reindex_workspace(paths)
    return {"applied": True, "chapter_count": len(chapters), "first_chapter": int(chapters[0]["number"]), "last_chapter": int(chapters[-1]["number"]), "checkpoint": checkpoint.name, "event": event}
