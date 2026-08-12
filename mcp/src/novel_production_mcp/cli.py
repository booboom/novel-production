from __future__ import annotations

import argparse
import json
import sys

from . import core
from .evals import run_deterministic_evals
from .writing_gates import run_writing_gate_evals


def main() -> None:
    parser = argparse.ArgumentParser(prog="novel-production")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("workspace_id")
    create.add_argument("title")
    create.add_argument("--genre", default="")
    create.add_argument("--premise", default="")
    create.add_argument("--chapters", type=int, default=30)

    takeover_import = sub.add_parser("takeover-import")
    takeover_import.add_argument("workspace_id")
    takeover_import.add_argument("title")
    takeover_import.add_argument("source_path")
    takeover_import.add_argument("--genre", default="")
    takeover_import.add_argument("--planned-chapters", type=int, default=0)

    takeover_analyze = sub.add_parser("takeover-analyze")
    takeover_analyze.add_argument("workspace_id")
    takeover_analyze.add_argument("--batch-size", type=int, default=5)
    takeover_analyze.add_argument("--max-chars", type=int, default=42000)
    takeover_analyze.add_argument("--force", action="store_true")

    takeover_proposal = sub.add_parser("takeover-proposal")
    takeover_proposal.add_argument("workspace_id")
    takeover_proposal.add_argument("--instruction", default="")

    takeover_apply = sub.add_parser("takeover-apply")
    takeover_apply.add_argument("workspace_id")
    takeover_apply.add_argument("--proposal", default="analysis/reports/takeover-proposal.json")
    takeover_apply.add_argument("--accept-unresolved-conflicts", action="store_true")

    takeover_status = sub.add_parser("takeover-status")
    takeover_status.add_argument("workspace_id")

    continuation_plan = sub.add_parser("continuation-plan")
    continuation_plan.add_argument("workspace_id")
    continuation_plan.add_argument("--count", type=int, default=30)
    continuation_plan.add_argument("--batch-size", type=int, default=10)
    continuation_plan.add_argument("--instruction", default="")

    continuation_apply = sub.add_parser("continuation-apply")
    continuation_apply.add_argument("workspace_id")
    continuation_apply.add_argument("proposal")

    ending_target = sub.add_parser("ending-target")
    ending_target.add_argument("workspace_id")
    ending_target.add_argument("ideal", type=int)
    ending_target.add_argument("--min", dest="minimum", type=int, default=0)
    ending_target.add_argument("--max", dest="maximum", type=int, default=0)
    ending_target.add_argument("--brief", default="")

    ending_options = sub.add_parser("ending-options")
    ending_options.add_argument("workspace_id")
    ending_options.add_argument("--instruction", default="")

    ending_apply = sub.add_parser("ending-apply")
    ending_apply.add_argument("workspace_id")
    ending_apply.add_argument("proposal")
    ending_apply.add_argument("--option", default="")

    ending_budget = sub.add_parser("ending-budget")
    ending_budget.add_argument("workspace_id")
    ending_budget.add_argument("--final-arc-start", type=int, default=0)

    ending_progress = sub.add_parser("ending-progress")
    ending_progress.add_argument("workspace_id")

    final_arc = sub.add_parser("final-arc")
    final_arc.add_argument("workspace_id")
    final_arc.add_argument("--start", type=int, default=0)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("workspace_id")
    finalize.add_argument("--force", action="store_true")

    for name in ("status", "validate", "migrate", "reindex", "observability", "events", "rebuild"):
        command = sub.add_parser(name)
        command.add_argument("workspace_id")
    sub.choices["events"].add_argument("--limit", type=int, default=100)
    sub.choices["events"].add_argument("--type", default="")
    sub.choices["rebuild"].add_argument("--prefix", default="")

    packet = sub.add_parser("packet")
    packet.add_argument("workspace_id")
    packet.add_argument("chapter", type=int)

    export = sub.add_parser("export")
    export.add_argument("workspace_id")
    export.add_argument("--format", choices=("txt", "md"), default="txt")
    export.add_argument("--output", dest="output_path", default="")

    run = sub.add_parser("run")
    run.add_argument("workspace_id")
    run.add_argument("chapter", type=int)
    run.add_argument("--no-commit", action="store_true")

    batch = sub.add_parser("batch")
    batch.add_argument("workspace_id")
    batch.add_argument("start", type=int)
    batch.add_argument("count", type=int)
    batch.add_argument("--no-commit", action="store_true")

    search = sub.add_parser("search")
    search.add_argument("workspace_id")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=6)

    route_test = sub.add_parser("route-test")
    route_test.add_argument("route_name")
    routes = sub.add_parser("routes")
    routes.add_argument("--workspace-id", default="")

    sub.add_parser("evals")
    sub.add_parser("writing-evals")

    writing_scan = sub.add_parser("writing-reference-scan")
    writing_scan.add_argument("workspace_id")
    writing_scan.add_argument("source_path")
    sub.add_parser("writing-index-rebuild")
    writing_rule = sub.add_parser("writing-rule")
    writing_rule.add_argument("workspace_id")
    writing_rule.add_argument("query")

    exemplars = sub.add_parser("exemplars")
    exemplars.add_argument("workspace_id")
    exemplars.add_argument("--enabled-only", action="store_true")

    scene_prepare = sub.add_parser("scene-prepare")
    scene_prepare.add_argument("workspace_id")
    scene_prepare.add_argument("chapter", type=int)
    scene_prepare.add_argument("--scene-json", default="{}")

    scene_save = sub.add_parser("scene-checkpoint-save")
    scene_save.add_argument("workspace_id")
    scene_save.add_argument("chapter", type=int)
    scene_save.add_argument("scene_index", type=int)
    scene_save.add_argument("--draft", default="")

    scene_load = sub.add_parser("scene-checkpoint-load")
    scene_load.add_argument("workspace_id")
    scene_load.add_argument("chapter", type=int)

    scene_assemble = sub.add_parser("scene-assemble")
    scene_assemble.add_argument("workspace_id")
    scene_assemble.add_argument("chapter", type=int)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = core.create_workspace(args.workspace_id, args.title, args.genre, args.premise, args.chapters)
        elif args.command == "takeover-import":
            result = core.import_existing_novel(args.workspace_id, args.title, args.source_path, args.genre, args.planned_chapters)
        elif args.command == "takeover-analyze":
            result = core.analyze_imported_novel(args.workspace_id, args.batch_size, args.max_chars, args.force)
        elif args.command == "takeover-proposal":
            result = core.generate_takeover_proposal(args.workspace_id, args.instruction)
        elif args.command == "takeover-apply":
            result = core.apply_takeover_proposal(args.workspace_id, args.proposal, args.accept_unresolved_conflicts)
        elif args.command == "takeover-status":
            result = core.takeover_status(args.workspace_id)
        elif args.command == "continuation-plan":
            result = core.generate_continuation_plan(
                args.workspace_id,
                args.count,
                args.instruction,
                batch_size=args.batch_size,
            )
        elif args.command == "continuation-apply":
            result = core.apply_continuation_plan(args.workspace_id, args.proposal)
        elif args.command == "ending-target":
            result = core.set_ending_target(args.workspace_id, args.ideal, args.minimum, args.maximum, args.brief)
        elif args.command == "ending-options":
            result = core.generate_ending_options(args.workspace_id, args.instruction)
        elif args.command == "ending-apply":
            result = core.apply_ending_plan(args.workspace_id, args.proposal, args.option)
        elif args.command == "ending-budget":
            result = core.rebalance_story_budget(args.workspace_id, args.final_arc_start)
        elif args.command == "ending-progress":
            result = core.check_ending_progress(args.workspace_id)
        elif args.command == "final-arc":
            result = core.enter_final_arc(args.workspace_id, args.start)
        elif args.command == "finalize":
            result = core.finalize_novel(args.workspace_id, args.force)
        elif args.command == "status":
            result = core.workspace_status(args.workspace_id)
        elif args.command == "validate":
            result = core.validate_workspace_by_id(args.workspace_id)
        elif args.command == "migrate":
            result = core.migrate_workspace(args.workspace_id)
        elif args.command == "packet":
            result = core.build_chapter_packet(args.workspace_id, args.chapter)
        elif args.command == "export":
            result = core.export_current_novel(args.workspace_id, args.format, args.output_path)
        elif args.command == "run":
            result = core.run_chapter_pipeline(args.workspace_id, args.chapter, not args.no_commit)
        elif args.command == "batch":
            result = core.run_batch(args.workspace_id, args.start, args.count, not args.no_commit)
        elif args.command == "search":
            result = core.search_workspace_memory(args.workspace_id, args.query, args.top_k)
        elif args.command == "reindex":
            result = core.reindex_workspace_by_id(args.workspace_id)
        elif args.command == "observability":
            result = core.workspace_observability_report(args.workspace_id)
        elif args.command == "events":
            result = core.workspace_event_log(args.workspace_id, args.limit, args.type)
        elif args.command == "rebuild":
            result = core.rebuild_workspace_projections(args.workspace_id, args.prefix)
        elif args.command == "route-test":
            result = core.test_route_for_workspace(args.route_name, None)
        elif args.command == "routes":
            result = core.list_routes_for_workspace(args.workspace_id or None)
        elif args.command == "evals":
            result = run_deterministic_evals()
        elif args.command == "writing-evals":
            result = run_writing_gate_evals()
        elif args.command == "writing-reference-scan":
            result = core.scan_writing_references(args.workspace_id, args.source_path)
        elif args.command == "writing-index-rebuild":
            result = core.rebuild_writing_index()
        elif args.command == "writing-rule":
            result = core.get_writing_rule(args.workspace_id, args.query)
        elif args.command == "exemplars":
            result = core.list_exemplars(args.workspace_id, args.enabled_only)
        elif args.command == "scene-prepare":
            result = core.prepare_scene_context(args.workspace_id, args.chapter, json.loads(args.scene_json or "{}"))
        elif args.command == "scene-checkpoint-save":
            result = core.save_scene_checkpoint(args.workspace_id, args.chapter, args.scene_index, draft=args.draft)
        elif args.command == "scene-checkpoint-load":
            result = core.load_scene_checkpoints(args.workspace_id, args.chapter)
        elif args.command == "scene-assemble":
            result = core.assemble_chapter_from_scene_checkpoints(args.workspace_id, args.chapter)
        else:
            raise RuntimeError("Unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
