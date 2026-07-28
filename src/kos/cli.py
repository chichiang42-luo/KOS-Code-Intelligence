from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_tools import agent_calls, agent_impact, agent_pack, agent_read_plan, agent_resolve, agent_who_calls
from .config import init_kos_dir
from .indexer import index_repo
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kos", description="KOS Code Intelligence MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init", help="Create .kos storage directories")
    init_cmd.add_argument("--repo", default=".", help="Repository root")

    index_cmd = sub.add_parser("index", help="Index a Python repository")
    index_cmd.add_argument("--repo", default=".", help="Repository root")
    index_cmd.add_argument("--repo-id", default=None, help="Stable repository id")
    index_cmd.add_argument("--store-root", default=None, help="Directory where .kos storage is written")

    search_cmd = sub.add_parser("search", help="Search symbols and paths")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--repo", default=".")
    search_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")
    search_cmd.add_argument("--limit", type=int, default=20)

    show_cmd = sub.add_parser("show", help="Show a node or edge")
    show_cmd.add_argument("entity_type", choices=["node", "edge"])
    show_cmd.add_argument("entity_id")
    show_cmd.add_argument("--repo", default=".")
    show_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")

    hist_cmd = sub.add_parser("history", help="Show entity history")
    hist_cmd.add_argument("entity_id")
    hist_cmd.add_argument("--repo", default=".")
    hist_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")

    neighborhood_cmd = sub.add_parser("neighborhood", help="Show a local graph slice")
    neighborhood_cmd.add_argument("node_id")
    neighborhood_cmd.add_argument("--repo", default=".")
    neighborhood_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")
    neighborhood_cmd.add_argument("--hops", type=int, default=1)
    neighborhood_cmd.add_argument("--edge-types", default="")

    path_cmd = sub.add_parser("path", help="Find a short directed path")
    path_cmd.add_argument("src_id")
    path_cmd.add_argument("dst_id")
    path_cmd.add_argument("--repo", default=".")
    path_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")
    path_cmd.add_argument("--max-hops", type=int, default=2)

    for command, help_text in [
        ("agent-resolve", "Resolve a symbol/path into an agent-friendly node target"),
        ("agent-who-calls", "Return incoming CALLS facts for a target"),
        ("agent-calls", "Return outgoing CALLS facts for a target"),
        ("agent-impact", "Return one-hop impact facts for a target"),
        ("agent-read-plan", "Return files an agent should read before editing a target"),
        ("agent-pack", "Return resolve, impact, read-plan, and evidence in one payload"),
    ]:
        agent_cmd = sub.add_parser(command, help=help_text)
        agent_cmd.add_argument("query")
        agent_cmd.add_argument("--repo", default=".")
        agent_cmd.add_argument("--store-root", default=None, help="Directory containing .kos storage")
        agent_cmd.add_argument("--limit-files", type=int, default=10)
        agent_cmd.add_argument("--limit-facts", type=int, default=40)

    serve_cmd = sub.add_parser("serve", help="Start REST API")
    serve_cmd.add_argument("--repo", default=".")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8031)

    args = parser.parse_args(argv)
    repo = Path(getattr(args, "repo", ".")).resolve()
    store_root = Path(args.store_root).resolve() if getattr(args, "store_root", None) else repo

    if args.command == "init":
        init_kos_dir(repo)
        print_json({"status": "ok", "kos_dir": str(repo / ".kos")})
        return 0
    if args.command == "index":
        print_json(index_repo(repo, args.repo_id, store_root))
        return 0
    if args.command == "serve":
        return serve(repo, args.host, args.port)

    store = Store(store_root)
    try:
        if args.command.startswith("agent-"):
            print_json(run_agent_command(store, args))
        elif args.command == "search":
            print_json(store.search(args.query, args.limit))
        elif args.command == "show":
            item = store.get_node(args.entity_id) if args.entity_type == "node" else store.get_edge(args.entity_id)
            print_json(item or {"error": "not_found", "entity_id": args.entity_id})
        elif args.command == "history":
            print_json(store.history(args.entity_id))
        elif args.command == "neighborhood":
            edge_types = [item.strip() for item in args.edge_types.split(",") if item.strip()] or None
            print_json(store.neighborhood(args.node_id, args.hops, edge_types))
        elif args.command == "path":
            print_json(store.path(args.src_id, args.dst_id, args.max_hops))
    finally:
        store.close()
    return 0


def run_agent_command(store: Store, args: argparse.Namespace) -> dict:
    if args.command == "agent-resolve":
        return agent_resolve(store, args.query)
    if args.command == "agent-who-calls":
        return agent_who_calls(store, args.query, args.limit_facts)
    if args.command == "agent-calls":
        return agent_calls(store, args.query, args.limit_facts)
    if args.command == "agent-impact":
        return agent_impact(store, args.query, args.limit_facts)
    if args.command == "agent-read-plan":
        return agent_read_plan(store, args.query, args.limit_files, args.limit_facts)
    if args.command == "agent-pack":
        return agent_pack(store, args.query, args.limit_files, args.limit_facts)
    return {"status": "not_found", "query": getattr(args, "query", ""), "target": None, "files": [], "facts": []}


def serve(repo: Path, host: str, port: int) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError:
        print_json({"error": "missing_dependency", "detail": "Install the api extra: fastapi and uvicorn"})
        return 2
    from .api import create_app

    uvicorn.run(create_app(repo), host=host, port=port)
    return 0


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
