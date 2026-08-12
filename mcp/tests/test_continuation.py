from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp import continuation
from novel_production_mcp.event_store import seed_existing_workspace
from novel_production_mcp.models import ModelResult
from novel_production_mcp.provider import ProviderError
from novel_production_mcp.storage import get_workspace, read_json, read_yaml, write_json, write_yaml


@pytest.fixture()
def workspace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    source = tmp_path / "source"
    root.mkdir(); config.mkdir(); source.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    return root, source


def _result(content: dict, route: str = "extractor") -> ModelResult:
    text = json.dumps(content, ensure_ascii=False)
    return ModelResult(content=text, provider="mock", model="mock-model", route=route, latency_ms=1, input_hash="in", output_hash="out")


def _reader_orientation() -> dict[str, str]:
    return {
        "task_origin": "上一章留下未完成调查",
        "task_object": "异常来电及其来源",
        "why_now": "新消息即将到达",
        "stakes": "延误会失去追踪机会",
        "method_reason": "核对消息和时间记录可以缩小来源",
        "establish_before": "第一次验证消息之前",
    }


def _narrative_timing(chapter: int) -> dict[str, object]:
    return {
        "established_before_chapter": ["上一章留下的调查线索"],
        "introduce_this_chapter": [{
            "element_id": f"element_chapter_{chapter:04d}",
            "description": "本章首次建立的调查进展",
            "establish_before": "第一次使用该进展之前",
            "allow_early_hint": False,
        }],
    }


def _rhythm_signature(chapter: int) -> dict[str, str]:
    return {
        "opening_mode": f"承接-{chapter}",
        "core_action": f"推进-{chapter}",
        "evidence_type": f"证据-{chapter}",
        "emotional_turn": f"转折-{chapter}",
        "ending_hook_type": f"钩子-{chapter}",
    }


