#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--uv", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--generation-mode", choices=("agent", "external"), default="external")
    args = parser.parse_args()

    path = Path(args.config).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"现有 MCP 配置不是合法 JSON，未覆盖：{path}: {exc}")
    else:
        data = {}
    if not isinstance(data, dict):
        raise SystemExit(f"MCP 配置根节点必须是 JSON object：{path}")
    settings = data.setdefault("settings", {})
    if not isinstance(settings, dict):
        raise SystemExit(f"settings 必须是 JSON object：{path}")
    # pi-mcp-extension currently reads the tool-call timeout from global settings;
    # the per-server field is retained below for clients that support it.
    settings["requestTimeoutMs"] = 900000
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"mcpServers 必须是 JSON object：{path}")
    servers["novel-production"] = {
        "transport": "stdio",
        "command": str(Path(args.uv).expanduser().resolve()),
        "args": [
            "--directory",
            str((Path(args.skill_root).expanduser().resolve() / "mcp")),
            "run",
            "novel-production-mcp",
        ],
        "lifecycle": "eager",
        "requestTimeoutMs": 900000,
        "env": {
            "NOVEL_CONFIG_DIR": str(Path(args.config_dir).expanduser().resolve()),
            "NOVEL_WORKSPACE_ROOT": str(Path(args.workspace_root).expanduser().resolve()),
            "NOVEL_GENERATION_MODE": args.generation_mode,
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
