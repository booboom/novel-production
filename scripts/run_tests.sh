#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 -m compileall -q "$ROOT/scripts" "$ROOT/tests"
python3 -m pytest -q "$ROOT/tests"

cd "$ROOT/mcp"

run_system_python() {
  PYTHONPATH=src python3 -m compileall -q src tests
  PYTHONPATH=src python3 -m pytest -q
  PYTHONPATH=src python3 -m novel_production_mcp.cli evals
  PYTHONPATH=src python3 -m novel_production_mcp.cli writing-evals
}

if python3 - <<'PY' >/dev/null 2>&1
import pytest, yaml
PY
then
  run_system_python
elif command -v uv >/dev/null 2>&1; then
  uv sync --extra dev
  uv run pytest -q
  uv run novel-production evals
  uv run novel-production writing-evals
else
  echo "缺少 pytest/PyYAML，且未安装 uv。请先执行 brew install uv。" >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
skill = Path('../SKILL.md').read_text(encoding='utf-8')
assert skill.startswith('---\n')
assert '\nname: novel-production\n' in skill
assert '\ndescription:' in skill
print('Skill metadata: OK')
PY
