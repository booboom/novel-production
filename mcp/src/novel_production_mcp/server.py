from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import core
from .agent_mode import (
    AgentTaskPending,
    agent_resume_factory_context,
    agent_task_status,
    resume_agent_task,
)
from .evals import run_deterministic_evals
from .models import ProgressCallback

INSTRUCTIONS = (
    "Novel Production manages long-form novel workspaces. Never let a writer model edit canon or state directly. "
    "Use proposal→human apply for planning; use packet→draft→independent review→targeted repair→semantic delta validation→transactional commit for chapters. "
    "SQLite events are the durable source of truth and files are rebuildable projections. Select bounded Writing Rule Cards before drafting, require evidence-backed structured Writing Gates, target only failed gates during repair, and keep these semantic literary checks separate from deterministic Canon validation. For an existing novel, import original text read-only, analyze in evidence-backed batches, synthesize a human-reviewed takeover proposal, establish a continuation boundary, then set an ending target before planning future chapters. Treat ENDING PLAN and STORY BUDGET as hard constraints: contiguous volume/outline coverage, no chapters beyond max, final-arc no-new-long-lines gates, and completion audit. Stop on blocking validation issues or pending canon changes. In Agent Native mode, generation tools return agent_task; read its messages, generate the raw content with the host Agent, and submit it through novel_submit_agent_task."
)

mcp = FastMCP("Novel Production", instructions=INSTRUCTIONS, json_response=True)

HEARTBEAT_INTERVAL_SECONDS = 15.0
RESULT_STRING_LIMIT = 600
RESULT_LIST_PREVIEW = 5


def _compact_tool_value(value: Any, *, key: str = "") -> Any:
    """Keep MCP chat responses useful without echoing large workspace payloads."""
    if key == "agent_task":
        # The host Agent needs the full prompt/messages to complete this task.
        return value
    if isinstance(value, str):
        if len(value) <= RESULT_STRING_LIMIT:
            return value
        return value[:RESULT_STRING_LIMIT] + "…（已截断，完整内容已写入工作区文件）"
    if isinstance(value, list):
        if len(value) <= RESULT_LIST_PREVIEW:
            return [_compact_tool_value(item) for item in value]
        return {
            "count": len(value),
            "preview": [_compact_tool_value(item) for item in value[:RESULT_LIST_PREVIEW]],
            "truncated": True,
        }
    if isinstance(value, dict):
        if key != "agent_task" and "task_id" in value and "messages" in value:
            compacted = {
                name: _compact_tool_value(item, key=str(name))
                for name, item in value.items()
                if name != "messages"
            }
            compacted["message_count"] = len(value.get("messages", []))
            return compacted
        return {
            str(name): _compact_tool_value(item, key=str(name))
            for name, item in value.items()
        }
    return value


def _compact_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    compacted = _compact_tool_value(result)
    if not isinstance(compacted, dict):
        return {"status": "completed", "result": compacted}
    if "agent_task" in compacted:
        compacted.setdefault("status", "waiting_for_agent")
    else:
        compacted.setdefault("status", "completed")
    return compacted


