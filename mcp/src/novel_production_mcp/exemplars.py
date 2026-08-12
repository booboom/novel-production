from __future__ import annotations

"""Copyright-conscious Exemplar Cards and bounded selection."""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass
class ExemplarCard:
    id: str
    name: str
    scene_types: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    techniques: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    demonstrates: list[str] = field(default_factory=list)
    anti_patterns_avoided: list[str] = field(default_factory=list)
    abstracted_pattern: str = ""
    why_it_works: list[str] = field(default_factory=list)
    when_to_use: list[str] = field(default_factory=list)
    when_not_to_use: list[str] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    short_excerpt: str = ""
    quality_score: float = 0.0
    enabled: bool = True
    layer: str = "reference"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExemplarCard":
        item = dict(value)
        exemplar_id = str(item.get("id", "")).strip()
        if not exemplar_id or not exemplar_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Exemplar id must be stable ASCII letters/digits/hyphen/underscore")
        pattern = str(item.get("abstracted_pattern", "")).strip()
        if not pattern:
            raise ValueError(f"Exemplar {exemplar_id} requires abstracted_pattern")
        refs = item.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"Exemplar {exemplar_id} requires source_refs")
        try:
            score = max(0.0, min(1.0, float(item.get("quality_score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        return cls(
            id=exemplar_id,
            name=str(item.get("name", exemplar_id)),
            scene_types=_tags(item.get("scene_types")),
            domains=_tags(item.get("domains")),
            techniques=_tags(item.get("techniques")),
            genres=_tags(item.get("genres")),
            demonstrates=_ids(item.get("demonstrates")),
            anti_patterns_avoided=_tags(item.get("anti_patterns_avoided")),
            abstracted_pattern=pattern,
            why_it_works=_strings(item.get("why_it_works")),
            when_to_use=_tags(item.get("when_to_use")),
            when_not_to_use=_tags(item.get("when_not_to_use")),
            source_refs=[dict(ref) for ref in refs if isinstance(ref, Mapping)],
            short_excerpt=_short_excerpt(item.get("short_excerpt", "")),
            quality_score=score,
            enabled=bool(item.get("enabled", True)),
            layer=str(item.get("layer", "reference") or "reference"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scene_types": list(self.scene_types),
            "domains": list(self.domains),
            "techniques": list(self.techniques),
            "genres": list(self.genres),
            "demonstrates": list(self.demonstrates),
            "anti_patterns_avoided": list(self.anti_patterns_avoided),
            "abstracted_pattern": self.abstracted_pattern,
            "why_it_works": list(self.why_it_works),
            "when_to_use": list(self.when_to_use),
            "when_not_to_use": list(self.when_not_to_use),
            "source_refs": [dict(ref) for ref in self.source_refs],
            "short_excerpt": self.short_excerpt,
            "quality_score": self.quality_score,
            "enabled": self.enabled,
            "layer": self.layer,
        }


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _tags(value: Any) -> list[str]:
    return [item.lower() for item in _strings(value)]


def _ids(value: Any) -> list[str]:
    # Rule IDs keep original casing for stable cross-links.
    return _strings(value)


def _short_excerpt(value: Any) -> str:
    # Excerpts are optional evidence, never a substitute for abstraction.
    return str(value or "").strip()[:280]


def exemplar_signature(card: ExemplarCard | Mapping[str, Any]) -> str:
    value = card.to_dict() if isinstance(card, ExemplarCard) else card
    fields = [
        value.get("abstracted_pattern", ""),
        *(_tags(value.get("techniques"))),
        *(_tags(value.get("scene_types"))),
    ]
    return "|".join(str(item) for item in fields).lower()


def deduplicate_exemplars(values: Iterable[Mapping[str, Any] | ExemplarCard]) -> list[dict[str, Any]]:
    result: list[ExemplarCard] = []
    by_signature: dict[str, ExemplarCard] = {}
    for raw in values:
        card = raw if isinstance(raw, ExemplarCard) else ExemplarCard.from_mapping(raw)
        key = exemplar_signature(card)
        existing = by_signature.get(key)
        if existing is None:
            by_signature[key] = card
            result.append(card)
            continue
        known = {
            (str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page")), str(ref.get("chapter")))
            for ref in existing.source_refs
        }
        for ref in card.source_refs:
            ref_key = (str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page")), str(ref.get("chapter")))
            if ref_key not in known:
                existing.source_refs.append(ref)
        existing.demonstrates = list(dict.fromkeys([*existing.demonstrates, *card.demonstrates]))
        existing.techniques = list(dict.fromkeys([*existing.techniques, *card.techniques]))
        existing.scene_types = list(dict.fromkeys([*existing.scene_types, *card.scene_types]))
        existing.quality_score = max(existing.quality_score, card.quality_score)
    return [card.to_dict() for card in result]


def select_exemplars(
    scene: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
    exemplars: Iterable[Mapping[str, Any]],
    *,
    limit: int = 2,
    token_budget: int = 1800,
) -> list[dict[str, Any]]:
    scene_types = set(
        _tags(scene.get("scene_types") or ([scene.get("scene_type")] if scene.get("scene_type") else []))
    )
    techniques = set(_tags(scene.get("techniques")))
    genres = set(_tags(scene.get("genres") or ([scene.get("genre")] if scene.get("genre") else [])))
    rule_ids = {str(rule.get("id")) for rule in rules if str(rule.get("id", "")).strip()}
    candidates: list[tuple[float, ExemplarCard]] = []
    for raw in exemplars:
        card = raw if isinstance(raw, ExemplarCard) else ExemplarCard.from_mapping(raw)
        if not card.enabled:
            continue
        if card.when_not_to_use and scene_types.intersection(card.when_not_to_use):
            continue
        score = card.quality_score
        score += 4 * len(scene_types.intersection(card.scene_types))
        score += 3 * len(rule_ids.intersection(card.demonstrates))
        score += 2 * len(techniques.intersection(card.techniques))
        score += 1 * len(genres.intersection(card.genres))
        if card.layer == "project":
            score += 5
        # Require at least one relevance signal beyond raw quality alone.
        if score > card.quality_score:
            candidates.append((score, card))
    selected: list[dict[str, Any]] = []
    used = 0
    for _, card in sorted(candidates, key=lambda pair: (-pair[0], -pair[1].quality_score, pair[1].id)):
        cost = max(1, len(card.abstracted_pattern) // 4)
        if selected and used + cost > token_budget:
            continue
        selected.append(card.to_dict())
        used += cost
        if len(selected) >= max(0, limit):
            break
    return selected


def build_exemplar_index(exemplars: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, list[str]]]:
    index: dict[str, dict[str, list[str]]] = {
        "by_rule": {},
        "by_scene_type": {},
        "by_technique": {},
        "by_genre": {},
        "by_source": {},
    }
    for raw in exemplars:
        card = raw if isinstance(raw, ExemplarCard) else ExemplarCard.from_mapping(raw)
        for key, values in (
            ("by_rule", card.demonstrates),
            ("by_scene_type", card.scene_types),
            ("by_technique", card.techniques),
            ("by_genre", card.genres),
        ):
            for value in values:
                bucket = index[key].setdefault(value, [])
                if card.id not in bucket:
                    bucket.append(card.id)
        for ref in card.source_refs:
            source = str(ref.get("reference_id", "")).strip()
            if source:
                bucket = index["by_source"].setdefault(source, [])
                if card.id not in bucket:
                    bucket.append(card.id)
    return index


def writer_safe_exemplars(exemplars: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Strip long excerpts and keep only method-facing fields for Writer packets."""
    safe: list[dict[str, Any]] = []
    for raw in exemplars:
        card = raw if isinstance(raw, ExemplarCard) else ExemplarCard.from_mapping(raw)
        safe.append(
            {
                "id": card.id,
                "name": card.name,
                "scene_types": list(card.scene_types),
                "techniques": list(card.techniques),
                "demonstrates": list(card.demonstrates),
                "abstracted_pattern": card.abstracted_pattern,
                "why_it_works": list(card.why_it_works),
                "when_to_use": list(card.when_to_use),
                "when_not_to_use": list(card.when_not_to_use),
            }
        )
    return safe
