#!/usr/bin/env bash
set -euo pipefail

# Novel Production v2.10 multi-agent one-click installer.
# 执行身份：当前普通用户，不要 sudo。

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="install"
PROFILE="cloud"
BASE_URL="https://api.nexaportai.com/v1"
MODEL="gpt-5.6-sol"
API_MODE="responses"
CONFIG_DIR="${NOVEL_CONFIG_DIR:-$HOME/.config/novel-production}"
WORKSPACE_ROOT="${NOVEL_WORKSPACE_ROOT:-$HOME/Novels}"
NON_INTERACTIVE=0
SKIP_NETWORK=0
PATCH_EXISTING=0
AGENT_SPEC=""
SKIP_PI=0
CODEX_GENERATION_MODE="agent"
PI_GENERATION_MODE="external"
HERMES_GENERATION_MODE="external"

usage() {
  cat <<USAGE
Usage: ./install.sh [options]

  --check                    只检查，不修改
  --repair                   幂等修复依赖/Agent MCP/目录，不覆盖现有 providers/routes
  --reconfigure              用指定 Base URL / model / API mode 更新现有 NexaPort 配置
  --agent NAME[,NAME...]     安装到指定 Agent：pi,codex,hermes,all
                              例：--agent codex 或 --agent pi,hermes
  --codex-mode MODE          Codex MCP 生成模式，默认 agent
  --pi-mode MODE             pi MCP 生成模式，默认 external
  --hermes-mode MODE         Hermes MCP 生成模式，默认 external
  --profile cloud|hybrid     默认 cloud；hybrid 使用 Ollama 写正文/修复
  --base-url URL             默认 https://api.nexaportai.com/v1
  --model NAME               默认 gpt-5.6-sol
  --api-mode MODE            NexaPort 请求协议：responses（默认）或 chat_completions
  --config-dir PATH          默认 ~/.config/novel-production
  --workspace-root PATH      默认 ~/Novels
  --non-interactive          不询问 API Key；未指定 --agent 时自动选择已检测 Agent
  --skip-network             --check 时不测试 director/extractor 网络连通性
  --skip-pi                  兼容旧参数：从自动选择中排除 pi

不传 --agent 时，会检测 pi / Codex / Hermes 并让你单选、多选或全部安装。
公共配置、Python/MCP 引擎、API Key 和小说工作区只安装一次。
USAGE
}

ORIGINAL_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE="check" ;;
    --repair) MODE="repair" ;;
    --reconfigure) PATCH_EXISTING=1 ;;
    --agent) AGENT_SPEC="$2"; shift ;;
    --codex-mode) CODEX_GENERATION_MODE="$2"; shift ;;
    --pi-mode) PI_GENERATION_MODE="$2"; shift ;;
    --hermes-mode) HERMES_GENERATION_MODE="$2"; shift ;;
    --profile) PROFILE="$2"; shift ;;
    --base-url) BASE_URL="$2"; shift ;;
    --model) MODEL="$2"; shift ;;
    --api-mode) API_MODE="$2"; shift ;;
    --config-dir) CONFIG_DIR="$2"; shift ;;
    --workspace-root) WORKSPACE_ROOT="$2"; shift ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    --skip-network) SKIP_NETWORK=1 ;;
    --skip-pi) SKIP_PI=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [[ "$API_MODE" != "responses" && "$API_MODE" != "chat_completions" ]]; then
  echo "--api-mode 只能是 responses 或 chat_completions" >&2
  exit 2
fi

if [[ "$PROFILE" != "cloud" && "$PROFILE" != "hybrid" ]]; then
  echo "--profile 只能是 cloud 或 hybrid" >&2
  exit 2
fi

for configured_mode in "$CODEX_GENERATION_MODE" "$PI_GENERATION_MODE" "$HERMES_GENERATION_MODE"; do
  if [[ "$configured_mode" != "agent" && "$configured_mode" != "external" ]]; then
    echo "Agent 生成模式只能是 agent 或 external" >&2
    exit 2
  fi
done

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

has_token() {
  local haystack=",${1},"
  local needle=",${2},"
  [[ "$haystack" == *"$needle"* ]]
}

add_agent() {
  local current="$1"
  local name="$2"
  if [[ -z "$current" ]]; then
    printf '%s' "$name"
  elif has_token "$current" "$name"; then
    printf '%s' "$current"
  else
    printf '%s,%s' "$current" "$name"
  fi
}

