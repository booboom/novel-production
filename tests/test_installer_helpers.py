from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "python3"


def test_setup_config_defaults_and_secret_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config"
        proc = subprocess.run(
            [
                PYTHON,
                str(ROOT / "scripts" / "setup_config.py"),
                "--config-dir",
                str(config),
                "--skill-root",
                str(ROOT),
                "--profile",
                "cloud",
                "--api-key-stdin",
            ],
            input="secret-key",
            text=True,
            capture_output=True,
            check=True,
        )
        providers = (config / "providers.toml").read_text(encoding="utf-8")
        routes = (config / "routes.toml").read_text(encoding="utf-8")
        assert 'base_url = "https://api.nexaportai.com/v1"' in providers
        assert 'api_mode = "responses"' in providers
        assert 'model = "gpt-5.6-sol"' in routes
        assert (config / ".env").read_text(encoding="utf-8") == "NEXAPORT_API_KEY=secret-key\n"
        assert oct((config / ".env").stat().st_mode & 0o777) == "0o600"
        assert "secret-key" not in proc.stdout


def test_setup_config_reconfigure_patches_nexaport_api_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config"
        config.mkdir()
        (config / "providers.toml").write_text(
            '[providers.nexaport]\nbase_url = "https://old.example/v1"\napi_key_env = "NEXAPORT_API_KEY"\nallow_empty_key = false\n',
            encoding="utf-8",
        )
        (config / "routes.toml").write_text(
            '[routes.director]\nprovider = "nexaport"\nmodel = "old-model"\n', encoding="utf-8"
        )
        subprocess.run(
            [
                PYTHON,
                str(ROOT / "scripts" / "setup_config.py"),
                "--config-dir", str(config),
                "--skill-root", str(ROOT),
                "--patch-existing",
                "--api-mode", "responses",
            ],
            text=True, capture_output=True, check=True,
        )
        providers = (config / "providers.toml").read_text(encoding="utf-8")
        assert 'base_url = "https://api.nexaportai.com/v1"' in providers
        assert 'api_mode = "responses"' in providers


def test_configure_pi_mcp_preserves_existing_servers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")
        subprocess.run(
            [
                PYTHON,
                str(ROOT / "scripts" / "configure_pi_mcp.py"),
                "--config",
                str(config),
                "--uv",
                "/usr/bin/true",
                "--skill-root",
                str(ROOT),
                "--config-dir",
                str(base / "config"),
                "--workspace-root",
                str(base / "novels"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(config.read_text(encoding="utf-8"))
        assert "other" in data["mcpServers"]
        assert data["settings"]["requestTimeoutMs"] == 900000
        server = data["mcpServers"]["novel-production"]
        assert server["transport"] == "stdio"
        assert server["lifecycle"] == "eager"
        assert server["args"][-1] == "novel-production-mcp"
        assert server["env"]["NOVEL_GENERATION_MODE"] == "external"


def test_installer_help_documents_multi_agent_selection() -> None:
    proc = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--agent NAME[,NAME...]" in proc.stdout
    assert "pi,codex,hermes,all" in proc.stdout
    assert "--codex-mode MODE" in proc.stdout


def test_installer_rejects_unknown_agent_before_install() -> None:
    proc = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--agent", "unknown", "--check"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "不支持的 Agent" in proc.stderr


def test_multi_agent_installer_smoke_with_fake_clis() -> None:
    import os
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        package = base / "skill"
        shutil.copytree(
            ROOT,
            package,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc"),
        )
        home = base / "home"
        fake_bin = base / "bin"
        home.mkdir()
        fake_bin.mkdir()

        python_exe = shutil.which("python3")
        assert python_exe

        (fake_bin / "uv").write_text(
            f'''#!/usr/bin/env bash\n'''
            f'''if [[ "${{1:-}}" == "sync" ]]; then\n'''
            f'''  mkdir -p .venv/bin\n'''
            f'''  ln -sf "{python_exe}" .venv/bin/python\n'''
            f'''  exit 0\n'''
            f'''fi\n'''
            f'''exit 0\n''',
            encoding="utf-8",
        )
        (fake_bin / "codex").write_text(
            '''#!/usr/bin/env bash\necho "codex $*" >> "$HOME/codex.log"\nexit 0\n''', encoding="utf-8"
        )
        (fake_bin / "hermes").write_text(
            '''#!/usr/bin/env bash\necho "hermes $*" >> "$HOME/hermes.log"\nif [[ "${1:-}" == "mcp" && "${2:-}" == "list" ]]; then echo "novel-production stdio enabled"; fi\nexit 0\n''',
            encoding="utf-8",
        )
        (fake_bin / "pi").write_text(
            '''#!/usr/bin/env bash\necho "pi $*" >> "$HOME/pi.log"\nif [[ "${1:-}" == "list" ]]; then\n  if [[ -f "$HOME/.pi-extension-installed" ]]; then echo "pi-mcp-extension"; fi\n  exit 0\nfi\nif [[ "${1:-}" == "install" ]]; then touch "$HOME/.pi-extension-installed"; exit 0; fi\nexit 0\n''',
            encoding="utf-8",
        )
        for path in fake_bin.iterdir():
            path.chmod(0o755)

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(
            [
                "bash",
                str(package / "install.sh"),
                "--agent",
                "all",
                "--non-interactive",
                "--skip-network",
            ],
            text=True,
            capture_output=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (home / ".pi/agent/skills/novel-production/SKILL.md").is_file()
        assert (home / ".agents/skills/novel-production/SKILL.md").is_file()
        assert (home / ".hermes/skills/novel-production/SKILL.md").is_file()
        pi_data = json.loads((home / ".pi/agent/mcp.json").read_text(encoding="utf-8"))
        assert "novel-production" in pi_data["mcpServers"]
        assert "mcp add" in (home / "codex.log").read_text(encoding="utf-8")
        codex_log = (home / "codex.log").read_text(encoding="utf-8")
        hermes_log = (home / "hermes.log").read_text(encoding="utf-8")
        assert "NOVEL_GENERATION_MODE=agent" in codex_log
        assert "mcp add" in hermes_log
        assert "NOVEL_GENERATION_MODE=external" in hermes_log
