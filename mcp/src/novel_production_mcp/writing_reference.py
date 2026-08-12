from __future__ import annotations

import hashlib
import importlib
import json
import re
import shutil
import zipfile
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from .models import WorkspacePaths
from .storage import read_json, sha256_file, utc_now, write_json
from .writing_doctrine import ensure_writing_workspace, validate_rule_card, writing_doctrine_root


def _exemplar_module() -> Any:
    return importlib.import_module("novel_production_mcp.exemplars")


SUPPORTED_SUFFIXES = {".pdf", ".epub", ".txt", ".md", ".markdown"}


def scan_reference_library(paths: WorkspacePaths, source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise ValueError("Writing reference path does not exist")
    candidates = [source] if source.is_file() else sorted(
        path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    registry_path = writing_doctrine_root() / "references" / "manifest.json"
    registry = cast(dict[str, Any], read_json(registry_path, {"schema_version": 1, "references": {}}))
    references = cast(dict[str, Any], registry.setdefault("references", {}))
    current_paths = {str(path): sha256_file(path) for path in candidates}
    result: dict[str, Any] = {"new": [], "modified": [], "unchanged": [], "deleted": []}
    for path in candidates:
        key = str(path)
        digest = current_paths[key]
        previous = references.get(key, {}) if isinstance(references, Mapping) else {}
        if previous and previous.get("source_sha256") == digest:
            result["unchanged"].append(previous.get("reference_id"))
            continue
        imported = import_writing_reference(paths, key, path.stem)
        global_reference = writing_doctrine_root() / "references" / imported["reference_id"]
        shutil.copytree(Path(imported["path"]), global_reference, dirs_exist_ok=True)
        status = "modified" if previous else "new"
        result[status].append(imported["reference_id"])
        references[key] = {
            "reference_id": imported["reference_id"],
            "source_sha256": digest,
            "source_path": key,
            "updated_at": utc_now(),
        }
    if source.is_dir():
        prefix = str(source) + "/"
        for key, value in list(references.items()):
            if key.startswith(prefix) and key not in current_paths:
                result["deleted"].append(value.get("reference_id"))
                value["deleted_at"] = utc_now()
    registry["scanned_at"] = utc_now()
    write_json(registry_path, registry)
    result["scanned"] = len(candidates)
    result["registry"] = str(registry_path)
    return result


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "\n".join(parser.parts)


def _source_sections(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    sections: list[dict[str, Any]] = []
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("PDF writing references require pypdf; rerun install.sh --repair") from exc
        reader = PdfReader(str(path))
        for index, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append({"locator": f"page:{index}", "text": text})
    elif suffix == ".epub":
        with zipfile.ZipFile(path) as archive:
            names = sorted(name for name in archive.namelist() if name.lower().endswith((".xhtml", ".html", ".htm")))
            for name in names:
                text = _html_text(archive.read(name).decode("utf-8", errors="replace")).strip()
                if text:
                    sections.append({"locator": f"chapter:{name}", "text": text})
    else:
        text = path.read_text(encoding="utf-8-sig")
        headings = list(re.finditer(r"(?m)^#{1,6}\s+.+$", text)) if suffix in {".md", ".markdown"} else []
        if headings:
            for index, match in enumerate(headings):
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                block = text[match.start():end].strip()
                if block:
                    sections.append({"locator": f"section:{match.group(0).lstrip('#').strip()}", "text": block})
        elif text.strip():
            sections.append({"locator": "section:full-text", "text": text.strip()})
    chunks: list[dict[str, Any]] = []
    for section in sections:
        value = str(section["text"])
        for offset in range(0, len(value), 12000):
            chunk = value[offset:offset + 12000].strip()
            if chunk:
                chunks.append({
                    "section_id": f"section-{len(chunks) + 1:04d}",
                    "locator": section["locator"],
                    "text": chunk,
                    "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                })
    return chunks


def import_writing_reference(paths: WorkspacePaths, source_path: str, title: str = "") -> dict[str, Any]:
    ensure_writing_workspace(paths)
    source = Path(source_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Writing reference must be an existing PDF, EPUB, TXT, MD, or Markdown file")
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Writing reference exceeds 100 MiB")
    digest = sha256_file(source)
    reference_id = f"writing-ref-{digest[:12]}"
    root = paths.root / "writing" / "references" / reference_id
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"source{source.suffix.lower()}"
    if not target.exists():
        shutil.copy2(source, target)
        target.chmod(0o444)
    sections = _source_sections(target)
    if not sections:
        raise ValueError("No extractable text found in writing reference")
    manifest = {
        "reference_id": reference_id,
        "title": title.strip() or source.stem,
        "source_name": source.name,
        "source_sha256": digest,
        "format": source.suffix.lower().lstrip("."),
        "imported_at": utc_now(),
        "section_count": len(sections),
        "status": "imported",
    }
    write_json(root / "manifest.json", manifest)
    write_json(root / "sections.json", {"sections": sections})
    return {**manifest, "path": str(root)}


def reference_batches(paths: WorkspacePaths, reference_id: str, max_chars: int = 36000) -> list[list[dict[str, Any]]]:
    root = paths.root / "writing" / "references" / reference_id
    try:
        data = json.loads((root / "sections.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid writing reference sections: {reference_id}") from exc
    sections = data.get("sections", [])
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for item in sections:
        length = len(str(item.get("text", "")))
        if current and size + length > max_chars:
            batches.append(current)
            current = []
            size = 0
        current.append(item)
        size += length
    if current:
        batches.append(current)
    return batches


def analysis_prompt(reference_id: str, batch: list[Mapping[str, Any]], reference_type: str = "auto") -> str:
    payload = [{"section_id": item.get("section_id"), "locator": item.get("locator"), "text": item.get("text")} for item in batch]
    mode = reference_type if reference_type in {"writing_theory", "fiction", "case_collection"} else "auto-detect"
    return (
        "判断资料类型并提炼可迁移的写作方法。只输出 JSON，不写小说正文。"
        '{"reference_type":"writing_theory|fiction|case_collection","principles":[{"id":"ASCII-id","name":"","principle":"","method":"","decision_rule":"",'
        '"failure_patterns":[""],"checklist":[""],"domains":[""],"scene_types":[""],"techniques":[""],"genres":[""],"source_refs":[{"reference_id":"","section":"","source_hash":""}]}],'
        '"exemplars":[{"id":"ASCII-id","name":"","scene_types":[],"domains":[],"techniques":[],"genres":[],'
        '"demonstrates":[],"anti_patterns_avoided":[],"abstracted_pattern":"","why_it_works":[],"when_to_use":[],"when_not_to_use":[],"source_refs":[{"reference_id":"","section":""}],"short_excerpt":"","quality_score":0.0}]}。'
        "Writing Theory 优先提炼 principle/method/decision_rule/checklist；Fiction 优先提炼 scene/execution/dialogue/pacing/tension patterns；Case Collection 混合。"
        "Exemplar 必须解释结构为什么有效并关联 Rule/Technique/Scene Pattern；不得保存整章或连续长原文，short_excerpt 最多 280 字。"
        f"资料类型提示={mode}\nreference_id={reference_id}\n资料：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def doctrine_proposal_prompt(reference_id: str, analyses: Iterable[Mapping[str, Any]]) -> str:
    return (
        "把以下写作方法分析去重、归类并检查相互冲突，转换为 Writing Rule Cards。只输出 JSON："
        '{"reference_id":"","conflicts":[{"rule_ids":[""],"description":"","resolution":""}],'
        '"taxonomy_report":{},"rules":[{"id":"REF-001","name":"","domain":"scene","domains":["scene"],'
        '"scene_types":[],"techniques":[],"genres":[],"failure_patterns":[""],'
        '"description":"","stage":["writer","reviewer","repairer"],"check_type":"semantic","severity":"high",'
        '"blocking":true,"criteria":[""],"evidence_required":[""],"writer_instruction":"",'
        '"review_instruction":"","repair_instruction":"",'
        '"applies_when":{},"source_refs":[{"reference_id":"","section":""}],"enabled":true}]}。'
        "在提炼阶段完成多标签分类。critical 必须极少，仅用于章节无法成立的问题；high 不得默认 blocking。"
        "不得复制大段原文，不得提出关闭 Canon/Timeline/Knowledge 等硬门禁的规则。\n"
        f"reference_id={reference_id}\n分析：\n{json.dumps(list(analyses), ensure_ascii=False)}"
    )


def validate_doctrine_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    rules = value.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Doctrine proposal requires non-empty rules")
    normalized = dict(value)
    validated = [validate_rule_card(item) for item in rules if isinstance(item, Mapping)]
    deduplicated: list[dict[str, Any]] = []
    for rule in validated:
        signature = re.sub(r"\W+", "", str(rule.get("description", "") + "".join(rule.get("criteria", [])))).lower()
        match = None
        for existing in deduplicated:
            other = re.sub(r"\W+", "", str(existing.get("description", "") + "".join(existing.get("criteria", [])))).lower()
            if signature == other or SequenceMatcher(None, signature, other).ratio() >= 0.88:
                match = existing
                break
        if match is None:
            deduplicated.append(rule)
            continue
        known = {(str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page"))) for ref in match["source_refs"]}
        for ref in rule["source_refs"]:
            key = (str(ref.get("reference_id")), str(ref.get("section")), str(ref.get("page")))
            if key not in known:
                match["source_refs"].append(ref)
                known.add(key)
        for field in ("domains", "scene_types", "techniques", "genres"):
            match[field] = list(dict.fromkeys([*match.get(field, []), *rule.get(field, [])]))
    normalized["rules"] = deduplicated
    normalized["deduplication"] = {"input_rules": len(validated), "output_rules": len(deduplicated)}
    conflicts = value.get("conflicts", [])
    if not isinstance(conflicts, list):
        raise ValueError("Doctrine proposal conflicts must be a list")
    exemplars = value.get("exemplars", [])
    if not isinstance(exemplars, list):
        raise ValueError("Doctrine proposal exemplars must be a list")
    normalized["exemplars"] = _exemplar_module().deduplicate_exemplars(exemplars) if exemplars else []
    normalized["exemplar_index"] = _exemplar_module().build_exemplar_index(normalized["exemplars"])
    return normalized