class _ProgressReporter:
    """Bridge progress callbacks from worker threads back to the MCP event loop."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._pending: list[Any] = []

    async def _emit(
        self,
        message: str,
        progress: float | None = None,
        total: float | None = 100.0,
    ) -> None:
        # Progress is advisory. A client disconnect must never turn a completed
        # core operation into a failed novel-production request.
        with contextlib.suppress(Exception):
            await self.ctx.info(message)
        if progress is not None:
            with contextlib.suppress(Exception):
                await self.ctx.report_progress(progress, total, message)

    def report(
        self,
        message: str,
        progress: float | None = None,
        total: float | None = 100.0,
    ) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(self._emit(message, progress, total), self.loop)
        except RuntimeError:
            return
        with self._lock:
            self._pending.append(future)

    async def emit(
        self,
        message: str,
        progress: float | None = None,
        total: float | None = 100.0,
    ) -> None:
        await self._emit(message, progress, total)

    async def drain(self) -> None:
        with self._lock:
            pending = self._pending[:]
            self._pending.clear()
        if pending:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in pending),
                return_exceptions=True,
            )


async def _heartbeat(reporter: _ProgressReporter, operation_started: float, message: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        elapsed = int(time.monotonic() - operation_started)
        minutes, seconds = divmod(elapsed, 60)
        await reporter.emit(f"心跳：{message}（已运行 {minutes}分{seconds:02d}秒）")


async def run_with_progress(
    ctx: Context | None,
    operation: Any,
    *,
    start_message: str,
    heartbeat_message: str,
) -> dict[str, Any]:
    """Run any blocking core operation with MCP-visible stages and a periodic heartbeat."""

    def execute(progress_callback: ProgressCallback) -> dict[str, Any]:
        def resume_factory(_task_id: str):
            return lambda _content, next_progress: execute(next_progress)

        with agent_resume_factory_context(resume_factory):
            return operation(progress_callback)

    if ctx is None:
        try:
            return _compact_tool_result(
                await asyncio.to_thread(
                    execute,
                    lambda _message, _progress=None, _total=100.0: None,
                )
            )
        except AgentTaskPending as exc:
            return _compact_tool_result({"agent_task": exc.task})

    reporter = _ProgressReporter(ctx)
    started = time.monotonic()
    await reporter.emit(start_message, 1, 100)
    heartbeat_task = asyncio.create_task(_heartbeat(reporter, started, heartbeat_message))
    try:
        result = await asyncio.to_thread(execute, reporter.report)
    except AgentTaskPending as exc:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await reporter.emit(
            f"当前 Agent 任务已准备：{exc.task['task_id']}；请生成内容后调用 novel_submit_agent_task",
            1,
            100,
        )
        await reporter.drain()
        return _compact_tool_result({"agent_task": exc.task})
    except Exception as exc:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await reporter.emit(f"执行失败：{type(exc).__name__}: {str(exc)[:300]}")
        await reporter.drain()
        raise
    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task
    reporter.report("执行完成：结果已返回", 100, 100)
    await reporter.drain()
    return _compact_tool_result(result)


@mcp.tool()
async def novel_create_workspace(
    workspace_id: str,
    title: str,
    genre: str = "",
    premise: str = "",
    planned_chapters: int = 30,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create an isolated v3 novel workspace with fact IDs, event store, retrieval, observability, ending target, and deterministic story budget."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.create_workspace(workspace_id, title, genre, premise, planned_chapters),
        start_message="已连接 MCP：准备创建小说工作区",
        heartbeat_message="小说工作区创建仍在运行",
    )


@mcp.tool()
async def novel_import_existing_novel(
    workspace_id: str,
    title: str,
    source_path: str,
    genre: str = "",
    planned_chapters: int = 0,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Import an existing UTF-8 novel into a new read-only-source takeover workspace. The original source is never modified."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.import_existing_novel(workspace_id, title, source_path, genre, planned_chapters),
        start_message="已连接 MCP：准备导入已有小说",
        heartbeat_message="已有小说导入仍在运行，正在写入只读原稿和索引",
    )


@mcp.tool()
async def novel_analyze_imported_novel(
    workspace_id: str,
    batch_size: int = 5,
    max_chars: int = 42000,
    force: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyze imported chapters in resumable evidence-backed batches without inventing missing canon."""
    return await run_with_progress(
        ctx,
        lambda progress: core.analyze_imported_novel(
            workspace_id,
            batch_size,
            max_chars,
            force,
            progress_callback=progress,
        ),
        start_message="已连接 MCP：准备提取已有章节证据",
        heartbeat_message="证据分析仍在运行，正在等待当前批次模型返回",
    )


@mcp.tool()
async def novel_generate_takeover_proposal(
    workspace_id: str,
    instruction: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Merge batch evidence into a non-destructive takeover proposal with characters, world, knowledge, items, timeline, plot, foreshadowing, reconstructed outlines, conflicts, and a continuation boundary."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_takeover_proposal(
            workspace_id,
            instruction,
            progress_callback=progress,
        ),
        start_message="已连接 MCP：准备综合接管提案",
        heartbeat_message="Director 综合仍在运行，正在等待模型返回",
    )


@mcp.tool()
async def novel_apply_takeover_proposal(
    workspace_id: str,
    proposal_relative_path: str = "analysis/reports/takeover-proposal.json",
    accept_unresolved_conflicts: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply a human-reviewed takeover proposal transactionally. High/critical unresolved conflicts block by default."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_takeover_proposal(
            workspace_id,
            proposal_relative_path,
            accept_unresolved_conflicts,
        ),
        start_message="已连接 MCP：准备应用接管提案",
        heartbeat_message="接管提案应用仍在运行，正在更新正典和索引",
    )


@mcp.tool()
async def novel_takeover_status(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Show import/analyze/proposal/apply progress, conflicts, and the continuation boundary for an existing-novel takeover."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.takeover_status(workspace_id),
        start_message="已连接 MCP：读取接管状态",
        heartbeat_message="接管状态读取仍在运行",
    )


