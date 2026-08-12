# 模型路由与 Provider 协议

全局配置：

```text
~/.config/novel-production/providers.toml
~/.config/novel-production/routes.toml
```

工作区默认不创建 `routes.toml`，直接使用上述全局配置。只有明确需要单本书覆盖路由时，才手工创建 `<workspace>/routes.toml`。密钥使用 `api_key_env` 引用环境变量或配置目录 `.env`。

## Provider API mode

每个 Provider 可独立选择协议：

```toml
api_mode = "chat_completions"  # POST <base_url>/chat/completions
api_mode = "responses"         # POST <base_url>/responses
stream = true                   # v2.7 默认启用 SSE；不支持流式的网关可设为 false
```

未填写 `api_mode` 时，为兼容旧配置，默认使用 `chat_completions`。

v2.7 新安装默认：

```text
NexaPortAI -> responses
Ollama     -> chat_completions
oMLX       -> chat_completions

两种协议都会优先解析 SSE 增量事件；如果供应商忽略 `stream=true` 并返回完整 JSON，也会正常解析。524/超时在接管分析中会触发批次拆分；备用路由仍通过 `fallback_routes` 配置。
```

Responses 模式会发送 `input` 与 `max_output_tokens`，并从 Responses API 的 `output[].content[].output_text` 提取正文；Chat Completions 模式继续发送 `messages` 与 `max_tokens`，从 `choices[0].message.content` 提取正文。两种模式都兼容 usage token 统计。

常用地址：

```text
NexaPortAI https://api.nexaportai.com/v1
Ollama     http://127.0.0.1:11434/v1
oMLX       http://127.0.0.1:8000/v1
```

路由支持：

```toml
provider = "nexaport"
model = "gpt-5.6-sol"
temperature = 0.1
max_tokens = 6000
timeout_sec = 600
retries = 1
fallback_routes = ["reviewer_backup"]
input_cost_per_million = 0
output_cost_per_million = 0
```

设置单价后，`novel_observability_report` 会汇总估算成本。供应商未返回 usage 时 token 和成本显示为 0，不会伪造数字。

## 生成模式：external vs agent

先调用 `novel_generation_mode`，再 `novel_list_routes`。以返回的 `mode` / `effective_source` / `mode_source` 为准，不要把 `configured_external_routes` 误报为当前执行供应商。

### external（默认全局配置）

MCP 按 `providers.toml` / `routes.toml` 调用外部模型。正式生成前可测：

- 规划/接管：`extractor`、`director`
- 正文前：`writer`

使用 `novel_test_route`。

### agent（宿主模型）

```toml
[generation]
mode = "agent"
```

或进程级：`NOVEL_GENERATION_MODE=agent`。

安装器默认：Codex → `agent`；pi / Hermes → `external`。进程级覆盖优先于工作区 `routes.toml`，避免多 Agent 串扰。可用 `./install.sh --codex-mode / --pi-mode / --hermes-mode` 调整。

Agent 模式下：

1. 禁止调用 `novel_test_route`（会跳过外网并返回 `current-agent`）。
2. 生成类工具返回 `agent_task`。
3. 宿主 Agent 读取 `messages`，生成符合 `response_format` 的**原始**内容。
4. `novel_submit_agent_task(task_id, content)` 交回 MCP；由 MCP 校验、落盘、记事件、推进状态。
5. 不要把 `agent_task` 包装对象当作生成内容提交。
6. 准备→提交必须在同一 MCP 进程；重启会丢失内存中的续接回调。

必需路由名：`director`、`macro`、`world`、`characters`、`volume`、`outline`、`writer`、`reviewer`、`repairer`、`extractor`。

推荐：强推理模型做导演/规划/审校/提取；本地或长文模型做正文与定点修复。密钥只放进程环境或 `NOVEL_CONFIG_DIR/.env`（0600），绝不写入工作区、Skill 或聊天。
