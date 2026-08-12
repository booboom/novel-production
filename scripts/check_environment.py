#!/usr/bin/env python3
"""First-run environment doctor for Novel Production Skill.

Uses only Python standard library so it can run before the MCP package is installed.
It never reads or prints secret values; it only reports whether required variables exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_VERSION = "2.11.2"
REQUIRED_ROUTES = (
    "director",
    "macro",
    "world",
    "characters",
    "volume",
    "outline",
    "writer",
    "reviewer",
    "repairer",
    "extractor",
)

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised only on old Python
    tomllib = None  # type: ignore[assignment]


def expand_env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def check_entry(name: str, ok: bool, detail: str, action: str = "", required: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "required": required,
        "detail": detail,
        "action": action,
    }


def is_writable_target(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.exists() and os.access(parent, os.W_OK)


def config_fingerprint(paths: list[Path], extra: str = "") -> str:
    h = hashlib.sha256()
    h.update(SKILL_VERSION.encode())
    h.update(extra.encode())
    for path in paths:
        h.update(str(path).encode())
        if path.exists() and path.is_file():
            h.update(path.read_bytes())
        else:
            h.update(b"<missing>")
    return h.hexdigest()


def marker_path(config_dir: Path) -> Path:
    return config_dir / ".environment-ready.json"


def read_marker(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_marker(path: Path, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "skill_version": SKILL_VERSION,
        "fingerprint": fingerprint,
        "status": "ready",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def load_local_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            values[key] = value
    return values

def inspect_runtime(skill_root: Path, config_dir: Path, workspace_root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(
        check_entry(
            "Python >= 3.11",
            py_ok,
            f"当前 Python: {sys.version.split()[0]}",
            "安装 Python 3.11+，然后使用该 Python 重新运行环境检查。" if not py_ok else "",
        )
    )

    uv_path = shutil.which("uv")
    checks.append(
        check_entry(
            "uv",
            bool(uv_path),
            uv_path or "未找到 uv",
            "macOS 普通用户执行：brew install uv" if not uv_path else "",
        )
    )

    checks.append(
        check_entry(
            "Skill 完整性",
            (skill_root / "SKILL.md").is_file() and (skill_root / "mcp" / "pyproject.toml").is_file(),
            str(skill_root),
            "请重新解压完整 Skill，确保 SKILL.md、mcp/、config/、scripts/ 位于同一目录。",
        )
    )

    config_ok = config_dir.exists() and is_writable_target(config_dir)
    checks.append(
        check_entry(
            "配置目录",
            config_ok,
            str(config_dir),
            f'mkdir -p "{config_dir}"' if not config_ok else "",
        )
    )

    workspace_ok = is_writable_target(workspace_root)
    checks.append(
        check_entry(
            "小说工作区目录",
            workspace_ok,
            str(workspace_root),
            f'mkdir -p "{workspace_root}"' if not workspace_ok else "",
        )
    )

    providers_path = config_dir / "providers.toml"
    routes_path = config_dir / "routes.toml"
    checks.append(
        check_entry(
            "providers.toml",
            providers_path.is_file(),
            str(providers_path),
            f'cp "{skill_root / "config" / "providers.example.toml"}" "{providers_path}"',
        )
    )
    checks.append(
        check_entry(
            "routes.toml",
            routes_path.is_file(),
            str(routes_path),
            f'cp "{skill_root / "config" / "routes.example.toml"}" "{routes_path}"',
        )
    )

    local_env = load_local_env_file(config_dir / ".env")
    providers: dict[str, Any] = {}
    routes: dict[str, Any] = {}
    parse_ok = py_ok and tomllib is not None and providers_path.is_file() and routes_path.is_file()
    if parse_ok:
        try:
            providers = load_toml(providers_path).get("providers", {})
            routes = load_toml(routes_path).get("routes", {})
            if not isinstance(providers, dict) or not isinstance(routes, dict):
                raise ValueError("providers/routes 必须是 TOML 表")
            checks.append(check_entry("TOML 配置语法", True, "providers.toml / routes.toml 可解析"))
        except Exception as exc:
            parse_ok = False
            checks.append(
                check_entry(
                    "TOML 配置语法",
                    False,
                    f"解析失败：{exc}",
                    "修正 TOML 语法后重新运行环境检查。",
                )
            )

    used_provider_names: set[str] = set()
    if parse_ok:
        missing_routes = [name for name in REQUIRED_ROUTES if not isinstance(routes.get(name), dict)]
        checks.append(
            check_entry(
                "必需模型路由",
                not missing_routes,
                "全部存在" if not missing_routes else "缺失：" + ", ".join(missing_routes),
                "在 routes.toml 中配置缺失路由。" if missing_routes else "",
            )
        )

        bad_routes: list[str] = []
        for route_name, raw in routes.items():
            if not isinstance(raw, dict):
                bad_routes.append(f"{route_name}: 配置不是表")
                continue
            provider_name = str(raw.get("provider", "")).strip()
            model = str(raw.get("model", "")).strip()
            if not provider_name or not model:
                bad_routes.append(f"{route_name}: provider/model 不能为空")
                continue
            used_provider_names.add(provider_name)
            if provider_name not in providers:
                bad_routes.append(f"{route_name}: 未找到供应商 {provider_name}")
        checks.append(
            check_entry(
                "路由引用完整性",
                not bad_routes,
                "正常" if not bad_routes else "; ".join(bad_routes),
                "修正 routes.toml 中的 provider/model，并确保供应商已存在。" if bad_routes else "",
            )
        )

        provider_errors: list[str] = []
        secret_missing: list[str] = []
        for provider_name in sorted(used_provider_names):
            raw = providers.get(provider_name)
            if not isinstance(raw, dict):
                continue
            base_url = str(raw.get("base_url", "")).strip()
            if not base_url:
                provider_errors.append(f"{provider_name}: base_url 为空")
            if "your-domain.example" in base_url or "example.com" in base_url:
                provider_errors.append(f"{provider_name}: base_url 仍是示例地址")
            api_mode = str(raw.get("api_mode", "chat_completions")).strip().lower()
            if api_mode not in {"chat_completions", "responses"}:
                provider_errors.append(f"{provider_name}: api_mode 不支持：{api_mode}")
            key_env = str(raw.get("api_key_env", "")).strip()
            allow_empty = bool(raw.get("allow_empty_key", False))
            if key_env and not allow_empty and not (os.environ.get(key_env) or local_env.get(key_env)):
                secret_missing.append(key_env)
        checks.append(
            check_entry(
                "供应商地址",
                not provider_errors,
                "正常" if not provider_errors else "; ".join(provider_errors),
                "编辑 providers.toml，把示例 Base URL 替换为真实接口地址。" if provider_errors else "",
            )
        )
        checks.append(
            check_entry(
                "API Key 环境变量",
                not secret_missing,
                "已配置（不会显示密钥值）" if not secret_missing else "缺失：" + ", ".join(sorted(set(secret_missing))),
                f"执行 ./install.sh --repair 交互保存密钥到 {config_dir / '.env'}，或设置对应环境变量。不要把真实 API Key 写入 Skill 或 TOML。" if secret_missing else "",
            )
        )

    venv_python = skill_root / "mcp" / ".venv" / "bin" / "python"
    deps_ok = False
    deps_detail = "MCP 虚拟环境尚未安装"
    if venv_python.is_file():
        try:
            proc = subprocess.run(
                [str(venv_python), "-c", "import mcp,yaml,pydantic; print('OK')"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            deps_ok = proc.returncode == 0
            deps_detail = "MCP/CLI Python 依赖已安装" if deps_ok else (proc.stderr.strip() or "依赖导入失败")
        except (OSError, subprocess.TimeoutExpired) as exc:
            deps_detail = str(exc)
    checks.append(
        check_entry(
            "MCP/CLI 运行依赖",
            deps_ok,
            deps_detail,
            f'cd "{skill_root / "mcp"}" && uv sync --extra dev',
        )
    )

    required_ok = all(item["ok"] for item in checks if item["required"])
    fingerprint = config_fingerprint(
        [providers_path, routes_path, config_dir / ".env", skill_root / "mcp" / "pyproject.toml"],
        extra=str(workspace_root),
    )
    return {
        "skill": "novel-production",
        "skill_version": SKILL_VERSION,
        "ready": required_ok,
        "config_dir": str(config_dir),
        "workspace_root": str(workspace_root),
        "fingerprint": fingerprint,
        "checks": checks,
        "notes": [
            "首次配置只需要完成 required=false 以外的失败项；API Key 可来自进程环境变量或配置目录 .env，只检查是否存在，不读取或输出值。",
            "Ollama/oMLX/云接口是否在线属于运行时状态，不写入首次初始化标记；首次正式生成前建议测试实际模型路由。",
            "v2.9 的 ./install.sh 可自动检测并配置 pi、Codex、Hermes；公共核心只安装一次，各 Agent 只增加 Skill 入口和 MCP 注册。Provider 默认启用 SSE 流式请求。",
        ],
    }


def render_text(report: dict[str, Any], cached: bool = False) -> str:
    if cached:
        return "Novel Production 环境已完成首次初始化；配置未变化，无需重复检查。"
    lines = [
        f"Novel Production v{report['skill_version']} 首次环境检查",
        "=" * 44,
    ]
    for item in report["checks"]:
        icon = "✓" if item["ok"] else "✗"
        level = "必需" if item["required"] else "可选"
        lines.append(f"{icon} [{level}] {item['name']}: {item['detail']}")
        if not item["ok"] and item["action"]:
            lines.append(f"    处理：{item['action']}")
    lines.append("")
    if report["ready"]:
        lines.append("结果：环境已就绪。后续调用不会重复执行完整检查。")
    else:
        lines.append("结果：环境尚未就绪。完成上面标记为 ✗ 的必需项后重新调用本 Skill。")
    lines.append("")
    lines.append("v2.9 一键安装默认使用 NexaPortAI Base URL https://api.nexaportai.com/v1，并可选择 pi / Codex / Hermes；Provider 默认启用 SSE 流式请求，API Key 可安全保存在配置目录 .env。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Novel Production first-run environment checker")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--force", action="store_true", help="忽略已就绪标记并执行完整检查")
    parser.add_argument("--no-mark", action="store_true", help="就绪时不写首次初始化标记")
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parent.parent
    config_dir = expand_env_path("NOVEL_CONFIG_DIR", "~/.config/novel-production")
    workspace_root = expand_env_path("NOVEL_WORKSPACE_ROOT", "~/Novels")
    providers_path = config_dir / "providers.toml"
    routes_path = config_dir / "routes.toml"
    fingerprint = config_fingerprint(
        [providers_path, routes_path, config_dir / ".env", skill_root / "mcp" / "pyproject.toml"],
        extra=str(workspace_root),
    )
    marker = read_marker(marker_path(config_dir))
    cached = bool(
        not args.force
        and marker
        and marker.get("skill_version") == SKILL_VERSION
        and marker.get("fingerprint") == fingerprint
        and marker.get("status") == "ready"
    )

    if cached:
        payload = {
            "skill": "novel-production",
            "skill_version": SKILL_VERSION,
            "ready": True,
            "cached": True,
            "message": "environment already initialized and configuration fingerprint is unchanged",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_text(payload, cached=True))
        return 0

    report = inspect_runtime(skill_root, config_dir, workspace_root)
    report["cached"] = False
    if report["ready"] and not args.no_mark:
        write_marker(marker_path(config_dir), report["fingerprint"])
        report["marker"] = str(marker_path(config_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
