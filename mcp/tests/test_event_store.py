from pathlib import Path

import pytest

from novel_production_mcp import core
from novel_production_mcp.event_store import commit_projection_event, projection_status, rebuild_projections
from novel_production_mcp.storage import get_workspace


def test_projection_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(tmp_path / "novels"))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(tmp_path / "config"))
    core.create_workspace("demo", "测试")
    paths = get_workspace("demo")
    commit_projection_event(paths, event_type="test", payload={}, projections={"state/test.txt": "canonical\n"})
    target = paths.root / "state" / "test.txt"
    target.write_text("drift\n", encoding="utf-8")
    assert projection_status(paths)["ok"] is False
    result = rebuild_projections(paths, "state/test")
    assert result["count"] == 1
    assert target.read_text(encoding="utf-8") == "canonical\n"


def test_adopt_manual_file_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_WORKSPACE_ROOT", str(tmp_path / "novels"))
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(tmp_path / "config"))
    core.create_workspace("demo", "测试")
    paths = get_workspace("demo")
    premise = paths.root / "canon" / "premise.md"
    premise.write_text("# 测试\n\n人工修改\n", encoding="utf-8")
    assert projection_status(paths)["ok"] is False
    result = core.adopt_file_changes("demo", ["canon/premise.md"], "用户确认修改")
    assert result["adopted"] == ["canon/premise.md"]
    assert projection_status(paths)["ok"] is True