remove_agent() {
  local current="$1"
  local remove="$2"
  local result=""
  local oldifs="$IFS"
  IFS=','
  for item in $current; do
    if [[ -n "$item" && "$item" != "$remove" ]]; then
      result="$(add_agent "$result" "$item")"
    fi
  done
  IFS="$oldifs"
  printf '%s' "$result"
}

detect_agents() {
  local detected=""
  if command -v pi >/dev/null 2>&1 || [[ -d "$HOME/.pi" ]]; then
    detected="$(add_agent "$detected" "pi")"
  fi
  if command -v codex >/dev/null 2>&1 || [[ -d "$HOME/.codex" ]]; then
    detected="$(add_agent "$detected" "codex")"
  fi
  if command -v hermes >/dev/null 2>&1 || [[ -d "$HOME/.hermes" ]]; then
    detected="$(add_agent "$detected" "hermes")"
  fi
  if [[ "$SKIP_PI" -eq 1 ]]; then
    detected="$(remove_agent "$detected" "pi")"
  fi
  printf '%s' "$detected"
}

normalize_agent_spec() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
  if [[ -z "$raw" || "$raw" == "none" || "$raw" == "core" ]]; then
    printf ''
    return 0
  fi
  if [[ "$raw" == "all" || "$raw" == "a" ]]; then
    raw="pi,codex,hermes"
  fi
  local result=""
  local oldifs="$IFS"
  IFS=','
  for item in $raw; do
    case "$item" in
      pi|codex|hermes) result="$(add_agent "$result" "$item")" ;;
      *) echo "不支持的 Agent：${item}（仅支持 pi,codex,hermes,all）" >&2; return 2 ;;
    esac
  done
  IFS="$oldifs"
  if [[ "$SKIP_PI" -eq 1 ]]; then
    result="$(remove_agent "$result" "pi")"
  fi
  printf '%s' "$result"
}

interactive_agent_selection() {
  local detected="$1"
  local answer=""
  printf '\n检测到以下 Agent：\n' >/dev/tty
  if has_token "$detected" "pi"; then printf '  [1] pi      ✓ 已检测\n' >/dev/tty; else printf '  [1] pi      - 未检测\n' >/dev/tty; fi
  if has_token "$detected" "codex"; then printf '  [2] Codex   ✓ 已检测\n' >/dev/tty; else printf '  [2] Codex   - 未检测\n' >/dev/tty; fi
  if has_token "$detected" "hermes"; then printf '  [3] Hermes  ✓ 已检测\n' >/dev/tty; else printf '  [3] Hermes  - 未检测\n' >/dev/tty; fi
  printf '\n选择要安装到的 Agent（可多选，如 1,3；a=全部已检测；n=仅安装核心）' >/dev/tty
  if [[ -n "$detected" ]]; then
    printf ' [默认: 全部已检测]：' >/dev/tty
  else
    printf ' [默认: 仅核心]：' >/dev/tty
  fi
  IFS= read -r answer </dev/tty || true
  answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
  if [[ -z "$answer" || "$answer" == "a" || "$answer" == "all" ]]; then
    printf '%s' "$detected"
    return 0
  fi
  if [[ "$answer" == "n" || "$answer" == "none" || "$answer" == "core" ]]; then
    printf ''
    return 0
  fi
  local result=""
  local oldifs="$IFS"
  IFS=','
  for item in $answer; do
    case "$item" in
      1|pi) result="$(add_agent "$result" "pi")" ;;
      2|codex) result="$(add_agent "$result" "codex")" ;;
      3|hermes) result="$(add_agent "$result" "hermes")" ;;
      *) echo "无法识别的选择：$item" >&2; return 2 ;;
    esac
  done
  IFS="$oldifs"
  if [[ "$SKIP_PI" -eq 1 ]]; then
    result="$(remove_agent "$result" "pi")"
  fi
  printf '%s' "$result"
}

DETECTED_AGENTS="$(detect_agents)"
if [[ -n "$AGENT_SPEC" ]]; then
  SELECTED_AGENTS="$(normalize_agent_spec "$AGENT_SPEC")"
elif [[ "$NON_INTERACTIVE" -eq 0 && -r /dev/tty && "$MODE" != "check" ]]; then
  SELECTED_AGENTS="$(interactive_agent_selection "$DETECTED_AGENTS")"
else
  SELECTED_AGENTS="$DETECTED_AGENTS"
