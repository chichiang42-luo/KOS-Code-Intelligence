from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

NodeKind = Literal[
    "repository",
    "file",
    "module",
    "class",
    "interface",
    "enum",
    "record",
    "struct",
    "trait",
    "function",
    "method",
    "selector",
]
RelType = Literal["CONTAINS", "DEFINES", "IMPORTS", "CALLS", "MAY_CALL", "INHERITS"]
Status = Literal["active", "superseded", "deleted", "uncertain"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Span:
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class Evidence:
    type: str
    file_path: str
    span: Span | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {"type": self.type, "file_path": self.file_path, "data": self.data}
        if self.span:
            result["span"] = self.span.to_dict()
        return result


@dataclass(slots=True)
class Node:
    node_id: str
    repo_id: str
    kind: NodeKind
    name: str
    fqname: str
    language: str
    file_path: str | None
    span: Span | None
    signature: str | None = None
    parent_id: str | None = None
    doc: str | None = None
    hash: str = ""
    confidence: float = 1.0
    status: Status = "active"
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    history_ref: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.span:
            data["span"] = self.span.to_dict()
        return data


@dataclass(slots=True)
class Edge:
    edge_id: str
    src_id: str
    dst_id: str
    rel_type: RelType
    confidence: float
    evidence: list[Evidence] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    frequency: int = 1
    status: Status = "active"
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    history_ref: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data


@dataclass(slots=True)
class Observation:
    kind: str
    repo_id: str
    file_path: str
    name: str
    fqname: str
    span: Span | None = None
    parent: str | None = None
    target: str | None = None
    signature: str | None = None
    doc: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.span:
            data["span"] = self.span.to_dict()
        return data


@dataclass(slots=True)
class HistoryEvent:
    event_id: str
    entity_type: Literal["node", "edge", "run"]
    entity_id: str
    repo_id: str
    op: str
    reason: str
    actor: str = "kos"
    timestamp: str = field(default_factory=utc_now)
    patch: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