@mcp.tool()
async def novel_generate_continuation_plan(
    workspace_id: str,
    chapter_count: int = 30,
    instruction: str = "",
    batch_size: int = 10,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Generate a future-only continuation plan in persisted batches; resumes from the first incomplete batch and never overwrites existing chapter outlines."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_continuation_plan(
            workspace_id,
            chapter_count,
            instruction,
            batch_size=batch_size,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备规划后续 {chapter_count} 章",
        heartbeat_message="续写规划仍在运行，正在等待 Director 模型返回",
    )


@mcp.tool()
async def novel_apply_continuation_plan(
    workspace_id: str,
    proposal_relative_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply a reviewed future continuation plan to chapter outlines and future volume files, then enter chapter execution."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_continuation_plan(workspace_id, proposal_relative_path),
        start_message="已连接 MCP：准备应用续写规划",
        heartbeat_message="续写规划应用仍在运行，正在写入章节大纲和卷结构",
    )


@mcp.tool()
async def novel_set_ending_target(
    workspace_id: str,
    ideal_chapter: int,
    min_chapter: int = 0,
    max_chapter: int = 0,
    creative_brief: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Set a fixed ending chapter or min/ideal/max ending range and regenerate the deterministic whole-book story budget."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.set_ending_target(
            workspace_id,
            ideal_chapter,
            min_chapter,
            max_chapter,
            creative_brief,
        ),
        start_message="已连接 MCP：准备设置完结目标",
        heartbeat_message="完结目标设置仍在运行，正在重算全书预算",
    )


@mcp.tool()
async def novel_generate_ending_options(workspace_id: str, instruction: str = "", ctx: Context | None = None) -> dict[str, Any]:
    """Generate evidence-compatible ending options grounded in current canon, character arcs, plot threads, foreshadowing, and story budget."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_ending_options(
            workspace_id,
            instruction,
            progress_callback=progress,
        ),
        start_message="已连接 MCP：准备设计结局方案",
        heartbeat_message="结局设计仍在运行，正在等待 Director 模型返回",
    )


@mcp.tool()
async def novel_apply_ending_plan(
    workspace_id: str,
    proposal_relative_path: str,
    option_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply one reviewed ending option as required outcomes, must-resolve lines, relationship outcomes, and final-arc strategy."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_ending_plan(workspace_id, proposal_relative_path, option_id),
        start_message="已连接 MCP：准备应用结局方案",
        heartbeat_message="结局方案应用仍在运行，正在更新完结约束和索引",
    )


@mcp.tool()
async def novel_rebalance_story_budget(
    workspace_id: str,
    final_arc_start: int = 0,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Recompute the whole-book chapter budget; optionally move the final-arc threshold without changing the ending max."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.rebalance_story_budget(workspace_id, final_arc_start),
        start_message="已连接 MCP：准备重算故事预算",
        heartbeat_message="故事预算重算仍在运行",
    )


@mcp.tool()
async def novel_check_ending_progress(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Report chapters remaining, unresolved nonoptional plot lines/foreshadowing, pending ending outcomes, and finalization readiness."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.check_ending_progress(workspace_id),
        start_message="已连接 MCP：检查完结进度",
        heartbeat_message="完结进度检查仍在运行",
    )


@mcp.tool()
async def novel_enter_final_arc(
    workspace_id: str,
    start_chapter: int = 0,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Explicitly enter the final arc now or at a chosen future chapter; final-arc no-new-long-lines gates become active."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.enter_final_arc(workspace_id, start_chapter),
        start_message="已连接 MCP：准备进入终局",
        heartbeat_message="终局状态切换仍在运行",
    )


@mcp.tool()
async def novel_finalize_novel(
    workspace_id: str,
    force: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run the deterministic completion audit, emit final reports/end states, and lock the project as completed. Force records exceptions."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.finalize_novel(workspace_id, force),
        start_message="已连接 MCP：准备执行完结审计",
        heartbeat_message="完结审计仍在运行，正在生成最终报告",
    )


@mcp.tool()
async def novel_migrate_workspace(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Upgrade a v1/v2 workspace to the v3 event-store, semantic-state, ending-plan, and story-budget architecture."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.migrate_workspace(workspace_id),
        start_message="已连接 MCP：准备迁移工作区",
        heartbeat_message="工作区迁移仍在运行，正在重建事件源和索引",
    )


@mcp.tool()
async def novel_adopt_file_changes(
    workspace_id: str,
    relative_paths: list[str],
    note: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Explicitly approve selected manual UTF-8 edits as new SQLite projections after creating a checkpoint."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.adopt_file_changes(workspace_id, relative_paths, note),
        start_message="已连接 MCP：准备采纳手工文件变更",
        heartbeat_message="手工变更采纳仍在运行，正在创建检查点并重建索引",
    )


@mcp.tool()
async def novel_register_fact(
    workspace_id: str,
    fact_id: str,
    statement: str,
    category: str = "general",
    immutable: bool = False,
    visibility: str = "public",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Register a stable fact ID and public/private/secret visibility through an explicit canon event."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.register_fact(workspace_id, fact_id, statement, category, immutable, visibility),
        start_message="已连接 MCP：准备登记事实",
        heartbeat_message="事实登记仍在运行，正在更新正典索引",
    )


@mcp.tool()
async def novel_register_item(
    workspace_id: str,
    item_id: str,
    name: str,
    description: str = "",
    initial_balances: dict[str, int] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Register an item ID and optional initial character balances in the double-entry item ledger."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.register_item(workspace_id, item_id, name, description, initial_balances),
        start_message="已连接 MCP：准备登记道具",
        heartbeat_message="道具登记仍在运行，正在更新正典索引",
    )


@mcp.tool()
async def novel_set_travel_time(
    workspace_id: str,
    source_location_id: str,
    target_location_id: str,
    minutes: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Set a symmetric minimum travel time between two registered locations."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.set_travel_time(workspace_id, source_location_id, target_location_id, minutes),
        start_message="已连接 MCP：准备设置旅行时间",
        heartbeat_message="旅行时间设置仍在运行，正在更新地点索引",
    )


@mcp.tool()
async def novel_approve_character_ability(
    workspace_id: str,
    character_id: str,
    ability_id: str,
    level: int = 1,
    reason: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Explicitly approve a canon-changing character ability addition and record the decision."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.approve_character_ability(workspace_id, character_id, ability_id, level, reason),
        start_message="已连接 MCP：准备审批角色能力",
        heartbeat_message="角色能力审批仍在运行，正在更新正典索引",
    )


@mcp.tool()
async def novel_workspace_status(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Show stage, chapter counts, deterministic validation, and projection drift status."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.workspace_status(workspace_id),
        start_message="已连接 MCP：读取工作区状态",
        heartbeat_message="工作区状态读取仍在运行",
    )


@mcp.tool()
async def novel_validate_workspace(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Run structural validation and compare file projections with SQLite source of truth."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.validate_workspace_by_id(workspace_id),
        start_message="已连接 MCP：准备校验工作区",
        heartbeat_message="工作区校验仍在运行",
    )


@mcp.tool()
async def novel_list_routes(workspace_id: str = "", ctx: Context | None = None) -> dict[str, Any]:
    """Show the active generation mode, effective source, and configured external fallback routes."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.list_routes_for_workspace(workspace_id or None),
        start_message="已连接 MCP：读取模型路由",
        heartbeat_message="模型路由读取仍在运行",
    )


