from __future__ import annotations

import hashlib
import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import chain
from pathlib import Path
from typing import Any

from .envfile import parse_env_file
from .models import ModelResult, ProviderConfig, RouteConfig
from .storage import config_dir, sha256_text


DEFAULT_USER_AGENT = "novel-production/2.3"
SUPPORTED_API_MODES = {"chat_completions", "responses"}
SUPPORTED_GENERATION_MODES = {"external", "agent"}


HTTP_ERROR = urllib.error.HTTPError


class ProviderError(RuntimeError):
    """A routed model call failed after its retry and fallback policy was exhausted."""

    def __init__(
        self,
        message: str,
        *,
        status_codes: tuple[int, ...] = (),
        categories: tuple[str, ...] = (),
        routes: tuple[str, ...] = (),
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status_codes = status_codes
        self.categories = categories
        self.routes = routes
        self.diagnostics = diagnostics


RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _http_category(status_code: int) -> str:
    if status_code == 524:
        return "origin_timeout"
    if status_code == 429:
        return "rate_limit"
    if status_code in {408, 504}:
        return "timeout"
    if 400 <= status_code < 500:
        return "client_error"
    if status_code >= 500:
        return "server_error"
    return "http_error"


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None:
        return min(retry_after, 60.0)
    return _safe_float(min(2**max(0, attempt), 8), 0.0)


def _text_from_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_provider_data() -> dict[str, Any]:
    path = config_dir() / "providers.toml"
    data = _load_toml(path)
    if not data.get("providers"):
        raise FileNotFoundError(
            f"No providers configured. Copy providers.example.toml to {path} and edit it."
        )
    return data


def load_route_data(workspace_routes: Path | None = None) -> dict[str, Any]:
    global_data = _load_toml(config_dir() / "routes.toml")
    local_data = _load_toml(workspace_routes) if workspace_routes else {}
    merged = _merge_dicts(global_data, local_data)
    if not merged.get("routes"):
        raise FileNotFoundError("No model routes configured")
    return merged


def generation_mode_details(workspace_routes: Path | None = None) -> dict[str, Any]:
    """Resolve generation mode and report the winning configuration layer."""
    configured = os.environ.get("NOVEL_GENERATION_MODE", "").strip().lower()
    source = "process_env" if configured else ""
    if not configured and workspace_routes:
        local_data = _load_toml(workspace_routes)
        local_generation = local_data.get("generation", {}) if isinstance(local_data, dict) else {}
        if isinstance(local_generation, dict) and str(local_generation.get("mode", "")).strip():
            configured = str(local_generation["mode"]).strip().lower()
            source = "workspace_routes"
    if not configured:
        global_data = _load_toml(config_dir() / "routes.toml")
        global_generation = global_data.get("generation", {}) if isinstance(global_data, dict) else {}
        if isinstance(global_generation, dict) and str(global_generation.get("mode", "")).strip():
            configured = str(global_generation["mode"]).strip().lower()
            source = "global_routes"
    if not configured:
        configured = "external"
        source = "default"
    if configured not in SUPPORTED_GENERATION_MODES:
        raise ValueError(
            f"Unsupported generation mode: {configured!r}; expected 'external' or 'agent'"
        )
    return {
        "mode": configured,
        "source": source,
        "workspace_routes": str(workspace_routes) if workspace_routes else "",
        "global_routes": str(config_dir() / "routes.toml"),
        "process_override": source == "process_env",
    }


def load_generation_mode(workspace_routes: Path | None = None) -> str:
    """Resolve whether model content comes from external providers or the host Agent."""
    return str(generation_mode_details(workspace_routes)["mode"])


def resolve_provider(name: str, provider_data: dict[str, Any]) -> ProviderConfig:
    raw = provider_data.get("providers", {}).get(name)
    if not isinstance(raw, dict):
        raise KeyError(f"Unknown provider: {name}")
    base_url = str(raw.get("base_url", "")).rstrip("/")
    if not base_url:
        raise ValueError(f"Provider {name} has no base_url")
    api_mode = str(raw.get("api_mode", "chat_completions")).strip().lower()
    if api_mode not in SUPPORTED_API_MODES:
        raise ValueError(
            f"Provider {name} has unsupported api_mode={api_mode!r}; "
            "expected 'chat_completions' or 'responses'"
        )
    return ProviderConfig(
        name=name,
        base_url=base_url,
        api_key_env=str(raw.get("api_key_env", "")),
        allow_empty_key=bool(raw.get("allow_empty_key", False)),
        api_mode=api_mode,
        stream=bool(raw.get("stream", True)),
        extra_headers={str(k): str(v) for k, v in raw.get("extra_headers", {}).items()},
    )


def resolve_route(name: str, route_data: dict[str, Any]) -> RouteConfig:
    raw = route_data.get("routes", {}).get(name)
    if not isinstance(raw, dict):
        raise KeyError(f"Unknown route: {name}")
    return RouteConfig(
        name=name,
        provider=str(raw["provider"]),
        model=str(raw["model"]),
        temperature=_safe_float(raw.get("temperature", 0.7), 0.7),
        max_tokens=_safe_int(raw.get("max_tokens", 4096), 4096),
        timeout_sec=_safe_int(raw.get("timeout_sec", 600), 600),
        retries=max(0, _safe_int(raw.get("retries", 1), 1)),
        fallback_routes=[str(v) for v in raw.get("fallback_routes", [])],
        stream=bool(raw["stream"]) if "stream" in raw else None,
        input_cost_per_million=_safe_float(raw.get("input_cost_per_million", 0.0), 0.0),
        output_cost_per_million=_safe_float(raw.get("output_cost_per_million", 0.0), 0.0),
    )


def list_routes(workspace_routes: Path | None = None) -> dict[str, Any]:
    route_data = load_route_data(workspace_routes)
    return {
        name: {
            "provider": raw.get("provider"),
            "model": raw.get("model"),
            "temperature": raw.get("temperature", 0.7),
            "max_tokens": raw.get("max_tokens", 4096),
            "timeout_sec": raw.get("timeout_sec", 600),
            "fallback_routes": raw.get("fallback_routes", []),
            "stream": raw.get("stream", None),
            "input_cost_per_million": raw.get("input_cost_per_million", 0.0),
            "output_cost_per_million": raw.get("output_cost_per_million", 0.0),
        }
        for name, raw in route_data.get("routes", {}).items()
    }


def messages_hash(messages: list[dict[str, str]]) -> str:
    stable = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _content_from_response(body: dict[str, Any]) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI-compatible response: {json.dumps(body)[:1000]}") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _content_from_responses_api(body: dict[str, Any]) -> str:
    # Some OpenAI-compatible gateways expose the SDK convenience field directly.
    top_level = body.get("output_text")
    if isinstance(top_level, str) and top_level:
        return top_level

    parts: list[str] = []
    output = body.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])

    if parts:
        return "\n".join(parts)
    raise RuntimeError(f"Unexpected Responses API response: {json.dumps(body)[:1000]}")


