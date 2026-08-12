#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_BASE_URL = "https://api.nexaportai.com/v1"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_API_MODE = "responses"


def patch_nexaport_base_url(path: Path, base_url: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    section = ""
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        if section == "[providers.nexaport]" and stripped.startswith("base_url") and "=" in line:
            out.append(f'base_url = "{base_url.rstrip("/")}"')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise RuntimeError("providers.toml 中没有找到 [providers.nexaport] base_url")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")




def patch_nexaport_api_mode(path: Path, api_mode: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    section = ""
    out: list[str] = []
    found_section = False
    replaced = False
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "[providers.nexaport]" and found_section and not replaced and not inserted:
                out.append(f'api_mode = "{api_mode}"')
                inserted = True
            section = stripped
            if section == "[providers.nexaport]":
                found_section = True
        if section == "[providers.nexaport]" and stripped.startswith("api_mode") and "=" in line:
            out.append(f'api_mode = "{api_mode}"')
            replaced = True
        else:
            out.append(line)
    if section == "[providers.nexaport]" and found_section and not replaced and not inserted:
        out.append(f'api_mode = "{api_mode}"')
        inserted = True
    if not found_section:
        raise RuntimeError("providers.toml 中没有找到 [providers.nexaport]")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def patch_nexaport_models(path: Path, model: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    out = list(lines)
    sections: dict[str, tuple[int, int]] = {}
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[routes.") and stripped.endswith("]"):
            starts.append((stripped, index))
    for pos, (name, start) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(lines)
        sections[name] = (start, end)
    for start, end in sections.values():
        provider = ""
        model_line = None
        for index in range(start + 1, end):
            stripped = lines[index].strip()
            if stripped.startswith("provider") and "=" in stripped:
                provider = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            elif stripped.startswith("model") and "=" in stripped:
                model_line = index
        if provider == "nexaport" and model_line is not None:
            out[model_line] = f'model = "{model}"'
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def upsert_env(path: Path, key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("API Key 不能包含换行")
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = key + "="
    out: list[str] = []
    replaced = False
    for line in existing:
        if line.strip().startswith(prefix):
            out.append(prefix + value)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(prefix + value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--profile", choices=("cloud", "hybrid"), default="cloud")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-mode", choices=("chat_completions", "responses"), default=DEFAULT_API_MODE)
    parser.add_argument("--api-key-stdin", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--patch-existing", action="store_true")
    args = parser.parse_args()

    config_dir = Path(args.config_dir).expanduser().resolve()
    skill_root = Path(args.skill_root).expanduser().resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    providers = config_dir / "providers.toml"
    routes = config_dir / "routes.toml"

    providers_created = args.replace_existing or not providers.exists()
    routes_created = args.replace_existing or not routes.exists()
    if providers_created:
        shutil.copy2(skill_root / "config" / "providers.example.toml", providers)
    if routes_created:
        template = "routes.example.toml" if args.profile == "cloud" else "routes.hybrid.example.toml"
        shutil.copy2(skill_root / "config" / template, routes)

    provider_text = providers.read_text(encoding="utf-8")
    provider_is_placeholder = "your-domain.example" in provider_text or "example.com/v1" in provider_text
    if providers_created or args.patch_existing or provider_is_placeholder:
        patch_nexaport_base_url(providers, args.base_url)
    if providers_created or args.patch_existing:
        patch_nexaport_api_mode(providers, args.api_mode)
    if routes_created or args.patch_existing:
        patch_nexaport_models(routes, args.model)

    if args.api_key_stdin:
        key = sys.stdin.read().rstrip("\r\n")
        if key:
            upsert_env(config_dir / ".env", "NEXAPORT_API_KEY", key)

    print(f"providers={providers}")
    print(f"routes={routes}")
    print(f"base_url={args.base_url.rstrip('/')}")
    print(f"model={args.model}")
    print(f"api_mode={args.api_mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
