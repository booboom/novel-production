from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .event_store import open_connection
from .models import WorkspacePaths
from .storage import read_text, utc_now

VECTOR_DIMS = 384


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
    grams: list[str] = []
    chinese = "".join(ch for ch in lowered if "\u4e00" <= ch <= "\u9fff")
    grams.extend(chinese[i:i + 2] for i in range(max(0, len(chinese) - 1)))
    grams.extend(chinese[i:i + 3] for i in range(max(0, len(chinese) - 2)))
    return words + grams


def _hash_vector(text: str) -> dict[int, float]:
    counts = Counter(_tokens(text))
    vector: dict[int, float] = {}
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % VECTOR_DIMS
        sign = 1.0 if ((value >> 8) & 1) == 0 else -1.0
        vector[index] = vector.get(index, 0.0) + sign * (1.0 + math.log1p(count))
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    return {index: value / norm for index, value in vector.items()}


def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


def _chunks(text: str, max_chars: int = 1200, overlap: int = 180) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            result.append(current)
            current = current[-overlap:] + "\n\n" + paragraph
        else:
            start = 0
            while start < len(paragraph):
                result.append(paragraph[start:start + max_chars])
                start += max(1, max_chars - overlap)
            current = ""
    if current:
        result.append(current)
    return result


def _iter_sources(paths: WorkspacePaths) -> Iterable[tuple[Path, str, int | None]]:
    roots = [
        (paths.root / "chapters", "chapter"),
        (paths.root / "planning", "planning"),
        (paths.root / "canon", "canon"),
        (paths.root / "characters" / "profiles", "character_profile"),
        (paths.root / "characters" / "arcs", "character_arc"),
    ]
    for root, kind in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".txt"}:
                continue
            if "proposals" in path.parts:
                continue
            chapter: int | None = None
            match = re.search(r"chapter-(\d+)", path.stem)
            if match:
                chapter = int(match.group(1))
            yield path, kind, chapter


def reindex_workspace(paths: WorkspacePaths) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path, kind, chapter in _iter_sources(paths):
        text = read_text(path)
        for ordinal, chunk in enumerate(_chunks(text)):
            content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            relative = path.relative_to(paths.root).as_posix()
            chunk_id = hashlib.sha256(f"{relative}:{ordinal}:{content_hash}".encode("utf-8")).hexdigest()[:32]
            vector = _hash_vector(chunk)
            records.append({
                "chunk_id": chunk_id,
                "source_path": relative,
                "kind": kind,
                "chapter": chapter,
                "ordinal": ordinal,
                "text": chunk,
                "vector_json": json.dumps(vector, separators=(",", ":")),
                "content_hash": content_hash,
                "updated_at": utc_now(),
            })
    with open_connection(paths) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM retrieval_chunks")
            try:
                conn.execute("DELETE FROM retrieval_fts")
            except Exception:
                pass
            for record in records:
                conn.execute(
                    """
                    INSERT INTO retrieval_chunks(
                      chunk_id, source_path, kind, chapter, ordinal, text, vector_json, content_hash, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record["chunk_id"], record["source_path"], record["kind"], record["chapter"],
                        record["ordinal"], record["text"], record["vector_json"], record["content_hash"],
                        record["updated_at"],
                    ),
                )
                try:
                    conn.execute(
                        "INSERT INTO retrieval_fts(chunk_id, text) VALUES(?,?)",
                        (record["chunk_id"], record["text"]),
                    )
                except Exception:
                    pass
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"indexed_chunks": len(records), "sources": len({r['source_path'] for r in records})}


def _lexical_score(query_tokens: Counter[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = Counter(_tokens(text))
    overlap = sum(min(count, text_tokens.get(token, 0)) for token, count in query_tokens.items())
    phrase_bonus = 0.0
    query_chars = "".join(query_tokens.keys())
    if query_chars and query_chars in text.lower():
        phrase_bonus = 2.0
    return (overlap / max(1, sum(query_tokens.values()))) + phrase_bonus


def search_memory(
    paths: WorkspacePaths,
    query: str,
    *,
    top_k: int = 6,
    exclude_chapter_gte: int | None = None,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    top_k = max(1, min(30, int(top_k)))
    query_vector = _hash_vector(query)
    query_tokens = Counter(_tokens(query))
    with open_connection(paths) as conn:
        rows = conn.execute(
            "SELECT chunk_id, source_path, kind, chapter, ordinal, text, vector_json FROM retrieval_chunks"
        ).fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        chapter = row["chapter"]
        if exclude_chapter_gte is not None and chapter is not None and int(chapter) >= exclude_chapter_gte:
            continue
        vector = {int(key): float(value) for key, value in json.loads(row["vector_json"]).items()}
        semantic = max(0.0, _cosine(query_vector, vector))
        lexical = _lexical_score(query_tokens, str(row["text"]))
        kind_boost = 0.05 if row["kind"] in {"chapter", "character_profile"} else 0.0
        score = 0.62 * semantic + 0.38 * min(1.0, lexical) + kind_boost
        if score <= 0:
            continue
        scored.append({
            "chunk_id": row["chunk_id"],
            "source_path": row["source_path"],
            "kind": row["kind"],
            "chapter": chapter,
            "ordinal": row["ordinal"],
            "score": round(score, 6),
            "text": row["text"],
        })
    scored.sort(key=lambda item: (-item["score"], item["source_path"], item["ordinal"]))
    return scored[:top_k]


def format_retrieval_context(results: list[dict[str, Any]], max_chars: int = 8000) -> str:
    if not results:
        return "（无相关历史检索结果）"
    parts = [
        "以下内容来自旧章节/资料的混合检索，只能作为低优先级参考；若与结构化正典冲突，必须忽略检索结果。"
    ]
    total = len(parts[0])
    for item in results:
        block = (
            f"\n### {item['source_path']} · score={item['score']}\n"
            f"{item['text'].strip()}\n"
        )
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)
