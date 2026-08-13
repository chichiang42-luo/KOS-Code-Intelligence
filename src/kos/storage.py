from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import DB_SCHEMA_VERSION, __version__
from .ids import stable_id
from .schemas import Edge, HistoryEvent, Node, utc_now


class StoreError(RuntimeError):
    """Base class for storage errors that can be shown to users."""


class IncompatibleSchemaError(StoreError):
    def __init__(self, found: int | None) -> None:
        self.found = found
        super().__init__(
            f"incompatible index schema {found!r}; run `kos index --rebuild` to create schema {DB_SCHEMA_VERSION}"
        )


class RepositoryBindingError(StoreError):
    pass


def inspect_store(root: Path) -> dict[str, Any]:
    db_path = root.resolve() / ".kos" / "graph.db"
    if not db_path.exists():
        return {"state": "uninitialized", "schema_version": None}
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if not tables:
            return {"state": "uninitialized", "schema_version": version or None}
        if version != DB_SCHEMA_VERSION:
            return {"state": "incompatible", "schema_version": version}
        row = conn.execute("SELECT * FROM repositories LIMIT 1").fetchone()
        if not row:
            return {"state": "uninitialized", "schema_version": version}
        result = dict(row)
        result.update({"state": "initialized", "schema_version": version})
        return result
    except sqlite3.DatabaseError as exc:
        return {"state": "incompatible", "schema_version": None, "error": str(exc)}
    finally:
        conn.close()


def backup_database(root: Path) -> Path | None:
    kos_dir = root.resolve() / ".kos"
    db_path = kos_dir / "graph.db"
    if not db_path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = kos_dir / "backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        source = Path(f"{db_path}{suffix}")
        if source.exists():
            shutil.copy2(source, backup_dir / source.name)
    return backup_dir


