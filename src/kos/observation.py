from __future__ import annotations

import ast
import tokenize
from pathlib import Path

from .languages import detect_language, iter_source_files, module_name_from_path
from .schemas import Observation, Span


def observe_repo(repo_path: Path, repo_id: str | None = None) -> list[Observation]:
    root = repo_path.resolve()
    rid = repo_id or root.name
    observations: list[Observation] = []
    for path in iter_source_files(root):
        observations.extend(observe_file(path, root, rid))
    return observations


def iter_python_files(repo_path: Path) -> list[Path]:
    """Compatibility helper retained for callers that only want Python files."""
    return [path for path in iter_source_files(repo_path) if detect_language(path) == "python"]


def observe_file(file_path: Path, repo_root: Path, repo_id: str) -> list[Observation]:
    language = detect_language(file_path)
    if language is None:
        return []
    if language != "python":
        from .tree_sitter_observer import observe_tree_sitter_file

        return observe_tree_sitter_file(file_path, repo_root, repo_id, language)
    return observe_python_file(file_path, repo_root, repo_id)


def observe_python_file(file_path: Path, repo_root: Path, repo_id: str) -> list[Observation]:
    rel_path = file_path.relative_to(repo_root).as_posix()
    module = module_name_from_path(Path(rel_path), "python")
    base_raw = {"language": "python", "parser": "python_ast"}
    observations = [
        Observation("file", repo_id, rel_path, file_path.name, rel_path, raw=dict(base_raw)),
        Observation("module", repo_id, rel_path, module, module, parent=rel_path, raw=dict(base_raw)),
    ]
    source = read_python_source(file_path)
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        observations.append(
            Observation(
                "parse_error",
                repo_id,
                rel_path,
                "SyntaxError",
                f"{module}:SyntaxError",
                span=Span(exc.lineno or 1, exc.offset or 0, exc.lineno or 1, exc.offset or 0),
                raw={**base_raw, "message": exc.msg},
            )
        )
        return observations
    visitor = AstObserver(repo_id, rel_path, module, file_path.name == "__init__.py")
    visitor.visit(tree)
    for observation in visitor.observations:
        observation.raw.setdefault("language", "python")
        observation.raw.setdefault("parser", "python_ast")
    observations.extend(visitor.observations)
    return observations


def read_python_source(file_path: Path) -> str:
    try:
        with tokenize.open(file_path) as handle:
            return handle.read()
    except (SyntaxError, UnicodeDecodeError):
        return file_path.read_text(encoding="utf-8", errors="replace")


class AstObserver(ast.NodeVisitor):
    def __init__(self, repo_id: str, file_path: str, module: str, is_package: bool = False) -> None:
        self.repo_id = repo_id
        self.file_path = file_path
        self.module = module
        self.is_package = is_package
        self.scope_stack: list[tuple[str, str]] = [("module", module)]
        self.class_stack: list[str] = []
        self.observations: list[Observation] = []

    @property
    def current_scope(self) -> tuple[str, str]:
        return self.scope_stack[-1]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.observations.append(
                Observation(
                    "import",
                    self.repo_id,
                    self.file_path,
                    local_name,
                    self.module,
                    span=span_for(node),
                    target=alias.name,
                    raw={"alias": alias.asname, "style": "import"},
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            target = resolve_import_target(self.module, self.is_package, node.level, node.module, alias.name)
            self.observations.append(
                Observation(
                    "import",
                    self.repo_id,
                    self.file_path,
                    alias.asname or alias.name,
                    self.module,
                    span=span_for(node),
                    target=target,
                    raw={
                        "alias": alias.asname,
                        "module": node.module,
                        "level": node.level,
                        "style": "from",
                    },
                )
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_kind, parent_fqname = self.current_scope
        fqname = f"{parent_fqname}.{node.name}" if parent_kind != "module" else f"{self.module}.{node.name}"
        self.observations.append(
            Observation(
                "class",
                self.repo_id,
                self.file_path,
                node.name,
                fqname,
                span=span_for(node),
                parent=parent_fqname,
                doc=ast.get_docstring(node),
            )
        )
        for base in node.bases:
            self.observations.append(
                Observation(
                    "inherit",
                    self.repo_id,
                    self.file_path,
                    node.name,
                    fqname,
                    span=span_for(base),
                    target=expr_name(base),
                )
            )
        self.scope_stack.append(("class", fqname))
        self.class_stack.append(fqname)
        self.generic_visit(node)
        self.class_stack.pop()
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "function")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, default_kind: str) -> None:
        parent_kind, parent_fqname = self.current_scope
        kind = "method" if parent_kind == "class" else default_kind
        fqname = f"{parent_fqname}.{node.name}" if parent_kind != "module" else f"{self.module}.{node.name}"
        self.observations.append(
            Observation(
                kind,
                self.repo_id,
                self.file_path,
                node.name,
                fqname,
                span=span_for(node),
                parent=parent_fqname,
                signature=signature_for(node),
                doc=ast.get_docstring(node),
            )
        )
        self.scope_stack.append((kind, fqname))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        _scope_kind, caller = self.current_scope
        self.observations.append(
            Observation(
                "call",
                self.repo_id,
                self.file_path,
                expr_name(node.func),
                caller,
                span=span_for(node),
                parent=caller,
                target=expr_name(node.func),
                raw={
                    "module": self.module,
                    "class_fqname": self.class_stack[-1] if self.class_stack else None,
                },
            )
        )
        self.generic_visit(node)


def resolve_import_target(
    current_module: str,
    is_package: bool,
    level: int,
    imported_module: str | None,
    imported_name: str,
) -> str:
    if level == 0:
        return ".".join(part for part in (imported_module, imported_name) if part)
    package_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    keep = max(0, len(package_parts) - (level - 1))
    prefix = package_parts[:keep]
    suffix = [part for part in (imported_module, imported_name) if part]
    return ".".join([*prefix, *suffix])


def span_for(node: ast.AST) -> Span:
    return Span(
        getattr(node, "lineno", 1),
        getattr(node, "col_offset", 0),
        getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        getattr(node, "end_col_offset", getattr(node, "col_offset", 0)),
    )


def expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = expr_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return expr_name(node.func)
    if isinstance(node, ast.Subscript):
        return expr_name(node.value)
    return ast.unparse(node) if hasattr(ast, "unparse") else node.__class__.__name__


def signature_for(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    for arg in [*node.args.posonlyargs, *node.args.args]:
        args.append(arg.arg)
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    for arg in node.args.kwonlyargs:
        args.append(arg.arg)
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(args)})"
