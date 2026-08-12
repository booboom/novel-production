from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from novel_production_mcp import core
from novel_production_mcp.provider import ProviderError, call_route, generation_mode_details


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        body = {"choices": [{"message": {"content": f"ok:{payload['model']}"}}], "usage": {"total_tokens": 3}}
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def test_agent_mode_route_inspection_never_calls_external_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "routes.toml").write_text(
        '[generation]\nmode = "agent"\n\n[routes.writer]\nprovider = "nexaport"\nmodel = "demo-model"\n',
        encoding="utf-8",
    )

    def unexpected_external_call(*_args, **_kwargs):
        pytest.fail("Agent mode must not call an external provider during route testing")

    monkeypatch.setattr(core, "test_route", unexpected_external_call)

    routes = core.list_routes_for_workspace()
    result = core.test_route_for_workspace("writer")

    assert routes["generation_mode"] == "agent"
    assert routes["effective_source"] == "current-agent"
    assert routes["configured_external_routes"]["writer"]["provider"] == "nexaport"
    assert result == {
        "ok": True,
        "skipped": True,
        "generation_mode": "agent",
        "route": "writer",
        "provider": "current-agent",
        "model": "current-agent",
        "reason": "Agent mode skips external-provider connectivity tests",
    }


def test_process_agent_mode_cannot_be_overridden_by_workspace_external_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    global_routes = config / "routes.toml"
    workspace_routes = tmp_path / "workspace-routes.toml"
    global_routes.write_text(
        '[generation]\nmode = "external"\n\n[routes.writer]\nprovider = "nexaport"\nmodel = "global"\n',
        encoding="utf-8",
    )
    workspace_routes.write_text(
        '[generation]\nmode = "external"\n\n[routes.writer]\nprovider = "ollama"\nmodel = "local"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    monkeypatch.setenv("NOVEL_GENERATION_MODE", "agent")

    details = generation_mode_details(workspace_routes)
    assert details["mode"] == "agent"
    assert details["source"] == "process_env"
    assert details["process_override"] is True


def test_external_mode_route_test_calls_configured_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "routes.toml").write_text(
        '[generation]\nmode = "external"\n\n[routes.writer]\nprovider = "nexaport"\nmodel = "demo-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        core,
        "test_route",
        lambda route_name, _routes: {
            "ok": True,
            "route": route_name,
            "provider": "nexaport",
            "model": "demo-model",
        },
    )

    result = core.test_route_for_workspace("writer")

    assert result["generation_mode"] == "external"
    assert result["skipped"] is False
    assert result["provider"] == "nexaport"


def test_call_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n', encoding="utf-8"
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n', encoding="utf-8"
    )
    try:
        result = call_route("writer", [{"role": "user", "content": "hello"}])
        assert result.content == "ok:demo-model"
        assert result.model == "demo-model"
        assert result.input_hash
        assert result.output_hash
        assert result.attempt_count == 1
    finally:
        server.shutdown()


def test_call_route_loads_key_from_local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"authorization": "", "user_agent": ""}

    class AuthHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["authorization"] = self.headers.get("Authorization", "")
            seen["user_agent"] = self.headers.get("User-Agent", "")
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            body = {"choices": [{"message": {"content": f"ok:{payload['model']}"}}]}
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    monkeypatch.delenv("NEXAPORT_API_KEY", raising=False)
    (config / ".env").write_text("NEXAPORT_API_KEY=secret-from-file\n", encoding="utf-8")
    (config / "providers.toml").write_text(
        f'[providers.nexaport]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\napi_key_env = "NEXAPORT_API_KEY"\nallow_empty_key = false\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.director]\nprovider = "nexaport"\nmodel = "demo-model"\nretries = 0\n', encoding="utf-8"
    )
    try:
        result = call_route("director", [{"role": "user", "content": "hello"}])
        assert result.content == "ok:demo-model"
        assert seen["authorization"] == "Bearer secret-from-file"
        assert seen["user_agent"] == "novel-production/2.3"
    finally:
        server.shutdown()


def test_call_route_responses_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class ResponsesHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["user_agent"] = self.headers.get("User-Agent", "")
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            seen["payload"] = payload
            body = {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": f"ok:{payload['model']}"}
                        ],
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            }
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), ResponsesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\napi_mode = "responses"\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\ntemperature = 0.4\nmax_tokens = 321\nretries = 0\ninput_cost_per_million = 1\noutput_cost_per_million = 2\n',
        encoding="utf-8",
    )
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}]
    try:
        result = call_route("writer", messages)
        assert result.content == "ok:demo-model"
        assert result.raw_usage == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
        assert result.estimated_cost_usd == pytest.approx((11 + 14) / 1_000_000)
        assert seen["path"] == "/v1/responses"
        assert seen["user_agent"] == "novel-production/2.3"
        payload = seen["payload"]
        assert isinstance(payload, dict)
        assert payload["input"] == messages
        assert payload["max_output_tokens"] == 321
        assert payload["temperature"] == 0.4
        assert payload["stream"] is True
        assert "messages" not in payload
        assert "max_tokens" not in payload
    finally:
        server.shutdown()


