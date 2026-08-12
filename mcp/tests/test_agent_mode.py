from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from novel_production_mcp import core, server
from novel_production_mcp.event_store import seed_existing_workspace
from novel_production_mcp.storage import get_workspace, write_yaml


def _reader_orientation() -> dict[str, str]:
    return {
        "task_origin": "上一批次留下待处理主线",
        "task_object": "当前主线证据",
        "why_now": "本章承接上一章立即推进",
        "stakes": "延误会失去证据",
        "method_reason": "按既定调查步骤推进",
        "establish_before": "第一次调查行动之前",
    }


def _narrative_timing(chapter: int) -> dict[str, object]:
    return {
        "established_before_chapter": ["上一章已经建立的连续任务"],
        "introduce_this_chapter": [{
            "element_id": f"element_chapter_{chapter:04d}",
            "description": "本章首次推进的主线信息",
            "establish_before": "本章第一次使用该信息之前",
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


def _distinct_title(chapter: int) -> str:
    return f"{chr(0x4E00 + chapter)}灯照见{chr(0x5000 + chapter)}墙"


def _semantic_audits() -> dict[str, object]:
    return {
        "knowledge_provenance_audit": {"complete": True, "claims": [], "summary": "无越界知识"},
        "authorization_audit": {"complete": True, "actions": [], "summary": "无受限操作"},
        "outline_fidelity_audit": {"matches": True, "mismatches": [], "summary": "符合大纲"},
        "reasoning_chain_audit": {"valid": True, "chains": [], "summary": "推理成立"},
        "state_coverage_audit": {"complete": True, "changes": [], "summary": "无状态变化"},
        "repetition_audit": {"distinct": True, "repetitions": [], "summary": "无重复"},
        "planning_language_audit": {"naturalized": True, "occurrences": [], "summary": "规划术语已自然化"},
    }


@dataclass
class FakeContext:
    infos: list[str] = field(default_factory=list)

    async def info(self, message: str, **_: object) -> None:
        self.infos.append(message)

    async def report_progress(
        self,
        _progress: float,
        _total: float | None = None,
        _message: str | None = None,
    ) -> None:
        return None


def test_agent_mode_resumes_stage_generation_with_host_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "routes.toml").write_text(
        "[generation]\nmode = \"agent\"\n\n"
        "[routes.director]\nprovider = \"unused\"\nmodel = \"unused\"\n",
        encoding="utf-8",
    )

    core.create_workspace("demo", "测试小说")
    ctx = FakeContext()

    pending = asyncio.run(server.novel_generate_stage_proposal("demo", "director", ctx=ctx))

    task = pending["agent_task"]
    assert task["route"] == "director"
    assert task["purpose"] == "stage.director"
    assert task["response_format"] == "yaml_or_markdown"
    assert task["messages"]

    status = asyncio.run(server.novel_agent_task_status(task["task_id"], ctx=ctx))
    assert status["status"] == "pending"
    assert status["resume_available"] is True

    result = asyncio.run(
        server.novel_submit_agent_task(
            task["task_id"],
            "# 导演提案\n\n当前 Agent 生成的内容。",
            ctx=ctx,
        )
    )

    proposal_path = Path(result["proposal"])
    assert proposal_path.exists()
    assert "当前 Agent 生成的内容" in proposal_path.read_text(encoding="utf-8")
    assert result["stage"] == "director"

    completed = asyncio.run(server.novel_agent_task_status(task["task_id"], ctx=ctx))
    assert completed["status"] == "completed"
    assert completed["resume_available"] is False


def test_agent_mode_creates_a_new_task_for_each_planning_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "routes.toml").write_text(
        "[generation]\nmode = \"agent\"\n\n"
        "[routes.director]\nprovider = \"unused\"\nmodel = \"unused\"\n",
        encoding="utf-8",
    )
    core.create_workspace("demo", "分批规划")
    core.set_ending_target("demo", 30)
    from novel_production_mcp.storage import get_workspace

    paths = get_workspace("demo")
    write_yaml(paths.root / "state" / "continuation-boundary.yaml", {
        "last_existing_chapter": 10,
        "next_chapter": 11,
    })
    seed_existing_workspace(paths)

    def content_for(task: dict[str, object]) -> str:
        prompt = str(task["messages"][1]["content"])
        match = re.search(r"规划从第(\d+)章到第(\d+)章", prompt)
        assert match
        start, end = int(match.group(1)), int(match.group(2))
        return json.dumps({
            "strategy": "分批推进",
            "chapters": [
                {
                    "number": number,
                    "title": _distinct_title(number),
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
        }, ensure_ascii=False)

    ctx = FakeContext()
    first = asyncio.run(server.novel_generate_continuation_plan("demo", 15, batch_size=10, ctx=ctx))
    first_task = first["agent_task"]
    second = asyncio.run(server.novel_submit_agent_task(first_task["task_id"], content_for(first_task), ctx=ctx))
    second_task = second["agent_task"]
    assert second_task["task_id"] != first_task["task_id"]
    assert "第21章到第25章" in second_task["messages"][1]["content"]
    final = asyncio.run(server.novel_submit_agent_task(second_task["task_id"], content_for(second_task), ctx=ctx))
    assert final["chapter_range"] == [11, 25]
    assert final["planned_count"] == 15


def test_agent_mode_chapter_pipeline_resumes_after_writer_without_repeating_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "novels"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "routes.toml").write_text(
        "[generation]\nmode = \"agent\"\n\n"
        "[routes.writer]\nprovider = \"unused\"\nmodel = \"unused\"\n\n"
        "[routes.reviewer]\nprovider = \"unused\"\nmodel = \"unused\"\n\n"
        "[routes.repairer]\nprovider = \"unused\"\nmodel = \"unused\"\n\n"
        "[routes.extractor]\nprovider = \"unused\"\nmodel = \"unused\"\n",
        encoding="utf-8",
    )

    core.create_workspace("demo", "Agent 章节恢复")
    paths = get_workspace("demo")
    packet_path = paths.chapter_file(1, "packet")
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text("# 第 1 章生产包\n\n只用于恢复测试。\n", encoding="utf-8")

    ctx = FakeContext()
    first = asyncio.run(server.novel_run_chapter("demo", 1, ctx=ctx))
    writer_task = first["agent_task"]
    assert writer_task["route"] == "writer"

    second = asyncio.run(
        server.novel_submit_agent_task(
            writer_task["task_id"],
            "陈默在宿舍里听见手机响了一声。",
            ctx=ctx,
        )
    )
    reviewer_task = second["agent_task"]
    assert reviewer_task["route"] == "reviewer"
    assert paths.chapter_file(1, "draft").exists()
    assert paths.chapter_file(1, "run").exists()

    third = asyncio.run(
        server.novel_submit_agent_task(
            reviewer_task["task_id"],
            json.dumps({
                "approved": True,
                "score": 95,
                "abnormal_events": [],
                "orientation_audit": {
                    "task_origin": "上一章任务延续",
                    "task_object": "当前待处理事件",
                    "why_now": "行动正在连续发生",
                    "stakes": "停止会导致任务失败",
                    "method_reason": "当前行动承接既定方案",
                    "established_before_consequential_action": True,
                    "sufficient": True,
                    "location": "开篇衔接",
                    "severity": "low",
                    "fix": "",
                },
                "narrative_timing_audit": {
                    "sequence_valid": True,
                    "premature_references": [],
                    "summary": "未发现提前引用",
                },
                **_semantic_audits(),
                "writing_quality": {
                    "score": 95,
                    "summary": "通过",
                    "gates": [{
                        "gate_id": rule_id,
                        "passed": True,
                        "severity": "low",
                        "evidence": ["陈默在宿舍里听见手机响了一声。"],
                        "reason": "正文形成具体事件并保持因果连续。",
                        "repair_instruction": "",
                        "analysis": {
                            "character": "陈默", "goal": "确认响声", "obstacle": "来源未知", "action": "听见并留意",
                            "consequence": "确认手机有动静", "start_state": "安静", "end_state": "手机响起", "changed_dimension": "knowledge",
                            "cause": "手机响起", "motivation": "判断异常", "next_pressure": "确认来源", "current_goal": "确认响声",
                            "motive": "规避风险", "timing_reason": "响声刚发生", "action_basis": "可听见响声",
                            "chapter_change": "出现异常", "downstream_dependency": "后续调查", "paragraph": "正文段落",
                            "attempted_function": "推进事件", "why_no_function": "不适用", "repeated_information": "无",
                            "prior_source": "无", "missing_or_present_novelty": "新响声", "emotion_claim": "警觉",
                            "observable_evidence": "听见响声", "event_density": "一个事件", "function_density": "推进",
                            "static_span": "无", "distribution": "setup", "active_goal": "确认异常", "state_change": "knowledge",
                            "ending_pressure": "继续确认"
                        },
                    } for rule_id in [
                        "SCENE-001", "SCENE-002", "CAUSALITY-001", "CHARACTER-001",
                        "CHAPTER-001", "ANTI-FLUFF-001", "NOVELTY-001", "WEB-001",
                    ]],
                },
                "issues": [],
                "summary": "通过",
            }, ensure_ascii=False),
            ctx=ctx,
        )
    )
    extractor_task = third["agent_task"]
    assert extractor_task["route"] == "extractor"
    assert extractor_task["purpose"] == "chapter.extractor"



def test_validate_agent_submission_size_and_json_rules() -> None:
    from novel_production_mcp.agent_mode import (
        DEFAULT_MAX_CONTENT_SIZE,
        WRITER_MAX_CONTENT_SIZE,
        max_content_size_for,
        validate_agent_submission,
        _response_format,
    )

    assert _response_format("writer", "chapter.writer") == "plain_text"
    assert _response_format("repairer", "chapter.repair") == "plain_text"
    assert _response_format("reviewer", "chapter.reviewer") == "json_object"
    assert _response_format("extractor", "chapter.extractor") == "json_object"
    assert max_content_size_for("reviewer") == DEFAULT_MAX_CONTENT_SIZE
    assert max_content_size_for("writer") == WRITER_MAX_CONTENT_SIZE

    normalized = validate_agent_submission(
        '{\n  "approved": true,\n  "score": 90\n}',
        route="reviewer",
        purpose="chapter.reviewer",
    )
    assert json.loads(normalized) == {"approved": True, "score": 90}
    assert "True" not in normalized  # JSON true, not Python True

    with pytest.raises(ValueError, match="invalid"):
        validate_agent_submission("{not-json", route="reviewer", purpose="chapter.reviewer")
    with pytest.raises(ValueError, match="empty"):
        validate_agent_submission("   ", route="writer", purpose="chapter.writer")
    oversized = "x" * (DEFAULT_MAX_CONTENT_SIZE + 1)
    with pytest.raises(ValueError, match="limit is"):
        validate_agent_submission(oversized, route="reviewer", purpose="chapter.reviewer")
