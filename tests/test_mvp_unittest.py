from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from kos.agent_tools import agent_read_plan, agent_resolve, agent_who_calls
from kos.indexer import index_repo
from kos.observation import observe_repo
from kos.storage import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "sample_shop"


class MvpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "sample_shop"
        shutil.copytree(SAMPLE, self.repo)
        shutil.rmtree(self.repo / ".kos", ignore_errors=True)

    def tearDown(self) -> None:
        for attempt in range(5):
            try:
                shutil.rmtree(self.tmp)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def test_observation_finds_defs_calls_and_parse_errors(self) -> None:
        observations = observe_repo(self.repo, "sample_shop")
        kinds = {obs.kind for obs in observations}
        self.assertIn("class", kinds)
        self.assertIn("function", kinds)
        self.assertIn("import", kinds)
        self.assertIn("call", kinds)
        self.assertEqual(1, len([obs for obs in observations if obs.kind == "parse_error"]))

    def test_index_search_and_neighborhood(self) -> None:
        stats = index_repo(self.repo, "sample_shop")
        self.assertEqual("ok", stats["status"])
        self.assertGreaterEqual(stats["nodes"], 10)
        self.assertGreaterEqual(stats["edges"], 10)

        store = Store(self.repo)
        try:
            results = store.search("verify_payment")
            self.assertTrue(results)
            verify_node = results[0]
            graph = store.neighborhood(verify_node["node_id"], hops=1)
            rel_types = {edge["rel_type"] for edge in graph["edges"]}
            self.assertIn("CALLS", rel_types)
        finally:
            store.close()

    def test_refactor_marks_missing_symbol_deleted(self) -> None:
        index_repo(self.repo, "sample_shop")
        service = self.repo / "app" / "payment" / "service.py"
        service.write_text(
            "def verify_payment_v2(order_id: str) -> bool:\n    return bool(order_id)\n",
            encoding="utf-8",
        )
        stats = index_repo(self.repo, "sample_shop")
        self.assertGreater(stats["nodes_deleted"], 0)
        store = Store(self.repo)
        try:
            self.assertTrue(store.search("verify_payment_v2"))
        finally:
            store.close()

    def test_agent_resolve_finds_unique_symbol(self) -> None:
        index_repo(self.repo, "sample_shop")
        store = Store(self.repo)
        try:
            result = agent_resolve(store, "verify_payment")
            self.assertEqual("ok", result["status"])
            self.assertEqual("app.payment.service.verify_payment", result["target"]["fqname"])
        finally:
            store.close()

    def test_agent_who_calls_returns_checkout_fact(self) -> None:
        index_repo(self.repo, "sample_shop")
        store = Store(self.repo)
        try:
            result = agent_who_calls(store, "verify_payment")
            self.assertEqual("ok", result["status"])
            callers = {fact["src"]["fqname"] for fact in result["facts"]}
            self.assertIn("app.order.service.checkout", callers)
        finally:
            store.close()

    def test_agent_read_plan_prioritizes_target_then_callers(self) -> None:
        index_repo(self.repo, "sample_shop")
        store = Store(self.repo)
        try:
            result = agent_read_plan(store, "verify_payment")
            self.assertEqual("ok", result["status"])
            files = result["files"]
            self.assertEqual("app/payment/service.py", files[0]["file_path"])
            self.assertIn("app/order/service.py", {item["file_path"] for item in files})
        finally:
            store.close()

    def test_agent_resolve_ambiguous_query_returns_candidates(self) -> None:
        index_repo(self.repo, "sample_shop")
        store = Store(self.repo)
        try:
            result = agent_resolve(store, "service")
            self.assertEqual("ambiguous", result["status"])
            self.assertLessEqual(len(result["candidates"]), 10)
            self.assertIsNone(result["target"])
        finally:
            store.close()

    def test_agent_resolve_not_found_is_stable_json_shape(self) -> None:
        index_repo(self.repo, "sample_shop")
        store = Store(self.repo)
        try:
            result = agent_resolve(store, "definitely_missing_symbol")
            self.assertEqual("not_found", result["status"])
            self.assertIsNone(result["target"])
            self.assertEqual([], result["files"])
            self.assertEqual([], result["facts"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