def test_existing_novel_takeover_end_to_end(workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _, source = workspace_env
    (source / "第1章 雨夜.md").write_text("第1章 雨夜\n陈默捡到一部手机。林雪看见后神色异常。\n", encoding="utf-8")
    (source / "第2章 来电.md").write_text("第2章 来电\n手机在零点响起。陈默没有告诉林雪短信内容。\n", encoding="utf-8")

    imported = core.import_existing_novel("oldbook", "旧书", str(source), "悬疑")
    paths = get_workspace("oldbook")
    assert imported["imported"]["manifest"]["chapter_count"] == 2
    assert read_yaml(paths.project, {})["last_committed_chapter"] == 2
    assert paths.chapter_file(1, "chapter").exists()
    assert not (os.stat(paths.root / "source" / "original" / "chapter-0001.txt").st_mode & 0o222)

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        if purpose == "takeover.extract":
            return _result({
                "facts": [{"id": "fact_phone_exists", "statement": "陈默持有异常手机", "category": "plot", "confidence": "confirmed", "evidence": [{"chapter": 1, "excerpt": "陈默捡到一部手机"}]}],
                "characters": [{"id": "char_chenmo", "name": "陈默", "role": "protagonist", "profile_observations": [], "speech_observations": [], "ability_observations": [], "state_observations": [], "knowledge_observations": [], "relationship_observations": []}],
                "world": {"rules": [], "locations": [], "factions": [], "organizations": []},
                "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": [],
            })
        if purpose == "takeover.synthesize":
            return _result({
                "retrospective_macro": "陈默获得异常手机并收到第一通来电。",
                "canon": {
                    "facts": [{"id": "fact_phone_exists", "statement": "陈默持有异常手机", "confidence": "confirmed", "evidence": [{"chapter": 1, "excerpt": "陈默捡到一部手机"}]}],
                    "immutable_facts": [],
                    "world": {"status": "reconstructed", "rules": [], "factions": [], "organizations": []},
                    "locations": [{"id": "loc_unknown", "name": "未明确地点", "confidence": "unknown", "evidence": []}],
                    "travel_minutes": {},
                    "items": [{"id": "item_phone", "name": "异常手机", "confidence": "confirmed", "evidence": [{"chapter": 1, "excerpt": "捡到一部手机"}]}],
                },
                "characters": [{
                    "id": "char_chenmo", "name": "陈默", "role": "protagonist",
                    "profile": {"immutable": {}, "personality": {"traits": ["谨慎"]}, "speech": {}, "abilities": {}},
                    "arc": {"current_phase": "异常初遇"},
                    "current_state": {"location": {"location_id": "loc_unknown", "place": "", "time": ""}, "knowledge": {"confirmed": ["fact_phone_exists"], "suspected": []}},
                    "knowledge_boundary": {"knows": ["fact_phone_exists"], "suspects": [], "must_not_know": []},
                    "evidence": [{"chapter": 2, "excerpt": "没有告诉林雪短信内容"}],
                }],
                "relationships": [],
                "timeline": {"current_story_time": "", "events": []},
                "item_ledger": {"items": {"item_phone": {"name": "异常手机"}}, "balances": {"char_chenmo": {"item_phone": 1}}, "events": []},
                "plot_ledger": {"threads": [{"id": "thread_phone", "status": "active", "progress": "手机在零点响起"}]},
                "foreshadowing": {"items": [{"id": "clue_linxue_reaction", "status": "unresolved", "description": "林雪看到手机时神色异常"}]},
                "reconstructed_volumes": [{"order": 1, "title": "异常来电", "chapter_range": [1, 2]}],
                "reconstructed_chapter_outlines": [
                    {"number": 1, "title": "雨夜", "mission": "获得手机", "participants": ["char_chenmo"]},
                    {"number": 2, "title": "来电", "mission": "收到来电", "participants": ["char_chenmo"]},
                ],
                "continuation_boundary": {"last_existing_chapter": 2, "next_chapter": 3, "story_time": "", "pov": "char_chenmo", "active_location": "loc_unknown", "active_characters": ["char_chenmo"], "active_threads": ["thread_phone"], "urgent_foreshadowing": ["clue_linxue_reaction"], "current_cliffhanger": "手机零点响起", "unresolved_questions": ["林雪为何认识手机"]},
                "conflicts": [], "confidence_summary": {"confirmed": 5, "inferred": 1, "unknown": 1},
            }, route="director")
        if purpose == "takeover.continuation_plan":
            return _result({
                "strategy": "继续追查来电来源",
                "chapter_range": [3, 4],
                "future_volumes": [],
                "chapters": [
                    {"number": 3, "title": "第二条短信", "reader_orientation": _reader_orientation(), "narrative_timing": _narrative_timing(3), "restricted_actions": [], "rhythm_signature": _rhythm_signature(3), "mission": "验证短信", "participants": ["char_chenmo"], "must_happen": ["验证信息"], "must_not_happen": ["直接揭露真相"], "threads_advanced": ["thread_phone"], "foreshadowing_actions": [], "target_chars": 2500, "pov_character": "char_chenmo", "pov_mode": "third_limited", "start_time": "", "end_time": "", "location_ids": ["loc_unknown"]},
                    {"number": 4, "title": "林雪", "reader_orientation": _reader_orientation(), "narrative_timing": _narrative_timing(4), "restricted_actions": [], "rhythm_signature": _rhythm_signature(4), "mission": "推进人物疑点", "participants": ["char_chenmo"], "must_happen": [], "must_not_happen": [], "threads_advanced": ["thread_phone"], "foreshadowing_actions": ["clue_linxue_reaction"], "target_chars": 2500, "pov_character": "char_chenmo", "pov_mode": "third_limited", "start_time": "", "end_time": "", "location_ids": ["loc_unknown"]},
                ],
                "risks": [],
            }, route="director")
        raise AssertionError(purpose)

    monkeypatch.setattr(core, "_call", fake_call)
    analyzed = core.analyze_imported_novel("oldbook", batch_size=2)
    assert analyzed["batch_count"] == 1
    proposal = core.generate_takeover_proposal("oldbook")
    assert proposal["continuation_boundary"]["next_chapter"] == 3
    applied = core.apply_takeover_proposal("oldbook")
    assert applied["applied"] is True
    assert read_yaml(paths.root / "state" / "continuation-boundary.yaml", {})["next_chapter"] == 3
    assert read_yaml(paths.root / "characters" / "profiles" / "char_chenmo.yaml", {})["name"] == "陈默"
    assert read_yaml(paths.root / "planning" / "chapter-outlines" / "chapter-0002.yaml", {})["source"] == "reconstructed"
    # v3 requires an explicit real ending target after takeover; the import default is only a provisional working horizon.
    core.set_ending_target("oldbook", 20)

    planned = core.generate_continuation_plan("oldbook", 2)
    assert planned["chapter_range"] == [3, 4]
    final = core.apply_continuation_plan("oldbook", "planning/proposals/continuation-0003-0004.json")
    assert final["first_chapter"] == 3
    assert paths.chapter_file(3, "outline").exists()
    assert read_yaml(paths.project, {})["current_stage"] == "chapter_execution"


def test_continuation_plans_batches_resume_and_extend_from_first_missing_outline(
    workspace_env: tuple[Path, Path],
) -> None:
    root, _ = workspace_env
    core.create_workspace("batched", "分批续写")
    paths = get_workspace("batched")
    core.set_ending_target("batched", 50)
    write_yaml(paths.root / "state" / "continuation-boundary.yaml", {
        "last_existing_chapter": 10,
        "next_chapter": 11,
        "story_time": "",
        "pov": "",
        "active_location": "",
        "active_characters": [],
        "active_threads": [],
        "urgent_foreshadowing": [],
        "current_cliffhanger": "",
        "unresolved_questions": [],
    })
    seed_existing_workspace(paths)

    calls: list[tuple[int, int]] = []
    fail_once = {"value": True}

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        assert paths_arg == paths
        assert route == "director"
        assert purpose == "takeover.continuation_plan"
        match = __import__("re").search(r"规划从第(\d+)章到第(\d+)章", messages[1]["content"])
        assert match
        start, end = int(match.group(1)), int(match.group(2))
        calls.append((start, end))
        if start == 21 and fail_once["value"]:
            fail_once["value"] = False
            raise RuntimeError("simulated planning failure")
        return _result({
            "strategy": "逐批推进主线",
            "chapters": [
                {
                    "number": number,
                    "title": f"{chr(0x4E00 + number)}灯照见{chr(0x5000 + number)}墙",
                    "reader_orientation": _reader_orientation(),
                    "narrative_timing": _narrative_timing(number),
                    "restricted_actions": [],
                    "rhythm_signature": _rhythm_signature(number),
                    "mission": "推进主线",
                    "participants": [],
                    "must_happen": [],
                    "must_not_happen": [],
                    "threads_advanced": [],
                    "foreshadowing_actions": [],
                    "target_chars": 2500,
                    "pov_character": "",
                    "pov_mode": "third_limited",
                    "start_time": "",
                    "end_time": "",
                    "location_ids": [],
                }
                for number in range(start, end + 1)
            ],
            "future_volumes": [],
            "risks": [],
        }, route="director")

    with pytest.raises(RuntimeError, match="simulated planning failure"):
        continuation.generate_continuation_plan(
            paths,
            fake_call,
            chapter_count=25,
            batch_size=10,
        )
    progress = read_json(paths.root / ".novel" / "continuation-plan-progress.json", {})
    assert progress["status"] == "failed"
    assert progress["completed_batches"] == 1
    assert (paths.root / "planning" / "proposals" / "continuation-0011-0020-batch.json").exists()

    resumed = continuation.generate_continuation_plan(
        paths,
        fake_call,
        chapter_count=25,
        batch_size=10,
    )
    assert resumed["resumed"] is True
    assert resumed["chapter_range"] == [11, 35]
    assert resumed["planned_count"] == 25
    assert resumed["completed_batches"] == 3
    assert calls == [(11, 20), (21, 30), (21, 30), (31, 35)]

    proposal_relative = Path(resumed["proposal"]).relative_to(root / "batched").as_posix()
    applied = continuation.apply_continuation_plan(paths, proposal_relative)
    assert applied["first_chapter"] == 11
    assert applied["last_chapter"] == 35
    assert read_yaml(paths.chapter_file(11, "outline"), {})["title"] == f"{chr(0x4E00 + 11)}灯照见{chr(0x5000 + 11)}墙"

    extension = continuation.generate_continuation_plan(
        paths,
        fake_call,
        chapter_count=5,
        batch_size=5,
    )
    assert extension["chapter_range"] == [36, 40]
    assert extension["planned_count"] == 5
    assert calls[-1] == (36, 40)


def test_takeover_blocks_unresolved_critical_conflict(workspace_env: tuple[Path, Path]) -> None:
    _, source = workspace_env
    (source / "1.txt").write_text("第一章。", encoding="utf-8")
    core.import_existing_novel("conflict", "冲突书", str(source))
    paths = get_workspace("conflict")
    proposal = {
        "canon": {"facts": [], "immutable_facts": [], "world": {}, "locations": [], "travel_minutes": {}, "items": []},
        "characters": [{"id": "char_a", "name": "甲", "role": "protagonist", "profile": {}, "arc": {}, "current_state": {}, "knowledge_boundary": {}}],
        "relationships": [], "timeline": {"events": []}, "item_ledger": {"items": {}, "balances": {}, "events": []},
        "plot_ledger": {"threads": []}, "foreshadowing": {"items": []}, "reconstructed_volumes": [], "reconstructed_chapter_outlines": [],
        "continuation_boundary": {"last_existing_chapter": 1, "next_chapter": 2},
        "conflicts": [{"id": "conflict_1", "severity": "critical", "status": "unresolved", "description": "生日互相矛盾"}],
    }
    write_json(paths.root / "analysis" / "reports" / "takeover-proposal.json", proposal)
    with pytest.raises(RuntimeError):
        core.apply_takeover_proposal("conflict")


def test_single_file_chinese_chapter_split(workspace_env: tuple[Path, Path]) -> None:
    _, source = workspace_env
    book = source / "整本小说.txt"
    book.write_text(
        "序言\n这是序。\n\n第十一章 风起\n十一章正文。\n\n第十二章 夜行\n十二章正文。\n",
        encoding="utf-8",
    )
    result = core.import_existing_novel("singlefile", "整本小说", str(book))
    paths = get_workspace("singlefile")
    manifest = result["imported"]["manifest"]
    assert manifest["chapter_count"] == 2
    assert manifest["first_chapter"] == 11
    assert manifest["last_chapter"] == 12
    assert (paths.root / "source" / "original" / "preface.txt").read_text(encoding="utf-8").startswith("序言")
    assert "十一章正文" in paths.chapter_file(11, "chapter").read_text(encoding="utf-8")


def test_takeover_splits_524_batches_and_persists_progress(
    workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = workspace_env
    for number in range(1, 5):
        (source / f"第{number}章.md").write_text(f"第{number}章\n正文 {number}。\n", encoding="utf-8")
    core.import_existing_novel("adaptive", "自适应", str(source))
    paths = get_workspace("adaptive")
    calls: list[list[int]] = []

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        assert purpose == "takeover.extract"
        text = messages[1]["content"]
        numbers = [int(value) for value in __import__("re").findall(r"===== CHAPTER (\d+) =====", text)]
        calls.append(numbers)
        if len(numbers) > 1:
            raise ProviderError(
                "simulated Cloudflare 524",
                status_codes=(524,),
                categories=("origin_timeout",),
                routes=("extractor",),
            )
        return _result({"facts": [], "characters": [], "world": {}, "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": []})

    monkeypatch.setattr(core, "_call", fake_call)
    result = core.analyze_imported_novel("adaptive", batch_size=4, max_chars=8000)
    status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    assert result["batch_count"] == 4
    assert len(result["adaptations"]) == 3
    assert status["phase"] == "analyzed"
    assert len(status["completed_batch_ids"]) == 4
    assert calls[0] == [1, 2, 3, 4]
    assert sorted(batch for batch in calls if len(batch) == 1) == [[1], [2], [3], [4]]


def test_takeover_resume_uses_completed_batch_cache(
    workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = workspace_env
    for number in range(1, 4):
        (source / f"第{number}章.md").write_text(f"第{number}章\n正文 {number}。\n", encoding="utf-8")
    core.import_existing_novel("resume", "恢复", str(source))
    paths = get_workspace("resume")
    calls: list[int] = []
    fail_once = {2}

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        text = messages[1]["content"]
        number = int(__import__("re").search(r"===== CHAPTER (\d+) =====", text).group(1))
        calls.append(number)
        if number in fail_once:
            fail_once.remove(number)
            raise ProviderError("simulated 524", status_codes=(524,), categories=("origin_timeout",), routes=("extractor",))
        return _result({"facts": [], "characters": [], "world": {}, "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": []})

    monkeypatch.setattr(core, "_call", fake_call)
    with pytest.raises(ProviderError):
        core.analyze_imported_novel("resume", batch_size=1, max_chars=8000)
    failed_status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    assert failed_status["phase"] == "failed"
    assert failed_status["failed_batch"] == "0002"

    result = core.analyze_imported_novel("resume", batch_size=1, max_chars=8000)
    final_status = read_yaml(paths.root / "analysis" / "status.yaml", {})
    assert result["batch_count"] == 3
    assert final_status["phase"] == "analyzed"
    assert calls.count(1) == 1
    assert calls.count(2) == 2
    assert calls.count(3) == 1


def test_takeover_resume_after_adaptive_split_does_not_retry_parent(
    workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = workspace_env
    for number in range(1, 5):
        (source / f"第{number}章.md").write_text(f"第{number}章\n正文 {number}。\n", encoding="utf-8")
    core.import_existing_novel("adaptive-resume", "自适应续跑", str(source))
    paths = get_workspace("adaptive-resume")
    calls: list[list[int]] = []
    interrupt_child = True

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        nonlocal interrupt_child
        numbers = [int(value) for value in __import__("re").findall(r"===== CHAPTER (\d+) =====", messages[1]["content"])]
        calls.append(numbers)
        if numbers == [1, 2, 3, 4]:
            raise ProviderError("simulated 524", status_codes=(524,), categories=("origin_timeout",), routes=("extractor",))
        if numbers == [1, 2] and interrupt_child:
            interrupt_child = False
            raise RuntimeError("simulated interruption after split")
        return _result({"facts": [], "characters": [], "world": {}, "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": []})

    monkeypatch.setattr(core, "_call", fake_call)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        core.analyze_imported_novel("adaptive-resume", batch_size=4, max_chars=8000)

    plan = read_json(paths.root / "analysis" / "plan.json", {})
    assert [item["status"] for item in plan["batches"][:3]] == ["split", "failed", "pending"]

    result = core.analyze_imported_novel("adaptive-resume", batch_size=4, max_chars=8000)
    assert result["batch_count"] == 2
    assert calls.count([1, 2, 3, 4]) == 1
    assert calls.count([1, 2]) == 2
    assert calls.count([3, 4]) == 1


def test_synthesis_ignores_batch_metadata_files(
    workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = workspace_env
    (source / "第1章.md").write_text("第1章\n正文。\n", encoding="utf-8")
    core.import_existing_novel("meta-filter", "元数据过滤", str(source))
    paths = get_workspace("meta-filter")

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        return _result({"facts": [], "characters": [], "world": {}, "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": []})

    monkeypatch.setattr(core, "_call", fake_call)
    core.analyze_imported_novel("meta-filter", batch_size=1, max_chars=8000)
    meta_path = paths.root / "analysis" / "chunks" / "batch-0001.meta.json"
    meta = read_json(meta_path, {})
    meta["meta_only_marker"] = "MUST_NOT_REACH_DIRECTOR"
    write_json(meta_path, meta)

    prompt = continuation._synthesis_prompt(paths)
    assert "MUST_NOT_REACH_DIRECTOR" not in prompt


def test_takeover_proposal_blocks_missing_batch_metadata(
    workspace_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source = workspace_env
    (source / "第1章.md").write_text("第1章\n正文。\n", encoding="utf-8")
    core.import_existing_novel("integrity", "完整性校验", str(source))
    paths = get_workspace("integrity")

    def fake_call(paths_arg, route, messages, *, purpose, chapter=None, repair_round=0):
        return _result({"facts": [], "characters": [], "world": {}, "items": [], "timeline_events": [], "plot_threads": [], "foreshadowing": [], "chapter_outlines": [], "volume_signals": [], "conflicts": []})

    monkeypatch.setattr(core, "_call", fake_call)
    core.analyze_imported_novel("integrity", batch_size=1, max_chars=8000)
    (paths.root / "analysis" / "chunks" / "batch-0001.meta.json").unlink()

    with pytest.raises(RuntimeError, match="batch 0001.*metadata file is missing"):
        core.generate_takeover_proposal("integrity")
