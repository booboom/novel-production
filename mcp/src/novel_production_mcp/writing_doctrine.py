from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .models import WorkspacePaths
from .storage import config_dir, read_json, read_yaml, utc_now, write_json, write_yaml
from .writing_profiles import writing_quality_config


RULE_FIELDS = (
    "id",
    "name",
    "domain",
    "description",
    "stage",
    "check_type",
    "severity",
    "blocking",
    "criteria",
    "evidence_required",
    "repair_instruction",
    "applies_when",
    "source_refs",
    "enabled",
)
VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}
VALID_STAGES = {"writer", "reviewer", "repairer"}


def validate_rule_card(rule: Mapping[str, Any]) -> dict[str, Any]:
    missing = [field for field in RULE_FIELDS if field not in rule]
    if missing:
        raise ValueError(f"Writing rule is missing fields: {', '.join(missing)}")
    rule_id = str(rule.get("id", "")).strip()
    if not rule_id or not all(char.isalnum() or char in {"-", "_"} for char in rule_id):
        raise ValueError("Writing rule id must be stable ASCII letters/digits/hyphen/underscore")
    stages = rule.get("stage")
    if not isinstance(stages, list) or not stages or not set(map(str, stages)).issubset(VALID_STAGES):
        raise ValueError(f"Writing rule {rule_id} has invalid stage")
    severity = str(rule.get("severity", ""))
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Writing rule {rule_id} has invalid severity")
    if str(rule.get("check_type", "")) not in {"semantic", "deterministic"}:
        raise ValueError(f"Writing rule {rule_id} has invalid check_type")
    if not isinstance(rule.get("criteria"), list) or not rule["criteria"]:
        raise ValueError(f"Writing rule {rule_id} requires criteria")
    if not isinstance(rule.get("evidence_required"), list):
        raise ValueError(f"Writing rule {rule_id} requires evidence_required list")
    if not isinstance(rule.get("applies_when"), Mapping):
        raise ValueError(f"Writing rule {rule_id} requires applies_when mapping")
    if not isinstance(rule.get("source_refs"), list) or not rule.get("source_refs"):
        raise ValueError(f"Writing rule {rule_id} requires non-empty source_refs list")
    normalized = dict(rule)
    normalized["id"] = rule_id
    normalized["stage"] = [str(value) for value in stages]
    normalized["severity"] = severity
    normalized["blocking"] = bool(rule.get("blocking"))
    normalized["enabled"] = bool(rule.get("enabled", True))
    normalized["criteria"] = [str(value).strip() for value in rule["criteria"] if str(value).strip()]
    normalized["evidence_required"] = [str(value).strip() for value in rule["evidence_required"] if str(value).strip()]
    normalized_refs: list[dict[str, Any]] = []
    for value in rule["source_refs"]:
        ref = dict(value) if isinstance(value, Mapping) else {"reference_id": str(value)}
        if not str(ref.get("reference_id", "")).strip() or not any(str(ref.get(key, "")).strip() for key in ("section", "page", "chapter")):
            raise ValueError(f"Writing rule {rule_id} source_refs require reference_id and section/page/chapter")
        normalized_refs.append(ref)
    normalized["source_refs"] = normalized_refs
    domain = str(normalized.get("domain", "")).strip()
    domains = normalized.get("domains", [])
    if not isinstance(domains, list):
        raise ValueError(f"Writing rule {rule_id} domains must be a list")
    normalized["domains"] = list(dict.fromkeys(
        value for value in [domain, *(str(item).strip().lower() for item in domains)] if value
    ))
    normalized["domain"] = normalized["domains"][0] if normalized["domains"] else domain
    applies_when = normalized.get("applies_when", {})
    for field, fallback in (
        ("scene_types", applies_when.get("scene_types", [])),
        ("techniques", []),
        ("genres", applies_when.get("genres", [])),
    ):
        values = normalized.get(field, fallback)
        if not isinstance(values, list):
            raise ValueError(f"Writing rule {rule_id} {field} must be a list")
        normalized[field] = list(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))
    normalized["failure_patterns"] = [
        str(item).strip() for item in normalized.get("failure_patterns", []) if str(item).strip()
    ]
    normalized["writer_instruction"] = str(normalized.get("writer_instruction", normalized.get("description", ""))).strip()
    normalized["review_instruction"] = str(normalized.get("review_instruction", "")).strip()
    return normalized


