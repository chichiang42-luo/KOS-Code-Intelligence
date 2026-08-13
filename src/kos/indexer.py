from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .discovery import discover
from .ids import stable_id
from .languages import detect_language, iter_source_files, language_counts
from .observation import observe_file
from .schemas import Observation, Span, utc_now
from .storage import (
    IncompatibleSchemaError,
    Store,
    backup_database,
    inspect_store,
    remove_database_files,
)


class IndexLockError(RuntimeError):
    pass


class IndexLock:
    def __init__(self, root: Path, timeout: float = 30.0) -> None:
        self.path = root.resolve() / ".kos" / "index.lock"
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> IndexLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()}\n".encode("ascii"))
                return self
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise IndexLockError(f"another KOS index update holds {self.path}") from None
                time.sleep(0.1)

    def __exit__(self, *_args: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _remove_stale_lock(self) -> bool:
        try:
            raw_pid = self.path.read_text(encoding="ascii").strip()
            pid = int(raw_pid)
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            try:
                old_enough = time.time() - self.path.stat().st_mtime > max(60.0, self.timeout)
            except OSError:
                return True
            if not old_enough:
                return False
        else:
            if process_is_alive(pid):
                return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def index_repo(
    repo_path: Path,
    repo_id: str | None = None,
    store_root: Path | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    root = validate_repo(repo_path)
    storage_root = (store_root or root).resolve()
    rid = repo_id or root.name
    with IndexLock(storage_root):
        state = inspect_store(storage_root)
        backup_path: Path | None = None
        if state["state"] == "incompatible":
            if not rebuild:
                raise IncompatibleSchemaError(state.get("schema_version"))
            backup_path = backup_database(storage_root)
            remove_database_files(storage_root)
        elif rebuild and state["state"] != "uninitialized":
            backup_path = backup_database(storage_root)
            remove_database_files(storage_root)
        result = _full_index(root, rid, storage_root)
        result["backup_path"] = str(backup_path) if backup_path else None
        return result


def update_repo(
    repo_path: Path,
    repo_id: str | None = None,
    store_root: Path | None = None,
) -> dict[str, Any]:
    root = validate_repo(repo_path)
    storage_root = (store_root or root).resolve()
    rid = repo_id or root.name
    state = inspect_store(storage_root)
    if state["state"] == "incompatible":
        raise IncompatibleSchemaError(state.get("schema_version"))
    if state["state"] == "uninitialized":
        return index_repo(root, rid, storage_root)

    with IndexLock(storage_root):
        with Store(storage_root) as store:
            store.bind_repository(rid, root)
            cached = store.file_records()
            current_paths = {
                path.relative_to(root).as_posix(): path
                for path in iter_source_files(root)
            }
            validate_parser_runtime(list(current_paths.values()))
            added = sorted(set(current_paths) - set(cached))
            deleted = sorted(set(cached) - set(current_paths))
            changed: list[str] = []
            metadata_only: list[str] = []
            for rel_path in sorted(set(current_paths) & set(cached)):
                stat = current_paths[rel_path].stat()
                old = cached[rel_path]
                if stat.st_size == old["size"] and stat.st_mtime_ns == old["mtime_ns"]:
                    continue
                digest = file_sha256(current_paths[rel_path])
                if digest == old["content_hash"]:
                    metadata_only.append(rel_path)
                else:
                    changed.append(rel_path)

            if not added and not changed and not deleted:
                return _no_change_result(store, rid, root, storage_root, metadata_only)

            started_perf = time.perf_counter()
            started_at = utc_now()
            observations_by_file: dict[str, list[Observation]] = {}
            file_records: list[dict[str, Any]] = []
            parse_errors = 0
            for rel_path, path in sorted(current_paths.items()):
                stat = path.stat()
                old = cached.get(rel_path)
                if rel_path not in added and rel_path not in changed:
                    observations = observations_from_json(old["observations_json"]) if old else []
                    content_hash = old["content_hash"] if old else file_sha256(path)
                    parse_status = old["parse_status"] if old else "ok"
                else:
                    content_hash = file_sha256(path)
                    parsed = observe_file(path, root, rid)
                    has_error = any(item.kind == "parse_error" for item in parsed)
                    if has_error and old:
                        observations = observations_from_json(old["observations_json"])
                        parse_status = "error_preserved"
                    else:
                        observations = parsed
                        parse_status = "error" if has_error else "ok"
                if parse_status != "ok":
                    parse_errors += 1
                observations_by_file[rel_path] = observations
                file_records.append(
                    make_file_record(
                        rel_path,
                        detect_language(path) or "unknown",
                        content_hash,
                        stat.st_size,
                        stat.st_mtime_ns,
                        parse_status,
                        observations,
                    )
                )
            return _commit(
                store,
                root,
                storage_root,
                rid,
                observations_by_file,
                file_records,
                "incremental",
                started_at,
                started_perf,
                len(added),
                len(changed),
                len(deleted),
                parse_errors,
            )


def index_status(
    repo_path: Path,
    repo_id: str | None = None,
    store_root: Path | None = None,
) -> dict[str, Any]:
    root = validate_repo(repo_path)
    storage_root = (store_root or root).resolve()
    rid = repo_id or root.name
    state = inspect_store(storage_root)
    base = {
        "status": "ok",
        "state": state["state"],
        "repo_id": state.get("repo_id", rid),
        "schema_version": state.get("schema_version"),
        "indexed_at": state.get("indexed_at"),
        "files_added": 0,
        "files_changed": 0,
        "files_deleted": 0,
        "changed_files": 0,
        "parse_errors": 0,
        "languages": {},
    }
    if state["state"] in {"uninitialized", "incompatible"}:
        if state["state"] == "uninitialized":
            paths = iter_source_files(root)
            count = len(paths)
            base.update({"files_added": count, "changed_files": count, "languages": language_counts(paths)})
        return base

    with Store(storage_root) as store:
        store.bind_repository(rid, root)
        cached = store.file_records()
        current_paths = {
            path.relative_to(root).as_posix(): path
            for path in iter_source_files(root)
        }
        added = set(current_paths) - set(cached)
        deleted = set(cached) - set(current_paths)
        changed = 0
        for rel_path in set(current_paths) & set(cached):
            stat = current_paths[rel_path].stat()
            old = cached[rel_path]
            if stat.st_size == old["size"] and stat.st_mtime_ns == old["mtime_ns"]:
                continue
            if file_sha256(current_paths[rel_path]) != old["content_hash"]:
                changed += 1
        change_count = len(added) + changed + len(deleted)
        base.update(
            {
                "state": "stale" if change_count else "fresh",
                "files_added": len(added),
                "files_changed": changed,
                "files_deleted": len(deleted),
                "changed_files": change_count,
                "parse_errors": sum(1 for item in cached.values() if item["parse_status"] != "ok"),
                "languages": language_counts(list(current_paths.values())),
            }
        )
        return base


def _full_index(root: Path, repo_id: str, storage_root: Path) -> dict[str, Any]:
    started_perf = time.perf_counter()
    started_at = utc_now()
    observations_by_file: dict[str, list[Observation]] = {}
    file_records: list[dict[str, Any]] = []
    parse_errors = 0
    paths = iter_source_files(root)
    validate_parser_runtime(paths)
    for path in paths:
        rel_path = path.relative_to(root).as_posix()
        observations = observe_file(path, root, repo_id)
        has_error = any(item.kind == "parse_error" for item in observations)
        parse_errors += int(has_error)
        stat = path.stat()
        observations_by_file[rel_path] = observations
        file_records.append(
            make_file_record(
                rel_path,
                detect_language(path) or "unknown",
                file_sha256(path),
                stat.st_size,
                stat.st_mtime_ns,
                "error" if has_error else "ok",
                observations,
            )
        )
    with Store(storage_root) as store:
        return _commit(
            store,
            root,
            storage_root,
            repo_id,
            observations_by_file,
            file_records,
            "full",
            started_at,
            started_perf,
            len(paths),
            0,
            0,
            parse_errors,
        )


def _commit(
    store: Store,
    root: Path,
    storage_root: Path,
    repo_id: str,
    observations_by_file: dict[str, list[Observation]],
    file_records: list[dict[str, Any]],
    mode: str,
    started_at: str,
    started_perf: float,
    files_added: int,
    files_changed: int,
    files_deleted: int,
    parse_errors: int,
) -> dict[str, Any]:
    observations = [
        observation
        for file_path in sorted(observations_by_file)
        for observation in observations_by_file[file_path]
    ]
    graph = discover(observations)
    finished_at = utc_now()
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    run = {
        "run_id": stable_id("run", repo_id, started_at, mode, length=20),
        "repo_id": repo_id,
        "mode": mode,
        "status": "ok",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "files_added": files_added,
        "files_changed": files_changed,
        "files_deleted": files_deleted,
        "error": None,
    }
    stats = store.commit_index(repo_id, root, graph.nodes, graph.edges, file_records, run)
    return {
        "repo_id": repo_id,
        "status": "ok",
        "mode": mode,
        "store_root": str(storage_root),
        "files_scanned": len(file_records),
        "files_added": files_added,
        "files_changed": files_changed,
        "files_deleted": files_deleted,
        "observations": len(observations),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "parse_errors": parse_errors,
        "languages": count_record_languages(file_records),
        "duration_ms": int((time.perf_counter() - started_perf) * 1000),
        **stats,
    }


def _no_change_result(
    store: Store,
    repo_id: str,
    root: Path,
    storage_root: Path,
    metadata_only: list[str],
) -> dict[str, Any]:
    node_count = store.conn.execute("SELECT count(*) FROM nodes_current WHERE status='active'").fetchone()[0]
    edge_count = store.conn.execute("SELECT count(*) FROM edges_current WHERE status='active'").fetchone()[0]
    return {
        "repo_id": repo_id,
        "status": "ok",
        "mode": "incremental",
        "store_root": str(storage_root),
        "files_scanned": len(store.file_records()),
        "files_added": 0,
        "files_changed": 0,
        "files_deleted": 0,
        "metadata_only": len(metadata_only),
        "observations": 0,
        "nodes": node_count,
        "edges": edge_count,
        "parse_errors": sum(1 for item in store.file_records().values() if item["parse_status"] != "ok"),
        "languages": count_record_languages(list(store.file_records().values())),
        "duration_ms": 0,
        "nodes_created": 0,
        "nodes_updated": 0,
        "nodes_deleted": 0,
        "edges_created": 0,
        "edges_updated": 0,
        "edges_deleted": 0,
    }


def make_file_record(
    file_path: str,
    language: str,
    content_hash: str,
    size: int,
    mtime_ns: int,
    parse_status: str,
    observations: list[Observation],
) -> dict[str, Any]:
    return {
        "file_path": file_path,
        "language": language,
        "content_hash": content_hash,
        "size": size,
        "mtime_ns": mtime_ns,
        "parse_status": parse_status,
        "observations_json": json.dumps(
            [item.to_dict() for item in observations],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def count_record_languages(file_records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in file_records:
        language = str(item.get("language", "unknown"))
        counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items()))


def observations_from_json(raw: str) -> list[Observation]:
    result: list[Observation] = []
    for data in json.loads(raw):
        span_data = data.get("span")
        span = Span(**span_data) if span_data else None
        result.append(
            Observation(
                kind=data["kind"],
                repo_id=data["repo_id"],
                file_path=data["file_path"],
                name=data["name"],
                fqname=data["fqname"],
                span=span,
                parent=data.get("parent"),
                target=data.get("target"),
                signature=data.get("signature"),
                doc=data.get("doc"),
                raw=data.get("raw") or {},
            )
        )
    return result


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_parser_runtime(paths: list[Path]) -> None:
    if any(detect_language(path) != "python" for path in paths):
        from .tree_sitter_observer import ensure_tree_sitter_runtime

        ensure_tree_sitter_runtime()


def validate_repo(repo_path: Path) -> Path:
    root = repo_path.resolve()
    if not root.exists():
        raise FileNotFoundError(f"repository does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"repository is not a directory: {root}")
    return root
