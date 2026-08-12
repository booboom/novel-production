from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.models import ModelResult
from novel_production_mcp.observability import observability_report, record_llm_call
from novel_production_mcp.storage import get_workspace


def test_observability_aggregate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(tmp_path / "novels"))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(tmp_path / "config"))
    core.create_workspace("demo", "测试")
    paths = get_workspace("demo")
    result = ModelResult(
        content="ok", provider="local", model="demo", route="writer", latency_ms=120,
        raw_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        input_hash="in", output_hash="out", attempt_count=1, estimated_cost_usd=0.001,
    )
    record_llm_call(paths, purpose="chapter.writer", chapter=1, result=result, success=True)
    report = observability_report(paths)
    assert report["calls"] == 1
    assert report["tokens"] == 15
    assert report["estimated_cost_usd"] == 0.001
    assert report["by_model"][0]["model"] == "demo"
