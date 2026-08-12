from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Mapping

from .models import WorkspacePaths
from .storage import config_dir, read_yaml


DEFAULT_WRITING_QUALITY: dict[str, Any] = {
    "enabled": True,
    "profile": "web_novel",
    "min_score": 85,
    "max_repair_rounds": 2,
    "block_high": True,
    "block_critical": True,
    "min_blocking_confidence": 0.75,
    "stop_on_unresolved_critical": True,
    "stop_on_unresolved_high": False,
    "allow_commit_with_warnings": True,
    "anti_fluff": True,
    "causality": True,
    "dialogue": True,
    "information_novelty": True,
    "max_rules_per_chapter": 30,
    "max_rules_per_scene": 12,
    "max_rule_tokens": 6000,
}

DEFAULT_CHARACTER_SIMULATION: dict[str, Any] = {
    "enabled": True,
    "theory_of_mind": True,
    "max_simulated_characters_per_scene": 4,
}

DEFAULT_SCENE_GENERATION: dict[str, Any] = {
    "enabled": False,
    "scene_level": False,
}

DEFAULT_WRITING_EXEMPLARS: dict[str, Any] = {
    "enabled": True,
    "max_exemplars_per_scene": 2,
    "max_exemplar_tokens": 1800,
}

DEFAULT_ARC_REFLECTION: dict[str, Any] = {
    "enabled": True,
}


def _toml_writing_quality(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    value = data.get("writing_quality", {}) if isinstance(data, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def writing_quality_config(paths: WorkspacePaths) -> dict[str, Any]:
    """Merge defaults, project baseline, global TOML, then workspace TOML."""
    config = dict(DEFAULT_WRITING_QUALITY)
    project = read_yaml(paths.project, {})
    project_config = project.get("writing_quality", {}) if isinstance(project, Mapping) else {}
    if isinstance(project_config, Mapping):
        config.update(project_config)
    global_config = _toml_writing_quality(config_dir() / "routes.toml")
    workspace_config = _toml_writing_quality(paths.routes)
    config.update(global_config)
    config.update(workspace_config)
    config["enabled"] = bool(config.get("enabled", True))
    config["profile"] = str(config.get("profile", "web_novel") or "").strip()
    config["min_score"] = max(0, min(100, int(config.get("min_score", 85) or 85)))
    config["max_repair_rounds"] = max(0, int(config.get("max_repair_rounds", 2) or 0))
    config["min_blocking_confidence"] = max(
        0.0,
        min(1.0, float(config.get("min_blocking_confidence", 0.75) or 0.0)),
    )
    for key in (
        "block_high",
        "block_critical",
        "stop_on_unresolved_critical",
        "stop_on_unresolved_high",
        "allow_commit_with_warnings",
    ):
        config[key] = bool(config.get(key, DEFAULT_WRITING_QUALITY[key]))
    explicit_limit = next(
        (value.get("max_rules_per_chapter") for value in (workspace_config, global_config) if "max_rules_per_chapter" in value),
        None,
    )
    legacy_limit = config.get("max_writer_rules")
    configured_limit = explicit_limit if explicit_limit is not None else legacy_limit if legacy_limit is not None else config.get("max_rules_per_chapter", 30)
    config["max_rules_per_chapter"] = max(1, min(100, int(configured_limit or 30)))
    config["max_rules_per_scene"] = max(1, min(50, int(config.get("max_rules_per_scene", 12) or 12)))
    config["max_rule_tokens"] = max(500, int(config.get("max_rule_tokens", 6000) or 6000))
    return config


def writing_profile_name(paths: WorkspacePaths) -> str:
    config = writing_quality_config(paths)
    return config["profile"] if config["enabled"] else ""


def _section_config(paths: WorkspacePaths, section: str, defaults: Mapping[str, Any]) -> dict[str, Any]:
    config = dict(defaults)
    project = read_yaml(paths.project, {})
    project_config = project.get(section, {}) if isinstance(project, Mapping) else {}
    if isinstance(project_config, Mapping):
        config.update(project_config)

    def _load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        value = data.get(section, {}) if isinstance(data, Mapping) else {}
        return dict(value) if isinstance(value, Mapping) else {}

    config.update(_load(config_dir() / "routes.toml"))
    config.update(_load(paths.routes))
    return config


def character_simulation_config(paths: WorkspacePaths) -> dict[str, Any]:
    config = _section_config(paths, "character_simulation", DEFAULT_CHARACTER_SIMULATION)
    config["enabled"] = bool(config.get("enabled", True))
    config["theory_of_mind"] = bool(config.get("theory_of_mind", True))
    config["max_simulated_characters_per_scene"] = max(
        0,
        min(12, int(config.get("max_simulated_characters_per_scene", 4) or 4)),
    )
    return config


def scene_generation_config(paths: WorkspacePaths) -> dict[str, Any]:
    config = _section_config(paths, "scene_generation", DEFAULT_SCENE_GENERATION)
    config["enabled"] = bool(config.get("enabled", False))
    config["scene_level"] = bool(config.get("scene_level", config["enabled"]))
    return config


def writing_exemplars_config(paths: WorkspacePaths) -> dict[str, Any]:
    config = _section_config(paths, "writing_exemplars", DEFAULT_WRITING_EXEMPLARS)
    config["enabled"] = bool(config.get("enabled", True))
    config["max_exemplars_per_scene"] = max(0, min(5, int(config.get("max_exemplars_per_scene", 2) or 2)))
    config["max_exemplar_tokens"] = max(200, int(config.get("max_exemplar_tokens", 1800) or 1800))
    return config


def arc_reflection_config(paths: WorkspacePaths) -> dict[str, Any]:
    config = _section_config(paths, "arc_reflection", DEFAULT_ARC_REFLECTION)
    config["enabled"] = bool(config.get("enabled", True))
    return config
