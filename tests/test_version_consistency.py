from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.11.2"


def _load_environment_checker_version() -> str:
    path = ROOT / "scripts" / "check_environment.py"
    spec = importlib.util.spec_from_file_location("novel_environment_check_version", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.SKILL_VERSION)


def test_release_version_is_consistent_across_distribution_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "mcp" / "pyproject.toml").read_text(encoding="utf-8"))
    package_init = (ROOT / "mcp" / "src" / "novel_production_mcp" / "__init__.py").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    _readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert re.search(rf'__version__\s*=\s*"{re.escape(EXPECTED_VERSION)}"', package_init)
    assert changelog.startswith(f"# Changelog\n\n## {EXPECTED_VERSION} - ")
    assert _readme.startswith("# Novel Production Skill + MCP\n")
    assert f"v{EXPECTED_VERSION}" not in _readme
    assert "CHANGELOG.md" in _readme
    assert f"# Novel Production v{EXPECTED_VERSION.rsplit('.', 1)[0]}\n" in skill
    assert _load_environment_checker_version() == EXPECTED_VERSION