@mcp.tool()
async def novel_generation_mode(workspace_id: str = "", ctx: Context | None = None) -> dict[str, Any]:
    """Show whether generation uses external providers or the current host Agent."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.generation_mode_for_workspace(workspace_id or None),
        start_message="已连接 MCP：读取生成模式",
        heartbeat_message="生成模式读取仍在运行",
    )


@mcp.tool()
async def novel_generate_stage_proposal(workspace_id: str, stage: str, instruction: str = "", ctx: Context | None = None) -> dict[str, Any]:
    """Generate a non-destructive director, macro, world, character, volume, or outline proposal."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_stage_proposal(
            workspace_id,
            stage,
            instruction,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备生成 {stage} 提案",
        heartbeat_message=f"{stage} 提案仍在运行，正在等待模型返回",
    )


@mcp.tool()
async def novel_submit_agent_task(
    task_id: str,
    content: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Submit content generated by the host Agent; the MCP validates and resumes the pending operation."""
    return await run_with_progress(
        ctx,
        lambda progress: resume_agent_task(task_id, content, progress),
        start_message=f"已连接 MCP：提交当前 Agent 任务 {task_id}",
        heartbeat_message="当前 Agent 结果校验和后续流程仍在运行",
    )


@mcp.tool()
async def novel_agent_task_status(task_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Show a pending Agent-mode task and whether this MCP process can resume it."""
    return await run_with_progress(
        ctx,
        lambda _progress: agent_task_status(task_id),
        start_message=f"已连接 MCP：读取 Agent 任务 {task_id} 状态",
        heartbeat_message="Agent 任务状态读取仍在运行",
    )


@mcp.tool()
async def novel_apply_stage_proposal(
    workspace_id: str,
    proposal_relative_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply a reviewed proposal through a transactional event and rebuild the retrieval index."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_stage_proposal(workspace_id, proposal_relative_path),
        start_message="已连接 MCP：准备应用阶段提案",
        heartbeat_message="阶段提案应用仍在运行，正在更新正典和索引",
    )


@mcp.tool()
async def novel_test_route(
    route_name: str,
    workspace_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Test an external route only in external mode; Agent mode skips without any network request."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.test_route_for_workspace(route_name, workspace_id or None),
        start_message=f"已连接 MCP：准备测试 {route_name} 路由",
        heartbeat_message=f"{route_name} 路由测试仍在运行，正在等待供应商响应",
    )


@mcp.tool()
async def novel_import_writing_reference(
    workspace_id: str,
    source_path: str,
    title: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Import and parse one PDF, EPUB, TXT, or Markdown writing reference without injecting the whole book into chapter prompts."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.import_writing_reference(workspace_id, source_path, title),
        start_message="已连接 MCP：准备导入写作参考书",
        heartbeat_message="写作参考书导入仍在运行，正在解析章节和页码",
    )


@mcp.tool()
async def novel_scan_writing_references(
    workspace_id: str,
    source_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Scan a file or directory incrementally; import new/modified writing references and skip unchanged files."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.scan_writing_references(workspace_id, source_path),
        start_message="已连接 MCP：扫描写作参考书目录",
        heartbeat_message="写作参考书扫描仍在运行",
    )


@mcp.tool()
async def novel_analyze_writing_reference(
    workspace_id: str,
    reference_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyze a parsed writing reference in resumable batches into principles, methods, decision rules, and failure patterns."""
    return await run_with_progress(
        ctx,
        lambda progress: core.analyze_writing_reference(workspace_id, reference_id, progress_callback=progress),
        start_message="已连接 MCP：准备分析写作参考书",
        heartbeat_message="写作参考书分析仍在运行，正在提炼方法论",
    )


@mcp.tool()
async def novel_generate_doctrine_proposal(
    workspace_id: str,
    reference_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Deduplicate analyzed writing principles, detect conflicts, and generate a user-reviewable Rule Card proposal."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_doctrine_proposal(workspace_id, reference_id, progress_callback=progress),
        start_message="已连接 MCP：准备生成 Writing Doctrine 提案",
        heartbeat_message="Writing Doctrine 提案仍在运行，正在去重和检测冲突",
    )


@mcp.tool()
async def novel_apply_doctrine_proposal(
    workspace_id: str,
    proposal_relative_path: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply a confirmed doctrine proposal to the project layer; this never disables Canon gates."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_doctrine_proposal(workspace_id, proposal_relative_path),
        start_message="已连接 MCP：准备应用 Writing Doctrine 提案",
        heartbeat_message="Writing Doctrine 应用仍在运行",
    )


@mcp.tool()
async def novel_list_exemplars(
    workspace_id: str,
    enabled_only: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """List bounded, abstracted Exemplar Cards and their derived index."""
    return await run_with_progress(ctx, lambda _progress: core.list_exemplars(workspace_id, enabled_only), start_message="已连接 MCP：读取 Exemplar Cards", heartbeat_message="Exemplar Cards 读取仍在运行")


@mcp.tool()
async def novel_select_scene_exemplars(
    workspace_id: str,
    scene: dict[str, Any],
    rules: list[dict[str, Any]] | None = None,
    limit: int = 2,
    token_budget: int = 1800,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Select a small scene-matched set of Exemplars from selected Rules and metadata."""
    return await run_with_progress(ctx, lambda _progress: core.select_scene_exemplars(workspace_id, scene, rules, limit=limit, token_budget=token_budget), start_message="已连接 MCP：选择场景 Exemplars", heartbeat_message="场景 Exemplar 选择仍在运行")


@mcp.tool()
async def novel_simulate_character_intents(
    characters: list[dict[str, Any]],
    scene: dict[str, Any],
    workspace_id: str = "",
    enabled: bool = True,
    include_tom: bool = True,
    max_characters: int = 4,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build privacy-filtered, auditable Character Intents without exposing other private intents."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.simulate_character_intents(
            characters,
            scene,
            workspace_id=workspace_id or None,
            enabled=enabled,
            include_tom=include_tom,
            max_characters=max_characters,
        ),
        start_message="已连接 MCP：模拟核心角色意图",
        heartbeat_message="角色意图模拟仍在运行",
    )


@mcp.tool()
async def novel_direct_scene(
    scene: dict[str, Any],
    intents: list[dict[str, Any]],
    workspace_id: str = "",
    enabled: bool = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Resolve colliding intents into an execution plan and lightweight world reaction."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.direct_scene_execution(
            scene,
            intents,
            workspace_id=workspace_id or None,
            enabled=enabled,
        ),
        start_message="已连接 MCP：导演场景执行计划",
        heartbeat_message="场景导演仍在运行",
    )


