from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import unittest
from pathlib import Path

try:
    import mcp  # noqa: F401
except ModuleNotFoundError:
    HAS_MCP = False
else:
    HAS_MCP = True

from kos.mcp_server import create_mcp_server
from kos.service import KosService

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "sample_shop"


@unittest.skipUnless(HAS_MCP, "MCP SDK is not installed")
class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "sample_shop"
        self.store_root = self.tmp / "store"
        shutil.copytree(SAMPLE, self.repo)
        shutil.rmtree(self.repo / ".kos", ignore_errors=True)
        self.service = KosService(self.repo, self.store_root, "sample_shop")
        self.service.index()

    def tearDown(self) -> None:
        for attempt in range(5):
            try:
                shutil.rmtree(self.tmp)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def test_mcp_lists_and_calls_structured_tools(self) -> None:
        server = create_mcp_server(self.service)
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            {
                "kos_status",
                "kos_languages",
                "kos_update",
                "kos_resolve",
                "kos_who_calls",
                "kos_calls",
                "kos_impact",
                "kos_read_plan",
                "kos_pack",
            },
            names,
        )
        result = asyncio.run(server.call_tool("kos_pack", {"query": "verify_payment"}))
        self.assertFalse(result.is_error)
        self.assertEqual("ok", result.structured_content["status"])
        self.assertEqual("fresh", result.structured_content["index"]["state"])


if __name__ == "__main__":
    unittest.main()
