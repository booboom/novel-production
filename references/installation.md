# 安装与首次调用（v2.9）

执行身份：macOS 当前普通用户，不加 `sudo`。

## 一键安装

把完整 Skill 放在一个**不会被随手删除的持久目录**里。最常见的是先放到你正在使用的某个 Agent Skill 目录，例如：

```text
~/.pi/agent/skills/novel-production/
```

然后执行：

```bash
cd ~/.pi/agent/skills/novel-production
./install.sh
```

v2.9 会自动检测：

```text
pi
Codex
Hermes
```

并显示：

```text
检测到以下 Agent：
  [1] pi      ✓ 已检测
  [2] Codex   ✓ 已检测
  [3] Hermes  ✓ 已检测

选择要安装到的 Agent（可多选，如 1,3；a=全部已检测；n=仅安装核心）：
```

可以输入：

```text
1      只安装 pi
2      只安装 Codex
3      只安装 Hermes
1,3    pi + Hermes
a      全部已检测 Agent
n      只安装 Novel Production 公共核心
```

也可以完全跳过交互：

```bash
./install.sh --agent pi
./install.sh --agent codex
./install.sh --agent hermes
./install.sh --agent pi,codex
./install.sh --agent pi,hermes
./install.sh --agent codex,hermes
./install.sh --agent all
```

## 公共核心只安装一次

无论选择几个 Agent，下面这些都是共享的，不会为每个 Agent 重复创建：

```text
~/.config/novel-production/providers.toml
~/.config/novel-production/routes.toml
~/.config/novel-production/.env
~/Novels/
当前 Skill 根目录/mcp/.venv/
```

所有 Agent 因此共用：

- NexaPortAI / Ollama / oMLX Provider；
- API Key；
- 模型路由；
- 小说工作区；
- SQLite 事件数据库；
- 人物、世界观、伏笔、时间线、完结状态；
- 同一套 Novel Production MCP 引擎。

不要同时让两个 Agent 对同一本小说执行写入型任务。

## 默认配置

默认 NexaPortAI：

```text
Base URL: https://api.nexaportai.com/v1
Model:    gpt-5.6-sol
```

安装器会自动：

- 准备 `uv` 与 Python 3.11+；
- 执行 `uv sync --extra dev`；
- 创建 `~/.config/novel-production/`；
- 创建 `~/Novels/`；
- 创建 `providers.toml` / `routes.toml`（已有文件默认不覆盖）；
- 首次缺少 Key 时静默询问；
- 将 Key 保存到 `~/.config/novel-production/.env`，权限 `0600`；
- 为所选 Agent 安装 Skill 入口；
- 为所选 Agent 自动注册 Novel Production MCP；
- 执行环境自检。

## pi

选择 pi 后自动：

```text
Skill: ~/.pi/agent/skills/novel-production
MCP:   ~/.pi/agent/mcp.json
```

若缺少 `pi-mcp-extension`，安装器会自动执行安装。

安装后重启 pi，检查：

```text
/mcp
```

调用：

```text
/skill:novel-production
```

修复：

```bash
./install.sh --repair --agent pi
```

## Codex

选择 Codex 后自动：

```text
Skill: ~/.agents/skills/novel-production
```

安装器通过 Codex 自己的 MCP CLI 注册 stdio server：

```text
novel-production
```

不会手工覆盖整个 `~/.codex/config.toml`。

安装后重启 Codex，检查：

```bash
codex mcp list
```

调用：

```text
$novel-production
```

修复：

```bash
./install.sh --repair --agent codex
```

## Hermes

选择 Hermes 后自动：

```text
Skill: ~/.hermes/skills/novel-production
MCP:   ~/.hermes/config.yaml 中的 mcp_servers.novel-production
```

安装器通过 Hermes 自己的 `hermes mcp add` 完成注册，因此不会自行重写用户的整个 YAML 配置。

安装后可以：

```bash
hermes mcp list
```

当前 Hermes 会话中配置变化后可执行：

```text
/reload-mcp
```

新会话中调用：

```text
/novel-production
```

修复：

```bash
./install.sh --repair --agent hermes
```

## API Key 不需要 export/source

正常安装后 Key 保存在：

```text
~/.config/novel-production/.env
```

因此不需要再手动：

```bash
export NEXAPORT_API_KEY="..."
source ~/.zshrc
```

系统环境变量如果已经存在，仍优先于 `.env`。安装器默认使用进程级 `NOVEL_GENERATION_MODE` 隔离各 Agent：Codex 为 `agent`，pi/Hermes 为 `external`。可通过 `--codex-mode`、`--pi-mode`、`--hermes-mode` 修改；选择全部 Agent 不会再让工作区 `routes.toml` 改变 Codex 的模式。

## Cloud 与 Hybrid

全部使用云模型：

```bash
./install.sh --profile cloud
```

本地 Ollama 负责正文与修复：

```bash
./install.sh --profile hybrid
```

Hybrid 模板：

```text
config/routes.hybrid.example.toml
```

## 检查与修复

检查全部已检测 Agent：

```bash
./install.sh --check
```

只检查某个 Agent：

```bash
./install.sh --check --agent codex
./install.sh --check --agent hermes
```

不进行模型联网测试：

```bash
./install.sh --check --skip-network --agent all
```

修复某个 Agent：

```bash
./install.sh --repair --agent pi
./install.sh --repair --agent codex
./install.sh --repair --agent hermes
```

全部修复：

```bash
./install.sh --repair --agent all
```

更新现有 NexaPort Base URL / 默认模型：

```bash
./install.sh --repair --reconfigure --agent all
```

## 升级建议

如果当前 Skill 实体目录已经是：

```text
~/.pi/agent/skills/novel-production
```

可以继续把它作为主目录。选择 Codex / Hermes 后，安装器会给对应 Agent 创建到当前 Skill 根目录的链接，从而共享同一份 Skill 文件。

因此升级时只需要替换主目录中的 Novel Production 文件，再运行：

```bash
./install.sh --repair --agent all
```

## 接管已有小说

安装完成后：

pi：

```text
/skill:novel-production 接管 /Users/你的用户名/Documents/小说/XXX
```

Codex：

```text
$novel-production 接管 /Users/你的用户名/Documents/小说/XXX
```

Hermes：

```text
/novel-production 接管 /Users/你的用户名/Documents/小说/XXX
```


## Provider 请求协议

v2.7 支持两种 Provider API mode，并默认启用 SSE 流式请求：

```toml
api_mode = "responses"
api_mode = "chat_completions"
stream = true
```

新安装默认 NexaPortAI 使用 `responses`；Ollama/oMLX 使用 `chat_completions`。旧配置缺少 `api_mode` 时按 `chat_completions` 处理。已有安装切换 NexaPort 到 Responses：

如果供应商不支持流式，可在对应 Provider 设置 `stream = false`；否则 v2.7 会优先使用 SSE 并兼容普通 JSON 响应。

```bash
./install.sh --repair --reconfigure --api-mode responses
```

切回 Chat Completions：

```bash
./install.sh --repair --reconfigure --api-mode chat_completions
```