@mcp.tool()
async def novel_propose_arc_reflection(
    character_id: str,
    events: list[dict[str, Any]],
    workspace_id: str = "",
    enabled: bool = True,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create an evidence-backed Arc Reflection proposal without changing canon."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.propose_arc_reflection(
            character_id,
            events,
            workspace_id=workspace_id or None,
            enabled=enabled,
        ),
        start_message="已连接 MCP：准备人物弧反思提案",
        heartbeat_message="人物弧反思仍在运行",
    )


@mcp.tool()
async def novel_prepare_scene(
    workspace_id: str,
    chapter_number: int,
    scene: dict[str, Any],
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Prepare scene-scoped rules, exemplars, character intents, and director plan."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.prepare_scene_context(workspace_id, chapter_number, scene),
        start_message="已连接 MCP：准备场景上下文",
        heartbeat_message="场景上下文准备仍在运行",
    )


@mcp.tool()
async def novel_save_scene_checkpoint(
    workspace_id: str,
    chapter_number: int,
    scene_index: int,
    draft: str = "",
    intents: list[dict[str, Any]] | None = None,
    execution_plan: dict[str, Any] | None = None,
    state_delta: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Persist a completed scene draft checkpoint for resume without committing the chapter."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.save_scene_checkpoint(
            workspace_id,
            chapter_number,
            scene_index,
            draft=draft,
            intents=intents,
            execution_plan=execution_plan,
            state_delta=state_delta,
            metadata=metadata,
        ),
        start_message="已连接 MCP：保存场景检查点",
        heartbeat_message="场景检查点保存仍在运行",
    )


