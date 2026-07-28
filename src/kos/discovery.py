from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .ids import stable_id
from .schemas import Edge, Evidence, Node, Observation


@dataclass(slots=True)
class GraphBuild:
    nodes: list[Node]
    edges: list[Edge]
    parse_errors: list[Observation]


def discover(observations: list[Observation]) -> GraphBuild:
    repo_id = observations[0].repo_id if observations else "repo"
    nodes: dict[str, Node] = {}
    fq_to_id: dict[str, str] = {}
    short_to_ids: dict[str, list[str]] = defaultdict(list)
    import_aliases: dict[str, dict[str, str]] = defaultdict(dict)

    repo_node = Node(
        node_id=stable_id("node", repo_id, "repository"),
        repo_id=repo_id,
        kind="repository",
        name=repo_id,
        fqname=repo_id,
        language="python",
        file_path=None,
        span=None,
    )
    nodes[repo_node.node_id] = repo_node
    fq_to_id[repo_node.fqname] = repo_node.node_id

    for obs in observations:
        if obs.kind not in {"file", "module", "class", "function", "method"}:
            continue
        node_id = node_id_for(obs)
        node = Node(
            node_id=node_id,
            repo_id=obs.repo_id,
            kind=obs.kind,  # type: ignore[arg-type]
            name=obs.name,
            fqname=obs.fqname,
            language="python",
            file_path=obs.file_path,
            span=obs.span,
            signature=obs.signature,
            doc=obs.doc,
            hash=stable_id("sha1", obs.fqname, obs.span.start_line if obs.span else 0, length=16),
            provenance=[Evidence("ast", obs.file_path, obs.span).to_dict()],
        )
        nodes[node_id] = node
        fq_to_id[obs.fqname] = node_id
        short_to_ids[obs.name].append(node_id)

    for obs in observations:
        if obs.kind not in {"file", "module", "class", "function", "method"}:
            continue
        node = nodes.get(node_id_for(obs))
        if not node:
            continue
        if obs.kind == "file":
            node.parent_id = repo_node.node_id
        elif obs.parent and obs.parent in fq_to_id:
            node.parent_id = fq_to_id[obs.parent]

    edges: dict[tuple[str, str, str], Edge] = {}

    def add_edge(src: str, dst: str, rel_type: str, confidence: float, evidence: Evidence) -> None:
        key = (src, dst, rel_type)
        if key in edges:
            edges[key].frequency += 1
            edges[key].evidence.append(evidence)
            return
        edges[key] = Edge(
            edge_id=stable_id("edge", src, dst, rel_type),
            src_id=src,
            dst_id=dst,
            rel_type=rel_type,  # type: ignore[arg-type]
            confidence=confidence,
            evidence=[evidence],
            score_breakdown={"syntax": 1.0, "symbol_resolution": confidence},
        )

    for obs in observations:
        if obs.kind == "file":
            add_edge(repo_node.node_id, fq_to_id[obs.fqname], "CONTAINS", 1.0, Evidence("file_scan", obs.file_path))
        elif obs.kind == "module":
            parent_id = fq_to_id.get(obs.parent or "")
            if parent_id:
                add_edge(parent_id, fq_to_id[obs.fqname], "CONTAINS", 1.0, Evidence("module_path", obs.file_path))
        elif obs.kind in {"class", "function", "method"}:
            parent_id = fq_to_id.get(obs.parent or "")
            if parent_id:
                add_edge(parent_id, fq_to_id[obs.fqname], "CONTAINS", 1.0, Evidence("ast_parent", obs.file_path, obs.span))
        elif obs.kind == "import":
            import_aliases[obs.fqname][obs.name] = obs.target or obs.name
            target_id = resolve_symbol(obs.target or "", fq_to_id, short_to_ids)
            src_id = fq_to_id.get(obs.fqname)
            if src_id and target_id:
                add_edge(src_id, target_id, "IMPORTS", 0.9, Evidence("ast_import", obs.file_path, obs.span, {"target": obs.target}))
        elif obs.kind == "inherit":
            src_id = fq_to_id.get(obs.fqname)
            target_id = resolve_symbol(obs.target or "", fq_to_id, short_to_ids)
            if src_id and target_id:
                add_edge(src_id, target_id, "INHERITS", 0.95, Evidence("ast_inherit", obs.file_path, obs.span, {"target": obs.target}))
        elif obs.kind == "call":
            src_id = fq_to_id.get(obs.fqname)
            target_name = apply_import_alias(obs.target or "", import_aliases.get(module_scope(obs.fqname), {}))
            target_id = resolve_symbol(target_name, fq_to_id, short_to_ids)
            if src_id and target_id and src_id != target_id:
                add_edge(src_id, target_id, "CALLS", 0.85, Evidence("ast_call", obs.file_path, obs.span, {"target": obs.target}))
            elif src_id:
                fallback_id = resolve_symbol(last_part(target_name), fq_to_id, short_to_ids)
                if fallback_id and fallback_id != src_id:
                    add_edge(src_id, fallback_id, "MAY_CALL", 0.45, Evidence("ast_call_unresolved", obs.file_path, obs.span, {"target": obs.target}))

    for obs in observations:
        if obs.kind in {"class", "function", "method"} and obs.parent:
            parent_id = fq_to_id.get(obs.parent)
            child_id = fq_to_id.get(obs.fqname)
            if parent_id and child_id:
                add_edge(parent_id, child_id, "DEFINES", 1.0, Evidence("ast_define", obs.file_path, obs.span))

    parse_errors = [obs for obs in observations if obs.kind == "parse_error"]
    return GraphBuild(list(nodes.values()), list(edges.values()), parse_errors)


def node_id_for(obs: Observation) -> str:
    span = obs.span.to_dict() if obs.span else {}
    return stable_id("node", obs.repo_id, obs.kind, obs.fqname, obs.file_path, span)


def resolve_symbol(name: str, fq_to_id: dict[str, str], short_to_ids: dict[str, list[str]]) -> str | None:
    if not name:
        return None
    if name in fq_to_id:
        return fq_to_id[name]
    if name.startswith(".") and name.strip(".") in fq_to_id:
        return fq_to_id[name.strip(".")]
    candidates = short_to_ids.get(last_part(name), [])
    if len(candidates) == 1:
        return candidates[0]
    for fqname, node_id in fq_to_id.items():
        if fqname.endswith(f".{name}") or fqname.endswith(f".{last_part(name)}"):
            return node_id
    return None


def apply_import_alias(name: str, aliases: dict[str, str]) -> str:
    head, *tail = name.split(".")
    if head in aliases:
        return ".".join([aliases[head], *tail])
    return name


def module_scope(fqname: str) -> str:
    parts = fqname.split(".")
    if len(parts) <= 2:
        return fqname
    return ".".join(parts[:-1])


def last_part(name: str) -> str:
    return name.rsplit(".", 1)[-1]