fi

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then return 0; fi
  log "未找到 uv，自动安装"
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  elif command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  else
    echo "无法自动安装 uv：既没有 brew 也没有 curl。" >&2
    exit 1
  fi
  command -v uv >/dev/null 2>&1 || { echo "uv 安装后仍不可用，请重新打开终端后运行 ./install.sh --repair" >&2; exit 1; }
}

venv_python() { printf '%s' "$ROOT/mcp/.venv/bin/python"; }

codex_binary() {
  local candidate
  if candidate="$(command -v codex 2>/dev/null)" && [[ -x "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  # ChatGPT Desktop bundles the Codex CLI but does not always add it to PATH.
  # Allow an explicit override, then check the standard per-user and system app paths.
  for candidate in \
    "${CODEX_CLI_PATH:-}" \
    "$HOME/Applications/ChatGPT.app/Contents/Resources/codex" \
    "/Applications/ChatGPT.app/Contents/Resources/codex"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

real_path() {
  python3 - "$1" <<'PY'
import os, sys
print(os.path.realpath(os.path.expanduser(sys.argv[1])))
PY
}

ensure_skill_link() {
  local agent="$1"
  local target="$2"
  local root_real target_real backup
  root_real="$(real_path "$ROOT")"
  mkdir -p "$(dirname "$target")"
  if [[ -e "$target" || -L "$target" ]]; then
    target_real="$(real_path "$target")"
    if [[ "$target_real" == "$root_real" ]]; then
      echo "✓ $agent Skill 已指向当前目录：$target"
      return 0
    fi
    backup="${target}.backup-$(date +%Y%m%d-%H%M%S)"
    mv "$target" "$backup"
    echo "已备份旧 $agent Skill：$backup"
  fi
  ln -s "$ROOT" "$target"
  echo "✓ $agent Skill：$target -> $ROOT"
}

run_env_check() {
  local py
  py="$(venv_python)"
  if [[ ! -x "$py" ]]; then py="$(command -v python3 || true)"; fi
  [[ -n "$py" ]] || { echo "未找到 Python" >&2; return 1; }
  NOVEL_CONFIG_DIR="$CONFIG_DIR" NOVEL_WORKSPACE_ROOT="$WORKSPACE_ROOT" "$py" "$ROOT/scripts/check_environment.py" --force
}

check_pi_config() {
  if ! has_token "$SELECTED_AGENTS" "pi"; then return 0; fi
  if ! command -v pi >/dev/null 2>&1; then
    echo "✗ 未找到 pi 命令"
    return 1
  fi
  if ! pi list 2>/dev/null | grep -q 'pi-mcp-extension'; then
    echo "✗ pi-mcp-extension 未安装"
    return 1
  fi
  "$(venv_python)" - "$HOME/.pi/agent/mcp.json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
try: data=json.loads(p.read_text())
except Exception as exc:
    print(f"✗ pi MCP 配置不可读: {exc}"); raise SystemExit(1)
server=data.get("mcpServers",{}).get("novel-production")
if not isinstance(server,dict):
    print("✗ pi MCP 未注册 novel-production"); raise SystemExit(1)
print("✓ pi-mcp-extension 已安装，novel-production MCP 已注册")
PY
}

check_codex_config() {
  if ! has_token "$SELECTED_AGENTS" "codex"; then return 0; fi
  local codex_bin
  codex_bin="$(codex_binary || true)"
  if [[ -z "$codex_bin" ]]; then
    echo "✗ 未找到 codex 命令"
    return 1
  fi
  local details
  details="$($codex_bin mcp get novel-production 2>/dev/null)" || { echo "✗ Codex MCP 未注册 novel-production"; return 1; }
  printf '%s' "$details" | grep -q 'NOVEL_GENERATION_MODE' || { echo "✗ Codex MCP 缺少进程级 generation mode；执行 ./install.sh --repair --agent codex"; return 1; }
  [[ -f "$HOME/.agents/skills/novel-production/SKILL.md" ]] || { echo "✗ Codex Skill 未安装"; return 1; }
  echo "✓ Codex Skill 与 MCP 已注册"
}

check_hermes_config() {
  if ! has_token "$SELECTED_AGENTS" "hermes"; then return 0; fi
  if ! command -v hermes >/dev/null 2>&1; then
    echo "✗ 未找到 hermes 命令"
    return 1
  fi
  hermes mcp list 2>/dev/null | grep -q 'novel-production' || { echo "✗ Hermes MCP 未注册 novel-production"; return 1; }
  [[ -f "$HOME/.hermes/skills/novel-production/SKILL.md" ]] || { echo "✗ Hermes Skill 未安装"; return 1; }
  echo "✓ Hermes Skill 与 MCP 已注册"
}

full_check() {
  log "环境检查"
  run_env_check
  log "MCP Python 注册检查"
  "$(venv_python)" -c 'from novel_production_mcp.server import mcp; print("✓ Novel Production MCP 可加载")'
  check_pi_config
  check_codex_config
  check_hermes_config
  if [[ "$SKIP_NETWORK" -eq 0 ]]; then
    log "模型连通性检查：extractor"
    (cd "$ROOT/mcp" && NOVEL_CONFIG_DIR="$CONFIG_DIR" NOVEL_WORKSPACE_ROOT="$WORKSPACE_ROOT" uv run novel-production route-test extractor)
    log "模型连通性检查：director"
    (cd "$ROOT/mcp" && NOVEL_CONFIG_DIR="$CONFIG_DIR" NOVEL_WORKSPACE_ROOT="$WORKSPACE_ROOT" uv run novel-production route-test director)
  fi
}

if [[ "$MODE" == "check" ]]; then
  ensure_uv
  if [[ ! -x "$(venv_python)" ]]; then
    warn "MCP 虚拟环境不存在；先执行 ./install.sh 或 ./install.sh --repair"
    exit 2
  fi
  full_check
  exit 0
fi

log "创建公共配置和小说目录"
mkdir -p "$CONFIG_DIR" "$WORKSPACE_ROOT"

ensure_uv
log "准备 Python 3.11+ 与 MCP/CLI 依赖（所有 Agent 共用一套）"
HOST_PY="$(command -v python3 || true)"
PY_SPEC="3.11"
if [[ -n "$HOST_PY" ]] && "$HOST_PY" - <<'PYCHK' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PYCHK
then
  PY_SPEC="$HOST_PY"
else
  uv python install 3.11 >/dev/null
fi
(cd "$ROOT/mcp" && uv sync --python "$PY_SPEC" --extra dev)
PY="$(venv_python)"

API_KEY=""
if [[ -n "${NEXAPORT_API_KEY:-}" ]]; then
  log "检测到当前环境已有 NEXAPORT_API_KEY，不重复询问"
elif [[ -f "$CONFIG_DIR/.env" ]] && grep -q '^NEXAPORT_API_KEY=.' "$CONFIG_DIR/.env"; then
  log "检测到 $CONFIG_DIR/.env 已保存 NEXAPORT_API_KEY，不重复询问"
elif [[ "$NON_INTERACTIVE" -eq 0 && -r /dev/tty ]]; then
  printf '\nNexaPortAI API Key（输入不会回显；直接回车可暂时跳过）：' >/dev/tty
  IFS= read -r -s API_KEY </dev/tty || true
  printf '\n' >/dev/tty
fi

log "生成/保留 Novel Production 公共配置"
SETUP_ARGS=(
  "$ROOT/scripts/setup_config.py"
  --config-dir "$CONFIG_DIR"
  --skill-root "$ROOT"
  --profile "$PROFILE"
  --base-url "$BASE_URL"
  --model "$MODEL"
  --api-mode "$API_MODE"
)
if [[ "$PATCH_EXISTING" -eq 1 ]]; then SETUP_ARGS+=(--patch-existing); fi
if [[ -n "$API_KEY" ]]; then
  printf '%s' "$API_KEY" | "$PY" "${SETUP_ARGS[@]}" --api-key-stdin
  unset API_KEY
else
  "$PY" "${SETUP_ARGS[@]}"
fi

if [[ -z "$SELECTED_AGENTS" ]]; then
  warn "未选择 Agent：仅安装了 Novel Production 公共核心。之后可运行 ./install.sh --agent pi|codex|hermes"
fi

if has_token "$SELECTED_AGENTS" "pi"; then
  log "安装到 pi"
  ensure_skill_link "pi" "$HOME/.pi/agent/skills/novel-production"
  if command -v pi >/dev/null 2>&1; then
    if ! pi list 2>/dev/null | grep -q 'pi-mcp-extension'; then
      echo "自动安装 pi-mcp-extension（用于 MCP 客户端能力）"
      pi install npm:pi-mcp-extension
    else
      echo "pi-mcp-extension 已安装"
    fi
    "$PY" "$ROOT/scripts/configure_pi_mcp.py" \
      --config "$HOME/.pi/agent/mcp.json" \
      --uv "$(command -v uv)" \
      --skill-root "$ROOT" \
      --config-dir "$CONFIG_DIR" \
      --workspace-root "$WORKSPACE_ROOT" \
      --generation-mode "$PI_GENERATION_MODE"
  else
    warn "已安装 pi Skill，但未找到 pi 命令；跳过 MCP 扩展/注册。安装 pi 后再次执行 ./install.sh --repair --agent pi"
  fi
fi

if has_token "$SELECTED_AGENTS" "codex"; then
  log "安装到 Codex"
  ensure_skill_link "Codex" "$HOME/.agents/skills/novel-production"
  CODEX_BIN="$(codex_binary || true)"
  if [[ -n "$CODEX_BIN" ]]; then
    "$CODEX_BIN" mcp remove novel-production >/dev/null 2>&1 || true
    "$CODEX_BIN" mcp add \
      --env "NOVEL_CONFIG_DIR=$CONFIG_DIR" \
      --env "NOVEL_WORKSPACE_ROOT=$WORKSPACE_ROOT" \
      --env "NOVEL_GENERATION_MODE=$CODEX_GENERATION_MODE" \
      novel-production -- \
      "$(command -v uv)" --directory "$ROOT/mcp" run novel-production-mcp
    echo "✓ Codex MCP 已注册：$CODEX_BIN"
  else
    warn "已安装 Codex Skill，但未找到 codex 命令；跳过 MCP 注册。安装 Codex 后再次执行 ./install.sh --repair --agent codex"
  fi
fi

if has_token "$SELECTED_AGENTS" "hermes"; then
  log "安装到 Hermes"
  ensure_skill_link "Hermes" "$HOME/.hermes/skills/novel-production"
  if command -v hermes >/dev/null 2>&1; then
    hermes mcp remove novel-production >/dev/null 2>&1 || true
    # Hermes 的 add 会先探测工具，再询问是否全部启用；空行表示默认全部启用。
    printf '\n' | hermes mcp add novel-production \
      --command "$(command -v uv)" \
      --env "NOVEL_CONFIG_DIR=$CONFIG_DIR" \
      --env "NOVEL_WORKSPACE_ROOT=$WORKSPACE_ROOT" \
      --env "NOVEL_GENERATION_MODE=$HERMES_GENERATION_MODE" \
      --args --directory "$ROOT/mcp" run novel-production-mcp
    if hermes mcp list 2>/dev/null | grep -q 'novel-production'; then
      echo "✓ Hermes MCP 已注册"
    else
      echo "Hermes MCP 自动注册失败。请确认 Hermes 标准安装包含 MCP 支持，然后运行：./install.sh --repair --agent hermes" >&2
      exit 1
    fi
  else
    warn "已安装 Hermes Skill，但未找到 hermes 命令；跳过 MCP 注册。安装 Hermes 后再次执行 ./install.sh --repair --agent hermes"
  fi
fi

log "运行确定性自检"
NOVEL_CONFIG_DIR="$CONFIG_DIR" NOVEL_WORKSPACE_ROOT="$WORKSPACE_ROOT" "$PY" "$ROOT/scripts/check_environment.py" --force || true

log "安装完成"
echo "已选择 Agent: ${SELECTED_AGENTS:-仅核心}"
echo "默认 NexaPortAI Base URL: $BASE_URL"
echo "默认 NexaPortAI model: $MODEL"
echo "默认 NexaPortAI API mode: $API_MODE"
echo "公共配置目录: $CONFIG_DIR"
echo "公共小说目录: $WORKSPACE_ROOT"
echo "API Key 可保存在: $CONFIG_DIR/.env (权限 0600)，无需手动 export/source。"
if has_token "$SELECTED_AGENTS" "pi"; then echo "pi：重启后用 /mcp，再用 /skill:novel-production"; fi
if has_token "$SELECTED_AGENTS" "pi"; then echo "pi generation mode：${PI_GENERATION_MODE}"; fi
if has_token "$SELECTED_AGENTS" "codex"; then echo "Codex：重启后用 codex mcp list，再调用 \$novel-production（generation mode：${CODEX_GENERATION_MODE}）"; fi
if has_token "$SELECTED_AGENTS" "hermes"; then echo "Hermes：新会话或 /reload-mcp 后调用 /novel-production"; fi
if has_token "$SELECTED_AGENTS" "hermes"; then echo "Hermes generation mode：${HERMES_GENERATION_MODE}"; fi
echo "完整检查: $ROOT/install.sh --check --agent ${SELECTED_AGENTS:-all}"
echo "自动修复: $ROOT/install.sh --repair --agent ${SELECTED_AGENTS:-all}"
