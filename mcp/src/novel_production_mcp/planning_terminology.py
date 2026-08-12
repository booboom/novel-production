from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .models import ValidationIssue


def _asset_path() -> Any:
    development = Path(__file__).resolve().parents[3] / "assets" / "planning-terminology.yaml"
    if development.exists():
        return development
    return files("novel_production_mcp").joinpath("planning-terminology.yaml")


def load_planning_terminology() -> dict[str, dict[str, Any]]:
    path = _asset_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms = data.get("terms", {}) if isinstance(data, Mapping) else {}
    if not isinstance(terms, Mapping):
        raise ValueError("Planning terminology must contain a terms mapping")
    normalized: dict[str, dict[str, Any]] = {}
    for key, raw in terms.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"Planning terminology entry {key} must be a mapping")
        aliases = raw.get("aliases", [])
        replacements = raw.get("writer_replacements", {})
        if not isinstance(aliases, list) or not aliases:
            raise ValueError(f"Planning terminology entry {key} requires aliases")
        if not isinstance(replacements, Mapping):
            raise ValueError(f"Planning terminology entry {key} requires writer_replacements")
        entry = dict(raw)
        entry["key"] = str(key)
        entry["aliases"] = [str(value).strip() for value in aliases if str(value).strip()]
        entry["writer_replacements"] = {
            str(alias).strip(): str(replacement).strip()
            for alias, replacement in replacements.items()
            if str(alias).strip()
        }
        entry["warning_occurrences"] = max(1, int(raw.get("warning_occurrences", 1) or 1))
        entry["blocking_occurrences"] = max(
            entry["warning_occurrences"],
            int(raw.get("blocking_occurrences", entry["warning_occurrences"]) or entry["warning_occurrences"]),
        )
        normalized[str(key)] = entry
    return normalized


def reader_visible_terms(
    outline: Mapping[str, Any] | None,
    *canon_sources: Any,
    require_established: bool = False,
) -> set[str]:
    values = outline.get("reader_visible_terms", []) if isinstance(outline, Mapping) else []
    if not isinstance(values, list):
        return set()
    declared = {str(value).strip() for value in values if str(value).strip()}
    if not require_established:
        return declared
    canon_text = "\n".join(
        text
        for source in canon_sources
        for _, text in _scalar_strings(source)
    )
    return {term for term in declared if term in canon_text}


def _scalar_strings(value: Any, path: str = "root") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _scalar_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _scalar_strings(item, f"{path}[{index}]")


def relevant_planning_terms(*sources: Any) -> list[dict[str, Any]]:
    catalog = load_planning_terminology()
    scalar_sources = [item for source in sources for item in _scalar_strings(source)]
    relevant: list[dict[str, Any]] = []
    for entry in catalog.values():
        matches: list[dict[str, str]] = []
        for path, text in scalar_sources:
            for alias in entry["aliases"]:
                if alias in text:
                    matches.append({"source": path, "alias": alias})
        if bool(entry.get("always_active")) or matches:
            value = dict(entry)
            value["matches"] = matches
            relevant.append(value)
    return relevant


def sanitize_writer_value(
    value: Any,
    *,
    allowed_terms: Iterable[str] = (),
) -> Any:
    allowed = {str(item).strip() for item in allowed_terms if str(item).strip()}
    catalog = load_planning_terminology()
    replacements: list[tuple[str, str]] = []
    for entry in catalog.values():
        mapping = entry.get("writer_replacements", {})
        for alias in entry.get("aliases", []):
            if alias in allowed:
                continue
            replacement = str(mapping.get(alias, "")).strip()
            replacements.append((alias, replacement))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)

    def sanitize(item: Any) -> Any:
        if isinstance(item, str):
            result = item
            for alias, replacement in replacements:
                result = result.replace(alias, replacement)
            return result
        if isinstance(item, Mapping):
            return {
                sanitize(key) if isinstance(key, str) else key: sanitize(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [sanitize(child) for child in item]
        if isinstance(item, tuple):
            return tuple(sanitize(child) for child in item)
        return item

    return sanitize(value)


def writer_stage_guidance(*sources: Any) -> list[str]:
    return list(dict.fromkeys(
        str(entry.get("writer_instruction", "")).strip()
        for entry in relevant_planning_terms(*sources)
        if str(entry.get("writer_instruction", "")).strip()
    ))


def reviewer_stage_context(
    *sources: Any,
    allowed_terms: Iterable[str] = (),
) -> dict[str, Any]:
    terms: list[dict[str, Any]] = []
    for entry in relevant_planning_terms(*sources):
        terms.append({
            "name": entry.get("name", ""),
            "aliases": list(entry.get("aliases", [])),
            "prose_policy": entry.get("prose_policy", "internal_only"),
            "warning_occurrences": entry.get("warning_occurrences", 1),
            "blocking_occurrences": entry.get("blocking_occurrences", 1),
            "matched_sources": list(entry.get("matches", [])),
            "reviewer_instruction": entry.get("reviewer_instruction", ""),
        })
    return {
        "reader_visible_terms": sorted({str(item).strip() for item in allowed_terms if str(item).strip()}),
        "terms": terms,
    }


def repairer_stage_context(
    *sources: Any,
    allowed_terms: Iterable[str] = (),
) -> dict[str, Any]:
    terms: list[dict[str, Any]] = []
    for entry in relevant_planning_terms(*sources):
        terms.append({
            "name": entry.get("name", ""),
            "aliases": list(entry.get("aliases", [])),
            "repair_instruction": entry.get("repair_instruction", ""),
        })
    return {
        "reader_visible_terms": sorted({str(item).strip() for item in allowed_terms if str(item).strip()}),
        "terms": terms,
    }


def _evidence_snippets(text: str, aliases: Iterable[str], limit: int = 5) -> list[str]:
    snippets: list[str] = []
    for alias in aliases:
        start = 0
        while len(snippets) < limit:
            index = text.find(alias, start)
            if index < 0:
                break
            left = max(0, index - 35)
            right = min(len(text), index + len(alias) + 35)
            snippets.append(text[left:right].replace("\n", " ").strip())
            start = index + len(alias)
    return snippets


def detect_planning_term_literalization(
    draft_text: str,
    *sources: Any,
    allowed_terms: Iterable[str] = (),
) -> list[ValidationIssue]:
    allowed = {str(item).strip() for item in allowed_terms if str(item).strip()}
    issues: list[ValidationIssue] = []
    for entry in relevant_planning_terms(*sources):
        counts = {
            alias: draft_text.count(alias)
            for alias in entry.get("aliases", [])
            if alias not in allowed and draft_text.count(alias) > 0
        }
        total = sum(counts.values())
        if not total:
            continue
        warning = int(entry.get("warning_occurrences", 1) or 1)
        blocking = int(entry.get("blocking_occurrences", warning) or warning)
        if total < warning:
            continue
        severity = "high" if total >= blocking else "medium"
        issues.append(ValidationIssue(
            "planning_term_literalization",
            f"Draft repeats planning terminology '{entry.get('name', entry.get('key'))}' {total} time(s)",
            severity,
            details={
                "term": entry.get("name", entry.get("key")),
                "occurrences": counts,
                "evidence": _evidence_snippets(draft_text, counts.keys()),
                "repair_instruction": entry.get("repair_instruction", ""),
            },
        ))
    return issues