def remove_database_files(root: Path) -> None:
    db_path = root.resolve() / ".kos" / "graph.db"
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.kos_dir = self.root / ".kos"
        self.logs_dir = self.kos_dir / "logs"
        self.db_path = self.kos_dir / "graph.db"
        self.kos_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        state = inspect_store(self.root)
        if state["state"] == "incompatible":
            raise IncompatibleSchemaError(state.get("schema_version"))
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._history_buffer: list[dict[str, Any]] = []
        self.init_db()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_db(self) -> None:
        self.conn.executescript(
            f"""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS repositories (
              repo_id TEXT PRIMARY KEY,
              repo_path TEXT NOT NULL,
              language TEXT NOT NULL,
              tool_version TEXT NOT NULL,
              schema_version INTEGER NOT NULL,
              indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS indexed_files (
              file_path TEXT PRIMARY KEY,
              repo_id TEXT NOT NULL,
              language TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              parse_status TEXT NOT NULL,
              observations_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
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
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes_current(file_path);
            CREATE TABLE IF NOT EXISTS edges_current (
              edge_id TEXT PRIMARY KEY,
              repo_id TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_history_entity ON history_events(entity_id, timestamp);
            CREATE TABLE IF NOT EXISTS index_runs (
              run_id TEXT PRIMARY KEY,
              repo_id TEXT NOT NULL,
              mode TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              duration_ms INTEGER NOT NULL,
              files_added INTEGER NOT NULL,
              files_changed INTEGER NOT NULL,
              files_deleted INTEGER NOT NULL,
              error TEXT
            );
            PRAGMA user_version={DB_SCHEMA_VERSION};
            """
        )
        self.conn.commit()

    def bind_repository(self, repo_id: str, repo_path: Path) -> None:
        row = self.conn.execute("SELECT repo_id, repo_path FROM repositories LIMIT 1").fetchone()
        if not row:
            return
        expected = os.path.normcase(str(repo_path.resolve()))
        actual = os.path.normcase(str(Path(row["repo_path"]).resolve()))
        if row["repo_id"] != repo_id or actual != expected:
            raise RepositoryBindingError(
                f"store is bound to {row['repo_id']} at {row['repo_path']}; use a different --store-root"
            )

    def repository_metadata(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM repositories LIMIT 1").fetchone()
        return dict(row) if row else None

    def file_records(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM indexed_files ORDER BY file_path")
        return {row["file_path"]: dict(row) for row in rows}

    def commit_index(
        self,
        repo_id: str,
        repo_path: Path,
        nodes: list[Node],
        edges: list[Edge],
        file_records: list[dict[str, Any]],
        run: dict[str, Any],
    ) -> dict[str, int]:
        self.bind_repository(repo_id, repo_path)
        existing_nodes = {row["node_id"]: row for row in self.conn.execute("SELECT * FROM nodes_current")}
        existing_edges = {row["edge_id"]: row for row in self.conn.execute("SELECT * FROM edges_current")}
        node_ids = {node.node_id for node in nodes}
        edge_ids = {edge.edge_id for edge in edges}
        stats = {
            "nodes_created": 0,
            "nodes_updated": 0,
            "nodes_deleted": 0,
            "edges_created": 0,
            "edges_updated": 0,
            "edges_deleted": 0,
        }
        now = run["finished_at"]
        run_id = run["run_id"]
        self._history_buffer.clear()

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            for node in nodes:
                old = existing_nodes.get(node.node_id)
                op: str | None = None
                if old:
                    old_data = json.loads(old["data"])
                    node.created_at = old_data.get("created_at", node.created_at)
                    if old["status"] != "active" or not comparable(old_data, node.to_dict()):
                        node.version = int(old["version"]) + 1
                        node.updated_at = now
                        op = "restore" if old["status"] != "active" else "update"
                        stats["nodes_updated"] += 1
                else:
                    node.updated_at = now
                    op = "create"
                    stats["nodes_created"] += 1
                if op:
                    self._upsert_node(node)
                    self._append_history(self._event(run_id, "node", node.node_id, repo_id, op))

            for node_id, old in existing_nodes.items():
                if old["status"] == "active" and node_id not in node_ids:
                    self._mark_deleted("node", old, now)
                    stats["nodes_deleted"] += 1
                    self._append_history(self._event(run_id, "node", node_id, repo_id, "delete"))

            for edge in edges:
                old = existing_edges.get(edge.edge_id)
                op = None
                if old:
                    old_data = json.loads(old["data"])
                    edge.created_at = old_data.get("created_at", edge.created_at)
                    if old["status"] != "active" or not comparable(old_data, edge.to_dict()):
                        edge.version = int(old["version"]) + 1
                        edge.updated_at = now
                        op = "restore" if old["status"] != "active" else "update"
                        stats["edges_updated"] += 1
                else:
                    edge.updated_at = now
                    op = "create"
                    stats["edges_created"] += 1
                if op:
                    self._upsert_edge(repo_id, edge)
                    self._append_history(self._event(run_id, "edge", edge.edge_id, repo_id, op))

            for edge_id, old in existing_edges.items():
                if old["status"] == "active" and edge_id not in edge_ids:
                    self._mark_deleted("edge", old, now)
                    stats["edges_deleted"] += 1
                    self._append_history(self._event(run_id, "edge", edge_id, repo_id, "delete"))

            self.conn.execute("DELETE FROM indexed_files")
            self.conn.executemany(
                """
                INSERT INTO indexed_files(
                  file_path, repo_id, language, content_hash, size, mtime_ns,
                  parse_status, observations_json, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        item["file_path"],
                        repo_id,
                        item["language"],
                        item["content_hash"],
                        item["size"],
                        item["mtime_ns"],
                        item["parse_status"],
                        item["observations_json"],
                        now,
                    )
                    for item in file_records
                ],
            )
            self.conn.execute(
                """
                INSERT INTO repositories(repo_id, repo_path, language, tool_version, schema_version, indexed_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(repo_id) DO UPDATE SET
                  repo_path=excluded.repo_path,
                  language=excluded.language,
                  tool_version=excluded.tool_version,
                  schema_version=excluded.schema_version,
                  indexed_at=excluded.indexed_at
                """,
                (repo_id, str(repo_path.resolve()), "polyglot", __version__, DB_SCHEMA_VERSION, now),
            )
            self._insert_run(run)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            self._history_buffer.clear()
            raise

        try:
            append_jsonl_many(self.logs_dir / "history.jsonl", self._history_buffer)
            append_jsonl(self.logs_dir / "runs.jsonl", run)
            self._write_current_snapshots()
        except OSError:
            pass
        finally:
            self._history_buffer.clear()
        return stats

    def record_failed_run(self, run: dict[str, Any]) -> None:
        try:
            self._insert_run(run)
            self.conn.commit()
            append_jsonl(self.logs_dir / "runs.jsonl", run)
        except (sqlite3.DatabaseError, OSError):
            self.conn.rollback()

    def commit_graph(self, repo_id: str, nodes: list[Node], edges: list[Edge]) -> dict[str, int]:
        now = utc_now()
        run = {
            "run_id": stable_id("run", repo_id, now, "legacy"),
            "repo_id": repo_id,
            "mode": "full",
            "status": "ok",
            "started_at": now,
            "finished_at": now,
            "duration_ms": 0,
            "files_added": 0,
            "files_changed": 0,
            "files_deleted": 0,
            "error": None,
        }
        return self.commit_index(repo_id, self.root, nodes, edges, [], run)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{query}%"
        rows = self.conn.execute(
            """
            SELECT data FROM nodes_current
            WHERE status='active' AND (name LIKE ? OR fqname LIKE ? OR file_path LIKE ?)
            ORDER BY CASE WHEN name=? THEN 0 WHEN fqname=? THEN 1 ELSE 2 END, length(fqname), fqname
            LIMIT ?
            """,
            (like, like, like, query, query, limit),
        )
        return [json.loads(row["data"]) for row in rows]

    def find_nodes(self, query: str, strategy: str, limit: int = 20) -> list[dict[str, Any]]:
        if strategy == "fqname_exact":
            sql = "SELECT data FROM nodes_current WHERE status='active' AND fqname=? ORDER BY fqname LIMIT ?"
            params: tuple[Any, ...] = (query, limit)
        elif strategy == "name_exact":
            sql = "SELECT data FROM nodes_current WHERE status='active' AND name=? ORDER BY fqname LIMIT ?"
            params = (query, limit)
        elif strategy == "file_exact":
            normalized = query.replace("\\", "/")
            sql = """
            SELECT data FROM nodes_current
            WHERE status='active' AND (file_path=? OR fqname=?)
            ORDER BY CASE WHEN kind='file' THEN 0 WHEN kind='module' THEN 1 ELSE 2 END, fqname
            LIMIT ?
            """
            params = (normalized, normalized, limit)
        elif strategy == "fuzzy":
            normalized = query.replace("\\", "/")
            like = f"%{normalized}%"
            sql = """
            SELECT data FROM nodes_current
            WHERE status='active' AND (name LIKE ? OR fqname LIKE ? OR file_path LIKE ?)
            ORDER BY CASE WHEN name=? THEN 0 WHEN fqname=? THEN 1 ELSE 2 END, length(fqname), fqname
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
                f"""
                SELECT data FROM edges_current
                WHERE status='active' AND (src_id IN ({placeholders}) OR dst_id IN ({placeholders}))
                """,
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
            (
                node.node_id,
                node.repo_id,
                node.kind,
                node.name,
                node.fqname,
                node.file_path,
                node.status,
                node.version,
                json.dumps(data, ensure_ascii=False),
            ),
        )

    def _upsert_edge(self, repo_id: str, edge: Edge) -> None:
        data = edge.to_dict()
        self.conn.execute(
            """
            INSERT INTO edges_current(edge_id, repo_id, src_id, dst_id, rel_type, status, version, data)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(edge_id) DO UPDATE SET
              repo_id=excluded.repo_id, src_id=excluded.src_id, dst_id=excluded.dst_id,
              rel_type=excluded.rel_type, status=excluded.status, version=excluded.version, data=excluded.data
            """,
            (
                edge.edge_id,
                repo_id,
                edge.src_id,
                edge.dst_id,
                edge.rel_type,
                edge.status,
                edge.version,
                json.dumps(data, ensure_ascii=False),
            ),
        )

    def _mark_deleted(self, entity_type: str, row: sqlite3.Row, now: str) -> None:
        data = json.loads(row["data"])
        data["status"] = "deleted"
        data["version"] = int(row["version"]) + 1
        data["updated_at"] = now
        table = "nodes_current" if entity_type == "node" else "edges_current"
        id_column = "node_id" if entity_type == "node" else "edge_id"
        self.conn.execute(
            f"UPDATE {table} SET status='deleted', version=?, data=? WHERE {id_column}=?",
            (data["version"], json.dumps(data, ensure_ascii=False), row[id_column]),
        )

    def _event(self, run_id: str, entity_type: str, entity_id: str, repo_id: str, op: str) -> HistoryEvent:
        return HistoryEvent(
            stable_id("evt", run_id, entity_type, entity_id, op, length=20),
            entity_type,  # type: ignore[arg-type]
            entity_id,
            repo_id,
            op,
            f"{entity_type} {op}",
        )

    def _append_history(self, event: HistoryEvent) -> None:
        data = event.to_dict()
        self.conn.execute(
            """
            INSERT INTO history_events(event_id, entity_type, entity_id, repo_id, op, timestamp, data)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                event.entity_type,
                event.entity_id,
                event.repo_id,
                event.op,
                event.timestamp,
                json.dumps(data, ensure_ascii=False),
            ),
        )
        self._history_buffer.append(data)

    def _insert_run(self, run: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO index_runs(
              run_id, repo_id, mode, status, started_at, finished_at, duration_ms,
              files_added, files_changed, files_deleted, error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run["run_id"],
                run["repo_id"],
                run["mode"],
                run["status"],
                run["started_at"],
                run["finished_at"],
                run["duration_ms"],
                run["files_added"],
                run["files_changed"],
                run["files_deleted"],
                run.get("error"),
            ),
        )

    def _write_current_snapshots(self) -> None:
        active_nodes = self.conn.execute("SELECT data FROM nodes_current WHERE status='active'")
        active_edges = self.conn.execute("SELECT data FROM edges_current WHERE status='active'")
        nodes = [json.loads(row["data"]) for row in active_nodes]
        edges = [json.loads(row["data"]) for row in active_edges]
        self._write_snapshot("latest_nodes.jsonl", nodes)
        self._write_snapshot("latest_edges.jsonl", edges)

    def _write_snapshot(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.kos_dir / "snapshots" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        temp_path.replace(path)


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
    return {key: value for key, value in old.items() if key not in ignored} == {
        key: value for key, value in new.items() if key not in ignored
    }
