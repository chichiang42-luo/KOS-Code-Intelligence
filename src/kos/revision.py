from __future__ import annotations

from .schemas import Edge, utc_now


def supersede_edge(edge: Edge, reason: str) -> Edge:
    edge.status = "superseded"
    edge.updated_at = utc_now()
    edge.score_breakdown["revision"] = 1.0
    if edge.evidence:
        edge.evidence[-1].data["revision_reason"] = reason
    return edge