def _iter_sse_events(lines: Any):
    event_name = ""
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _usage_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    nested = payload.get("response")
    if isinstance(nested, dict) and isinstance(nested.get("usage"), dict):
        return nested["usage"]
    return None


def _read_sse_response(lines: Any, api_mode: str) -> tuple[str, dict[str, Any] | None]:
    parts: list[str] = []
    usage: dict[str, Any] | None = None
    completed_payload: dict[str, Any] | None = None
    for event_name, data_text in _iter_sse_events(lines):
        if data_text.strip() == "[DONE]":
            break
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid SSE JSON event: {data_text[:500]}") from exc
        if not isinstance(payload, dict):
            continue
        event_type = str(payload.get("type") or event_name)
        if event_type in {"error", "response.failed", "response.incomplete"} or isinstance(payload.get("error"), dict):
            detail = payload.get("error") or payload.get("response") or payload
            raise RuntimeError(
                f"Streaming provider event {event_type}: {json.dumps(detail, ensure_ascii=False)[:1200]}"
            )
        usage = _usage_from_payload(payload) or usage
        if api_mode == "responses":
            if event_type.endswith("output_text.delta"):
                delta = payload.get("delta")
                if isinstance(delta, str):
                    parts.append(delta)
            elif event_type == "response.completed":
                completed_payload = payload.get("response") if isinstance(payload.get("response"), dict) else payload
                usage = _usage_from_payload(completed_payload) or usage
            elif not parts and isinstance(payload.get("output_text"), str):
                parts.append(payload["output_text"])
        else:
            choices = payload.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                if isinstance(delta, dict):
                    piece = _text_from_content(delta.get("content"))
                    if piece:
                        parts.append(piece)

    if not parts and completed_payload is not None:
        parts.append(_content_from_responses_api(completed_payload))
    content = "".join(parts)
    if not content:
        raise RuntimeError("Streaming response completed without output text")
    return content, usage


