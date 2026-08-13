from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from . import OUTPUT_SCHEMA_VERSION
from .storage import Store

AGENT_FACT_LIMIT = 40
AGENT_FILE_LIMIT = 10
AGENT_CANDIDATE_LIMIT = 10
IMPACT_REL_TYPES = {"CALLS", "IMPORTS", "INHERITS"}


def agent_resolve(store: Store, query: str, candidate_limit: int = AGENT_CANDIDATE_LIMIT) -> dict[str, Any]:
    candidates, truncated = resolve_candidates_with_meta(store, query, candidate_limit)
    if not candidates:
        return empty_result("not_found", query)
    if len(candidates) == 1:
        result = empty_result("ok", query)
        result["target"] = summarize_node(candidates[0])
        result["limits"] = limits(candidate_limit=candidate_limit)
        return result
    result = empty_result("ambiguous", query)
    result["candidates"] = [summarize_node(node) for node in candidates]
    result["limits"] = limits(
        candidate_limit=candidate_limit,
        candidates_returned=len(candidates),
        candidates_truncated=truncated,
    )
    return result


def agent_who_calls(store: Store, query: str, fact_limit: int = AGENT_FACT_LIMIT) -> dict[str, Any]:
    resolved = agent_resolve(store, query)
    if resolved["status"] != "ok":
        return resolved
    target_id = resolved["target"]["node_id"]
    edges = store.edges_for_node(target_id, direction="in", rel_types=["CALLS"], limit=fact_limit + 1)
    facts = facts_for_edges(store, edges[:fact_limit])
    files = build_file_plan(resolved["target"], facts, max_files=AGENT_FILE_LIMIT + 1)
    return with_payload(
        resolved,
        files[:AGENT_FILE_LIMIT],
        facts,
        fact_limit=fact_limit,
        facts_truncated=len(edges) > fact_limit,
        files_truncated=len(files) > AGENT_FILE_LIMIT,
    )


def agent_calls(store: Store, query: str, fact_limit: int = AGENT_FACT_LIMIT) -> dict[str, Any]:
    resolved = agent_resolve(store, query)
    if resolved["status"] != "ok":
        return resolved
    target_id = resolved["target"]["node_id"]
    edges = store.edges_for_node(target_id, direction="out", rel_types=["CALLS"], limit=fact_limit + 1)
    facts = facts_for_edges(store, edges[:fact_limit])
    files = build_file_plan(resolved["target"], facts, max_files=AGENT_FILE_LIMIT + 1)
    return with_payload(
        resolved,
        files[:AGENT_FILE_LIMIT],
        facts,
        fact_limit=fact_limit,
        facts_truncated=len(edges) > fact_limit,
        files_truncated=len(files) > AGENT_FILE_LIMIT,
    )


def agent_impact(store: Store, query: str, fact_limit: int = AGENT_FACT_LIMIT) -> dict[str, Any]:
    resolved = agent_resolve(store, query)
    if resolved["status"] != "ok":
        return resolved
    target_id = resolved["target"]["node_id"]
    edges = store.edges_for_node(target_id, direction="both", rel_types=sorted(IMPACT_REL_TYPES), limit=fact_limit + 1)
    facts = facts_for_edges(store, edges[:fact_limit])
    files = build_file_plan(resolved["target"], facts, max_files=AGENT_FILE_LIMIT + 1)
    return with_payload(
        resolved,
        files[:AGENT_FILE_LIMIT],
        facts,
        fact_limit=fact_limit,
        facts_truncated=len(edges) > fact_limit,
        files_truncated=len(files) > AGENT_FILE_LIMIT,
    )


def agent_read_plan(
    store: Store,
    query: str,
    max_files: int = AGENT_FILE_LIMIT,
    fact_limit: int = AGENT_FACT_LIMIT,
) -> dict[str, Any]:
    resolved = agent_resolve(store, query)
    if resolved["status"] != "ok":
        return resolved
    target_id = resolved["target"]["node_id"]
    edges = store.edges_for_node(target_id, direction="both", rel_types=sorted(IMPACT_REL_TYPES), limit=fact_limit + 1)
    facts = facts_for_edges(store, edges[:fact_limit])
    files = build_file_plan(resolved["target"], facts, max_files=max_files + 1)
    return with_payload(
        resolved,
        files[:max_files],
        facts,
        file_limit=max_files,
        fact_limit=fact_limit,
        facts_truncated=len(edges) > fact_limit,
        files_truncated=len(files) > max_files,
    )


def agent_pack(
    store: Store,
    query: str,
    max_files: int = AGENT_FILE_LIMIT,
    fact_limit: int = AGENT_FACT_LIMIT,
) -> dict[str, Any]:
    resolved = agent_resolve(store, query)
    if resolved["status"] != "ok":
        return resolved
    target_id = resolved["target"]["node_id"]
    edges = store.edges_for_node(target_id, direction="both", rel_types=sorted(IMPACT_REL_TYPES), limit=fact_limit + 1)
    facts = facts_for_edges(store, edges[:fact_limit])
    files = build_file_plan(resolved["target"], facts, max_files=max_files + 1)
    return with_payload(
        resolved,
        files[:max_files],
        facts,
        file_limit=max_files,
        fact_limit=fact_limit,
        facts_truncated=len(edges) > fact_limit,
        files_truncated=len(files) > max_files,
    )


