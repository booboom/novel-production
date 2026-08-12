from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_environment.py"
    spec = importlib.util.spec_from_file_location("novel_env_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_environment_check_detects_missing_config():
    module = load_module()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config"
        novels = Path(tmp) / "novels"
        report = module.inspect_runtime(root, config, novels)
        assert report["ready"] is False
        failed = {entry["name"] for entry in report["checks"] if not entry["ok"]}
        assert "providers.toml" in failed
        assert "routes.toml" in failed


def test_marker_round_trip():
    module = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "marker.json"
        module.write_marker(path, "abc")
        marker = module.read_marker(path)
        assert marker is not None
        assert marker["skill_version"] == module.SKILL_VERSION
        assert marker["fingerprint"] == "abc"
        assert marker["status"] == "ready"


def test_environment_check_rejects_unknown_provider_api_mode(monkeypatch):
    module = load_module()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "config"
        novels = base / "novels"
        config.mkdir()
        novels.mkdir()
        (config / "providers.toml").write_text(
            '[providers.nexaport]\nbase_url = "https://api.nexaportai.com/v1"\napi_key_env = "NEXAPORT_API_KEY"\nallow_empty_key = false\napi_mode = "bad"\n',
            encoding="utf-8",
        )
        routes = []
        for name in module.REQUIRED_ROUTES:
            routes.append(f'[routes.{name}]\nprovider = "nexaport"\nmodel = "demo"\n')
        (config / "routes.toml").write_text("\n".join(routes), encoding="utf-8")
        monkeypatch.setenv("NEXAPORT_API_KEY", "secret")
        report = module.inspect_runtime(root, config, novels)
        provider_check = next(entry for entry in report["checks"] if entry["name"] == "供应商地址")
        assert provider_check["ok"] is False
        assert "api_mode" in provider_check["detail"]
