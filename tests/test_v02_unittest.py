from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from kos.evaluator import run_evaluation
from kos.indexer import IndexLock, IndexLockError, index_repo, index_status, update_repo
from kos.service import KosService
from kos.storage import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "sample_shop"


class V02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "sample_shop"
        self.store_root = self.tmp / "store"
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

    def test_status_and_incremental_add_change_delete(self) -> None:
        self.assertEqual("uninitialized", index_status(self.repo, "sample_shop", self.store_root)["state"])
        index_repo(self.repo, "sample_shop", self.store_root)
        self.assertEqual("fresh", index_status(self.repo, "sample_shop", self.store_root)["state"])

        added = self.repo / "app" / "new_feature.py"
        added.write_text("def new_feature():\n    return True\n", encoding="utf-8")
        payment = self.repo / "app" / "payment" / "service.py"
        payment.write_text(payment.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        (self.repo / "app" / "auth" / "repository.py").unlink()

        status = index_status(self.repo, "sample_shop", self.store_root)
        self.assertEqual("stale", status["state"])
        self.assertEqual(1, status["files_added"])
        self.assertEqual(1, status["files_deleted"])
        result = update_repo(self.repo, "sample_shop", self.store_root)
        self.assertEqual("incremental", result["mode"])
        self.assertEqual("fresh", index_status(self.repo, "sample_shop", self.store_root)["state"])

    def test_line_movement_keeps_node_id(self) -> None:
        service = KosService(self.repo, self.store_root, "sample_shop")
        service.index()
        before = service.resolve("verify_payment")["target"]["node_id"]
        path = self.repo / "app" / "payment" / "service.py"
        path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        service.update()
        after = service.resolve("verify_payment")["target"]["node_id"]
        self.assertEqual(before, after)

    def test_syntax_error_preserves_previous_file_graph(self) -> None:
        service = KosService(self.repo, self.store_root, "sample_shop")
        service.index()
        path = self.repo / "app" / "payment" / "service.py"
        path.write_text("def verify_payment(:\n", encoding="utf-8")
        result = service.update()
        self.assertGreaterEqual(result["parse_errors"], 1)
        self.assertEqual("ok", service.resolve("verify_payment")["status"])

    def test_relative_import_alias_and_self_calls_resolve(self) -> None:
        helper = self.repo / "app" / "payment" / "helper.py"
        helper.write_text("def approve(value):\n    return bool(value)\n", encoding="utf-8")
        workflow = self.repo / "app" / "payment" / "workflow.py"
        workflow.write_text(
            "from .helper import approve as approve_payment\n\n"
            "class Workflow:\n"
            "    def run(self, value):\n"
            "        return self.finish(approve_payment(value))\n\n"
            "    def finish(self, value):\n"
            "        return value\n",
            encoding="utf-8",
        )
        index_repo(self.repo, "sample_shop", self.store_root)
        service = KosService(self.repo, self.store_root, "sample_shop")
        called = {
            fact["dst"]["fqname"]
            for fact in service.calls("app.payment.workflow.Workflow.run")["facts"]
        }
        self.assertIn("app.payment.helper.approve", called)
        self.assertIn("app.payment.workflow.Workflow.finish", called)

    def test_duplicate_short_name_is_not_arbitrarily_selected(self) -> None:
        (self.repo / "app" / "dup_a.py").write_text("def execute():\n    return 'a'\n", encoding="utf-8")
        (self.repo / "app" / "dup_b.py").write_text("def execute():\n    return 'b'\n", encoding="utf-8")
        (self.repo / "app" / "caller.py").write_text(
            "def call_unknown():\n    return execute()\n",
            encoding="utf-8",
        )
        index_repo(self.repo, "sample_shop", self.store_root)
        service = KosService(self.repo, self.store_root, "sample_shop")
        result = service.calls("call_unknown")
        self.assertEqual([], result["facts"])

    def test_rebuild_backs_up_legacy_database(self) -> None:
        kos_dir = self.store_root / ".kos"
        kos_dir.mkdir(parents=True)
        conn = sqlite3.connect(kos_dir / "graph.db")
        conn.execute("CREATE TABLE legacy(value TEXT)")
        conn.commit()
        conn.close()
        status = index_status(self.repo, "sample_shop", self.store_root)
        self.assertEqual("incompatible", status["state"])
        result = index_repo(self.repo, "sample_shop", self.store_root, rebuild=True)
        self.assertTrue(result["backup_path"])
        self.assertTrue(Path(result["backup_path"]).joinpath("graph.db").exists())

    def test_service_contract_includes_freshness_and_limits(self) -> None:
        service = KosService(self.repo, self.store_root, "sample_shop")
        service.index()
        result = service.pack("verify_payment")
        self.assertEqual("1.0", result["schema_version"])
        self.assertEqual("fresh", result["index"]["state"])
        self.assertIn("facts_truncated", result["limits"])
        self.assertIsNone(result["error"])

    def test_service_reuses_bound_repo_id(self) -> None:
        KosService(self.repo, self.store_root, "stable-repo-id").index()
        service = KosService(self.repo, self.store_root)
        self.assertEqual("stable-repo-id", service.repo_id)
        self.assertEqual("ok", service.resolve("verify_payment")["status"])

    def test_evaluation_suite(self) -> None:
        cases = self.tmp / "cases.json"
        cases.write_text(
            json.dumps(
                {
                    "version": 1,
                    "cases": [
                        {
                            "id": "verify",
                            "query": "verify_payment",
                            "target_fqname": "app.payment.service.verify_payment",
                            "required_files": ["app/payment/service.py"],
                            "required_facts": [
                                {
                                    "rel_type": "CALLS",
                                    "src_fqname": "app.order.service.checkout",
                                    "dst_fqname": "app.payment.service.verify_payment",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = run_evaluation(KosService(self.repo, self.store_root, "sample_shop"), cases)
        self.assertEqual("ok", report["status"])
        self.assertEqual(1, report["summary"]["passed"])

    def test_history_only_records_real_changes(self) -> None:
        index_repo(self.repo, "sample_shop", self.store_root)
        with Store(self.store_root) as store:
            initial = store.conn.execute("SELECT count(*) FROM history_events").fetchone()[0]
        index_repo(self.repo, "sample_shop", self.store_root)
        with Store(self.store_root) as store:
            after = store.conn.execute("SELECT count(*) FROM history_events").fetchone()[0]
        self.assertEqual(initial, after)

    def test_stale_index_lock_is_removed_when_owner_pid_is_dead(self) -> None:
        lock_path = self.store_root / ".kos" / "index.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text("2147483647\n", encoding="ascii")

        with IndexLock(self.store_root, timeout=0.1):
            self.assertEqual(str(os.getpid()), lock_path.read_text(encoding="ascii").strip())
        self.assertFalse(lock_path.exists())

    def test_live_index_lock_is_not_removed(self) -> None:
        lock_path = self.store_root / ".kos" / "index.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")

        with self.assertRaises(IndexLockError):
            with IndexLock(self.store_root, timeout=0.01):
                pass


if __name__ == "__main__":
    unittest.main()
