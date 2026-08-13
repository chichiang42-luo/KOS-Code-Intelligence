from __future__ import annotations

from pathlib import Path

from .service import KosService


def create_app(repo_path: Path | str = ".", store_root: Path | str | None = None):
    try:
        from fastapi import FastAPI, HTTPException, Query
    except ModuleNotFoundError as exc:
        raise RuntimeError('FastAPI is not installed. Install with: pip install "kos-code-intelligence[api]"') from exc

    service = KosService(repo_path, store_root)
    app = FastAPI(title="KOS Code Intelligence API", version="0.2.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return service.doctor()

    @app.get("/index/status")
    def api_status() -> dict:
        return service.status()

    @app.post("/index/update")
    def api_update() -> dict:
        return service.update()

    @app.post("/index/repo")
    def api_index_repo() -> dict:
        return service.index()

    @app.get("/agent/pack")
    def agent_pack(q: str = Query(...), file_limit: int = 10, fact_limit: int = 40) -> dict:
        return service.pack(q, file_limit, fact_limit)

    @app.get("/search")
    def search(q: str = Query(...), limit: int = 20) -> list[dict]:
        with service.open_store() as store:
            return store.search(q, limit)

    @app.get("/nodes/{node_id}")
    def get_node(node_id: str) -> dict:
        with service.open_store() as store:
            node = store.get_node(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="node not found")
        return node

    @app.get("/edges/{edge_id}")
    def get_edge(edge_id: str) -> dict:
        with service.open_store() as store:
            edge = store.get_edge(edge_id)
        if not edge:
            raise HTTPException(status_code=404, detail="edge not found")
        return edge

    @app.get("/history/{entity_id}")
    def history(entity_id: str) -> list[dict]:
        with service.open_store() as store:
            return store.history(entity_id)

    @app.get("/graph/neighborhood")
    def neighborhood(node_id: str, hops: int = 1, edge_types: str = "") -> dict:
        types = [item.strip() for item in edge_types.split(",") if item.strip()] or None
        with service.open_store() as store:
            return store.neighborhood(node_id, hops, types)

    @app.get("/graph/path")
    def path(src_id: str, dst_id: str, max_hops: int = 2) -> dict:
        with service.open_store() as store:
            return store.path(src_id, dst_id, max_hops)

    return app


try:
    app = create_app(Path.cwd())
except RuntimeError:
    app = None
