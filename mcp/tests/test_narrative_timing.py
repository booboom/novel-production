from __future__ import annotations

from pathlib import Path

import pytest

from novel_production_mcp.models import WorkspacePaths
from novel_production_mcp.narrative_timing import (
    build_narrative_timing_review_context,
    normalize_narrative_timing,
)
from novel_production_mcp.storage import write_yaml


def _timing(element_id: str, description: str) -> dict[str, object]:
    return {
        "established_before_chapter": ["上一章已经建立的任务"],
        "introduce_this_chapter": [{
            "element_id": element_id,
            "description": description,
            "establish_before": "第一次明确使用该信息之前",
            "allow_early_hint": False,
        }],
    }


def test_narrative_timing_requires_structured_first_introductions() -> None:
    with pytest.raises(ValueError, match="allow_early_hint"):
        normalize_narrative_timing({
            "narrative_timing": {
                "established_before_chapter": [],
                "introduce_this_chapter": [{
                    "element_id": "method_access_records",
                    "description": "门禁和监控记录成为调查手段",
                    "establish_before": "第一次调取记录之前",
                }],
            },
        }, 2)


def test_narrative_timing_preserves_first_available_chapter_contract() -> None:
    timing = normalize_narrative_timing({
        "narrative_timing": _timing(
            "method_access_records",
            "门禁和监控记录成为调查手段",
        ),
    }, 2)

    introduction = timing["introduce_this_chapter"][0]
    assert introduction["element_id"] == "method_access_records"
    assert introduction["allow_early_hint"] is False


def test_reviewer_context_collects_structured_and_legacy_future_timing(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "book")
    outlines = paths.root / "planning" / "chapter-outlines"
    outlines.mkdir(parents=True)
    write_yaml(paths.chapter_file(1, "outline"), {
        "number": 1,
        "title": "补交失物表",
        "mission": "重新核对三号箱",
    })
    write_yaml(paths.chapter_file(2, "outline"), {
        "number": 2,
        "title": "核对门禁记录",
        "chapter_task": "调取门禁和监控记录",
        "mission": "确认断电期间谁接近旧楼",
    })
    write_yaml(paths.chapter_file(3, "outline"), {
        "number": 3,
        "title": "时间差",
        "narrative_timing": _timing(
            "evidence_phone_clock_drift",
            "铜镜导致手机时间停滞成为可验证证据",
        ),
    })

    context = build_narrative_timing_review_context(paths, 1)

    assert context["legacy_future_outline_summaries"][0]["chapter"] == 2
    assert "门禁和监控" in context["legacy_future_outline_summaries"][0]["chapter_task"]
    assert context["future_first_introductions"] == [{
        "element_id": "evidence_phone_clock_drift",
        "description": "铜镜导致手机时间停滞成为可验证证据",
        "earliest_chapter": 3,
        "allow_early_hint": False,
    }]
