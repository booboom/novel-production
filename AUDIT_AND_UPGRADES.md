# v2.4 自检与升级完成报告

## 一键安装层

v2.4 新增根目录 `install.sh`，目标是把“解压后手工配置十几步”压缩为一次安装：

```bash
cd ~/.pi/agent/skills/novel-production
./install.sh
```

安装器会自动：

- 检测/安装 `uv`；
- 使用现有 Python >=3.11，只有缺失时才让 uv 安装 3.11；
- 安装 MCP/CLI 依赖；
- 创建配置目录和小说工作区；
- 生成默认配置但不覆盖已有用户配置；
- 静默读取 API Key 并保存到 `.env`，权限 0600；
- 检测 pi，安装 `pi-mcp-extension`；
- 保留现有 `mcpServers`，只新增/更新 `novel-production`；
- 配置 eager stdio MCP；
- 运行首次环境检查。

支持：

```text
./install.sh --check
./install.sh --repair
./install.sh --reconfigure
./install.sh --profile cloud
./install.sh --profile hybrid
./install.sh --check --skip-network
```

## NexaPortAI 默认配置

默认 Base URL 已改为：

```text
https://api.nexaportai.com/v1
```

默认 cloud profile 的十个必需路由统一使用：

```text
provider = nexaport
model = gpt-5.6-sol
```

原先本地 Qwen Writer/Repairer 的配置保留为：

```text
config/routes.hybrid.example.toml
```

## 本地 Secret 文件

MCP 引擎新增 `NOVEL_CONFIG_DIR/.env` 读取支持。

优先级：

```text
进程环境变量
→ .env
→ allow_empty_key
```

安装器写入：

```text
~/.config/novel-production/.env
```

并强制 `0600`。真实 Key 不写入 `providers.toml`、`routes.toml`、MCP JSON、小说工作区或日志。

环境检查指纹现在包含 `.env`，因此新增/删除密钥会使缓存自动失效。

## pi MCP 自动注册

基于 pi 当前的 `pi-mcp-extension` 配置格式：

```text
~/.pi/agent/mcp.json
mcpServers.novel-production
transport = stdio
lifecycle = eager
```

配置脚本使用 JSON merge，保留用户原有 MCP server。

## API 请求兼容性

OpenAI-compatible 请求新增：

```text
Accept: application/json
User-Agent: novel-production/2.3
```

并在上游返回 `error code: 1010` 时追加 WAF/Cloudflare 客户端签名提示，避免只显示无上下文错误码。

## CLI 诊断

新增：

```bash
uv run novel-production routes
uv run novel-production route-test extractor
uv run novel-production route-test director
uv run novel-production route-test writer
```

## 测试结果

本次本地回归：

```text
Installer/environment helper tests: 4/4 passed
MCP/core pytest: 24/24 passed
Deterministic eval fixtures: 8/8 passed
Skill metadata: passed
Python compileall: passed
Static MCP tool count: 44
```

新增覆盖包括：

- 默认 NexaPortAI Base URL；
- 默认 cloud model；
- `.env` API Key 加载；
- `.env` 权限 0600；
- API Key 不出现在安装器输出；
- API User-Agent；
- pi MCP JSON merge 不破坏已有 server。

## 沙箱限制

当前执行环境无法联网下载 uv 管理的 Python/MCP 包，因此不能在这里实际执行 `uv sync` 的联网安装或与 pi 的真实第三方扩展启动握手。核心 Python 测试、配置合并测试、MCP 静态注册和确定性 eval 已通过。用户 Mac 正常联网时，`install.sh` 会执行真实 `uv sync` 和 `pi install npm:pi-mcp-extension`。


## v2.6 Provider 双协议升级

- `ProviderConfig.api_mode` 支持 `chat_completions` / `responses`。
- NexaPortAI 新安装默认 `responses`；本地 Ollama/oMLX 默认 `chat_completions`。
- Responses 请求走 `<base_url>/responses`，请求字段为 `input` / `max_output_tokens`。
- Responses 输出从 typed `output` 数组解析 `output_text`。
- 兼容旧配置：缺省 `api_mode` 时仍走 Chat Completions。
- 默认 User-Agent 继续固定为 `novel-production/2.3`。

## v2.7 稳定性升级

- Provider 默认发送 `stream=true`，Responses 与 Chat Completions 均支持 SSE 增量文本解析；供应商返回完整 JSON 时保持兼容。
- HTTP 524、429、408、5xx、网络错误和协议错误分级处理；重试只针对可恢复错误，429 读取 `Retry-After`。
- Provider 最终失败抛出带 `status_codes`、`categories`、`routes` 的 `ProviderError`，供接管流程识别 origin timeout。
- 接管分析将每个批次完成状态即时写入 `analysis/status.yaml`；`source_hash` 一致的已完成批次自动复用。
- 多章节批次遇到 524/超时自动二分拆分；不可继续时记录 `failed_batch` / `next_batch` 并阻止生成不完整提案。
- 批次计划写入 `analysis/plan.json`，持久化父子拆分关系；提案汇总只读取真实批次 JSON，不读取 `*.meta.json`，并校验批次完整性。
- Provider 捕获 SSE 失败事件及 `request_id` / `CF-Ray` 诊断信息；重新分析时旧提案不会继续显示为 ready。
- 验证结果：MCP pytest `32/32`，安装器/环境检查 `9/9`，新增 Provider/接管专项测试覆盖 SSE、524 分类、自适应拆分和恢复缓存。

## 待办：可选 LangChain 模型适配层（暂不实施）

当前继续使用 `provider.py` + `urllib`，不切换运行时和默认 Provider。

后续可评估增加可选 backend：

- OpenAI-compatible 供应商使用 `ChatOpenAI`；Anthropic 使用 `ChatAnthropic`。
- 保留现有路由、重试、fallback、诊断、`ModelResult`、成本记录和接管恢复逻辑。
- 若继续使用当前 Python MCP，优先评估 Python LangChain 适配；不要仅为模型接入而迁移整个 skill 到 TypeScript。
- 先做双 backend 对照测试，覆盖普通 JSON、SSE、Responses、Chat Completions、524、usage、JSON 提取和 fallback，再决定是否切换默认实现。
- 未完成兼容性验证前，不改变现有生产 backend。
