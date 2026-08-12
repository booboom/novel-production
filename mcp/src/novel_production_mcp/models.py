from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

Severity = Literal["info", "low", "medium", "high", "critical"]
ProgressCallback = Callable[[str, float | None, float | None], None]


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity = "medium"
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
            "details": self.details,
        }


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


@dataclass(slots=True)
class RouteConfig:
    name: str
    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout_sec: int = 600
    retries: int = 1
    fallback_routes: list[str] = field(default_factory=list)
    stream: bool | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0


@dataclass(slots=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str = ""
    allow_empty_key: bool = False
    api_mode: str = "chat_completions"
    stream: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResult:
    content: str
    provider: str
    model: str
    route: str
    latency_ms: int
    raw_usage: dict[str, Any] | None = None
    input_hash: str = ""
    output_hash: str = ""
    attempt_count: int = 1
    estimated_cost_usd: float = 0.0


@dataclass(slots=True)
class WorkspacePaths:
    root: Path

    @property
    def project(self) -> Path:
        return self.root / "project.yaml"

    @property
    def routes(self) -> Path:
        return self.root / "routes.toml"

    @property
    def database(self) -> Path:
        return self.root / ".novel" / "events.sqlite3"

    @property
    def metrics_dir(self) -> Path:
        return self.root / "metrics"

    def chapter_file(self, number: int, kind: str) -> Path:
        stem = f"chapter-{number:04d}"
        mapping = {
            "outline": self.root / "planning" / "chapter-outlines" / f"{stem}.yaml",
            "packet": self.root / "packets" / f"{stem}.md",
            "draft": self.root / "drafts" / f"{stem}.md",
            "chapter": self.root / "chapters" / f"{stem}.md",
            "audit": self.root / "audits" / f"{stem}.json",
            "delta": self.root / "deltas" / f"{stem}.json",
            "run": self.root / "runs" / f"{stem}.json",
        }
        if kind not in mapping:
            raise ValueError(f"Unknown chapter file kind: {kind}")
        return mapping[kind]
