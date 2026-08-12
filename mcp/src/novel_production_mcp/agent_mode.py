from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import ProgressCallback, WorkspacePaths
from .storage import read_json, utc_now, write_json

ResumeCallback = Callable[[str, ProgressCallback], dict[str, Any]]
ResumeFactory = Callable[[str], ResumeCallback]

# Payload guards for host-Agent submissions. Large JSON reviews/deltas are the
# common truncation surface; writer plain text can be larger.
DEFAULT_MAX_CONTENT_SIZE = 100_000
WRITER_MAX_CONTENT_SIZE = 500_000
ROUTE_MAX_CONTENT_SIZE = {
    "writer": WRITER_MAX_CONTENT_SIZE,
    "reviewer": DEFAULT_MAX_CONTENT_SIZE,
    "extractor": DEFAULT_MAX_CONTENT_SIZE,
    "repairer": WRITER_MAX_CONTENT_SIZE,
}

_CURRENT_SUBMISSION: ContextVar[dict[str, Any] | None] = ContextVar(
    "novel_current_agent_submission",
    default=None,
)
_CURRENT_RESUME_FACTORY: ContextVar[ResumeFactory | None] = ContextVar(
    "novel_current_agent_resume_factory",
    default=None,
)
_RESUMES: dict[str, ResumeCallback] = {}
_TASK_PATHS: dict[str, Path] = {}
_LOCK = threading.Lock()


class AgentTaskPending(RuntimeError):
    """Signal that the current Agent must generate and submit model content."""

    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        super().__init__(f"Agent task is ready for submission: {task['task_id']}")


@contextmanager
def agent_submission_context(submission: dict[str, Any]) -> Iterator[None]:
    token = _CURRENT_SUBMISSION.set(submission)
    try:
        yield
    finally:
        _CURRENT_SUBMISSION.reset(token)


@contextmanager
def agent_resume_factory_context(factory: ResumeFactory) -> Iterator[None]:
    token = _CURRENT_RESUME_FACTORY.set(factory)
    try:
        yield
    finally:
        _CURRENT_RESUME_FACTORY.reset(token)


def current_agent_submission() -> dict[str, Any] | None:
    return _CURRENT_SUBMISSION.get()


def _task_path(paths: WorkspacePaths, task_id: str) -> Path:
    return paths.root / ".novel" / "agent-tasks" / f"{task_id}.json"


