from __future__ import annotations

import pytest

from novel_production_mcp.orientation import normalize_reader_orientation


def test_reader_orientation_requires_complete_planning_basis() -> None:
    with pytest.raises(ValueError, match="reader_orientation requires non-empty fields"):
        normalize_reader_orientation({
            "reader_orientation": {
                "task_origin": "校史馆要求补交失物照片",
                "task_object": "三号箱中的铜镜",
                "why_now": "施工前必须完成交接",
                "stakes": "",
                "method_reason": "门禁记录可以确认断电期间是否有人接近",
                "establish_before": "第一次检查监控之前",
            },
        }, 1)


def test_reader_orientation_preserves_minimum_causal_chain() -> None:
    orientation = normalize_reader_orientation({
        "reader_orientation": {
            "task_origin": "校史馆要求补交失物照片",
            "task_object": "三号箱中的铜镜",
            "why_now": "施工前必须完成交接",
            "stakes": "无法核验会由经手人承担遗失或调换责任",
            "method_reason": "门禁记录可以确认断电期间是否有人接近",
            "establish_before": "第一次检查监控之前",
        },
    }, 1)

    assert orientation["task_origin"] == "校史馆要求补交失物照片"
    assert orientation["stakes"] == "无法核验会由经手人承担遗失或调换责任"
    assert orientation["establish_before"] == "第一次检查监控之前"
