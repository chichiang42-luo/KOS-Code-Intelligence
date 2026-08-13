from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import init_kos_dir
from .evaluator import run_evaluation
from .service import KosService
from .storage import StoreError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kos", description="Local code intelligence for coding agents")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init", help="Create KOS storage directories")
    add_repo_options(init_cmd, include_repo_id=False)

    index_cmd = sub.add_parser("index", help="Build a full multi-language code index")
    add_repo_options(index_cmd)
    index_cmd.add_argument("--rebuild", action="store_true", help="Back up and replace an incompatible index")

    update_cmd = sub.add_parser("update", help="Incrementally update the code index")
    add_repo_options(update_cmd)

    status_cmd = sub.add_parser("status", help="Report index freshness")
    add_repo_options(status_cmd)

    languages_cmd = sub.add_parser("languages", help="List supported and indexed languages")
    add_repo_options(languages_cmd, include_repo_id=False)

    doctor_cmd = sub.add_parser("doctor", help="Run installation and index diagnostics")
    add_repo_options(doctor_cmd)

    eval_cmd = sub.add_parser("eval", help="Run a versioned KOS evaluation suite")
    add_repo_options(eval_cmd)
    eval_cmd.add_argument("--cases", required=True, help="Path to an evaluation JSON file")

    search_cmd = sub.add_parser("search", help="Search symbols and paths")
    add_repo_options(search_cmd, include_repo_id=False)
    search_cmd.add_argument("query")
    search_cmd.add_argument("--limit", type=int, default=20)

    show_cmd = sub.add_parser("show", help="Show a node or edge")
    add_repo_options(show_cmd, include_repo_id=False)
    show_cmd.add_argument("entity_type", choices=["node", "edge"])
    show_cmd.add_argument("entity_id")

    history_cmd = sub.add_parser("history", help="Show entity history")
    add_repo_options(history_cmd, include_repo_id=False)
    history_cmd.add_argument("entity_id")

    neighborhood_cmd = sub.add_parser("neighborhood", help="Show a local graph slice")
    add_repo_options(neighborhood_cmd, include_repo_id=False)
    neighborhood_cmd.add_argument("node_id")
    neighborhood_cmd.add_argument("--hops", type=int, default=1)
    neighborhood_cmd.add_argument("--edge-types", default="")

    path_cmd = sub.add_parser("path", help="Find a short directed path")
    add_repo_options(path_cmd, include_repo_id=False)
    path_cmd.add_argument("src_id")
    path_cmd.add_argument("dst_id")
    path_cmd.add_argument("--max-hops", type=int, default=2)

    for command, help_text in [
        ("agent-resolve", "Resolve a symbol or path"),
        ("agent-who-calls", "Return incoming CALLS facts"),
        ("agent-calls", "Return outgoing CALLS facts"),
        ("agent-impact", "Return one-hop impact facts"),
        ("agent-read-plan", "Return files to read before editing"),
        ("agent-pack", "Return the default agent context pack"),
    ]:
        command_parser = sub.add_parser(command, help=help_text)
        add_repo_options(command_parser, include_repo_id=False)
        command_parser.add_argument("query")
        command_parser.add_argument("--limit-files", type=int, default=10)
        command_parser.add_argument("--limit-facts", type=int, default=40)

    serve_cmd = sub.add_parser("serve", help="Start the experimental REST API")
    add_repo_options(serve_cmd, include_repo_id=False)
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8031)

    args = parser.parse_args(argv)
    service = service_from_args(args)

    try:
        if args.command == "init":
            init_kos_dir(service.store_root)
            print_json({"status": "ok", "store_root": str(service.store_root)})
            return 0
        if args.command == "index":
            print_json(service.index(rebuild=args.rebuild))
            return 0
        if args.command == "update":
            print_json(service.update())
            return 0
        if args.command == "status":
            result = service.status()
            print_json(result)
            return int(result.get("status") == "error")
        if args.command == "languages":
            result = service.languages()
            print_json(result)
            return int(result.get("status") == "error")
        if args.command == "doctor":
            result = service.doctor()
            print_json(result)
            return int(result["status"] != "ok")
        if args.command == "eval":
            result = run_evaluation(service, Path(args.cases).resolve())
            print_json(result)
            return int(result["status"] != "ok")
        if args.command == "serve":
            return serve(service, args.host, args.port)
        if args.command.startswith("agent-"):
            result = run_agent_command(service, args)
            print_json(result)
            return int(result["status"] == "error")
        return run_graph_command(service, args)
    except (OSError, ValueError, StoreError) as exc:
        print_json(
            {
                "status": "error",
                "error": {
                    "code": exc.__class__.__name__,
                    "message": str(exc),
                    "hint": "Run `kos doctor` for diagnostics.",
                },
            }
        )
        return 1


def add_repo_options(parser: argparse.ArgumentParser, include_repo_id: bool = True) -> None:
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--store-root", default=None, help="Directory containing .kos storage")
    if include_repo_id:
        parser.add_argument("--repo-id", default=None, help="Stable repository id")


def service_from_args(args: argparse.Namespace) -> KosService:
    return KosService(
        repo_path=Path(getattr(args, "repo", ".")).resolve(),
        store_root=Path(args.store_root).resolve() if getattr(args, "store_root", None) else None,
        repo_id=getattr(args, "repo_id", None),
    )


def run_agent_command(service: KosService, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "agent-resolve":
        return service.resolve(args.query)
    if args.command == "agent-who-calls":
        return service.who_calls(args.query, args.limit_facts)
    if args.command == "agent-calls":
        return service.calls(args.query, args.limit_facts)
    if args.command == "agent-impact":
        return service.impact(args.query, args.limit_facts)
    if args.command == "agent-read-plan":
        return service.read_plan(args.query, args.limit_files, args.limit_facts)
    return service.pack(args.query, args.limit_files, args.limit_facts)


def run_graph_command(service: KosService, args: argparse.Namespace) -> int:
    state = service.status()
    if state.get("status") == "error" or state["state"] in {"uninitialized", "incompatible"}:
        print_json(
            {
                "status": "error",
                "error": {
                    "code": f"index_{state['state']}",
                    "message": f"KOS index is {state['state']}",
                    "hint": "Run `kos update` or `kos index --rebuild`.",
                },
            }
        )
        return 1
    with service.open_store() as store:
        if args.command == "search":
            result: Any = store.search(args.query, args.limit)
        elif args.command == "show":
            result = store.get_node(args.entity_id) if args.entity_type == "node" else store.get_edge(args.entity_id)
            result = result or {"status": "not_found", "entity_id": args.entity_id}
        elif args.command == "history":
            result = store.history(args.entity_id)
        elif args.command == "neighborhood":
            edge_types = [item.strip() for item in args.edge_types.split(",") if item.strip()] or None
            result = store.neighborhood(args.node_id, args.hops, edge_types)
        else:
            result = store.path(args.src_id, args.dst_id, args.max_hops)
    print_json(result)
    return 0


def serve(service: KosService, host: str, port: int) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError:
        print_json(
            {
                "status": "error",
                "error": {
                    "code": "missing_dependency",
                    "message": "FastAPI and Uvicorn are not installed.",
                    "hint": 'Install with `pip install "kos-code-intelligence[api]"`.',
                },
            }
        )
        return 1
    from .api import create_app

    uvicorn.run(create_app(service.repo_path, service.store_root), host=host, port=port)
    return 0


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
