from __future__ import annotations

import copy
import re
from typing import Any, Mapping


_CHAPTER_HEADING_RE = re.compile(
    r"^(?:#\s*)?(?:第\s*[0-9零〇一二两三四五六七八九十百千万]+\s*[章回节]|chapter\s+[0-9]+)\s*[：:—\-]?\s*",
    re.IGNORECASE,
)
_TRAILING_TEMPLATE_INDEX_RE = re.compile(r"(?<=\D)\d{1,4}$")


def _title_and_inferred_mini_arc(value: object, chapter_number: int) -> tuple[str, str]:
    title = str(value or "").strip()
    title = _CHAPTER_HEADING_RE.sub("", title).strip()
    mini_arc = ""
    if "·" in title:
        mini_arc, title = (part.strip() for part in title.rsplit("·", 1))
    title = _TRAILING_TEMPLATE_INDEX_RE.sub("", title).strip(" \t·:：—-")
    if not title:
        raise ValueError(
            f"Chapter {chapter_number} must have a standalone title without a chapter number, "
            "arc prefix, or trailing sequence number"
        )
    return title, mini_arc


def normalize_generated_chapter_title(value: object, chapter_number: int) -> str:
    """Return the reader-facing title without heading, arc prefix, or template index."""
    title, _ = _title_and_inferred_mini_arc(value, chapter_number)
    return title


def normalize_generated_chapter_outline(
    chapter: Mapping[str, Any],
    chapter_number: int,
) -> dict[str, Any]:
    """Separate reader title from planning-only mini-arc and chapter-task metadata."""
    normalized = copy.deepcopy(dict(chapter))
    title, inferred_mini_arc = _title_and_inferred_mini_arc(
        normalized.get("title"),
        chapter_number,
    )
    normalized["title"] = title
    normalized["mini_arc"] = str(normalized.get("mini_arc") or inferred_mini_arc).strip()
    normalized["chapter_task"] = str(normalized.get("chapter_task") or title).strip()
    return normalized
