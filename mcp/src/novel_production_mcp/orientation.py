from __future__ import annotations

from typing import Any, Mapping


READER_ORIENTATION_FIELDS = (
    "task_origin",
    "task_object",
    "why_now",
    "stakes",
    "method_reason",
    "establish_before",
)


def normalize_reader_orientation(
    chapter: Mapping[str, Any],
    chapter_number: int,
) -> dict[str, str]:
    """Validate planning facts needed to orient the reader before consequential action."""
    raw = chapter.get("reader_orientation")
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"Chapter {chapter_number} must contain a reader_orientation mapping"
        )
    missing = [
        field
        for field in READER_ORIENTATION_FIELDS
        if not isinstance(raw.get(field), str) or not str(raw.get(field)).strip()
    ]
    if missing:
        raise ValueError(
            f"Chapter {chapter_number} reader_orientation requires non-empty fields: "
            + ", ".join(missing)
        )
    return {field: str(raw[field]).strip() for field in READER_ORIENTATION_FIELDS}