@mcp.tool()
async def novel_load_scene_checkpoints(
    workspace_id: str,
    chapter_number: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Load scene checkpoint progress for a chapter."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.load_scene_checkpoints(workspace_id, chapter_number),
        start_message="已连接 MCP：读取场景检查点",
        heartbeat_message="场景检查点读取仍在运行",
    )


@mcp.tool()
async def novel_assemble_chapter_from_scenes(
    workspace_id: str,
    chapter_number: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Assemble a chapter draft from completed scene checkpoints."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.assemble_chapter_from_scene_checkpoints(workspace_id, chapter_number),
        start_message="已连接 MCP：合并场景草稿",
        heartbeat_message="场景草稿合并仍在运行",
    )


@mcp.tool()
async def novel_apply_belief_updates(
    workspace_id: str,
    character_id: str,
    updates: list[dict[str, Any]],
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply evidence-backed belief updates to a character dynamic state."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.apply_belief_updates_for_character(workspace_id, character_id, updates),
        start_message="已连接 MCP：应用信念更新",
        heartbeat_message="信念更新仍在运行",
    )

@mcp.tool()
async def novel_list_writing_rules(
    workspace_id: str,
    enabled_only: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """List merged Core + Genre + Project Rule Cards and their source references."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.list_writing_rules(workspace_id, enabled_only),
        start_message="已连接 MCP：读取 Writing Rule Cards",
        heartbeat_message="Writing Rule Cards 读取仍在运行",
    )


@mcp.tool()
async def novel_rebuild_writing_rule_index(ctx: Context | None = None) -> dict[str, Any]:
    """Rebuild the derived global Writing Rule retrieval index from canonical Rule Cards."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.rebuild_writing_index(),
        start_message="已连接 MCP：重建 Writing Rule 索引",
        heartbeat_message="Writing Rule 索引重建仍在运行",
    )


@mcp.tool()
async def novel_get_writing_rule(
    workspace_id: str,
    query: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Query Writing Rules by ID, name, taxonomy tag, genre, or source reference."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.get_writing_rule(workspace_id, query),
        start_message="已连接 MCP：查询 Writing Rule",
        heartbeat_message="Writing Rule 查询仍在运行",
    )


@mcp.tool()
async def novel_enable_writing_rule(workspace_id: str, rule_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Enable one Writing Rule Card in this project."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.enable_writing_rule(workspace_id, rule_id),
        start_message=f"已连接 MCP：启用写作规则 {rule_id}",
        heartbeat_message=f"写作规则 {rule_id} 启用仍在运行",
    )