def _task_id(purpose: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", purpose.lower()).strip("-") or "model"
    return f"agent-{slug}-{uuid.uuid4().hex[:12]}"


def _response_format(route: str, purpose: str) -> str:
    if route in {"writer", "repairer"}:
        return "plain_text"
    if route in {"reviewer", "extractor"} or purpose.startswith(("takeover.", "writing_reference.")) or purpose == "ending.options":
        return "json_object"
    if route in {"world", "characters", "volume", "outline"} or purpose.startswith("stage."):
        return "yaml_or_markdown"
    return "text_or_structured"


def max_content_size_for(route: str, purpose: str = "") -> int:
    """Return the hard byte/char limit for an Agent submission."""
    if purpose.startswith(("takeover.", "writing_reference.")) or purpose == "ending.options":
        return DEFAULT_MAX_CONTENT_SIZE
    return ROUTE_MAX_CONTENT_SIZE.get(route, DEFAULT_MAX_CONTENT_SIZE)


def validate_agent_submission(
    content: str,
    *,
    route: str,
    purpose: str,
    response_format: str | None = None,
) -> str:
    """Validate size and, for JSON routes, syntax before resuming a task.

    Returns the normalized content string. For ``json_object`` routes the body is
    re-serialized with ``ensure_ascii=False`` so downstream parsers see stable UTF-8 JSON.
    """
    if not isinstance(content, str):
        raise ValueError("Agent task content must be a string")
    limit = max_content_size_for(route, purpose)
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ValueError(
            f"Agent task content is {size} bytes; {route or purpose or 'task'} "
            f"limit is {limit} bytes. Split or compress the payload before submit."
        )
    fmt = response_format or _response_format(route, purpose)
    if fmt == "json_object":
        text = content.strip()
        if not text:
            raise ValueError("JSON Agent task content is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON Agent task content is invalid near line {exc.lineno} col {exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(parsed, (dict, list)):
            raise ValueError("JSON Agent task content must decode to an object or array")
        # Normalize once so truncated/pretty payloads become deterministic.
        return json.dumps(parsed, ensure_ascii=False)
    if fmt == "plain_text" and not content.strip():
        raise ValueError("Writer Agent task content is empty")
    return content



def create_agent_task(
    paths: WorkspacePaths,
    route: str,
    messages: list[dict[str, str]],
    *,
    purpose: str,
    chapter: int | None = None,
    repair_round: int = 0,
    input_hash: str,
) -> dict[str, Any]:
    factory = _CURRENT_RESUME_FACTORY.get()
    if factory is None:
        raise RuntimeError(
            "Agent generation mode requires an MCP tool call with a resumable operation context"
        )
    task_id = _task_id(purpose)
    max_content_size = max_content_size_for(route, purpose)
    response_format = _response_format(route, purpose)
    task = {
        "task_id": task_id,
        "status": "pending",
        "created_at": utc_now(),
        "workspace_id": paths.root.name,
        "workspace_path": str(paths.root),
        "route": route,
        "purpose": purpose,
        "chapter": chapter,
        "repair_round": repair_round,
        "input_hash": input_hash,
        "response_format": response_format,
        "max_content_size": max_content_size,
        "instructions": (
            "使用当前 Agent 生成下面任务所需的模型内容。只返回可提交的原始内容，"
            "不要返回 agent_task 包装对象、分析过程或 API 调用说明。"
            f" response_format={response_format}；内容体积不得超过 {max_content_size} 字节。"
            + (
                " JSON 路由必须返回单个合法 JSON 对象或数组。"
                if response_format == "json_object"
                else " plain_text 路由只返回正文，不要包 JSON。"
                if response_format == "plain_text"
                else ""
            )
        ),
        "messages": messages,
        "submit_tool": "novel_submit_agent_task",
        "task_path": f".novel/agent-tasks/{task_id}.json",
    }
    path = _task_path(paths, task_id)
    with _LOCK:
        _RESUMES[task_id] = factory(task_id)
        _TASK_PATHS[task_id] = path
    write_json(path, task)
    return task


def _update_task(path: Path, **updates: Any) -> dict[str, Any]:
    task = read_json(path, {})
    if not isinstance(task, dict) or not task:
        raise FileNotFoundError(f"Agent task not found: {path}")
    task.update(updates)
    write_json(path, task)
    return task


def resume_agent_task(
    task_id: str,
    content: str,
    progress_callback: ProgressCallback,
) -> dict[str, Any]:
    with _LOCK:
        callback = _RESUMES.get(task_id)
    if callback is None:
        raise RuntimeError(
            f"Agent task {task_id} is not resumable in this MCP process; restart-safe task replay is not available"
        )
    with _LOCK:
        task_path = _TASK_PATHS.get(task_id)
    if task_path is None:
        for candidate in Path.home().glob("Novels/*/.novel/agent-tasks/" + task_id + ".json"):
            task_path = candidate
            break
    if task_path is None:
        raise FileNotFoundError(f"Agent task file not found: {task_id}")
    task = read_json(task_path, {})
    if str(task.get("status")) != "pending":
        raise RuntimeError(f"Agent task {task_id} is already {task.get('status', 'unknown')}")
    route = str(task.get("route", ""))
    purpose = str(task.get("purpose", ""))
    response_format = str(task.get("response_format") or _response_format(route, purpose))
    try:
        content = validate_agent_submission(
            content,
            route=route,
            purpose=purpose,
            response_format=response_format,
        )
    except ValueError as exc:
        _update_task(task_path, status="pending", last_validation_error=str(exc)[:2000], last_validation_at=utc_now())
        raise
    submission = {
        "task_id": task_id,
        "route": route,
        "purpose": purpose,
        "input_hash": task.get("input_hash", ""),
        "content": content,
        "consumed": False,
    }
    try:
        with agent_submission_context(submission):
            result = callback(content, progress_callback)
    except AgentTaskPending:
        _update_task(
            task_path,
            status="completed",
            submitted_at=utc_now(),
            submitted_output_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        with _LOCK:
            _RESUMES.pop(task_id, None)
        raise
    except Exception as exc:
        _update_task(task_path, status="failed", failed_at=utc_now(), error=str(exc)[:2000])
        with _LOCK:
            _RESUMES.pop(task_id, None)
        raise
    _update_task(
        task_path,
        status="completed",
        submitted_at=utc_now(),
        submitted_output_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    with _LOCK:
        _RESUMES.pop(task_id, None)
    return result


def agent_task_status(task_id: str) -> dict[str, Any]:
    with _LOCK:
        task_path = _TASK_PATHS.get(task_id)
    candidates = [task_path] if task_path is not None else Path.home().glob("Novels/*/.novel/agent-tasks/" + task_id + ".json")
    for candidate in candidates:
        if candidate is None:
            continue
        task = read_json(candidate, {})
        if isinstance(task, dict) and task:
            with _LOCK:
                task["resume_available"] = task_id in _RESUMES
            return task
    raise FileNotFoundError(f"Agent task not found: {task_id}")
