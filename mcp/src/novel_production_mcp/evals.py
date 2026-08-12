from __future__ import annotations

import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import core
from .storage import get_workspace, write_yaml
from .validation import validate_delta


def _seed(workspace_id: str) -> None:
    paths = get_workspace(workspace_id)
    write_yaml(paths.root / "canon" / "facts.yaml", {"facts": [
        {"id": "fact_phone_anomaly", "statement": "手机会自动收到消息"},
        {"id": "fact_org_identity", "statement": "林雪属于异常调查局"},
    ]})
    write_yaml(paths.root / "canon" / "locations.yaml", {
        "locations": [
            {"id": "loc_dorm", "name": "宿舍"},
            {"id": "loc_north", "name": "城北"},
        ],
        "travel_minutes": {"loc_dorm": {"loc_north": 30}},
    })
    write_yaml(paths.root / "characters" / "index.yaml", {"characters": [
        {"id": "char_001", "name": "陈默", "role": "protagonist"},
        {"id": "char_002", "name": "林雪", "role": "supporting"},
    ]})
    for char_id, name in (("char_001", "陈默"), ("char_002", "林雪")):
        write_yaml(paths.root / "characters" / "profiles" / f"{char_id}.yaml", {
            "id": char_id, "name": name, "abilities": {"ability_photography": 4} if char_id == "char_001" else {}
        })
        write_yaml(paths.root / "characters" / "arcs" / f"{char_id}.yaml", {"character_id": char_id})
        write_yaml(paths.root / "state" / "characters" / f"{char_id}.yaml", {
            "character_id": char_id,
            "location": {"location_id": "loc_dorm", "place": "宿舍", "time": "2026-01-01T20:00:00+08:00"},
        })
    write_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {"characters": {
        "char_001": {"knows": ["fact_phone_anomaly"], "suspects": [], "must_not_know": ["fact_org_identity"]},
        "char_002": {"knows": ["fact_org_identity"], "suspects": [], "must_not_know": []},
    }})
    write_yaml(paths.root / "state" / "timeline.yaml", {
        "current_story_time": "2026-01-01T20:00:00+08:00",
        "events": [{"id": "event_existing", "time": "2026-01-01T20:00:00+08:00", "location_id": "loc_dorm", "participants": ["char_001"]}],
    })
    write_yaml(paths.root / "state" / "item-ledger.yaml", {
        "items": {"item_potion": {"name": "药剂"}},
        "balances": {"char_001": {"item_potion": 0}},
        "events": [],
    })
    write_yaml(paths.root / "state" / "foreshadowing.yaml", {
        "items": [{"id": "clue_old_key", "status": "active", "deadline_chapter": 99}]
    })
    write_yaml(paths.chapter_file(1, "outline"), {
        "number": 1, "title": "测试章", "participants": ["char_001", "char_002"],
        "pov_character": "char_001", "pov_mode": "third_limited",
    })


def _load_fixtures() -> list[dict[str, Any]]:
    root = files("novel_production_mcp").joinpath("eval_fixtures")
    result = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".json"):
            result.append(json.loads(entry.read_text(encoding="utf-8")))
    return result


def run_deterministic_evals() -> dict[str, Any]:
    previous_root = os.environ.get("NOVEL_WORKSPACE_ROOT")
    previous_config = os.environ.get("NOVEL_CONFIG_DIR")
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="novel-evals-") as temp:
            base = Path(temp)
            os.environ["NOVEL_WORKSPACE_ROOT"] = str(base / "novels")
            os.environ["NOVEL_CONFIG_DIR"] = str(base / "config")
            core.create_workspace("eval-workspace", "评测小说")
            _seed("eval-workspace")
            paths = get_workspace("eval-workspace")
            for fixture in _load_fixtures():
                if fixture.get("name") == "foreshadowing_missed":
                    write_yaml(paths.root / "state" / "foreshadowing.yaml", {
                        "items": [{"id": "clue_old_key", "status": "active", "deadline_chapter": 1}]
                    })
                else:
                    write_yaml(paths.root / "state" / "foreshadowing.yaml", {
                        "items": [{"id": "clue_old_key", "status": "active", "deadline_chapter": 99}]
                    })
                delta = {"chapter": 1, "character_updates": {}, "relationship_updates": [], "timeline_events": [],
                         "inventory_events": [], "ability_events": [], "utterances": [], "pov_observations": [],
                         "plot_updates": [], "foreshadowing_updates": [], "canon_change_requests": []}
                delta.update(fixture.get("delta", {}))
                report = validate_delta(paths, 1, delta, str(fixture.get("draft", "")))
                codes = {issue.code for issue in report.issues}
                expected = set(fixture.get("expected_codes", []))
                passed = expected.issubset(codes)
                results.append({
                    "name": fixture["name"],
                    "passed": passed,
                    "expected_codes": sorted(expected),
                    "actual_codes": sorted(codes),
                })
    finally:
        if previous_root is None:
            os.environ.pop("NOVEL_WORKSPACE_ROOT", None)
        else:
            os.environ["NOVEL_WORKSPACE_ROOT"] = previous_root
        if previous_config is None:
            os.environ.pop("NOVEL_CONFIG_DIR", None)
        else:
            os.environ["NOVEL_CONFIG_DIR"] = previous_config
    return {"passed": all(item["passed"] for item in results), "total": len(results), "passed_count": sum(1 for item in results if item["passed"]), "results": results}
