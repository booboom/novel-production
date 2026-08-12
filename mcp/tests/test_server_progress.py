from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field

import pytest

from novel_production_mcp import server


@dataclass
class FakeContext:
    infos: list[str] = field(default_factory=list)
    progress: list[tuple[float, float | None, str | None]] = field(default_factory=list)

    async def info(self, message: str, **_: object) -> None:
        self.infos.append(message)

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.progress.append((progress, total, message))


def test_mcp_progress_has_stage_and_periodic_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext()
    monkeypatch.setattr(server, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def operation(progress) -> dict[str, bool]:
        progress("阶段 2/3：正在等待模型返回", 40, 100)
        time.sleep(0.035)
        return {"ok": True}

    result = asyncio.run(
        server.run_with_progress(
            ctx,
            operation,
            start_message="开始",
            heartbeat_message="模型仍在运行",
        )
    )

    assert result == {"ok": True, "status": "completed"}
    assert any("阶段 2/3" in message for message in ctx.infos)
    assert any("心跳：模型仍在运行" in message for message in ctx.infos)
    assert ctx.progress[-1][0] == 100


def test_takeover_tool_passes_progress_callback_to_core(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext()

    def fake_generate(workspace_id: str, instruction: str, progress_callback=None) -> dict[str, str]:
        assert workspace_id == "demo"
        assert instruction == ""
        assert progress_callback is not None
        progress_callback("阶段 3/4：正在调用 Director 模型", 30, 100)
        return {"status": "ok"}

    monkeypatch.setattr(server.core, "generate_takeover_proposal", fake_generate)
    result = asyncio.run(server.novel_generate_takeover_proposal("demo", ctx=ctx))

    assert result == {"status": "ok"}
    assert any("Director" in message for message in ctx.infos)


def test_mcp_result_compacts_large_status_payloads() -> None:
    result = asyncio.run(
        server.run_with_progress(
            None,
            lambda _progress: {
                "workspace_id": "demo",
                "path": "/tmp/demo",
                "ending": {"unresolved_threads": [f"thread_{index}" for index in range(20)]},
                "preview": "x" * 1200,
            },
            start_message="开始",
            heartbeat_message="仍在运行",
        )
    )

    assert result["status"] == "completed"
    assert result["ending"]["unresolved_threads"]["count"] == 20
    assert result["ending"]["unresolved_threads"]["truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False)) < 1400


def test_all_mcp_tools_use_async_progress_boundary() -> None:
    tools = asyncio.run(server.mcp.list_tools())

    assert len(tools) == 70
    assert all(inspect.iscoroutinefunction(getattr(server, tool.name)) for tool in tools)
    assert all("ctx" not in tool.model_dump()["inputSchema"].get("properties", {}) for tool in tools)