def resolve_candidates(store: Store, query: str, limit: int) -> list[dict[str, Any]]:
    return resolve_candidates_with_meta(store, query, limit)[0]


def resolve_candidates_with_meta(store: Store, query: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
    query = query.strip()
    if not query:
        return [], False
    strategies = [
        store.find_nodes(query, "fqname_exact", limit + 1),
        store.find_nodes(query, "name_exact", limit + 1),
        store.find_nodes(query, "file_exact", limit + 1),
        store.find_nodes(query, "fuzzy", limit + 1),
    ]
    for candidates in strategies:
        unique = dedupe_nodes(candidates)
        if unique:
            return unique[:limit], len(unique) > limit
    return [], False


def facts_for_edges(store: Store, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for edge in edges:
        src = store.get_node(edge["src_id"])
        dst = store.get_node(edge["dst_id"])
        facts.append(
            {
                "edge_id": edge["edge_id"],
                "rel_type": edge["rel_type"],
                "src": summarize_node(src) if src else {"node_id": edge["src_id"]},
                "dst": summarize_node(dst) if dst else {"node_id": edge["dst_id"]},
                "confidence": edge.get("confidence"),
                "evidence": trim_evidence(edge.get("evidence", [])),
            }
        )
    return facts


def build_file_plan(target: dict[str, Any], facts: list[dict[str, Any]], max_files: int) -> list[dict[str, Any]]:
    files: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add(node: dict[str, Any] | None, priority: int, reason: str) -> None:
        if not node or not node.get("file_path"):
            return
        path = node["file_path"]
        existing = files.get(path)
        if existing:
            existing["priority"] = min(existing["priority"], priority)
            if reason not in existing["reason"]:
                existing["reason"] += f"; {reason}"
            if node["node_id"] not in existing["related_nodes"]:
                existing["related_nodes"].append(node["node_id"])
            return
        files[path] = {
            "file_path": path,
            "priority": priority,
            "reason": reason,
            "related_nodes": [node["node_id"]],
        }

    add(target, 1, "target definition")
    target_id = target["node_id"]
    for fact in facts:
        if fact["rel_type"] == "CALLS" and fact["dst"].get("node_id") == target_id:
            add(fact["src"], 2, "calls target")
        elif fact["rel_type"] == "CALLS" and fact["src"].get("node_id") == target_id:
            add(fact["dst"], 3, "called by target")
        elif fact["rel_type"] == "IMPORTS" and fact["dst"].get("node_id") == target_id:
            add(fact["src"], 4, "imports target")
        elif fact["rel_type"] == "IMPORTS" and fact["src"].get("node_id") == target_id:
            add(fact["dst"], 4, "imported by target")
        elif fact["rel_type"] == "INHERITS":
            other = fact["src"] if fact["src"].get("node_id") != target_id else fact["dst"]
            add(other, 4, "inheritance relation")
    return sorted(files.values(), key=lambda item: (item["priority"], item["file_path"]))[:max_files]


def with_payload(
    resolved: dict[str, Any],
    files: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    file_limit: int = AGENT_FILE_LIMIT,
    fact_limit: int = AGENT_FACT_LIMIT,
    files_truncated: bool = False,
    facts_truncated: bool = False,
) -> dict[str, Any]:
    return {
        **resolved,
        "files": files,
        "facts": facts,
        "limits": limits(
            file_limit=file_limit,
            fact_limit=fact_limit,
            facts_returned=len(facts),
            files_returned=len(files),
            files_truncated=files_truncated,
            facts_truncated=facts_truncated,
        ),
    }


def empty_result(status: str, query: str) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "status": status,
        "query": query,
        "target": None,
        "candidates": [],
        "files": [],
        "facts": [],
        "limits": limits(),
        "index": {
            "state": "unknown",
            "repo_id": None,
            "indexed_at": None,
            "changed_files": 0,
            "languages": {},
        },
        "error": None,
    }


def summarize_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id"),
        "fqname": node.get("fqname"),
        "kind": node.get("kind"),
        "language": node.get("language"),
        "name": node.get("name"),
        "file_path": node.get("file_path"),
        "span": node.get("span"),
        "signature": node.get("signature"),
    }


def trim_evidence(evidence: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    return evidence[:limit]


def limits(**overrides: int | bool) -> dict[str, int | bool]:
    base = {
        "candidate_limit": AGENT_CANDIDATE_LIMIT,
        "file_limit": AGENT_FILE_LIMIT,
        "fact_limit": AGENT_FACT_LIMIT,
        "candidates_returned": 0,
        "files_returned": 0,
        "facts_returned": 0,
        "candidates_truncated": False,
        "files_truncated": False,
        "facts_truncated": False,
    }
    base.update(overrides)
    return base


def dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for node in nodes:
        result[node["node_id"]] = node
    return list(result.values())


def as_compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
