from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.event_store import seed_existing_workspace
from novel_production_mcp.storage import get_workspace, write_yaml


WRITING_RULE_IDS = [
    "SCENE-001", "SCENE-002", "CAUSALITY-001", "CHARACTER-001",
    "CHAPTER-001", "ANTI-FLUFF-001", "NOVELTY-001", "WEB-001",
]


class PipelineHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        system = payload["messages"][0]["content"]
        if "独立小说质量审校器" in system:
            content = json.dumps({
                "approved": True,
                "score": 93,
                "abnormal_events": [],
                "orientation_audit": {
                    "task_origin": "主角收到异常消息",
                    "task_object": "异常手机",
                    "why_now": "消息刚刚出现",
                    "stakes": "不处理会错过来源",
                    "method_reason": "检查手机可以确认消息记录",
                    "established_before_consequential_action": True,
                    "sufficient": True,
                    "location": "开篇",
                    "severity": "low",
                    "fix": "",
                },
                "narrative_timing_audit": {
                    "sequence_valid": True,
                    "premature_references": [],
                    "summary": "未发现提前引用",
                },
                "knowledge_provenance_audit": {"complete": True, "claims": [], "summary": "无越界知识"},
                "authorization_audit": {"complete": True, "actions": [], "summary": "无受限操作"},
                "outline_fidelity_audit": {"matches": True, "mismatches": [], "summary": "符合大纲"},
                "reasoning_chain_audit": {"valid": True, "chains": [], "summary": "推理成立"},
                "state_coverage_audit": {
                    "complete": True,
                    "changes": [{"change_id": "change_ch001_timeline", "requires_delta": True}],
                    "summary": "时间线变化需入账",
                },
                "repetition_audit": {"distinct": True, "repetitions": [], "summary": "无重复"},
                "planning_language_audit": {"naturalized": True, "occurrences": [], "summary": "规划术语已自然化"},
                "writing_quality": {
                    "score": 93,
                    "summary": "所有适用写作门禁均有正文证据",
                    "gates": [{
                        "gate_id": rule_id,
                        "passed": True,
                        "severity": "low",
                        "evidence": ["陈默坐在宿舍里，异常手机忽然响了一声。"],
                        "reason": "测试正文承担本章任务并形成明确事件。",
                        "repair_instruction": "",
                        "analysis": {
                            "character": "char_001", "goal": "确认异常", "obstacle": "信息不足", "action": "检查手机",
                            "consequence": "异常被确认", "start_state": "未知", "end_state": "发现异常", "changed_dimension": "knowledge",
                            "cause": "手机响起", "motivation": "确认风险", "next_pressure": "追查来源", "current_goal": "确认异常",
                            "motive": "避免遗漏", "timing_reason": "消息刚出现", "action_basis": "可观察响声",
                            "chapter_change": "异常进入主线", "downstream_dependency": "后续追查", "paragraph": "正文段落",
                            "attempted_function": "推进事件", "why_no_function": "不适用", "repeated_information": "无",
                            "prior_source": "无", "missing_or_present_novelty": "新异常", "emotion_claim": "惊讶",
                            "observable_evidence": "手机响起", "event_density": "一个有效事件", "function_density": "推进主线",
                        "static_span": "无", "distribution": "setup", "active_goal": "确认异常", "state_change": "knowledge",
                            "ending_pressure": "追查来源"
                        },
                    } for rule_id in WRITING_RULE_IDS],
                },
                "issues": [],
                "summary": "通过",
            }, ensure_ascii=False)
        elif "状态增量提取器" in system:
            content = json.dumps({
                "chapter": 1,
                "character_updates": {},
                "utterances": [],
                "pov_observations": [],
                "relationship_updates": [],
                "timeline_events": [{
                    "id": "event_ch001_001",
                    "time": "2026-01-01T20:05:00+08:00",
                    "location_id": "loc_dorm",
                    "participants": ["char_001"],
                    "summary": "手机响起",
                    "fact_ids": [],
                }],
                "inventory_events": [],
                "ability_events": [],
                "plot_updates": [],
                "foreshadowing_updates": [],
                "state_coverage": {
                    "detected_changes": [{"change_id": "change_ch001_timeline"}],
                    "representations": [{
                        "change_id": "change_ch001_timeline",
                        "delta_section": "timeline_events",
                        "delta_id": "event_ch001_001",
                    }],
                    "untracked_changes": [],
                },
                "canon_change_requests": [],
            }, ensure_ascii=False)
        else:
            content = "陈默坐在宿舍里，异常手机忽然响了一声。"
        body = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def test_full_chapter_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), PipelineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = tmp_path / "novels"
        config = tmp_path / "config"
        root.mkdir()
        config.mkdir()
        monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(root))
        monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
        (config / "providers.toml").write_text(
            f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n',
            encoding="utf-8",
        )
        (config / "routes.toml").write_text(
            "\n".join(
                f'[routes.{name}]\nprovider = "local"\nmodel = "mock"\nretries = 0\n'
                for name in ("writer", "reviewer", "repairer", "extractor")
            ),
            encoding="utf-8",
        )
        core.create_workspace("demo", "测试小说")
        paths = get_workspace("demo")
        write_yaml(paths.root / "canon" / "locations.yaml", {
            "locations": [{"id": "loc_dorm", "name": "宿舍"}], "travel_minutes": {}
        })
        write_yaml(paths.root / "characters" / "index.yaml", {
            "characters": [{"id": "char_001", "name": "陈默", "role": "protagonist"}]
        })
        write_yaml(paths.root / "characters" / "profiles" / "char_001.yaml", {
            "id": "char_001", "name": "陈默", "abilities": {}
        })
        write_yaml(paths.root / "characters" / "arcs" / "char_001.yaml", {"character_id": "char_001"})
        write_yaml(paths.root / "state" / "characters" / "char_001.yaml", {
            "character_id": "char_001",
            "location": {"location_id": "loc_dorm", "time": "2026-01-01T20:00:00+08:00"},
        })
        write_yaml(paths.root / "characters" / "knowledge-boundaries.yaml", {
            "characters": {"char_001": {"knows": [], "suspects": [], "must_not_know": []}}
        })
        write_yaml(paths.chapter_file(1, "outline"), {
            "number": 1,
            "title": "手机响起",
            "mission": "制造异常",
            "participants": ["char_001"],
            "pov_character": "char_001",
            "pov_mode": "third_limited",
        })
        seed_existing_workspace(paths)
        result = core.run_chapter_pipeline("demo", 1, auto_commit=True)
        assert result["status"] == "committed", result
        assert paths.chapter_file(1, "chapter").exists()
        report = core.workspace_observability_report("demo")
        assert report["calls"] == 3
        assert report["tokens"] == 90
    finally:
        server.shutdown()
