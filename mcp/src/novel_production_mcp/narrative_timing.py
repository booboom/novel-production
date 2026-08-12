from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .models import WorkspacePaths
from .storage import read_yaml


ELEMENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _string_list(value: Any, field: str, chapter_number: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"Chapter {chapter_number} narrative_timing.{field} must be a list"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"Chapter {chapter_number} narrative_timing.{field}[{index}] "
                "must be a non-empty string"
            )
        result.append(item.strip())
    return result


def normalize_narrative_timing(
    chapter: Mapping[str, Any],
    chapter_number: int,
) -> dict[str, Any]:
    """Validate the chapter-level contract for when narrative elements may appear."""
    raw = chapter.get("narrative_timing")
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"Chapter {chapter_number} must contain a narrative_timing mapping"
        )
    if "established_before_chapter" not in raw or "introduce_this_chapter" not in raw:
        raise ValueError(
            f"Chapter {chapter_number} narrative_timing requires "
            "established_before_chapter and introduce_this_chapter"
        )

    established = _string_list(
        raw.get("established_before_chapter"),
        "established_before_chapter",
        chapter_number,
    )
    introductions = raw.get("introduce_this_chapter")
    if not isinstance(introductions, list):
        raise ValueError(
            f"Chapter {chapter_number} narrative_timing.introduce_this_chapter "
            "must be a list"
        )

    normalized_introductions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    required = {"element_id", "description", "establish_before", "allow_early_hint"}
    for index, item in enumerate(introductions):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"Chapter {chapter_number} narrative_timing.introduce_this_chapter[{index}] "
                "must be a mapping"
            )
        missing = sorted(required.difference(item))
        element_id = str(item.get("element_id", "")).strip()
        description = str(item.get("description", "")).strip()
        establish_before = str(item.get("establish_before", "")).strip()
        allow_early_hint = item.get("allow_early_hint")
        if missing or not ELEMENT_ID_RE.fullmatch(element_id) or not description or not establish_before or not isinstance(allow_early_hint, bool):
            raise ValueError(
                f"Chapter {chapter_number} narrative_timing.introduce_this_chapter[{index}] "
                "requires ASCII snake_case element_id, non-empty description/establish_before, "
                "and boolean allow_early_hint"
            )
        if element_id in seen_ids:
            raise ValueError(
                f"Chapter {chapter_number} narrative_timing repeats element_id {element_id}"
            )
        seen_ids.add(element_id)
        normalized_introductions.append({
            "element_id": element_id,
            "description": description,
            "establish_before": establish_before,
            "allow_early_hint": allow_early_hint,
        })

    return {
        "established_before_chapter": established,
        "introduce_this_chapter": normalized_introductions,
    }


def _outline_number(path: Path) -> int | None:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else None


def build_narrative_timing_review_context(
    paths: WorkspacePaths,
    chapter_number: int,
    *,
    legacy_lookahead: int = 12,
) -> dict[str, Any]:
    """Build reviewer-only future timing evidence without exposing it to the writer."""
    current_outline = read_yaml(paths.chapter_file(chapter_number, "outline"), {})
    current_timing = (
        current_outline.get("narrative_timing", {})
        if isinstance(current_outline, dict)
        else {}
    )
    future_introductions_by_id: dict[str, dict[str, Any]] = {}
    legacy_future_summaries: list[dict[str, Any]] = []

    outlines: list[tuple[int, Path]] = []
    for path in (paths.root / "planning" / "chapter-outlines").glob("chapter-*.yaml"):
        number = _outline_number(path)
        if number is not None and number > chapter_number:
            outlines.append((number, path))

    for number, path in sorted(outlines):
        outline = read_yaml(path, {})
        if not isinstance(outline, dict):
            continue
        timing = outline.get("narrative_timing")
        introductions = timing.get("introduce_this_chapter") if isinstance(timing, dict) else None
        if isinstance(introductions, list):
            for item in introductions:
                if not isinstance(item, dict):
                    continue
                element_id = str(item.get("element_id", "")).strip()
                description = str(item.get("description", "")).strip()
                if not element_id or not description:
                    continue
                future_introductions_by_id.setdefault(element_id, {
                    "element_id": element_id,
                    "description": description,
                    "earliest_chapter": number,
                    "allow_early_hint": bool(item.get("allow_early_hint", False)),
                })
            continue
        if len(legacy_future_summaries) >= legacy_lookahead:
            continue
        summary = {
            "chapter": number,
            "title": outline.get("title", ""),
            "mini_arc": outline.get("mini_arc", ""),
            "chapter_task": outline.get("chapter_task", ""),
            "mission": outline.get("mission", ""),
            "must_happen": outline.get("must_happen", []),
        }
        legacy_future_summaries.append(summary)

    return {
        "current_chapter": chapter_number,
        "current_timing": current_timing if isinstance(current_timing, dict) else {},
        "future_first_introductions": list(future_introductions_by_id.values()),
        "legacy_future_outline_summaries": legacy_future_summaries,
        "policy": (
            "Future material is reviewer-only. Treat a concrete fact, method, evidence source, "
            "suspect direction, relationship, institution rule, or solution as premature when it "
            "appears before its earliest established chapter unless an allowed early hint covers "
            "that exact degree of disclosure."
        ),
    }
