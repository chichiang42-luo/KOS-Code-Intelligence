from __future__ import annotations

from pathlib import Path

from .indexer import index_repo
from .storage import Store


def create_app(repo_path: Path | str = ".", store_root: Path | str | None = None):
    try:
        from fastapi import FastAPI, HTTPException, Query
    except ModuleNotFoundError as exc:
        raise RuntimeError("FastAPI is not installed. Install with: pip install fastapi uvicorn") from exc

    repo = Path(repo_path).resolve()
    storage_root = Path(store_root).resolve() if store_root else repo
    app = FastAPI(title="KOS Code Intelligence API", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/index/repo")
    def api_index_repo(payload: dict | None = None) -> dict:
        payload = payload or {}
        target = Path(payload.get("repo_path") or repo).resolve()
        output = Path(payload.get("store_root") or storage_root).resolve()
        return index_repo(target, payload.get("repo_id"), output)

    @app.get("/search")
    def search(q: str = Query(...), limit: int = 20) -> list[dict]:
        with_store = Store(storage_root)
        try:
            return with_store.search(q, limit)
        finally:
            with_store.close()

    @app.get("/nodes/{node_id}")
    def get_node(node_id: str) -> dict:
        store = Store(storage_root)
        try:
            node = store.get_node(node_id)
        finally:
            store.close()
        if not node:
            raise HTTPException(status_code=404, detail="node not found")
        return node

    @app.get("/edges/{edge_id}")
    def get_edge(edge_id: str) -> dict:
        store = Store(storage_root)
        try:
            edge = store.get_edge(edge_id)
        finally:
            store.close()
        if not edge:
            raise HTTPException(status_code=404, detail="edge not found")
        return edge

    @app.get("/history/{entity_id}")
    def history(entity_id: str) -> list[dict]:
        store = Store(storage_root)
        try:
            return store.history(entity_id)
        finally:
            store.close()

    @app.get("/graph/neighborhood")
    def neighborhood(node_id: str, hops: int = 1, edge_types: str = "") -> dict:
        types = [item.strip() for item in edge_types.split(",") if item.strip()] or None
        store = Store(storage_root)
        try:
            return store.neighborhood(node_id, hops, types)
        finally:
            store.close()

    @app.get("/graph/path")
    def path(src_id: str, dst_id: str, max_hops: int = 2) -> dict:
        store = Store(storage_root)
        try:
            return store.path(src_id, dst_id, max_hops)
        finally:
            store.close()

    return app


try:
    app = create_app(Path.cwd())
except RuntimeError:
    app = None
