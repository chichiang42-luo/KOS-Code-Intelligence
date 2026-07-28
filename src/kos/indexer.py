from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import init_kos_dir
from .discovery import discover
from .observation import observe_repo
from .storage import Store, append_jsonl_many


def index_repo(repo_path: Path, repo_id: str | None = None, store_root: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    root = repo_path.resolve()
    storage_root = (store_root or root).resolve()
    init_kos_dir(storage_root)
    rid = repo_id or root.name
    observations = observe_repo(root, rid)
    graph = discover(observations)
    store = Store(storage_root)
    try:
        stats = store.commit_graph(rid, graph.nodes, graph.edges)
    finally:
        store.close()
    append_jsonl_many(storage_root / ".kos" / "logs" / "observations.jsonl", [obs.to_dict() for obs in observations])
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "repo_id": rid,
        "repo_path": str(root),
        "store_root": str(storage_root),
        "status": "ok",
        "files_scanned": len({obs.file_path for obs in observations if obs.kind == "file"}),
        "observations": len(observations),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "parse_errors": len(graph.parse_errors),
        "duration_ms": duration_ms,
        **stats,
    }
