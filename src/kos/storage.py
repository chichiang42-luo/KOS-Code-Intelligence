from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .ids import stable_id
from .schemas import Edge, HistoryEvent, Node


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.kos_dir = root / ".kos"
        self.logs_dir = self.kos_dir / "logs"
        self.db_path = self.kos_dir / "graph.db"
        self.kos_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._history_buffer: list[dict[str, Any]] = []
        self.init_db()

    def close(self) -> None:
        self.conn.close()

    def init_db(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS nodes_current (
              node_id TEXT PRIMARY KEY,
              repo_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              name TEXT NOT NULL,
              fqname TEXT NOT NULL,
              file_path TEXT,
              status TEXT NOT NULL,
              version INTEGER NOT NULL,
              data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes_current(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_fqname ON nodes_current(fqname);
            CREATE TABLE IF NOT EXISTS edges_current (
              edge_id TEXT PRIMARY KEY,
              src_id TEXT NOT NULL,
              dst_id TEXT NOT NULL,
              rel_type TEXT NOT NULL,
              status TEXT NOT NULL,
              version INTEGER NOT NULL,
              data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_src ON edges_current(src_id);
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges_current(dst_id);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges_current(rel_type);
            CREATE TABLE IF NOT EXISTS history_events (
              event_id TEXT PRIMARY KEY,
              entity_type TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              repo_id TEXT NOT NULL,
              op TEXT NOT NULL,
              timestamp TEXT NOT NULL,
              data TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def commit_graph(self, repo_id: str, nodes: list[Node], edges: list[Edge]) -> dict[str, int]:
        existing_nodes = {row["node_id"]: row for row in self.conn.execute("SELECT * FROM nodes_current WHERE status='active'")}
        existing_edges = {row["edge_id"]: row for row in self.conn.execute("SELECT * FROM edges_current WHERE status='active'")}
        node_ids = {node.node_id for node in nodes}
        edge_ids = {edge.edge_id for edge in edges}
        created_nodes = updated_nodes = deleted_nodes = 0
        created_edges = updated_edges = deleted_edges = 0

        for node in nodes:
            old = existing_nodes.get(node.node_id)
            if old:
                old_data = json.loads(old["data"])
                node.version = int(old["version"]) + (0 if comparable(old_data, node.to_dict()) else 1)
                updated_nodes += 1
                op = "update" if node.version > int(old["version"]) else "reconfirm"
            else:
                created_nodes += 1
                op = "create"
            self._upsert_node(node)
            self._append_history(HistoryEvent(stable_id("evt", node.node_id, op, node.version), "node", node.node_id, repo_id, op, f"node {op}"))

        for edge in edges:
            old = existing_edges.get(edge.edge_id)
            if old:
                old_data = json.loads(old["data"])
                edge.version = int(old["version"]) + (0 if comparable(old_data, edge.to_dict()) else 1)
                updated_edges += 1
                op = "update" if edge.version > int(old["version"]) else "reconfirm"
            else:
                created_edges += 1
                op = "create"
            self._upsert_edge(edge)
            self._append_history(HistoryEvent(stable_id("evt", edge.edge_id, op, edge.version), "edge", edge.edge_id, repo_id, op, f"edge {op}"))

        for node_id in set(existing_nodes) - node_ids:
            self.conn.execute("UPDATE nodes_current SET status='deleted' WHERE node_id=?", (node_id,))
            deleted_nodes += 1
            self._append_history(HistoryEvent(stable_id("evt", node_id, "deleted"), "node", node_id, repo_id, "deleted", "missing from latest index"))

        for edge_id in set(existing_edges) - edge_ids:
            self.conn.execute("UPDATE edges_current SET status='deleted' WHERE edge_id=?", (edge_id,))
            deleted_edges += 1
            self._append_history(HistoryEvent(stable_id("evt", edge_id, "deleted"), "edge", edge_id, repo_id, "deleted", "missing from latest index"))

        self.conn.commit()
        append_jsonl_many(self.logs_dir / "history.jsonl", self._history_buffer)
        self._history_buffer.clear()
        self._write_snapshot("latest_nodes.jsonl", [node.to_dict() for node in nodes])
        self._write_snapshot("latest_edges.jsonl", [edge.to_dict() for edge in edges])
        return {
            "nodes_created": created_nodes,
            "nodes_updated": updated_nodes,
            "nodes_deleted": deleted_nodes,
            "edges_created": created_edges,
            "edges_updated": updated_edges,
            "edges_deleted": deleted_edges,
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{query}%"
        rows = self.conn.execute(
            """
            SELECT data FROM nodes_current
            WHERE status='active' AND (name LIKE ? OR fqname LIKE ? OR file_path LIKE ?)
            ORDER BY CASE WHEN name=? THEN 0 WHEN fqname=? THEN 1 ELSE 2 END, length(fqname)
            LIMIT ?
            """,
            (like, like, like, query, query, limit),
        )
        return [json.loads(row["data"]) for row in rows]

    def find_nodes(self, query: str, strategy: str, limit: int = 20) -> list[dict[str, Any]]:
        if strategy == "fqname_exact":
            sql = "SELECT data FROM nodes_current WHERE status='active' AND fqname=? ORDER BY length(fqname) LIMIT ?"
            params: tuple[Any, ...] = (query, limit)
        elif strategy == "name_exact":
            sql = "SELECT data FROM nodes_current WHERE status='active' AND name=? ORDER BY length(fqname) LIMIT ?"
            params = (query, limit)
        elif strategy == "file_exact":
            normalized = query.replace("\\", "/")
            sql = """
            SELECT data FROM nodes_current
            WHERE status='active' AND (file_path=? OR fqname=?)
            ORDER BY CASE WHEN kind='file' THEN 0 WHEN kind='module' THEN 1 ELSE 2 END, length(fqname)
            LIMIT ?
            """
            params = (normalized, normalized, limit)
        elif strategy == "fuzzy":
            normalized = query.replace("\\", "/")
            like = f"%{normalized}%"
            sql = """
            SELECT data FROM nodes_current
            WHERE status='active' AND (name LIKE ? OR fqname LIKE ? OR file_path LIKE ?)
            ORDER BY CASE WHEN name=? THEN 0 WHEN fqname=? THEN 1 ELSE 2 END, length(fqname)
            LIMIT ?
            """
            params = (like, like, like, query, query, limit)
        else:
            raise ValueError(f"unknown node search strategy: {strategy}")
        rows = self.conn.execute(sql, params)
        return [json.loads(row["data"]) for row in rows]

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM nodes_current WHERE node_id=?", (node_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM edges_current WHERE edge_id=?", (edge_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def edges_for_node(
        self,
        node_id: str,
        direction: str = "both",
        rel_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if direction == "in":
            direction_sql = "dst_id=?"
            direction_params: tuple[Any, ...] = (node_id,)
        elif direction == "out":
            direction_sql = "src_id=?"
            direction_params = (node_id,)
        elif direction == "both":
            direction_sql = "(src_id=? OR dst_id=?)"
            direction_params = (node_id, node_id)
        else:
            raise ValueError(f"unknown edge direction: {direction}")

        rel_sql = ""
        rel_params: tuple[Any, ...] = ()
        if rel_types:
            placeholders = ",".join("?" for _ in rel_types)
            rel_sql = f" AND rel_type IN ({placeholders})"
            rel_params = tuple(rel_types)

        rows = self.conn.execute(
            f"""
            SELECT data FROM edges_current
            WHERE status='active' AND {direction_sql}{rel_sql}
            ORDER BY rel_type, edge_id
            LIMIT ?
            """,
            (*direction_params, *rel_params, limit),
        )
        return [json.loads(row["data"]) for row in rows]

    def history(self, entity_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT data FROM history_events WHERE entity_id=? ORDER BY timestamp",
            (entity_id,),
        )
        return [json.loads(row["data"]) for row in rows]

    def neighborhood(self, node_id: str, hops: int = 1, edge_types: list[str] | None = None) -> dict[str, Any]:
        seen_nodes = {node_id}
        frontier = {node_id}
        edge_map: dict[str, dict[str, Any]] = {}
        for _ in range(max(1, hops)):
            next_frontier: set[str] = set()
            placeholders = ",".join("?" for _ in frontier)
            rows = self.conn.execute(
                f"SELECT data FROM edges_current WHERE status='active' AND (src_id IN ({placeholders}) OR dst_id IN ({placeholders}))",
                (*frontier, *frontier),
            )
            for row in rows:
                edge = json.loads(row["data"])
                if edge_types and edge["rel_type"] not in edge_types:
                    continue
                edge_map[edge["edge_id"]] = edge
                for key in ("src_id", "dst_id"):
                    if edge[key] not in seen_nodes:
                        seen_nodes.add(edge[key])
                        next_frontier.add(edge[key])
            frontier = next_frontier
            if not frontier:
                break
        nodes = [self.get_node(item) for item in seen_nodes]
        return {
            "center": node_id,
            "nodes": [node for node in nodes if node],
            "edges": list(edge_map.values()),
            "partial": False,
        }

    def path(self, src_id: str, dst_id: str, max_hops: int = 2) -> dict[str, Any]:
        queue: list[tuple[str, list[str], list[str]]] = [(src_id, [src_id], [])]
        visited = {src_id}
        while queue:
            current, node_path, edge_path = queue.pop(0)
            if len(edge_path) >= max_hops:
                continue
            rows = self.conn.execute(
                "SELECT edge_id, src_id, dst_id FROM edges_current WHERE status='active' AND src_id=?",
                (current,),
            )
            for row in rows:
                next_node = row["dst_id"]
                next_edges = [*edge_path, row["edge_id"]]
                next_nodes = [*node_path, next_node]
                if next_node == dst_id:
                    return {"nodes": next_nodes, "edges": next_edges, "found": True}
                if next_node not in visited:
                    visited.add(next_node)
                    queue.append((next_node, next_nodes, next_edges))
        return {"nodes": [], "edges": [], "found": False}

    def _upsert_node(self, node: Node) -> None:
        data = node.to_dict()
        self.conn.execute(
            """
            INSERT INTO nodes_current(node_id, repo_id, kind, name, fqname, file_path, status, version, data)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET
              repo_id=excluded.repo_id, kind=excluded.kind, name=excluded.name, fqname=excluded.fqname,
              file_path=excluded.file_path, status=excluded.status, version=excluded.version, data=excluded.data
            """,
            (node.node_id, node.repo_id, node.kind, node.name, node.fqname, node.file_path, node.status, node.version, json.dumps(data, ensure_ascii=False)),
        )

    def _upsert_edge(self, edge: Edge) -> None:
        data = edge.to_dict()
        self.conn.execute(
            """
            INSERT INTO edges_current(edge_id, src_id, dst_id, rel_type, status, version, data)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(edge_id) DO UPDATE SET
              src_id=excluded.src_id, dst_id=excluded.dst_id, rel_type=excluded.rel_type,
              status=excluded.status, version=excluded.version, data=excluded.data
            """,
            (edge.edge_id, edge.src_id, edge.dst_id, edge.rel_type, edge.status, edge.version, json.dumps(data, ensure_ascii=False)),
        )

    def _append_history(self, event: HistoryEvent) -> None:
        data = event.to_dict()
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO history_events(event_id, entity_type, entity_id, repo_id, op, timestamp, data)
            VALUES(?,?,?,?,?,?,?)
            """,
            (event.event_id, event.entity_type, event.entity_id, event.repo_id, event.op, event.timestamp, json.dumps(data, ensure_ascii=False)),
        )
        if cursor.rowcount:
            self._history_buffer.append(data)

    def _write_snapshot(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.kos_dir / "snapshots" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    append_jsonl_many(path, [obj])


def append_jsonl_many(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    for attempt in range(5):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def comparable(old: dict[str, Any], new: dict[str, Any]) -> bool:
    ignored = {"created_at", "updated_at", "version", "history_ref"}
    return {k: v for k, v in old.items() if k not in ignored} == {k: v for k, v in new.items() if k not in ignored}