def writing_doctrine_root() -> Path:
    root = config_dir() / "writing-doctrine"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_global_doctrine() -> Path:
    root = writing_doctrine_root()
    for relative in ("core", "scenes", "techniques", "genre", "references", "proposals"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    if not (root / "index.yaml").exists():
        write_yaml(root / "index.yaml", _empty_rule_index())
    return root


def _empty_rule_index() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "by_rule_id": {},
        "by_domain": {},
        "by_scene_type": {},
        "by_technique": {},
        "by_genre": {},
        "by_stage": {},
        "by_severity": {},
        "by_source": {},
    }


def _index_add(index: dict[str, Any], bucket: str, key: str, rule_id: str) -> None:
    if not key:
        return
    values = index[bucket].setdefault(key, [])
    if rule_id not in values:
        values.append(rule_id)


def _canonical_rule_path(root: Path, rule: Mapping[str, Any]) -> Path:
    rule_id = str(rule["id"])
    scenes = rule.get("scene_types", []) or []
    techniques = rule.get("techniques", []) or []
    genres = rule.get("genres", []) or []
    domains = rule.get("domains", []) or []
    if scenes:
        parent = root / "scenes" / str(scenes[0])
    elif techniques:
        parent = root / "techniques" / str(techniques[0])
    elif genres:
        parent = root / "genre" / str(genres[0])
    else:
        parent = root / "core" / str(domains[0] if domains else "other")
    return parent / f"{rule_id.lower()}.yaml"


