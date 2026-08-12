from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Iterable, Mapping

from .models import ValidationIssue, ValidationReport, WorkspacePaths
from .storage import read_json, utc_now, write_json
from .writing_profiles import writing_quality_config


# Universal analysis keys every gate must fill. Rule Card evidence_required is
# merged on top so causality/scene-change fields stay mandatory too.
CORE_ANALYSIS_FIELDS = ("character", "goal", "obstacle", "action", "consequence")


def required_analysis_fields(rule: Mapping[str, Any] | None = None) -> list[str]:
    """Return ordered analysis keys: universal five + rule evidence_required."""
    fields: list[str] = []
    seen: set[str] = set()
    for name in (*CORE_ANALYSIS_FIELDS, *list((rule or {}).get("evidence_required", []) or [])):
        key = str(name).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        fields.append(key)
    return fields


def analysis_schema_for_rule(rule: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Bound analysis_schema dict used in prompts and chapter-rules.json."""
    labels = {
        "character": "角色 ID 或名称",
        "goal": "当前即时目标",
        "obstacle": "阻力或未知",
        "action": "采取的行动",
        "consequence": "行动结果",
        "start_state": "场景开始状态",
        "end_state": "场景结束状态",
        "changed_dimension": "变化维度",
        "cause": "直接原因",
        "motivation": "人物动机",
        "next_pressure": "后续压力",
        "current_goal": "当前目标",
    }
    return {name: labels.get(name, name) for name in required_analysis_fields(rule)}


def writing_review_schema_hint(rules: Iterable[Mapping[str, Any]]) -> str:
    ids = [str(rule.get("id", "")) for rule in rules]
    sample_rule = next(iter(rules), {})
    analysis = analysis_schema_for_rule(sample_rule if isinstance(sample_rule, Mapping) else {})
    # Always show the universal five even when no rules selected.
    if not analysis:
        analysis = analysis_schema_for_rule({})
    return json.dumps({
        "writing_quality": {
            "score": 0,
            "summary": "基于正文证据的简短结论",
            "gates": [{
                "gate_id": "必须逐项覆盖本章适用规则之一：" + ", ".join(ids),
                "rule_id": "与 gate_id 完全相同",
                "passed": True,
                "severity": "low|medium|high|critical",
                "blocking": False,
                "confidence": 0.0,
                "evidence": ["正文中的具体短句或明确段落定位"],
                "reason": "该证据为什么满足或违反规则，不得只说节奏拖沓/不够自然",
                "repair_instruction": "失败时给出不改变正典的定点修复动作；通过时为空字符串",
                "analysis": analysis,
            }],
        },
    }, ensure_ascii=False, indent=2)




def validate_writing_review(
    review: Mapping[str, Any],
    selected_rules: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    quality = review.get("writing_quality")
    if not isinstance(quality, Mapping):
        return [ValidationIssue("writing_quality_missing", "Reviewer omitted writing_quality", "critical", "writing_quality")]
    gates = quality.get("gates")
    if not isinstance(gates, list):
        return [ValidationIssue("writing_gates_malformed", "writing_quality.gates must be a list", "critical", "writing_quality.gates")]
    rules = {str(rule.get("id", "")): rule for rule in selected_rules}
    seen: set[str] = set()
    for index, gate in enumerate(gates):
        path = f"writing_quality.gates[{index}]"
        if not isinstance(gate, Mapping):
            issues.append(ValidationIssue("writing_gate_malformed", "Writing gate result must be an object", "critical", path))
            continue
        gate_id = str(gate.get("gate_id", ""))
        if gate_id not in rules:
            issues.append(ValidationIssue("writing_gate_unknown", f"Reviewer returned unknown writing gate {gate_id}", "high", path))
            continue
        if gate_id in seen:
            issues.append(ValidationIssue("writing_gate_duplicate", f"Reviewer duplicated writing gate {gate_id}", "high", path))
        seen.add(gate_id)
        if not isinstance(gate.get("passed"), bool):
            issues.append(ValidationIssue("writing_gate_pass_missing", f"Writing gate {gate_id} requires boolean passed", "critical", path))
        rule_id = str(gate.get("rule_id", gate_id))
        if gate.get("passed") is False and "rule_id" not in gate:
            issues.append(ValidationIssue("writing_gate_rule_id_missing", f"Failed writing gate {gate_id} requires rule_id", "medium", path))
        if rule_id != gate_id:
            issues.append(ValidationIssue("writing_gate_rule_id_mismatch", f"Writing gate {gate_id} rule_id must match gate_id", "high", path))
        evidence = gate.get("evidence")
        if not isinstance(evidence, list) or not any(str(value).strip() for value in evidence):
            issues.append(ValidationIssue("writing_gate_evidence_missing", f"Writing gate {gate_id} requires concrete正文 evidence", "medium", path))
        if not str(gate.get("reason", "")).strip():
            issues.append(ValidationIssue("writing_gate_reason_missing", f"Writing gate {gate_id} requires a reason tied to evidence", "medium", path))
        if gate.get("passed") is False:
            try:
                confidence = float(gate.get("confidence", -1))
            except (TypeError, ValueError):
                confidence = -1
            if not 0.0 <= confidence <= 1.0:
                issues.append(ValidationIssue("writing_gate_confidence_missing", f"Failed writing gate {gate_id} requires confidence 0..1", "medium", path))
            if not isinstance(gate.get("blocking"), bool):
                issues.append(ValidationIssue("writing_gate_blocking_missing", f"Failed writing gate {gate_id} requires boolean blocking", "medium", path))
            elif bool(gate.get("blocking")) != bool(rules[gate_id].get("blocking", False)):
                issues.append(ValidationIssue("writing_gate_blocking_mismatch", f"Writing gate {gate_id} blocking must match its Rule Card", "medium", path))
        analysis = gate.get("analysis")
        required_evidence = required_analysis_fields(rules[gate_id])
        missing_evidence = [
            name for name in required_evidence
            if not isinstance(analysis, Mapping) or not str(analysis.get(name, "")).strip()
        ]
        if missing_evidence:
            issues.append(ValidationIssue(
                "writing_gate_analysis_incomplete",
                f"Writing gate {gate_id} analysis is missing required evidence fields",
                "critical",
                path,
                {"missing": missing_evidence, "required": required_evidence},
            ))
        if gate.get("passed") is False and not str(gate.get("repair_instruction", "")).strip():
            issues.append(ValidationIssue("writing_gate_repair_missing", f"Failed writing gate {gate_id} requires repair_instruction", "high", path))
    missing = sorted(set(rules) - seen)
    if missing:
        issues.append(ValidationIssue(
            "writing_gates_incomplete",
            "Reviewer did not evaluate every selected Writing Rule",
            "critical",
            "writing_quality.gates",
            {"missing_gate_ids": missing},
        ))
    try:
        score = int(quality.get("score", -1))
    except (TypeError, ValueError):
        score = -1
    if not 0 <= score <= 100:
        issues.append(ValidationIssue("writing_score_invalid", "Writing quality score must be 0..100", "high", "writing_quality.score"))
    return issues


_SCHEMA_BLOCKING_CODES = {
    "writing_quality_missing",
    "writing_gates_malformed",
    "writing_gate_malformed",
    "writing_gate_unknown",
    "writing_gate_duplicate",
    "writing_gate_pass_missing",
    "writing_gate_rule_id_mismatch",
    "writing_gates_incomplete",
    "writing_gate_analysis_incomplete",
    "writing_score_invalid",
}


def _gate_assessment(rule: Mapping[str, Any], gate: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    severity = str(rule.get("severity") or gate.get("severity") or "medium").lower()
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "low"
    evidence = gate.get("evidence")
    has_evidence = isinstance(evidence, list) and any(str(value).strip() for value in evidence)
    has_reason = bool(str(gate.get("reason", "")).strip())
    try:
        confidence = float(gate.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    sufficient = has_evidence and has_reason and confidence >= float(config.get("min_blocking_confidence", 0.75))
    rule_blocking = bool(rule.get("blocking", False))
    failed = gate.get("passed") is False
    can_block = failed and rule_blocking and sufficient and (
        (severity == "critical" and bool(config.get("block_critical", True)))
        or (severity == "high" and bool(config.get("block_high", True)))
    )
    if can_block:
        disposition = "block"
    elif failed and severity in {"critical", "high", "medium"}:
        disposition = "repair_candidate"
    elif failed:
        disposition = "warning"
    else:
        disposition = "passed"
    return {
        "gate_id": str(gate.get("gate_id", "")),
        "severity": severity,
        "blocking": rule_blocking,
        "confidence": confidence,
        "sufficient_evidence": sufficient,
        "disposition": disposition,
        "blocks_commit": can_block,
    }


def _report_data(
    paths: WorkspacePaths,
    chapter_number: int,
    audit: Mapping[str, Any],
    selected_rules: list[Mapping[str, Any]],
    repair_rounds: int = 0,
) -> dict[str, Any]:
    quality = audit.get("writing_quality", {}) if isinstance(audit, Mapping) else {}
    gates = quality.get("gates", []) if isinstance(quality, Mapping) else []
    rule_map = {str(rule.get("id")): rule for rule in selected_rules}
    assessed = []
    for item in gates if isinstance(gates, list) else []:
        if not isinstance(item, Mapping):
            continue
        value = dict(item)
        value.update(_gate_assessment(rule_map.get(str(item.get("gate_id")), {}), item, writing_quality_config(paths)))
        assessed.append(value)
    passed = [item for item in assessed if item.get("passed") is True]
    failed = [item for item in assessed if item.get("passed") is False]
    by_domain = {str(rule.get("id")): str(rule.get("domain")) for rule in selected_rules}
    return {
        "chapter": chapter_number,
        "generated_at": utc_now(),
        "score": int(quality.get("score", 0) or 0) if isinstance(quality, Mapping) else 0,
        "approved": bool(audit.get("approved")),
        "status": str(audit.get("writing_quality_status", "approved")),
        "applied_rules": [str(rule.get("id")) for rule in selected_rules],
        "passed_gates": passed,
        "failed_gates": failed,
        "repair_rounds": repair_rounds,
        "fluff_flags": [item for item in failed if by_domain.get(str(item.get("gate_id"))) == "anti_fluff"],
        "causality_flags": [item for item in failed if by_domain.get(str(item.get("gate_id"))) == "causality"],
        "dialogue_flags": [item for item in failed if by_domain.get(str(item.get("gate_id"))) == "dialogue"],
        "novelty_flags": [item for item in failed if by_domain.get(str(item.get("gate_id"))) == "information_novelty"],
        "summary": str(quality.get("summary", "")) if isinstance(quality, Mapping) else "",
        "report_path": str(paths.root / "analysis" / "writing-quality" / f"chapter-{chapter_number:04d}.json"),
    }


def _report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# 第 {report.get('chapter')} 章 Writing Quality Report",
        "",
        f"- 分数：{report.get('score')}",
        f"- 通过：{'是' if report.get('approved') else '否'}",
        f"- 状态：{report.get('status', 'approved')}",
        f"- 修复轮次：{report.get('repair_rounds', 0)}",
        f"- 应用规则：{', '.join(report.get('applied_rules', []))}",
        "",
        "## 失败门禁",
        "",
    ]
    failed = report.get("failed_gates", [])
    if not failed:
        lines.append("无。")
    for gate in failed if isinstance(failed, list) else []:
        lines.extend([
            f"### {gate.get('gate_id')} ({gate.get('severity')})",
            "",
            f"- 证据：{'；'.join(str(value) for value in gate.get('evidence', []))}",
            f"- 原因：{gate.get('reason', '')}",
            f"- 修复：{gate.get('repair_instruction', '')}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def apply_writing_quality_gate(
    paths: WorkspacePaths,
    chapter_number: int,
    audit: dict[str, Any],
    selected_rules: list[Mapping[str, Any]],
    *,
    write_report: bool = True,
    repair_rounds: int = 0,
) -> dict[str, Any]:
    config = writing_quality_config(paths)
    if not config.get("enabled"):
        return audit
    validation = validate_writing_review(audit, selected_rules, config)
    issues = audit.get("issues") if isinstance(audit.get("issues"), list) else []
    existing = {(str(item.get("type", "")), str(item.get("location", ""))) for item in issues if isinstance(item, Mapping)}
    for issue in validation:
        signature = (issue.code, str(issue.path or "writing_quality"))
        if signature not in existing:
            issues.append({
                "type": issue.code,
                "severity": issue.severity,
                "location": issue.path or "writing_quality",
                "evidence": issue.message,
                "fix": "Rerun the Writing Reviewer with every selected Rule Card and concrete正文 evidence",
                "details": issue.details,
            })
            existing.add(signature)
    rule_map = {str(rule.get("id")): rule for rule in selected_rules}
    quality = audit.get("writing_quality", {})
    gates = quality.get("gates", []) if isinstance(quality, Mapping) else []
    for gate in gates if isinstance(gates, list) else []:
        if not isinstance(gate, Mapping):
            continue
        rule = rule_map.get(str(gate.get("gate_id", "")))
        if not rule or gate.get("passed") is not False:
            continue
        severity = str(gate.get("severity") or rule.get("severity") or "medium")
        signature = ("writing_gate", str(gate.get("gate_id")))
        if signature not in existing:
            issues.append({
                "type": "writing_gate",
                "severity": severity,
                "location": str(gate.get("gate_id")),
                "evidence": "；".join(str(value) for value in gate.get("evidence", [])),
                "fix": str(gate.get("repair_instruction", "")),
                "details": {"gate_id": gate.get("gate_id"), "reason": gate.get("reason", "")},
            })
            existing.add(signature)
    audit["issues"] = issues
    schema_blocking = any(issue.code in _SCHEMA_BLOCKING_CODES for issue in validation)
    assessments = [
        _gate_assessment(rule_map.get(str(gate.get("gate_id")), {}), gate, config)
        for gate in gates if isinstance(gates, list) and isinstance(gate, Mapping)
    ]
    unresolved = [item for item in assessments if item["blocks_commit"]]
    max_rounds = int(config.get("max_repair_rounds", 2))
    at_limit = repair_rounds >= max_rounds
    unresolved_critical = any(item["severity"] == "critical" for item in unresolved)
    unresolved_high = any(item["severity"] == "high" for item in unresolved)
    hard_stop = schema_blocking or (
        at_limit and (
            unresolved_critical and bool(config.get("stop_on_unresolved_critical", True))
            or unresolved_high and bool(config.get("stop_on_unresolved_high", False))
        )
    )
    repair_required = bool(unresolved) and not at_limit
    warnings = bool([item for item in assessments if item["disposition"] in {"repair_candidate", "warning"}])
    try:
        score = int(quality.get("score", 0) or 0) if isinstance(quality, Mapping) else 0
    except (TypeError, ValueError):
        score = 0
    warnings = warnings or score < int(config.get("min_score", 85))
    if hard_stop:
        status = "blocked"
    elif repair_required:
        status = "repair_required"
    elif unresolved or warnings:
        status = "approved_with_warnings"
    else:
        status = "approved"
    audit["writing_quality_status"] = status
    if isinstance(audit.get("writing_quality"), dict):
        audit["writing_quality"]["gate_assessments"] = assessments
    if status in {"blocked", "repair_required"}:
        audit["approved"] = False
    elif status == "approved_with_warnings" and bool(config.get("allow_commit_with_warnings", True)):
        non_writing_blockers = [
            item for item in issues
            if isinstance(item, Mapping)
            and str(item.get("type", "")) != "writing_gate"
            and str(item.get("type", "")) not in {issue.code for issue in validation}
            and str(item.get("severity", "")) in {"critical", "high"}
        ]
        if not non_writing_blockers:
            audit["approved"] = True
    if write_report:
        report = _report_data(paths, chapter_number, audit, selected_rules, repair_rounds)
        report_dir = paths.root / "analysis" / "writing-quality"
        report_dir.mkdir(parents=True, exist_ok=True)
        write_json(report_dir / f"chapter-{chapter_number:04d}.json", report)
        (report_dir / f"chapter-{chapter_number:04d}.md").write_text(_report_markdown(report), encoding="utf-8")
    return audit


def validate_writing_report_for_commit(paths: WorkspacePaths, chapter_number: int) -> ValidationReport:
    config = writing_quality_config(paths)
    if not config.get("enabled"):
        return ValidationReport(ok=True)
    path = paths.root / "analysis" / "writing-quality" / f"chapter-{chapter_number:04d}.json"
    report = read_json(path, {})
    issues: list[ValidationIssue] = []
    if not isinstance(report, Mapping) or not report:
        issues.append(ValidationIssue("writing_report_missing", "Writing Quality Report is required before commit", "critical", str(path)))
    else:
        if str(report.get("status", "")) not in {"approved", "approved_with_warnings"} or not bool(report.get("approved")):
            issues.append(ValidationIssue("writing_report_not_approved", "Writing Quality Report has blocking failures", "critical", str(path), {"failed_gates": report.get("failed_gates", [])}))
        applied_ids = [str(value) for value in report.get("applied_rules", []) if str(value)]
        if not applied_ids:
            issues.append(ValidationIssue("writing_rules_missing", "Writing Quality Report has no applied Rule Cards", "critical", str(path)))
        else:
            from .writing_doctrine import load_writing_rules

            rule_map = {str(rule.get("id")): rule for rule in load_writing_rules(paths)}
            selected = [rule_map[rule_id] for rule_id in applied_ids if rule_id in rule_map]
            if len(selected) != len(applied_ids):
                issues.append(ValidationIssue("writing_rules_unknown", "Writing Quality Report references unknown Rule Cards", "critical", str(path)))
            audit = read_json(paths.chapter_file(chapter_number, "audit"), {})
            issues.extend(
                issue for issue in validate_writing_review(audit, selected, config)
                if issue.code in _SCHEMA_BLOCKING_CODES
            )
    return ValidationReport(ok=not issues, issues=issues)


def targeted_repair_brief(audit: Mapping[str, Any]) -> dict[str, Any]:
    quality = audit.get("writing_quality", {}) if isinstance(audit, Mapping) else {}
    gates = quality.get("gates", []) if isinstance(quality, Mapping) else []
    assessments = quality.get("gate_assessments", []) if isinstance(quality, Mapping) else []
    allowed = {
        str(item.get("gate_id")) for item in assessments
        if isinstance(item, Mapping) and item.get("disposition") in {"block", "repair_candidate"}
    }
    failed = [
        dict(item) for item in gates
        if isinstance(item, Mapping) and item.get("passed") is False and str(item.get("gate_id")) in allowed
    ]
    existing = [
        dict(item) for item in audit.get("issues", [])
        if isinstance(item, Mapping) and str(item.get("severity")) in {"high", "critical"}
    ]
    return {
        "failed_gate_ids": [str(item.get("gate_id")) for item in failed],
        "writing_failures": failed,
        "existing_blocking_issues": existing,
        "instruction": "只修列出的失败项；保留未标记内容、事实、事件顺序、人物知识和状态。",
    }


def run_writing_gate_evals() -> dict[str, Any]:
    root = files("novel_production_mcp").joinpath("writing_eval_fixtures")
    results: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".json"):
            continue
        fixture = json.loads(entry.read_text(encoding="utf-8"))
        rules = fixture.get("rules", [])
        review = fixture.get("review", {})
        issues = validate_writing_review(review, rules, {"min_score": 85})
        quality = review.get("writing_quality", {}) if isinstance(review, Mapping) else {}
        failed = {str(item.get("gate_id")) for item in quality.get("gates", []) if isinstance(item, Mapping) and item.get("passed") is False}
        expected = set(fixture.get("expected_failed_gate_ids", []))
        expect_valid = bool(fixture.get("expect_schema_valid", True))
        structural = [item for item in issues if item.code in _SCHEMA_BLOCKING_CODES]
        passed = failed == expected and (not structural if expect_valid else bool(issues))
        results.append({"name": fixture.get("name", entry.name), "passed": passed, "expected_failed_gate_ids": sorted(expected), "actual_failed_gate_ids": sorted(failed), "schema_issues": [item.code for item in issues]})
    return {"passed": all(item["passed"] for item in results), "total": len(results), "passed_count": sum(1 for item in results if item["passed"]), "results": results}
