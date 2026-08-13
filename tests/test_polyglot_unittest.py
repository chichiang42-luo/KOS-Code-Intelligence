from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kos.indexer import index_status, validate_parser_runtime
from kos.observation import observe_file, observe_repo
from kos.service import KosService
from kos.tree_sitter_observer import TreeSitterRuntimeError, tree_sitter_runtime_supported

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "sample_polyglot"


class PolyglotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "sample_polyglot"
        self.store_root = self.tmp / "store"
        shutil.copytree(SAMPLE, self.repo)
        self.service = KosService(self.repo, self.store_root, "sample_polyglot")

    def tearDown(self) -> None:
        for attempt in range(5):
            try:
                shutil.rmtree(self.tmp)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def test_observation_supports_all_declared_languages(self) -> None:
        observations = observe_repo(self.repo, "sample_polyglot")
        languages = {str(item.raw.get("language")) for item in observations if item.kind == "file"}
        self.assertEqual(
            {
                "python",
                "javascript",
                "typescript",
                "tsx",
                "css",
                "bash",
                "go",
                "java",
                "rust",
                "c",
                "cpp",
            },
            languages,
        )
        self.assertEqual([], [item for item in observations if item.kind == "parse_error"])

    def test_typescript_import_alias_and_calls_are_connected(self) -> None:
        result = self.service.index()
        self.assertEqual(0, result["parse_errors"])
        calls = self.service.calls("checkout")
        self.assertEqual("typescript", calls["target"]["language"])
        self.assertIn(
            "frontend.src.payment.verifyPayment",
            {fact["dst"]["fqname"] for fact in calls["facts"]},
        )
        callers = self.service.who_calls("verifyPayment")
        self.assertIn("frontend.src.app.checkout", {fact["src"]["fqname"] for fact in callers["facts"]})

    def test_java_go_shell_and_css_graph_facts(self) -> None:
        self.service.index()

        java = self.service.impact("PaymentService")
        self.assertEqual("com.example.payment.PaymentService", java["target"]["fqname"])
        self.assertIn(
            "com.example.payment.BaseService",
            {fact["dst"]["fqname"] for fact in java["facts"] if fact["rel_type"] == "INHERITS"},
        )

        go = self.service.calls("Processor.Verify")
        self.assertIn("go.payment.payment.validate", {fact["dst"]["fqname"] for fact in go["facts"]})

        shell = self.service.calls("build")
        self.assertIn("scripts.lib.helper", {fact["dst"]["fqname"] for fact in shell["facts"]})

        selector = self.service.resolve(".checkout-button:hover")
        self.assertEqual("css", selector["target"]["language"])
        css_imports = self.service.impact("frontend.src.styles.checkout")
        self.assertIn("IMPORTS", {fact["rel_type"] for fact in css_imports["facts"]})

    def test_non_python_incremental_update_and_language_status(self) -> None:
        result = self.service.index()
        self.assertEqual(2, result["languages"]["typescript"])
        runtime = self.repo / "frontend" / "public" / "runtime.js"
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\nexport function stopApp() {}\n", encoding="utf-8")

        stale = index_status(self.repo, "sample_polyglot", self.store_root)
        self.assertEqual("stale", stale["state"])
        self.assertEqual(1, stale["files_changed"])
        updated = self.service.update()
        self.assertEqual(1, updated["files_changed"])
        self.assertEqual("javascript", self.service.resolve("stopApp")["target"]["language"])

    def test_rust_c_and_cpp_relationships(self) -> None:
        self.service.index()

        rust_type = self.service.impact("rust.src.workflow.SubCommand")
        self.assertIn(
            "rust.src.workflow.Command",
            {fact["dst"]["fqname"] for fact in rust_type["facts"] if fact["rel_type"] == "INHERITS"},
        )
        rust_method = self.service.calls("rust.src.workflow.SubCommand.run")
        self.assertIn("rust.src.ops.npu_fused_ops", {fact["dst"]["fqname"] for fact in rust_method["facts"]})

        c_result = self.service.calls("run_workflow")
        self.assertEqual("c", c_result["target"]["language"])
        self.assertIn("native.c.fused_ops.npu_fused_ops", {fact["dst"]["fqname"] for fact in c_result["facts"]})

        cpp_type = self.service.impact("native.cpp.workflow.workflow.SubCommand")
        self.assertIn(
            "native.cpp.base.BaseCommand",
            {fact["dst"]["fqname"] for fact in cpp_type["facts"] if fact["rel_type"] == "INHERITS"},
        )
        cpp_method = self.service.calls("native.cpp.workflow.workflow.SubCommand.run")
        self.assertIn("native.cpp.fused_ops.npuFusedOps", {fact["dst"]["fqname"] for fact in cpp_method["facts"]})

    def test_duplicate_symbol_across_languages_is_ambiguous(self) -> None:
        (self.repo / "backend" / "checkout.py").write_text("def checkout():\n    return True\n", encoding="utf-8")
        self.service.index()
        result = self.service.resolve("checkout")
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual({"python", "typescript"}, {item["language"] for item in result["candidates"]})

    def test_languages_capability_reports_supported_and_indexed(self) -> None:
        self.service.index()
        result = self.service.languages()
        self.assertEqual("ok", result["status"])
        self.assertTrue({"java", "rust", "c", "cpp"} <= {item["name"] for item in result["supported"]})
        self.assertEqual(2, result["indexed"]["css"])

    def test_large_typescript_file_uses_stable_tree_cursor_traversal(self) -> None:
        path = self.repo / "frontend" / "src" / "generated-large.ts"
        source = "\n".join(
            f"export function generated{i}(value: number): number {{ return Math.max(value, {i}); }}"
            for i in range(700)
        )
        path.write_text(source, encoding="utf-8")

        observations = observe_file(path, self.repo, "sample_polyglot")
        self.assertEqual(700, sum(item.kind == "function" for item in observations))
        self.assertEqual([], [item for item in observations if item.kind == "parse_error"])

    def test_tree_sitter_runtime_version_guard(self) -> None:
        self.assertTrue(tree_sitter_runtime_supported("0.25.2"))
        self.assertFalse(tree_sitter_runtime_supported("0.26.0"))
        self.assertFalse(tree_sitter_runtime_supported("0.24.0"))

        typescript_path = self.repo / "frontend" / "src" / "app.ts"
        with patch("kos.tree_sitter_observer.version", return_value="0.26.0"):
            with self.assertRaisesRegex(TreeSitterRuntimeError, "requires >=0.25,<0.26"):
                validate_parser_runtime([typescript_path])


if __name__ == "__main__":
    unittest.main()
