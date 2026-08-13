from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .ids import stable_id
from .schemas import Edge, Evidence, Node, Observation

DEFINITION_KINDS = {
    "class",
    "enum",
    "function",
    "interface",
    "method",
    "record",
    "selector",
    "struct",
    "trait",
}
NODE_KINDS = {"file", "module", *DEFINITION_KINDS}


@dataclass(slots=True)
class GraphBuild:
    nodes: list[Node]
    edges: list[Edge]
    parse_errors: list[Observation]


def discover(observations: list[Observation]) -> GraphBuild:
    repo_id = observations[0].repo_id if observations else "repo"
    nodes: dict[str, Node] = {}
    fq_to_ids: dict[str, list[str]] = defaultdict(list)
    short_to_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    suffix_to_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    import_aliases: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)

    repo_node = Node(
        node_id=stable_id("node", repo_id, "repository"),
        repo_id=repo_id,
        kind="repository",
        name=repo_id,
        fqname=repo_id,
        language="polyglot",
        file_path=None,
        span=None,
    )
    nodes[repo_node.node_id] = repo_node
    fq_to_ids[repo_node.fqname].append(repo_node.node_id)

    for obs in observations:
        if obs.kind not in NODE_KINDS:
            continue
        language = observation_language(obs)
        node_id = node_id_for(obs)
        parser = obs.raw.get("parser", "syntax")
        node = Node(
            node_id=node_id,
            repo_id=obs.repo_id,
            kind=obs.kind,  # type: ignore[arg-type]
            name=obs.name,
            fqname=obs.fqname,
            language=language,
            file_path=obs.file_path,
            span=obs.span,
            signature=obs.signature,
            doc=obs.doc,
            hash=stable_id("sha256", obs.repo_id, obs.kind, obs.fqname, obs.file_path, length=16),
            provenance=[Evidence(str(parser), obs.file_path, obs.span).to_dict()],
        )
        nodes[node_id] = node
        fq_to_ids[obs.fqname].append(node_id)
        short_to_ids[(language, obs.name)].append(node_id)
        parts = obs.fqname.split(".")
        for index in range(len(parts)):
            suffix_to_ids[(language, ".".join(parts[index:]))].append(node_id)

    for obs in observations:
        if obs.kind not in NODE_KINDS:
            continue
        node = nodes.get(node_id_for(obs))
        if not node:
            continue
        if obs.kind == "file":
            node.parent_id = repo_node.node_id
        elif obs.parent:
            node.parent_id = resolve_exact_parent(
                obs.parent,
                observation_language(obs),
                obs.file_path,
                fq_to_ids,
                nodes,
            )

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
        language = observation_language(obs)
        parser = str(obs.raw.get("parser", "syntax"))
        evidence_prefix = "ast" if parser == "python_ast" else parser
        if obs.kind == "file":
            add_edge(repo_node.node_id, node_id_for(obs), "CONTAINS", 1.0, Evidence("file_scan", obs.file_path))
        elif obs.kind in {"module", *DEFINITION_KINDS}:
            node = nodes.get(node_id_for(obs))
            if node and node.parent_id:
                relation = "module_path" if obs.kind == "module" else f"{evidence_prefix}_parent"
                add_edge(node.parent_id, node.node_id, "CONTAINS", 1.0, Evidence(relation, obs.file_path, obs.span))
        elif obs.kind == "import":
            import_aliases[(language, obs.fqname)][obs.name] = obs.target or obs.name
            target_id = resolve_symbol(
                obs.target or "",
                language,
                fq_to_ids,
                short_to_ids,
                suffix_to_ids,
                nodes,
            )
            src_id = resolve_exact_parent(obs.fqname, language, obs.file_path, fq_to_ids, nodes)
            if src_id and target_id:
                add_edge(
                    src_id,
                    target_id,
                    "IMPORTS",
                    0.9,
                    Evidence(f"{evidence_prefix}_import", obs.file_path, obs.span, {"target": obs.target}),
                )
        elif obs.kind == "inherit":
            src_id = resolve_exact_parent(obs.fqname, language, obs.file_path, fq_to_ids, nodes)
            target_id = resolve_symbol(
                obs.target or "",
                language,
                fq_to_ids,
                short_to_ids,
                suffix_to_ids,
                nodes,
            )
            if src_id and target_id:
                add_edge(
                    src_id,
                    target_id,
                    "INHERITS",
                    0.95,
                    Evidence(f"{evidence_prefix}_inherit", obs.file_path, obs.span, {"target": obs.target}),
                )
        elif obs.kind == "call":
            src_id = resolve_exact_parent(obs.fqname, language, obs.file_path, fq_to_ids, nodes)
            aliases = import_aliases.get((language, obs.raw.get("module") or module_scope(obs.fqname)), {})
            target_name = normalize_call_target(
                obs.target or "",
                obs.raw.get("class_fqname"),
                aliases,
            )
            target_id = resolve_symbol(
                target_name,
                language,
                fq_to_ids,
                short_to_ids,
                suffix_to_ids,
                nodes,
            )
            if src_id and target_id and src_id != target_id:
                add_edge(
                    src_id,
                    target_id,
                    "CALLS",
                    0.85,
                    Evidence(f"{evidence_prefix}_call", obs.file_path, obs.span, {"target": obs.target}),
                )
            elif src_id:
                fallback_id = resolve_unique(short_to_ids.get((language, last_part(target_name)), []))
                if fallback_id and fallback_id != src_id:
                    add_edge(
                        src_id,
                        fallback_id,
                        "MAY_CALL",
                        0.45,
                        Evidence(
                            f"{evidence_prefix}_call_unresolved",
                            obs.file_path,
                            obs.span,
                            {"target": obs.target},
                        ),
                    )

    for obs in observations:
        if obs.kind in DEFINITION_KINDS and obs.parent:
            child_id = nodes.get(node_id_for(obs))
            if child_id and child_id.parent_id:
                parser = str(obs.raw.get("parser", "syntax"))
                evidence_prefix = "ast" if parser == "python_ast" else parser
                add_edge(
                    child_id.parent_id,
                    child_id.node_id,
                    "DEFINES",
                    1.0,
                    Evidence(f"{evidence_prefix}_define", obs.file_path, obs.span),
                )

    parse_errors = [obs for obs in observations if obs.kind == "parse_error"]
    return GraphBuild(list(nodes.values()), list(edges.values()), parse_errors)