def _read_model_response(response: Any, api_mode: str, streaming: bool) -> tuple[str, dict[str, Any] | None]:
    first_line = response.readline()
    if not first_line:
        raise RuntimeError("Empty model response")
    content_type = str(response.headers.get("Content-Type", "")).lower()
    first_text = first_line.decode("utf-8", errors="replace").lstrip()
    looks_like_sse = (
        streaming
        and (
            "text/event-stream" in content_type
            or first_text.startswith("data:")
            or first_text.startswith("event:")
            or first_text.startswith(":")
        )
    )
    if looks_like_sse:
        return _read_sse_response(chain([first_line], response), api_mode)

    raw = first_line + response.read()
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON model response: {raw[:1000]!r}") from exc
    if not isinstance(body, dict):
        raise RuntimeError("Model response must be a JSON object")
    content = _content_from_responses_api(body) if api_mode == "responses" else _content_from_response(body)
    return content, _usage_from_payload(body)


def _effective_stream(provider: ProviderConfig, route: RouteConfig) -> bool:
    return provider.stream if route.stream is None else route.stream


def _request_for_provider(
    provider: ProviderConfig,
    route: RouteConfig,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    if provider.api_mode == "responses":
        return (
            f"{provider.base_url}/responses",
            {
                "model": route.model,
                "input": messages,
                "temperature": route.temperature,
                "max_output_tokens": route.max_tokens,
                "stream": _effective_stream(provider, route),
            },
        )
    return (
        f"{provider.base_url}/chat/completions",
        {
            "model": route.model,
            "messages": messages,
            "temperature": route.temperature,
            "max_tokens": route.max_tokens,
            "stream": _effective_stream(provider, route),
        },
    )


def _token_counts(usage: dict[str, Any] | None) -> tuple[int, int]:
    raw = usage or {}
    try:
        prompt = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    except (TypeError, ValueError):
        prompt = 0
    try:
        completion = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    except (TypeError, ValueError):
        completion = 0
    return prompt, completion


def _estimated_cost(route: RouteConfig, usage: dict[str, Any] | None) -> float:
    prompt, completion = _token_counts(usage)
    return (
        prompt * route.input_cost_per_million / 1_000_000
        + completion * route.output_cost_per_million / 1_000_000
    )


def _response_diagnostics(headers: Any) -> list[str]:
    diagnostics: list[str] = []
    if headers is None:
        return diagnostics
    for label, names in (
        ("request_id", ("x-request-id", "request-id", "x-correlation-id")),
        ("cf-ray", ("cf-ray",)),
    ):
        for name in names:
            value = headers.get(name)
            if value:
                diagnostics.append(f"{label}={value}")
                break
    return diagnostics


def call_route(
    route_name: str,
    messages: list[dict[str, str]],
    workspace_routes: Path | None = None,
) -> ModelResult:
    provider_data = load_provider_data()
    route_data = load_route_data(workspace_routes)
    primary = resolve_route(route_name, route_data)
    candidates = [route_name, *primary.fallback_routes]
    errors: list[str] = []
    status_codes: list[int] = []
    categories: list[str] = []
    diagnostics: list[str] = []
    input_hash = messages_hash(messages)
    total_attempts = 0

    for candidate in candidates:
        route = resolve_route(candidate, route_data)
        provider = resolve_provider(route.provider, provider_data)
        key = ""
        if provider.api_key_env:
            key = os.environ.get(provider.api_key_env, "")
            if not key:
                key = parse_env_file(config_dir() / ".env").get(provider.api_key_env, "")
        if not key and not provider.allow_empty_key:
            errors.append(f"{candidate}: missing environment variable {provider.api_key_env}")
            continue
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "Authorization": f"Bearer {key or 'local'}",
            **provider.extra_headers,
        }
        endpoint, payload = _request_for_provider(provider, route, messages)
        for attempt in range(route.retries + 1):
            total_attempts += 1
            started = time.monotonic()
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=route.timeout_sec) as response:
                    content, usage = _read_model_response(response, provider.api_mode, _effective_stream(provider, route))
                return ModelResult(
                    content=content,
                    provider=provider.name,
                    model=route.model,
                    route=candidate,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    raw_usage=usage,
                    input_hash=input_hash,
                    output_hash=sha256_text(content),
                    attempt_count=total_attempts,
                    estimated_cost_usd=_estimated_cost(route, usage),
                )
            except urllib.error.URLError as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    status_code = int(exc.code)
                    category = _http_category(status_code)
                    status_codes.append(status_code)
                    categories.append(category)
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")[:1000]
                    except Exception:
                        detail = str(exc)
                    errors.append(f"{candidate} attempt {attempt + 1}: HTTP {status_code} [{category}] {detail}")
                    response_details = _response_diagnostics(exc.headers)
                    diagnostics.extend(response_details)
                    if response_details:
                        errors[-1] += " (" + ", ".join(response_details) + ")"
                    if attempt < route.retries and status_code in RETRYABLE_HTTP_STATUS:
                        time.sleep(_retry_delay(attempt, _retry_after_seconds(exc.headers.get("Retry-After"))))
                        continue
                    break
                categories.append("network_error")
                errors.append(f"{candidate} attempt {attempt + 1}: network error: {exc}")
                if attempt < route.retries:
                    time.sleep(_retry_delay(attempt))
                    continue
                break
            except TimeoutError as exc:
                categories.append("timeout")
                errors.append(f"{candidate} attempt {attempt + 1}: timeout: {exc}")
                if attempt < route.retries:
                    time.sleep(_retry_delay(attempt))
                    continue
                break
            except (json.JSONDecodeError, RuntimeError) as exc:
                categories.append("protocol_error")
                detail = str(exc)
                errors.append(f"{candidate} attempt {attempt + 1}: {detail}")
                break
    error_text = "\n".join(errors)
    if "error code: 1010" in error_text.lower():
        error_text += (
            "\nHint: HTTP error 1010 is commonly returned by an upstream/WAF such as Cloudflare "
            "when the API client signature is blocked. Novel Production sends an explicit API User-Agent; "
            "if this persists, allow the API path (for example /v1/*) through the upstream security rule."
        )
    raise ProviderError(
        f"All route attempts failed [input_hash={input_hash}]:\n" + error_text,
        status_codes=tuple(status_codes),
        categories=tuple(categories),
        routes=tuple(candidates),
        diagnostics=tuple(diagnostics),
    )


def test_route(route_name: str, workspace_routes: Path | None = None) -> dict[str, Any]:
    result = call_route(
        route_name,
        [
            {"role": "system", "content": "You are a connectivity test. Follow the user exactly."},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        workspace_routes,
    )
    return {
        "ok": result.content.strip().upper() == "OK",
        "content": result.content.strip(),
        "route": result.route,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "attempts": result.attempt_count,
    }
