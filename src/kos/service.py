from __future__ import annotations

import os
import platform
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import DB_SCHEMA_VERSION, OUTPUT_SCHEMA_VERSION, __version__
from .agent_tools import (
    agent_calls,
    agent_impact,
    agent_pack,
    agent_read_plan,
    agent_resolve,
    agent_who_calls,
    empty_result,
)
from .indexer import index_repo, index_status, update_repo
from .languages import supported_languages
from .storage import Store, StoreError, inspect_store

AgentFunction = Callable[..., dict[str, Any]]


class KosService:
    def __init__(
        self,
        repo_path: Path | str = ".",
        store_root: Path | str | None = None,
        repo_id: str | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.store_root = Path(store_root).resolve() if store_root else self.repo_path
        store_state = inspect_store(self.store_root)
        self.repo_id = repo_id or store_state.get("repo_id") or self.repo_path.name

    def status(self) -> dict[str, Any]:
        try:
            return index_status(self.repo_path, self.repo_id, self.store_root)
        except (OSError, StoreError) as exc:
            return {
                "status": "error",
                "state": "error",
                "repo_id": self.repo_id,
                "schema_version": None,
                "indexed_at": None,
                "changed_files": 0,
                "error": {"code": error_code(exc), "message": str(exc), "hint": error_hint(exc)},
            }

    def index(self, rebuild: bool = False) -> dict[str, Any]:
        return index_repo(self.repo_path, self.repo_id, self.store_root, rebuild=rebuild)

    def update(self) -> dict[str, Any]:
        return update_repo(self.repo_path, self.repo_id, self.store_root)

    def languages(self) -> dict[str, Any]:
        status = self.status()
        return {
            "status": "error" if status.get("status") == "error" else "ok",
            "supported": supported_languages(),
            "indexed": status.get("languages", {}),
            "index": index_payload(status),
            "error": status.get("error"),
        }

    def resolve(self, query: str, candidate_limit: int = 10) -> dict[str, Any]:
        return self._query(query, agent_resolve, candidate_limit)

    def who_calls(self, query: str, fact_limit: int = 40) -> dict[str, Any]:
        return self._query(query, agent_who_calls, fact_limit)

    def calls(self, query: str, fact_limit: int = 40) -> dict[str, Any]:
        return self._query(query, agent_calls, fact_limit)

    def impact(self, query: str, fact_limit: int = 40) -> dict[str, Any]:
        return self._query(query, agent_impact, fact_limit)

    def read_plan(self, query: str, file_limit: int = 10, fact_limit: int = 40) -> dict[str, Any]:
        return self._query(query, agent_read_plan, file_limit, fact_limit)

    def pack(self, query: str, file_limit: int = 10, fact_limit: int = 40) -> dict[str, Any]:
        return self._query(query, agent_pack, file_limit, fact_limit)

    def doctor(self) -> dict[str, Any]:
        status = self.status()
        grammar_ok, grammar_detail = grammar_health()
        checks = [
            {
                "name": "python",
                "ok": tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 11),
                "detail": platform.python_version(),
            },
            {
                "name": "repository",
                "ok": self.repo_path.is_dir(),
                "detail": str(self.repo_path),
            },
            {
                "name": "store_parent_writable",
                "ok": os.access(self.store_root if self.store_root.exists() else self.store_root.parent, os.W_OK),
                "detail": str(self.store_root),
            },
            {
                "name": "schema",
                "ok": status.get("state") != "incompatible",
                "detail": f"expected={DB_SCHEMA_VERSION}, found={status.get('schema_version')}",
            },
            {
                "name": "sqlite",
                "ok": bool(sqlite3.sqlite_version),
                "detail": sqlite3.sqlite_version,
            },
            {
                "name": "tree_sitter_grammars",
                "ok": grammar_ok,
                "detail": grammar_detail,
            },
        ]
        return {
            "status": "ok" if all(item["ok"] for item in checks) else "error",
            "version": __version__,
            "checks": checks,
            "index": status,
        }

    def open_store(self) -> Store:
        return Store(self.store_root)

    def _query(self, query: str, function: AgentFunction, *args: int) -> dict[str, Any]:
        status = self.status()
        if status.get("status") == "error" or status["state"] in {"uninitialized", "incompatible"}:
            result = empty_result("error", query)
            state = status.get("state", "error")
            result["index"] = index_payload(status)
            result["error"] = status.get("error") or {
                "code": f"index_{state}",
                "message": f"KOS index is {state}",
                "hint": (
                    "Run `kos update` to initialize the index."
                    if state == "uninitialized"
                    else "Run `kos index --rebuild` from the CLI."
                ),
            }
            return result
        try:
            with Store(self.store_root) as store:
                result = function(store, query, *args)
        except (OSError, StoreError) as exc:
            result = empty_result("error", query)
            result["error"] = {"code": error_code(exc), "message": str(exc), "hint": error_hint(exc)}
        result["schema_version"] = OUTPUT_SCHEMA_VERSION
        result["index"] = index_payload(status)
        return result


def index_payload(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": status.get("state", "error"),
        "repo_id": status.get("repo_id"),
        "indexed_at": status.get("indexed_at"),
        "changed_files": status.get("changed_files", 0),
        "languages": status.get("languages", {}),
    }


def grammar_health() -> tuple[bool, str]:
    try:
        from .tree_sitter_observer import parser_for

        languages = (
            "javascript",
            "typescript",
            "tsx",
            "css",
            "bash",
            "go",
            "java",
            "rust",
            "c",
            "cpp",
        )
        for language in languages:
            parser_for(language)
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    return True, ", ".join(languages)


def error_code(exc: BaseException) -> str:
    name = exc.__class__.__name__
    parts: list[str] = []
    current = ""
    for char in name:
        if char.isupper() and current:
            parts.append(current.lower())
            current = char
        else:
            current += char
    if current:
        parts.append(current.lower())
    return "_".join(parts)


def error_hint(exc: BaseException) -> str:
    if "incompatible" in str(exc).lower():
        return "Run `kos index --rebuild` from the CLI."
    return "Run `kos doctor` for repository and index diagnostics."