def test_provider_api_mode_defaults_to_chat_completions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"path": ""}

    class DefaultModeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            body = {"choices": [{"message": {"content": f"ok:{payload['model']}"}}]}
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), DefaultModeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n', encoding="utf-8"
    )
    try:
        result = call_route("writer", [{"role": "user", "content": "hello"}])
        assert result.content == "ok:demo-model"
        assert seen["path"] == "/v1/chat/completions"
    finally:
        server.shutdown()


def test_provider_rejects_unknown_api_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        '[providers.local]\nbase_url = "http://127.0.0.1:1/v1"\nallow_empty_key = true\napi_mode = "unknown"\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unsupported api_mode"):
        call_route("writer", [{"role": "user", "content": "hello"}])


def test_call_route_parses_responses_sse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class ResponsesSSEHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload["stream"] is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            events = [
                'event: response.created\ndata: {"type":"response.created"}\n\n',
                'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"你好"}\n\n',
                'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"，世界"}\n\n',
                'event: response.completed\ndata: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":2}}}\n\n',
            ]
            for event in events:
                self.wfile.write(event.encode())
                self.wfile.flush()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), ResponsesSSEHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\napi_mode = "responses"\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.extractor]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n',
        encoding="utf-8",
    )
    try:
        result = call_route("extractor", [{"role": "user", "content": "hello"}])
        assert result.content == "你好，世界"
        assert result.raw_usage == {"input_tokens": 3, "output_tokens": 2}
    finally:
        server.shutdown()


def test_call_route_parses_chat_completions_sse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class ChatSSEHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                'data: {"choices":[{"delta":{"role":"assistant","content":"stream"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"ed"}}]}\n\n',
                'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n',
                'data: [DONE]\n\n',
            ]
            for event in events:
                self.wfile.write(event.encode())
                self.wfile.flush()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), ChatSSEHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n',
        encoding="utf-8",
    )
    try:
        result = call_route("writer", [{"role": "user", "content": "hello"}])
        assert result.content == "streamed"
        assert result.raw_usage == {"prompt_tokens": 4, "completion_tokens": 2}
    finally:
        server.shutdown()


def test_call_route_surfaces_sse_failure_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedSSEHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'event: response.failed\ndata: {"type":"response.failed","response":{"error":{"message":"upstream exploded"}}}\n\n')
            self.wfile.flush()

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), FailedSSEHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.extractor]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n',
        encoding="utf-8",
    )
    try:
        with pytest.raises(ProviderError, match="response.failed"):
            call_route("extractor", [{"role": "user", "content": "hello"}])
    finally:
        server.shutdown()


def test_route_stream_false_overrides_provider_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class NonStreamHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            seen["payload"] = payload
            body = {"choices": [{"message": {"content": "plain-json"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), NonStreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\nstream = true\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.writer]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\nstream = false\n',
        encoding="utf-8",
    )
    try:
        result = call_route("writer", [{"role": "user", "content": "hello"}])
        assert result.content == "plain-json"
        assert seen["payload"]["stream"] is False
    finally:
        server.shutdown()


def test_call_route_exposes_524_for_adaptive_takeover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class TimeoutHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(524)
            self.send_header("Content-Type", "text/plain")
            self.send_header("X-Request-ID", "req-524")
            self.send_header("CF-Ray", "ray-524")
            self.end_headers()
            self.wfile.write(b"origin response timeout")

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), TimeoutHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("NOVEL_CONFIG_DIR", str(config))
    (config / "providers.toml").write_text(
        f'[providers.local]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\nallow_empty_key = true\n',
        encoding="utf-8",
    )
    (config / "routes.toml").write_text(
        '[routes.extractor]\nprovider = "local"\nmodel = "demo-model"\nretries = 0\n',
        encoding="utf-8",
    )
    try:
        with pytest.raises(ProviderError) as exc_info:
            call_route("extractor", [{"role": "user", "content": "hello"}])
        assert exc_info.value.status_codes == (524,)
        assert "origin_timeout" in exc_info.value.categories
        assert exc_info.value.diagnostics == ("request_id=req-524", "cf-ray=ray-524")
    finally:
        server.shutdown()
