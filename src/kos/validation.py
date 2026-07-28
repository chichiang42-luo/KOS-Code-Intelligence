from __future__ import annotations

from .schemas import Edge


def validate_edges(edges: list[Edge], min_accept_confidence: float = 0.65) -> list[Edge]:
    """Keep high-confidence facts active and downgrade weak CALLS facts to MAY_CALL."""
    validated: list[Edge] = []
    for edge in edges:
        if edge.rel_type == "CALLS" and edge.confidence < min_accept_confidence:
            edge.rel_type = "MAY_CALL"
            edge.confidence = min(edge.confidence, 0.49)
        validated.append(edge)
    return validated