def node_id_for(obs: Observation) -> str:
    return stable_id("node", obs.repo_id, obs.kind, obs.fqname, obs.file_path)


def observation_language(obs: Observation) -> str:
    return str(obs.raw.get("language", "python"))


def resolve_exact_parent(
    fqname: str,
    language: str,
    file_path: str,
    fq_to_ids: dict[str, list[str]],
    nodes: dict[str, Node],
) -> str | None:
    candidates = [
        node_id
        for node_id in fq_to_ids.get(fqname, [])
        if nodes[node_id].language == language and nodes[node_id].file_path == file_path
    ]
    if len(candidates) == 1:
        return candidates[0]
    same_language = [node_id for node_id in fq_to_ids.get(fqname, []) if nodes[node_id].language == language]
    return resolve_unique(same_language)


def resolve_symbol(
    name: str,
    language: str,
    fq_to_ids: dict[str, list[str]],
    short_to_ids: dict[tuple[str, str], list[str]],
    suffix_to_ids: dict[tuple[str, str], list[str]],
    nodes: dict[str, Node],
) -> str | None:
    if not name:
        return None
    normalized = name.strip(".") if name.startswith(".") else name
    exact = [node_id for node_id in fq_to_ids.get(normalized, []) if nodes[node_id].language == language]
    result = resolve_unique(exact)
    if result:
        return result
    result = resolve_unique(suffix_to_ids.get((language, normalized), []))
    if result:
        return result
    return resolve_unique(short_to_ids.get((language, last_part(normalized)), []))


def apply_import_alias(name: str, aliases: dict[str, str]) -> str:
    head, *tail = name.split(".")
    if head in aliases:
        return ".".join([aliases[head], *tail])
    return name


def normalize_call_target(name: str, class_fqname: str | None, aliases: dict[str, str]) -> str:
    if class_fqname and any(name.startswith(f"{prefix}.") for prefix in ("self", "cls", "this")):
        return f"{class_fqname}.{name.split('.', 1)[1]}"
    return apply_import_alias(name, aliases)


def resolve_unique(candidates: list[str]) -> str | None:
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def module_scope(fqname: str) -> str:
    parts = fqname.split(".")
    if len(parts) <= 2:
        return fqname
    return ".".join(parts[:-1])


def last_part(name: str) -> str:
    return name.rsplit(".", 1)[-1]