@mcp.tool()
async def novel_disable_writing_rule(workspace_id: str, rule_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Disable one semantic Writing Rule Card in this project; deterministic Canon gates are unaffected."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.disable_writing_rule(workspace_id, rule_id),
        start_message=f"已连接 MCP：禁用写作规则 {rule_id}",
        heartbeat_message=f"写作规则 {rule_id} 禁用仍在运行",
    )


@mcp.tool()
async def novel_test_writing_gates(ctx: Context | None = None) -> dict[str, Any]:
    """Run bundled bad-case and good-case Writing Gate fixtures, including slow-scene false-positive protection."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.test_writing_gates(),
        start_message="已连接 MCP：运行 Writing Quality 评测",
        heartbeat_message="Writing Quality 评测仍在运行",
    )


@mcp.tool()
async def novel_prepare_chapter(
    workspace_id: str,
    chapter_number: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build a bounded packet with structured canon and supplemental hybrid memory retrieval."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.build_chapter_packet(workspace_id, chapter_number),
        start_message=f"已连接 MCP：准备构建第 {chapter_number} 章生产包",
        heartbeat_message=f"第 {chapter_number} 章生产包仍在运行，正在整理正典和检索结果",
    )


@mcp.tool()
async def novel_generate_chapter_draft(workspace_id: str, chapter_number: int, ctx: Context | None = None) -> dict[str, Any]:
    """Call only the writer route and save a draft with a full model trace."""
    return await run_with_progress(
        ctx,
        lambda progress: core.generate_chapter_draft(
            workspace_id,
            chapter_number,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备生成第 {chapter_number} 章正文",
        heartbeat_message=f"第 {chapter_number} 章正文仍在运行，正在等待 writer 模型返回",
    )


@mcp.tool()
async def novel_review_chapter(workspace_id: str, chapter_number: int, ctx: Context | None = None) -> dict[str, Any]:
    """Run the independent reviewer and save a structured audit with quality telemetry."""
    return await run_with_progress(
        ctx,
        lambda progress: core.review_chapter(
            workspace_id,
            chapter_number,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备审校第 {chapter_number} 章",
        heartbeat_message=f"第 {chapter_number} 章审校仍在运行，正在等待 reviewer 模型返回",
    )


@mcp.tool()
async def novel_audit_existing_chapters(
    workspace_id: str,
    start_chapter: int,
    end_chapter: int,
    force: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run the current semantic gates against committed chapters and write reports without changing正文, canon, state, Delta, or active audits."""
    return await run_with_progress(
        ctx,
        lambda progress: core.audit_existing_chapters(
            workspace_id,
            start_chapter,
            end_chapter,
            force=force,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备只读审计第 {start_chapter}-{end_chapter} 章",
        heartbeat_message=f"第 {start_chapter}-{end_chapter} 章历史只读审计仍在运行",
    )


@mcp.tool()
async def novel_repair_chapter(workspace_id: str, chapter_number: int, ctx: Context | None = None) -> dict[str, Any]:
    """Apply a targeted repair, preserve draft history, and increment the repair round."""
    return await run_with_progress(
        ctx,
        lambda progress: core.repair_chapter(
            workspace_id,
            chapter_number,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备修复第 {chapter_number} 章",
        heartbeat_message=f"第 {chapter_number} 章修复仍在运行，正在等待 repairer 模型返回",
    )


@mcp.tool()
async def novel_extract_chapter_delta(workspace_id: str, chapter_number: int, ctx: Context | None = None) -> dict[str, Any]:
    """Extract fact-ID, utterance, travel, ability, and double-entry item changes, then validate deterministically."""
    return await run_with_progress(
        ctx,
        lambda progress: core.extract_delta(
            workspace_id,
            chapter_number,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备提取第 {chapter_number} 章状态变化",
        heartbeat_message=f"第 {chapter_number} 章状态提取仍在运行，正在等待 extractor 模型返回",
    )


@mcp.tool()
async def novel_commit_chapter(
    workspace_id: str,
    chapter_number: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Commit chapter and all state projections in one SQLite event transaction, then materialize files."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.commit_chapter(workspace_id, chapter_number),
        start_message=f"已连接 MCP：准备提交第 {chapter_number} 章",
        heartbeat_message=f"第 {chapter_number} 章事务提交仍在运行，正在更新投影和索引",
    )


@mcp.tool()
async def novel_export_current_novel(
    workspace_id: str,
    format: str = "txt",
    output_path: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Merge all currently committed chapter正文 into a UTF-8 TXT export by default."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.export_current_novel(workspace_id, format, output_path),
        start_message="已连接 MCP：准备导出当前正文合集",
        heartbeat_message="正文合集导出仍在运行，正在合并已提交章节",
    )


@mcp.tool()
async def novel_run_chapter(workspace_id: str, chapter_number: int, auto_commit: bool = True, ctx: Context | None = None) -> dict[str, Any]:
    """Run packet→writer→review→targeted repair→semantic delta validation→transactional commit."""
    return await run_with_progress(
        ctx,
        lambda progress: core.run_chapter_pipeline(
            workspace_id,
            chapter_number,
            auto_commit,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备运行第 {chapter_number} 章完整流程",
        heartbeat_message=f"第 {chapter_number} 章完整流程仍在运行",
    )


@mcp.tool()
async def novel_run_batch(workspace_id: str, start_chapter: int, count: int, auto_commit: bool = True, ctx: Context | None = None) -> dict[str, Any]:
    """Run sequential chapter production and stop at the first failed gate. Count is limited to 20."""
    return await run_with_progress(
        ctx,
        lambda progress: core.run_batch(
            workspace_id,
            start_chapter,
            count,
            auto_commit,
            progress_callback=progress,
        ),
        start_message=f"已连接 MCP：准备运行第 {start_chapter}-{start_chapter + count - 1} 章",
        heartbeat_message=f"第 {start_chapter}-{start_chapter + count - 1} 章批量流程仍在运行",
    )


@mcp.tool()
async def novel_search_memory(
    workspace_id: str,
    query: str,
    top_k: int = 6,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Search committed chapters and project material with local lexical + hash-vector hybrid retrieval."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.search_workspace_memory(workspace_id, query, top_k),
        start_message="已连接 MCP：准备检索工作区记忆",
        heartbeat_message="工作区记忆检索仍在运行",
    )


@mcp.tool()
async def novel_reindex_memory(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """Rebuild the local hybrid retrieval index from current committed files."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.reindex_workspace_by_id(workspace_id),
        start_message="已连接 MCP：准备重建记忆索引",
        heartbeat_message="记忆索引重建仍在运行，正在扫描工作区文件",
    )


@mcp.tool()
async def novel_observability_report(
    workspace_id: str,
    limit: int = 5000,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Report input hashes, token usage, estimated cost, failures, repair rounds, model comparisons, and quality trend."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.workspace_observability_report(workspace_id, limit),
        start_message="已连接 MCP：准备读取可观测性报告",
        heartbeat_message="可观测性报告读取仍在运行",
    )


@mcp.tool()
async def novel_event_log(
    workspace_id: str,
    limit: int = 100,
    event_type: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """List durable workspace events from the SQLite event store."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.workspace_event_log(workspace_id, limit, event_type),
        start_message="已连接 MCP：读取事件日志",
        heartbeat_message="事件日志读取仍在运行",
    )


@mcp.tool()
async def novel_rebuild_projections(
    workspace_id: str,
    prefix: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Repair missing or modified workspace files from SQLite projections."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.rebuild_workspace_projections(workspace_id, prefix),
        start_message="已连接 MCP：准备重建文件投影",
        heartbeat_message="文件投影重建仍在运行，正在从 SQLite 恢复文件",
    )


@mcp.tool()
async def novel_run_evals(ctx: Context | None = None) -> dict[str, Any]:
    """Run the bundled deterministic regression suite for knowledge, travel, items, abilities, POV fixtures, and state gates."""
    return await run_with_progress(
        ctx,
        lambda _progress: run_deterministic_evals(),
        start_message="已连接 MCP：准备运行确定性评估",
        heartbeat_message="确定性评估仍在运行",
    )


@mcp.tool()
async def novel_checkpoint(
    workspace_id: str,
    label: str = "manual",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create a full rollback checkpoint including the event store."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.checkpoint_workspace(workspace_id, label),
        start_message="已连接 MCP：准备创建回滚检查点",
        heartbeat_message="回滚检查点创建仍在运行，正在复制工作区",
    )


@mcp.tool()
async def novel_list_checkpoints(workspace_id: str, ctx: Context | None = None) -> dict[str, Any]:
    """List rollback checkpoints."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.list_checkpoints(workspace_id),
        start_message="已连接 MCP：读取回滚检查点",
        heartbeat_message="回滚检查点读取仍在运行",
    )


@mcp.tool()
async def novel_restore_checkpoint(
    workspace_id: str,
    checkpoint_name: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Restore a checkpoint, reseed the event store, and rebuild retrieval."""
    return await run_with_progress(
        ctx,
        lambda _progress: core.restore_workspace(workspace_id, checkpoint_name),
        start_message="已连接 MCP：准备恢复工作区检查点",
        heartbeat_message="工作区恢复仍在运行，正在重建事件源和索引",
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
