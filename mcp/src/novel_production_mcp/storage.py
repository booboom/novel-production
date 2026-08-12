from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

from .models import WorkspacePaths

WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def require_workspace_id(value: str) -> str:
    value = value.strip()
    if not WORKSPACE_ID_RE.fullmatch(value):
        raise ValueError(
            "workspace_id must be 2-64 characters and use only letters, numbers, dot, underscore, or hyphen"
        )
    return value


def workspace_root() -> Path:
    raw = os.environ.get("NOVEL_WORKSPACE_ROOT", "~/Novels")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    raw = os.environ.get("NOVEL_CONFIG_DIR", "~/.config/novel-production")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_workspace(workspace_id: str) -> WorkspacePaths:
    safe_id = require_workspace_id(workspace_id)
    base = workspace_root()
    target = (base / safe_id).resolve()
    if base != target and base not in target.parents:
        raise ValueError("workspace path escaped configured root")
    return WorkspacePaths(target)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return copy.deepcopy(default) if value is None else value


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)


def write_yaml(path: Path, data: Any) -> None:
    atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@contextlib.contextmanager
def workspace_lock(paths: WorkspacePaths, name: str = "workspace") -> Iterator[None]:
    lock_path = paths.root / ".locks" / f"{name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_checkpoint(paths: WorkspacePaths, label: str = "manual") -> Path:
    if not paths.root.exists():
        raise FileNotFoundError(f"Workspace does not exist: {paths.root}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "checkpoint"
    destination = paths.root / "checkpoints" / f"{stamp}-{safe_label}"
    destination.mkdir(parents=True, exist_ok=False)
    if paths.database.exists():
        try:
            with sqlite3.connect(paths.database, timeout=10) as conn:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:
            # The checkpoint still copies the database/WAL files; validation can report any later drift.
            pass
    excludes = {"checkpoints", ".locks", ".git"}
    for item in paths.root.iterdir():
        if item.name in excludes:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    write_json(destination / "checkpoint.json", {"created_at": utc_now(), "label": label})
    return destination


def restore_checkpoint(paths: WorkspacePaths, checkpoint_name: str) -> None:
    checkpoint = (paths.root / "checkpoints" / checkpoint_name).resolve()
    checkpoints_root = (paths.root / "checkpoints").resolve()
    if checkpoints_root not in checkpoint.parents or not checkpoint.is_dir():
        raise ValueError("Invalid checkpoint")
    preserve = {"checkpoints", ".locks"}
    for item in list(paths.root.iterdir()):
        if item.name in preserve:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in checkpoint.iterdir():
        if item.name == "checkpoint.json":
            continue
        target = paths.root / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