def rebuild_writing_rule_index() -> dict[str, Any]:
    root = ensure_global_doctrine()
    index = _empty_rule_index()
    for path in sorted(root.rglob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = read_yaml(path, {})
        raw_rules = data.get("rules", []) if isinstance(data, Mapping) else []
        if isinstance(data, Mapping) and "id" in data:
            raw_rules = [data]
        for raw in raw_rules if isinstance(raw_rules, list) else []:
            if not isinstance(raw, Mapping):
                continue
            rule = validate_rule_card(raw)
            rule_id = rule["id"]
            index["by_rule_id"][rule_id] = str(path.relative_to(root))
            for key in rule["domains"]:
                _index_add(index, "by_domain", key, rule_id)
            for key in rule["scene_types"]:
                _index_add(index, "by_scene_type", key, rule_id)
            for key in rule["techniques"]:
                _index_add(index, "by_technique", key, rule_id)
            for key in rule["genres"]:
                _index_add(index, "by_genre", key, rule_id)
            for key in rule["stage"]:
                _index_add(index, "by_stage", key, rule_id)
            _index_add(index, "by_severity", rule["severity"], rule_id)
            for ref in rule["source_refs"]:
                _index_add(index, "by_source", str(ref.get("reference_id", "")), rule_id)
    for bucket in index:
        if bucket.startswith("by_") and bucket != "by_rule_id":
            for key in index[bucket]:
                index[bucket][key].sort()
    write_yaml(root / "index.yaml", index)
    return index


def _reference_rules_from_index() -> list[dict[str, Any]]:
    root = ensure_global_doctrine()
    index = read_yaml(root / "index.yaml", {})
    card_files_exist = any(
        path.name != "index.yaml" for path in root.rglob("*.yaml")
    )
    if (
        not isinstance(index, Mapping)
        or not isinstance(index.get("by_rule_id"), Mapping)
        or (card_files_exist and not index.get("by_rule_id"))
    ):
        index = rebuild_writing_rule_index()
    rules: list[dict[str, Any]] = []
    missing = False
    for rule_id, relative in index.get("by_rule_id", {}).items():
        path = root / str(relative)
        if not path.exists():
            missing = True
            continue
        data = read_yaml(path, {})
        candidates = [data] if isinstance(data, Mapping) and "id" in data else data.get("rules", []) if isinstance(data, Mapping) else []
        for value in candidates if isinstance(candidates, list) else []:
            if isinstance(value, Mapping) and str(value.get("id")) == str(rule_id):
                rule = validate_rule_card(value)
                rule["layer"] = "reference"
                rules.append(rule)
                break
    if missing:
        rebuild_writing_rule_index()
        return _reference_rules_from_index()
    return rules


def apply_reference_rules(rules: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    root = ensure_global_doctrine()
    written: list[str] = []
    current_index = read_yaml(root / "index.yaml", _empty_rule_index())
    existing = _reference_rules_from_index()
    for raw in rules:
        rule = validate_rule_card(raw)
        signature = re.sub(r"\W+", "", rule["description"] + "".join(rule["criteria"])).lower()
        match = None
        for candidate in existing:
            other = re.sub(r"\W+", "", candidate["description"] + "".join(candidate["criteria"])).lower()
            if signature == other or SequenceMatcher(None, signature, other).ratio() >= 0.88:
                match = candidate
                break
        if match is not None:
            known_refs = {
                (str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page")))
                for ref in match["source_refs"]
            }
            for ref in rule["source_refs"]:
                key = (str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page")))
                if key not in known_refs:
                    match["source_refs"].append(ref)
            for field in ("domains", "scene_types", "techniques", "genres"):
                match[field] = list(dict.fromkeys([*match.get(field, []), *rule.get(field, [])]))
            rule = match
        existing_path = (current_index.get("by_rule_id", {}) or {}).get(rule["id"]) if match is not None else None
        path = root / str(existing_path) if existing_path else _canonical_rule_path(root, rule)
        write_yaml(path, rule)
        written.append(str(path))
        if match is None:
            existing.append(rule)
    index = rebuild_writing_rule_index()
    return {"written": written, "rule_count": len(index["by_rule_id"]), "index": str(root / "index.yaml")}


def _asset_root() -> Any:
    configured = os.environ.get("NOVEL_WRITING_DOCTRINE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    development = Path(__file__).resolve().parents[3] / "assets" / "writing_doctrine"
    if development.exists():
        return development
    return files("novel_production_mcp").joinpath("writing_assets")


def _rules_from_yaml(path: Any) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("rules", []) if isinstance(data, Mapping) else []
    if not isinstance(entries, list):
        raise ValueError(f"Writing doctrine file must contain rules list: {path}")
    return [validate_rule_card(item) for item in entries if isinstance(item, Mapping)]


def ensure_writing_workspace(paths: WorkspacePaths) -> None:
    for relative in ("writing/references", "writing/proposals", "analysis/writing-quality"):
        (paths.root / relative).mkdir(parents=True, exist_ok=True)
    doctrine = paths.root / "writing" / "project-doctrine.yaml"
    overrides = paths.root / "writing" / "rule-overrides.yaml"
    if not doctrine.exists():
        write_yaml(doctrine, {"rules": []})
    if not overrides.exists():
        write_yaml(overrides, {"rules": {}})


def load_writing_rules(paths: WorkspacePaths) -> list[dict[str, Any]]:
    ensure_writing_workspace(paths)
    config = writing_quality_config(paths)
    root = _asset_root()
    layered: dict[str, dict[str, Any]] = {}
    for name in ("scene.yaml", "causality.yaml", "character.yaml", "dialogue.yaml", "prose.yaml", "pacing.yaml", "anti_fluff.yaml", "information_novelty.yaml"):
        for rule in _rules_from_yaml(root.joinpath("core", name)):
            rule["layer"] = "core"
            layered[rule["id"]] = rule
    profile = str(config.get("profile", "")).strip()
    if profile:
        for rule in _rules_from_yaml(root.joinpath("genre", f"{profile}.yaml")):
            rule["layer"] = "genre"
            layered[rule["id"]] = rule
    for rule in _reference_rules_from_index():
        layered[rule["id"]] = rule
    project_data = read_yaml(paths.root / "writing" / "project-doctrine.yaml", {"rules": []})
    project_rules = project_data.get("rules", []) if isinstance(project_data, Mapping) else []
    for item in project_rules if isinstance(project_rules, list) else []:
        if isinstance(item, Mapping):
            rule = validate_rule_card(item)
            rule["layer"] = "project"
            layered[rule["id"]] = rule
    override_data = read_yaml(paths.root / "writing" / "rule-overrides.yaml", {"rules": {}})
    overrides = override_data.get("rules", {}) if isinstance(override_data, Mapping) else {}
    if isinstance(overrides, Mapping):
        for rule_id, values in overrides.items():
            if rule_id in layered and isinstance(values, Mapping):
                if "enabled" in values:
                    layered[rule_id]["enabled"] = bool(values["enabled"])
    return list(layered.values())


def _scene_tags(outline: Mapping[str, Any]) -> set[str]:
    tags: set[str] = set()
    for key in ("scene_type", "chapter_type", "dominant_scene"):
        value = outline.get(key)
        if isinstance(value, str) and value.strip():
            tags.add(value.strip().lower())
    for key in ("scene_types", "tags"):
        values = outline.get(key)
        if isinstance(values, list):
            tags.update(str(value).strip().lower() for value in values if str(value).strip())
    if len(outline.get("participants", []) or []) >= 2:
        tags.add("dialogue")
    text = " ".join(str(outline.get(key, "")) for key in ("mission", "chapter_task", "mini_arc")).lower()
    keyword_tags = {
        "conflict": ("冲突", "追逐", "战斗", "对抗", "危机"),
        "negotiation": ("谈判", "交涉", "协商", "交易"),
        "investigation": ("调查", "核对", "追查", "线索", "证据"),
        "revelation": ("揭露", "真相", "发现", "确认"),
    }
    for tag, keywords in keyword_tags.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)
    return tags


def _metadata(outline: Mapping[str, Any], project_genre: str) -> dict[str, set[str]]:
    return {
        "scene_types": _scene_tags(outline),
        "techniques": {
            str(value).strip().lower() for value in outline.get("techniques", []) or [] if str(value).strip()
        },
        "genres": {
            str(value).strip().lower() for value in outline.get("genres", []) or [] if str(value).strip()
        } | ({project_genre.strip().lower()} if project_genre.strip() else set()),
    }


def _taxonomy_relevance(rule: Mapping[str, Any], metadata: Mapping[str, set[str]]) -> int:
    score = 0
    if set(rule.get("scene_types", []) or []).intersection(metadata["scene_types"]):
        score += 8
    if set(rule.get("techniques", []) or []).intersection(metadata["techniques"]):
        score += 5
    if set(rule.get("genres", []) or []).intersection(metadata["genres"]):
        score += 3
    return score


def _applies(rule: Mapping[str, Any], *, tags: set[str], genre: str, participant_count: int) -> bool:
    condition = rule.get("applies_when", {})
    if not isinstance(condition, Mapping) or not condition:
        return True
    scene_types = {str(value).lower() for value in condition.get("scene_types", []) or []}
    genres = {str(value).lower() for value in condition.get("genres", []) or []}
    if scene_types and not scene_types.intersection(tags):
        return False
    if genres and genre.lower() not in genres:
        return False
    if int(condition.get("min_participants", 0) or 0) > participant_count:
        return False
    return True


def select_writing_rules(
    paths: WorkspacePaths,
    chapter_number: int,
    outline: Mapping[str, Any],
    *,
    stage: str = "writer",
) -> list[dict[str, Any]]:
    config = writing_quality_config(paths)
    if not config.get("enabled"):
        return []
    project = read_yaml(paths.project, {})
    genre = str(project.get("genre", "")) if isinstance(project, Mapping) else ""
    metadata = _metadata(outline, genre)
    tags = metadata["scene_types"]
    previous_failures: set[str] = set()
    for number in range(max(1, chapter_number - 3), chapter_number):
        report = read_json(paths.root / "analysis" / "writing-quality" / f"chapter-{number:04d}.json", {})
        for item in report.get("failed_gates", []) if isinstance(report, Mapping) else []:
            if isinstance(item, Mapping):
                previous_failures.add(str(item.get("gate_id", "")))
    rules = [
        rule for rule in load_writing_rules(paths)
        if rule.get("enabled")
        and stage in rule.get("stage", [])
        and _applies(rule, tags=tags, genre=genre, participant_count=len(outline.get("participants", []) or []))
    ]
    rules = [
        rule for rule in rules
        if not (rule.get("scene_types") or rule.get("techniques") or rule.get("genres"))
        or _taxonomy_relevance(rule, metadata) > 0
    ]
    toggles = {
        "anti_fluff": "anti_fluff",
        "causality": "causality",
        "dialogue": "dialogue",
        "information_novelty": "information_novelty",
    }
    rules = [rule for rule in rules if not any(rule.get("domain") == domain and not config.get(key, True) for key, domain in toggles.items())]
    layer_rank = {"project": 0, "genre": 1, "reference": 2, "core": 3}
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    domain_rank = {
        "scene": 0,
        "causality": 1,
        "character": 2,
        "chapter_value": 3,
        "dialogue": 4,
        "conflict_resolution": 5,
        "anti_fluff": 6,
        "information_novelty": 7,
        "pacing": 8,
        "prose": 9,
    }
    rules.sort(key=lambda rule: (
        0 if rule.get("blocking") else 1,
        severity_rank.get(str(rule.get("severity")), 5),
        -_taxonomy_relevance(rule, metadata),
        0 if rule["id"] in previous_failures else 1,
        domain_rank.get(str(rule.get("domain")), 50),
        layer_rank.get(str(rule.get("layer")), 3),
        str(rule["id"]),
    ))
    limit = int(config.get("max_rules_per_chapter", 30))
    token_budget = int(config.get("max_rule_tokens", 6000))
    selected: list[dict[str, Any]] = []
    used = 0
    essential_ids = {"SCENE-001", "CAUSALITY-001", "CHARACTER-001", "ANTI-FLUFF-001"}
    for rule in rules:
        estimated = max(1, len(str(rule)) // 4)
        if selected and (len(selected) >= limit or used + estimated > token_budget) and rule["id"] not in essential_ids:
            continue
        selected.append(rule)
        used += estimated
    ensure_writing_workspace(paths)
    enriched = []
    for rule in selected:
        item = dict(rule)
        # Bind the same analysis_schema the reviewer/repairer must fill.
        from .writing_gates import analysis_schema_for_rule, required_analysis_fields

        item["analysis_schema"] = analysis_schema_for_rule(rule)
        item["required_analysis_fields"] = required_analysis_fields(rule)
        enriched.append(item)
    write_json(paths.root / "analysis" / "writing-quality" / f"chapter-{chapter_number:04d}-rules.json", {
        "chapter": chapter_number,
        "stage": stage,
        "generated_at": utc_now(),
        "rules": enriched,
    })
    return selected


def format_rule_cards(rules: Iterable[Mapping[str, Any]], *, stage: str = "reviewer") -> str:
    blocks: list[str] = []
    for rule in rules:
        criteria = [str(item).strip() for item in rule.get("criteria", []) if str(item).strip()]
        criteria_text = "\n".join(f"- {item}" for item in criteria) if criteria else "(无额外判断标准)"
        if stage == "writer":
            instruction = str(rule.get("writer_instruction", rule.get("description", ""))).strip()
            block = instruction
            if criteria:
                block += "\n" + "\n".join(f"- {item}" for item in criteria)
            if block.strip():
                blocks.append(block.strip())
            continue
        # Lazy import keeps doctrine free of circular import at module load for
        # non-format call sites; format always needs the universal five.
        from .writing_gates import analysis_schema_for_rule, required_analysis_fields

        analysis_keys = required_analysis_fields(rule)
        analysis_schema = analysis_schema_for_rule(rule)
        blocks.append(
            f"### {rule.get('id')} / {rule.get('name')}\n"
            f"领域：{', '.join(str(item) for item in (rule.get('domains') or [rule.get('domain')]) if item) or '通用'}；"
            f"场景：{', '.join(rule.get('scene_types', [])) or '通用'}；"
            f"技巧：{', '.join(rule.get('techniques', [])) or '通用'}；"
            f"严重度：{rule.get('severity')}；blocking：{str(bool(rule.get('blocking'))).lower()}\n"
            f"{rule.get('description')}\n"
            f"写作指令：{rule.get('writer_instruction', rule.get('description', ''))}\n"
            f"判断标准：\n{criteria_text}\n"
            f"analysis 必填字段：{', '.join(analysis_keys)}\n"
            f"analysis_schema：{json.dumps(analysis_schema, ensure_ascii=False)}\n"
            f"修复方向：{rule.get('repair_instruction')}"
        )
    if blocks:
        return "\n\n".join(blocks)
    if stage == "writer":
        return "（本章没有额外写作要求）"
    return "（Writing Quality 已关闭或本章没有适用规则）"


def set_rule_enabled(paths: WorkspacePaths, rule_id: str, enabled: bool) -> dict[str, Any]:
    ensure_writing_workspace(paths)
    known = {rule["id"] for rule in load_writing_rules(paths)}
    if rule_id not in known:
        raise ValueError(f"Unknown writing rule: {rule_id}")
    path = paths.root / "writing" / "rule-overrides.yaml"
    data = read_yaml(path, {"rules": {}})
    rules = data.setdefault("rules", {})
    rules[rule_id] = {"enabled": bool(enabled)}
    write_yaml(path, data)
    return {"rule_id": rule_id, "enabled": bool(enabled), "path": str(path)}
