from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .service import KosService, error_code, error_hint
from .storage import StoreError


def create_mcp_server(service: KosService, log_level: str = "INFO"):
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as exc:
        raise RuntimeError("MCP SDK is not installed; install kos-code-intelligence from PyPI") from exc

    server = MCPServer(
        name="kos-code-intelligence",
        title="KOS Code Intelligence",
        description="Local polyglot code knowledge graph for coding agents.",
        instructions=(
            "Call kos_status first. If the index is uninitialized or stale, call kos_update. "
            "Use kos_pack as the default entry point before reading or editing a symbol."
        ),
        version=__version__,
        log_level=log_level.upper(),
    )

    @server.tool(name="kos_status", structured_output=True)
    def kos_status() -> dict[str, Any]:
        """Check whether the bound repository index is fresh."""
        return service.status()

    @server.tool(name="kos_languages", structured_output=True)
    def kos_languages() -> dict[str, Any]:
        """List supported languages and indexed source-file counts."""
        return service.languages()

    @server.tool(name="kos_update", structured_output=True)
    def kos_update() -> dict[str, Any]:
        """Create or incrementally update the bound repository index."""
        try:
            return service.update()
        except (OSError, ValueError, StoreError) as exc:
            return {
                "status": "error",
                "error": {"code": error_code(exc), "message": str(exc), "hint": error_hint(exc)},
            }

    @server.tool(name="kos_resolve", structured_output=True)
    def kos_resolve(query: str) -> dict[str, Any]:
        """Resolve a symbol, fully qualified name, or file path."""
        return service.resolve(query)

    @server.tool(name="kos_who_calls", structured_output=True)
    def kos_who_calls(query: str, fact_limit: int = 40) -> dict[str, Any]:
        """Return direct callers of a symbol."""
        return service.who_calls(query, bounded(fact_limit, 1, 200))

    @server.tool(name="kos_calls", structured_output=True)
    def kos_calls(query: str, fact_limit: int = 40) -> dict[str, Any]:
        """Return direct dependencies called by a symbol."""
        return service.calls(query, bounded(fact_limit, 1, 200))

    @server.tool(name="kos_impact", structured_output=True)
    def kos_impact(query: str, fact_limit: int = 40) -> dict[str, Any]:
        """Return one-hop call, import, and inheritance impact."""
        return service.impact(query, bounded(fact_limit, 1, 200))

    @server.tool(name="kos_read_plan", structured_output=True)
    def kos_read_plan(query: str, file_limit: int = 10, fact_limit: int = 40) -> dict[str, Any]:
        """Return the files an agent should read before editing a symbol."""
        return service.read_plan(
            query,
            bounded(file_limit, 1, 50),
            bounded(fact_limit, 1, 200),
        )

    @server.tool(name="kos_pack", structured_output=True)
    def kos_pack(query: str, file_limit: int = 10, fact_limit: int = 40) -> dict[str, Any]:
        """Return the default target, impact facts, evidence, and read plan."""
        return service.pack(
            query,
            bounded(file_limit, 1, 50),
            bounded(fact_limit, 1, 200),
        )

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kos-mcp", description="KOS MCP stdio server")
    parser.add_argument("--repo", default=".", help="Repository root bound to this MCP server")
    parser.add_argument("--store-root", default=None, help="Directory containing .kos storage")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    service = KosService(
        Path(args.repo).resolve(),
        Path(args.store_root).resolve() if args.store_root else None,
    )
    try:
        server = create_mcp_server(service, args.log_level)
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1
    server.run(transport="stdio")
    return 0


def bounded(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


if __name__ == "__main__":
    raise SystemExit(main())
