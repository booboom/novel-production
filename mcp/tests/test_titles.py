from __future__ import annotations

import pytest

from novel_production_mcp.titles import (
    normalize_generated_chapter_outline,
    normalize_generated_chapter_title,
)


@pytest.mark.parametrize(
    ("raw", "chapter", "expected"),
    [
        ("失物登记·补交失物表1", 1, "补交失物表"),
        ("失物登记·核对门禁记录2", 2, "核对门禁记录"),
        ("第3章 借来旧相机", 3, "借来旧相机"),
        ("404号房", 4, "404号房"),
    ],
)
def test_normalize_generated_chapter_title(raw: str, chapter: int, expected: str) -> None:
    assert normalize_generated_chapter_title(raw, chapter) == expected


def test_generated_chapter_title_cannot_be_only_a_heading() -> None:
    with pytest.raises(ValueError, match="standalone title"):
        normalize_generated_chapter_title("第7章", 7)


def test_legacy_title_moves_planning_metadata_into_separate_fields() -> None:
    outline = normalize_generated_chapter_outline({
        "number": 1,
        "title": "失物登记·补交失物表1",
    }, 1)

    assert outline["title"] == "补交失物表"
    assert outline["mini_arc"] == "失物登记"
    assert outline["chapter_task"] == "补交失物表"


def test_explicit_planning_metadata_is_preserved() -> None:
    outline = normalize_generated_chapter_outline({
        "number": 2,
        "title": "核对门禁记录",
        "mini_arc": "东旧楼取证",
        "chapter_task": "确认门禁记录缺失的一分钟",
    }, 2)

    assert outline["title"] == "核对门禁记录"
    assert outline["mini_arc"] == "东旧楼取证"
    assert outline["chapter_task"] == "确认门禁记录缺失的一分钟"
