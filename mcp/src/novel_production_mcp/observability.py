from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .event_store import open_connection
from .models import ModelResult, WorkspacePaths
from .storage import read_json, utc_now


def _usage(raw_usage: dict[str, Any] | None) -> tuple[int, int, int]:
    usage = raw_usage or {}
    prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total = int(usage.get("total_tokens", prompt + completion) or (prompt + completion))
    return prompt, completion, total


def record_llm_call(
    paths: WorkspacePaths,
    *,
    purpose: str,
    chapter: int | None,
    result: ModelResult | None,
    success: bool,
    repair_round: int = 0,
    error: Exception | None = None,
    route_name: str = "",
    input_hash: str = "",
) -> None:
    prompt_tokens, completion_tokens, total_tokens = _usage(result.raw_usage if result else None)
    row = {
        "created_at": utc_now(),
        "purpose": purpose,
        "chapter": chapter,
        "route": result.route if result else route_name,
        "provider": result.provider if result else "",
        "model": result.model if result else "",
        "success": bool(success),
        "latency_ms": int(result.latency_ms if result else 0),
        "attempts": int(result.attempt_count if result else 0),
        "input_hash": result.input_hash if result else input_hash,
        "output_hash": result.output_hash if result else "",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": float(result.estimated_cost_usd if result else 0.0),
        "repair_round": int(repair_round),
        "error_type": type(error).__name__ if error else "",
        "error_message": str(error)[:2000] if error else "",
    }
    paths.metrics_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.metrics_dir / "llm-calls.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with open_connection(paths) as conn:
        conn.execute(
            """
            INSERT INTO llm_calls(
              created_at, purpose, chapter, route, provider, model, success, latency_ms, attempts,
              input_hash, output_hash, prompt_tokens, completion_tokens, total_tokens,
              estimated_cost_usd, repair_round, error_type, error_message
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["created_at"], row["purpose"], row["chapter"], row["route"], row["provider"], row["model"],
                1 if row["success"] else 0, row["latency_ms"], row["attempts"], row["input_hash"],
                row["output_hash"], row["prompt_tokens"], row["completion_tokens"], row["total_tokens"],
                row["estimated_cost_usd"], row["repair_round"], row["error_type"], row["error_message"],
            ),
        )
        conn.commit()


def observability_report(paths: WorkspacePaths, limit: int = 5000) -> dict[str, Any]:
    limit = max(1, min(50000, int(limit)))
    with open_connection(paths) as conn:
        rows = conn.execute(
            "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    calls = [dict(row) for row in rows]
    by_model: dict[str, dict[str, Any]] = {}
    failure_types: Counter[str] = Counter()
    purpose_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "calls": 0, "failures": 0, "latency_ms": 0, "tokens": 0, "cost_usd": 0.0
    })
    for row in calls:
        model_key = f"{row['provider']}::{row['model']}"
        stats = by_model.setdefault(model_key, {
            "provider": row["provider"], "model": row["model"], "calls": 0, "failures": 0,
            "latency_ms": 0, "tokens": 0, "cost_usd": 0.0,
        })
        stats["calls"] += 1
        stats["failures"] += 0 if row["success"] else 1
        stats["latency_ms"] += int(row["latency_ms"] or 0)
        stats["tokens"] += int(row["total_tokens"] or 0)
        stats["cost_usd"] += float(row["estimated_cost_usd"] or 0.0)
        pstats = purpose_stats[str(row["purpose"])]
        pstats["calls"] += 1
        pstats["failures"] += 0 if row["success"] else 1
        pstats["latency_ms"] += int(row["latency_ms"] or 0)
        pstats["tokens"] += int(row["total_tokens"] or 0)
        pstats["cost_usd"] += float(row["estimated_cost_usd"] or 0.0)
        if not row["success"]:
            failure_types[str(row["error_type"] or "unknown")] += 1

    def finalize(stats: dict[str, Any]) -> dict[str, Any]:
        calls_count = max(1, int(stats["calls"]))
        return {
            **stats,
            "avg_latency_ms": round(stats["latency_ms"] / calls_count, 2),
            "failure_rate": round(stats["failures"] / calls_count, 4),
            "cost_usd": round(float(stats["cost_usd"]), 6),
        }

    audits: list[dict[str, Any]] = []
    for path in sorted((paths.root / "audits").glob("chapter-*.json")):
        audit = read_json(path, {})
        try:
            chapter = int(path.stem.split("-")[-1])
        except ValueError:
            continue
        audits.append({
            "chapter": chapter,
            "score": int(audit.get("score", 0) or 0),
            "approved": bool(audit.get("approved")),
            "issues": len(audit.get("issues", []) or []),
        })
    quality_trend = sorted(audits, key=lambda item: item["chapter"])
    return {
        "calls": len(calls),
        "successes": sum(1 for row in calls if row["success"]),
        "failures": sum(1 for row in calls if not row["success"]),
        "tokens": sum(int(row["total_tokens"] or 0) for row in calls),
        "estimated_cost_usd": round(sum(float(row["estimated_cost_usd"] or 0.0) for row in calls), 6),
        "failure_types": dict(failure_types),
        "by_model": [finalize(value) for value in by_model.values()],
        "by_purpose": {key: finalize(value) for key, value in purpose_stats.items()},
        "quality_trend": quality_trend,
    }
