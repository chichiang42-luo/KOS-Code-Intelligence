from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .languages import module_name_from_path
from .schemas import Observation, Span

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


class TreeSitterRuntimeError(ValueError):
    pass


def observe_tree_sitter_file(
    file_path: Path,
    repo_root: Path,
    repo_id: str,
    language: str,
) -> list[Observation]:
    rel_path = file_path.relative_to(repo_root).as_posix()
    source = file_path.read_bytes()
    module = module_name_from_path(Path(rel_path), language)
    try:
        tree = parser_for(language).parse(source)
    except Exception as exc:
        return base_observations(repo_id, rel_path, file_path.name, module, language) + [
            Observation(
                "parse_error",
                repo_id,
                rel_path,
                exc.__class__.__name__,
                f"{module}:{exc.__class__.__name__}",
                span=Span(1, 0, 1, 0),
                raw={"language": language, "message": str(exc)},
            )
        ]

    root = tree.root_node
    if language == "java":
        package = java_package(root, source)
        if package:
            module = f"{package}.{file_path.stem}$module"
    observations = base_observations(repo_id, rel_path, file_path.name, module, language)
    if root.has_error:
        error_node = first_error(root)
        observations.append(
            Observation(
                "parse_error",
                repo_id,
                rel_path,
                "SyntaxError",
                f"{module}:SyntaxError",
                span=span_for(error_node or root),
                raw={"language": language, "message": "Tree-sitter reported a syntax error"},
            )
        )
        return observations

    namespace = module
    if language == "java" and module.endswith("$module"):
        namespace = module.rsplit(".", 1)[0]
    observer = TreeSitterObserver(repo_id, rel_path, language, module, namespace, source, tree)
    observer.walk(root)
    observations.extend(observer.observations)
    return observations


def base_observations(
    repo_id: str,
    rel_path: str,
    file_name: str,
    module: str,
    language: str,
) -> list[Observation]:
    raw = {"language": language, "parser": "tree_sitter"}
    return [
        Observation("file", repo_id, rel_path, file_name, rel_path, raw=dict(raw)),
        Observation("module", repo_id, rel_path, module, module, parent=rel_path, raw=dict(raw)),
    ]


@cache
def parser_for(language: str) -> Any:
    ensure_tree_sitter_runtime()
    from tree_sitter import Language, Parser

    factories: dict[str, Callable[[], object]] = {}
    if language in {"javascript", "typescript", "tsx"}:
        if language == "javascript":
            import tree_sitter_javascript as grammar

            factories[language] = grammar.language
        else:
            import tree_sitter_typescript as grammar

            factories["typescript"] = grammar.language_typescript
            factories["tsx"] = grammar.language_tsx
    elif language == "css":
        import tree_sitter_css as grammar

        factories[language] = grammar.language
    elif language == "bash":
        import tree_sitter_bash as grammar

        factories[language] = grammar.language
    elif language == "go":
        import tree_sitter_go as grammar

        factories[language] = grammar.language
    elif language == "java":
        import tree_sitter_java as grammar

        factories[language] = grammar.language
    elif language == "rust":
        import tree_sitter_rust as grammar

        factories[language] = grammar.language
    elif language == "c":
        import tree_sitter_c as grammar

        factories[language] = grammar.language
    elif language == "cpp":
        import tree_sitter_cpp as grammar

        factories[language] = grammar.language
    else:
        raise ValueError(f"unsupported Tree-sitter language: {language}")
    return Parser(Language(factories[language]()))


def ensure_tree_sitter_runtime() -> None:
    try:
        installed = version("tree-sitter")
    except PackageNotFoundError as exc:
        raise RuntimeError("tree-sitter is not installed") from exc
    if not tree_sitter_runtime_supported(installed):
        raise TreeSitterRuntimeError(
            f"unsupported tree-sitter runtime {installed}; KOS v0.3.1 requires >=0.25,<0.26 "
            "because 0.26.0 can crash during AST traversal on Windows"
        )


def tree_sitter_runtime_supported(installed: str) -> bool:
    numbers = [int(item) for item in re.findall(r"\d+", installed)[:2]]
    release = tuple([*numbers, 0, 0][:2])
    return (0, 25) <= release < (0, 26)


class TreeSitterObserver:
    def __init__(
        self,
        repo_id: str,
        file_path: str,
        language: str,
        module: str,
        namespace: str,
        source: bytes,
        tree: Any,
    ) -> None:
        self.repo_id = repo_id
        self.file_path = file_path
        self.language = language
        self.module = module
        self.namespace = namespace
        self.source = source
        # py-tree-sitter Nodes do not own the underlying TSTree. Keep it rooted
        # for the complete traversal, especially on Windows where accessing a
        # Node after an early Tree release can terminate the process.
        self.tree = tree
        self.scope_stack: list[tuple[str, str]] = [("module", module)]
        self.class_stack: list[str] = []
        self.observations: list[Observation] = []

    @property
    def current_scope(self) -> tuple[str, str]:
        return self.scope_stack[-1]

    def walk(self, node: Any) -> None:
        if self.language in {"javascript", "typescript", "tsx"} and self.walk_javascript(node):
            return
        if self.language == "java" and self.walk_java(node):
            return
        if self.language == "go" and self.walk_go(node):
            return
        if self.language == "bash" and self.walk_bash(node):
            return
        if self.language == "css" and self.walk_css(node):
            return
        if self.language == "rust" and self.walk_rust(node):
            return
        if self.language == "c" and self.walk_c_family(node, cpp=False):
            return
        if self.language == "cpp" and self.walk_c_family(node, cpp=True):
            return
        for child in cursor_children(node):
            self.walk(child)

    def walk_javascript(self, node: Any) -> bool:
        if node.type == "import_statement":
            self.add_javascript_imports(node)
            return True
        if node.type in {"class_declaration", "class"}:
            self.add_scoped_definition(node, "class", class_like=True)
            return True
        if node.type in {"interface_declaration", "type_alias_declaration"}:
            self.add_scoped_definition(node, "interface", class_like=True)
            return True
        if node.type in {"function_declaration", "generator_function_declaration"}:
            self.add_scoped_definition(node, "function")
            return True
        if node.type == "method_definition":
            self.add_scoped_definition(node, "method")
            return True
        if node.type == "variable_declarator":
            value = field_child(node, "value")
            if value and value.type in {"arrow_function", "function_expression", "generator_function"}:
                self.add_scoped_definition(node, "function", body_node=value)
                return True
        if node.type in {"call_expression", "new_expression"}:
            function = field_child(node, "function") or field_child(node, "constructor")
            target = self.text(function) if function else ""
            if target == "require":
                arguments = field_child(node, "arguments")
                specifier = first_string(arguments, self.source) if arguments else None
                if specifier:
                    self.add_import(specifier, resolve_relative_module(self.file_path, specifier), node)
            else:
                self.add_call(target, node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        return False

    def walk_java(self, node: Any) -> bool:
        kinds = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        }
        if node.type == "import_declaration":
            target = self.text(node).removeprefix("import ").removesuffix(";").replace("static ", "").strip()
            self.add_import(target.rsplit(".", 1)[-1], target, node)
            return True
        if node.type in kinds:
            self.add_scoped_definition(node, kinds[node.type], class_like=True)
            return True
        if node.type in {"method_declaration", "constructor_declaration"}:
            self.add_scoped_definition(node, "method")
            return True
        if node.type == "method_invocation":
            name = field_child(node, "name")
            obj = field_child(node, "object")
            target = self.text(name)
            if obj:
                target = f"{self.text(obj)}.{target}"
            self.add_call(target, node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        if node.type == "object_creation_expression":
            target = field_child(node, "type")
            self.add_call(self.text(target), node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        return False

    def walk_go(self, node: Any) -> bool:
        if node.type == "import_spec":
            path = field_child(node, "path")
            target = strip_quotes(self.text(path))
            alias_node = field_child(node, "name")
            alias = self.text(alias_node) if alias_node else target.rsplit("/", 1)[-1]
            self.add_import(alias, target.replace("/", "."), node)
            return True
        if node.type == "type_spec":
            type_node = field_child(node, "type")
            kind = "interface" if type_node and type_node.type == "interface_type" else "struct"
            self.add_scoped_definition(node, kind, class_like=True, body_node=type_node)
            return True
        if node.type == "function_declaration":
            self.add_scoped_definition(node, "function")
            return True
        if node.type == "method_declaration":
            receiver = field_child(node, "receiver")
            receiver_name = go_receiver_type(self.text(receiver))
            self.add_scoped_definition(node, "method", owner_name=receiver_name)
            return True
        if node.type == "call_expression":
            function = field_child(node, "function")
            self.add_call(self.text(function), node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        return False

    def walk_bash(self, node: Any) -> bool:
        if node.type == "function_definition":
            self.add_scoped_definition(node, "function")
            return True
        if node.type == "command":
            name_node = field_child(node, "name")
            name = self.text(name_node)
            arguments = [child for child in cursor_children(node) if child != name_node]
            if name in {"source", "."} and arguments:
                specifier = strip_quotes(self.text(arguments[0]))
                self.add_import(specifier, resolve_relative_module(self.file_path, specifier), node)
            elif name:
                self.add_call(name, node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        return False

    def walk_css(self, node: Any) -> bool:
        if node.type == "import_statement":
            specifier = first_string(node, self.source)
            if specifier:
                self.add_import(specifier, resolve_relative_module(self.file_path, specifier), node)
            return True
        if node.type == "rule_set":
            selectors = next((child for child in cursor_children(node) if child.type == "selectors"), None)
            if selectors:
                for selector in cursor_children(selectors):
                    name = self.text(selector).strip()
                    if name:
                        self.add_definition("selector", name, f"{self.module}::{name}", self.module, selector)
            return True
        return False

    def walk_rust(self, node: Any) -> bool:
        if node.type == "use_declaration":
            argument = field_child(node, "argument")
            target = self.text(argument)
            normalized = resolve_rust_use(self.file_path, target)
            self.add_import(target.rsplit("::", 1)[-1], normalized, node)
            return True
        if node.type == "trait_item":
            self.add_scoped_definition(node, "trait", class_like=True)
            return True
        if node.type == "struct_item":
            self.add_scoped_definition(node, "struct", class_like=True)
            return True
        if node.type == "enum_item":
            self.add_scoped_definition(node, "enum", class_like=True)
            return True
        if node.type == "impl_item":
            self.walk_rust_impl(node)
            return True
        if node.type == "function_signature_item":
            self.add_scoped_definition(node, "method")
            return True
        if node.type == "function_item":
            kind = "method" if self.class_stack else "function"
            self.add_scoped_definition(node, kind)
            return True
        if node.type == "call_expression":
            function = field_child(node, "function")
            self.add_call(self.text(function).replace("::", "."), node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        if node.type == "macro_invocation":
            macro = field_child(node, "macro")
            self.add_call(self.text(macro).replace("::", ".").removesuffix("!"), node)
            return True
        return False

    def walk_rust_impl(self, node: Any) -> None:
        owner_node = field_child(node, "type")
        trait_node = field_child(node, "trait")
        owner_name = self.text(owner_node)
        owner_fqname = f"{self.namespace}.{owner_name}"
        if trait_node:
            self.observations.append(
                Observation(
                    "inherit",
                    self.repo_id,
                    self.file_path,
                    owner_name,
                    owner_fqname,
                    span=span_for(node),
                    target=self.text(trait_node).replace("::", "."),
                    raw=self.raw(),
                )
            )
        self.scope_stack.append(("struct", owner_fqname))
        self.class_stack.append(owner_fqname)
        body = field_child(node, "body")
        if body:
            self.walk(body)
        self.class_stack.pop()
        self.scope_stack.pop()

    def walk_c_family(self, node: Any, cpp: bool) -> bool:
        if node.type == "preproc_include":
            path = field_child(node, "path")
            specifier = strip_quotes(self.text(path))
            if specifier and not specifier.startswith("<"):
                self.add_import(specifier, resolve_relative_module(self.file_path, specifier), node)
            return True
        if cpp and node.type == "namespace_definition":
            name = self.text(field_child(node, "name"))
            body = field_child(node, "body")
            previous_namespace = self.namespace
            self.namespace = f"{self.namespace}.{name}" if name else self.namespace
            if body:
                self.walk(body)
            self.namespace = previous_namespace
            return True
        if node.type in {"class_specifier", "struct_specifier", "union_specifier"}:
            kind = "class" if node.type == "class_specifier" else "struct"
            self.add_scoped_definition(node, kind, class_like=True)
            return True
        if cpp and node.type == "enum_specifier":
            self.add_scoped_definition(node, "enum", class_like=True)
            return True
        if node.type == "function_definition":
            declarator = field_child(node, "declarator")
            name = declarator_name(declarator, self.source)
            if not name:
                return False
            if cpp and "::" in name:
                owner, method_name = name.rsplit("::", 1)
                self.add_named_scoped_definition(node, "method", method_name, owner)
            else:
                self.add_named_scoped_definition(node, "function", name)
            return True
        if node.type == "call_expression":
            function = field_child(node, "function")
            self.add_call(self.text(function).replace("::", "."), node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        if cpp and node.type == "new_expression":
            target = field_child(node, "type")
            self.add_call(self.text(target), node)
            for child in cursor_children(node):
                self.walk(child)
            return True
        return False

    def add_named_scoped_definition(
        self,
        node: Any,
        kind: str,
        name: str,
        owner_name: str | None = None,
    ) -> None:
        parent_kind, parent_fqname = self.current_scope
        if owner_name:
            dotted_owner = owner_name.replace("::", ".")
            parent_fqname = f"{self.namespace}.{dotted_owner}"
            kind = "method"
        fqname = f"{parent_fqname}.{name}" if parent_kind != "module" or owner_name else f"{self.namespace}.{name}"
        self.add_definition(kind, name, fqname, parent_fqname, node)
        self.scope_stack.append((kind, fqname))
        body = field_child(node, "body")
        if body:
            self.walk(body)
        self.scope_stack.pop()

    def add_scoped_definition(
        self,
        node: Any,
        kind: str,
        class_like: bool = False,
        body_node: Any | None = None,
        owner_name: str | None = None,
    ) -> None:
        name_node = field_child(node, "name")
        if name_node is None and node.type == "variable_declarator":
            name_node = field_child(node, "name")
        name = self.text(name_node)
        if not name:
            return
        parent_kind, parent_fqname = self.current_scope
        if owner_name:
            parent_fqname = f"{self.namespace}.{owner_name}"
        fqname = f"{parent_fqname}.{name}" if parent_kind != "module" or owner_name else f"{self.namespace}.{name}"
        self.add_definition(kind, name, fqname, parent_fqname, node)
        if class_like:
            self.add_inheritance(node, fqname, name)
        self.scope_stack.append((kind, fqname))
        if class_like:
            self.class_stack.append(fqname)
        body = body_node or field_child(node, "body") or field_child(node, "value")
        if body:
            self.walk(body)
        else:
            for child in cursor_children(node):
                if child is not name_node:
                    self.walk(child)
        if class_like:
            self.class_stack.pop()
        self.scope_stack.pop()

    def add_definition(self, kind: str, name: str, fqname: str, parent: str, node: Any) -> None:
        self.observations.append(
            Observation(
                kind,
                self.repo_id,
                self.file_path,
                name,
                fqname,
                span=span_for(node),
                parent=parent,
                signature=self.signature(node, name),
                raw=self.raw(),
            )
        )

    def add_inheritance(self, node: Any, fqname: str, name: str) -> None:
        fields = ("superclass", "interfaces") if self.language == "java" else ()
        candidates = [field_child(node, field) for field in fields]
        candidates.extend(
            child for child in cursor_children(node) if child.type in {"class_heritage", "base_class_clause"}
        )
        for candidate in [item for item in candidates if item is not None]:
            for target in inheritance_names(candidate, self.source):
                self.observations.append(
                    Observation(
                        "inherit",
                        self.repo_id,
                        self.file_path,
                        name,
                        fqname,
                        span=span_for(candidate),
                        target=target,
                        raw=self.raw(),
                    )
                )

    def add_javascript_imports(self, node: Any) -> None:
        source_node = field_child(node, "source")
        specifier = strip_quotes(self.text(source_node))
        target_module = resolve_relative_module(self.file_path, specifier)
        self.add_import(specifier, target_module, node)
        clause = next((child for child in cursor_children(node) if child.type == "import_clause"), None)
        if not clause:
            return
        for child in descendants(clause):
            if child.type == "import_specifier":
                imported = self.text(field_child(child, "name"))
                alias_node = field_child(child, "alias")
                local = self.text(alias_node) if alias_node else imported
                self.add_import(local, f"{target_module}.{imported}", child)
            elif child.type == "namespace_import":
                local = last_identifier(child, self.source)
                self.add_import(local, target_module, child)
        direct_ids = [
            child for child in cursor_children(clause) if child.type in {"identifier", "type_identifier"}
        ]
        for identifier in direct_ids:
            local = self.text(identifier)
            self.add_import(local, f"{target_module}.{local}", identifier)

    def add_import(self, name: str, target: str, node: Any) -> None:
        self.observations.append(
            Observation(
                "import",
                self.repo_id,
                self.file_path,
                name,
                self.module,
                span=span_for(node),
                target=target,
                raw=self.raw(),
            )
        )

    def add_call(self, target: str, node: Any) -> None:
        if not target:
            return
        _kind, caller = self.current_scope
        self.observations.append(
            Observation(
                "call",
                self.repo_id,
                self.file_path,
                target,
                caller,
                span=span_for(node),
                parent=caller,
                target=target,
                raw=self.raw(class_fqname=self.class_stack[-1] if self.class_stack else None),
            )
        )

    def signature(self, node: Any, name: str) -> str:
        body = field_child(node, "body") or field_child(node, "value")
        end = body.start_byte if body else min(node.end_byte, node.start_byte + 240)
        value = self.source[node.start_byte:end].decode("utf-8", errors="replace")
        value = " ".join(value.split())
        return value[:240] or name

    def text(self, node: Any | None) -> str:
        if node is None:
            return ""
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def raw(self, **extra: object) -> dict[str, object]:
        return {"language": self.language, "parser": "tree_sitter", "module": self.module, **extra}


def resolve_relative_module(file_path: str, specifier: str) -> str:
    clean = strip_quotes(specifier).replace("\\", "/")
    if not clean.startswith("."):
        return clean.replace("/", ".")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(file_path), clean))
    for suffix in (
        ".tsx",
        ".ts",
        ".jsx",
        ".js",
        ".mjs",
        ".cjs",
        ".css",
        ".bash",
        ".sh",
        ".rs",
        ".cpp",
        ".cxx",
        ".cc",
        ".hpp",
        ".hxx",
        ".hh",
        ".c",
        ".h",
    ):
        if joined.endswith(suffix):
            joined = joined[: -len(suffix)]
            break
    if joined.endswith("/index"):
        joined = joined[: -len("/index")]
    return joined.strip("./").replace("/", ".")


def span_for(node: Any) -> Span:
    return Span(node.start_point.row + 1, node.start_point.column, node.end_point.row + 1, node.end_point.column)


def cursor_children(node: Any, named_only: bool = True):
    cursor = node.walk()
    if not cursor.goto_first_child():
        return
    while True:
        child = cursor.node
        if not named_only or child.is_named:
            yield child
        if not cursor.goto_next_sibling():
            break


def field_child(node: Any, field_name: str) -> Any | None:
    cursor = node.walk()
    if not cursor.goto_first_child():
        return None
    while True:
        if cursor.field_name == field_name:
            return cursor.node
        if not cursor.goto_next_sibling():
            return None


def first_error(node: Any) -> Any | None:
    if node.type == "ERROR" or node.is_missing:
        return node
    for child in cursor_children(node, named_only=False):
        found = first_error(child)
        if found is not None:
            return found
    return None


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in {'"', "'", "`"} and value[-1] == value[0]:
        return value[1:-1]
    return value


def descendants(node: Any) -> list[Any]:
    result: list[Any] = []
    for child in cursor_children(node):
        result.append(child)
        result.extend(descendants(child))
    return result


def first_string(node: Any, source: bytes) -> str | None:
    for child in [node, *descendants(node)]:
        if child.type in {"string", "string_value", "interpreted_string_literal", "raw_string_literal"}:
            text = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return strip_quotes(text)
    return None


def last_identifier(node: Any, source: bytes) -> str:
    identifiers = [
        child
        for child in [node, *descendants(node)]
        if child.type in {"identifier", "type_identifier", "property_identifier"}
    ]
    if not identifiers:
        return ""
    child = identifiers[-1]
    return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")


def inheritance_names(node: Any, source: bytes) -> list[str]:
    names: list[str] = []
    accepted = {"identifier", "type_identifier", "scoped_type_identifier", "generic_type"}
    for child in descendants(node):
        if child.type in accepted and not any(parent in accepted for parent in ancestor_types(child, node)):
            value = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            names.append(value)
    return list(dict.fromkeys(names))


def ancestor_types(node: Any, stop: Any) -> list[str]:
    result: list[str] = []
    current = node.parent
    while current is not None and current != stop:
        result.append(current.type)
        current = current.parent
    return result


def go_receiver_type(value: str) -> str:
    identifiers = re.findall(r"[A-Za-z_]\w*", value)
    return identifiers[-1] if identifiers else "receiver"


def java_package(root: Any, source: bytes) -> str | None:
    for child in cursor_children(root):
        if child.type == "package_declaration":
            value = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return value.removeprefix("package ").removesuffix(";").strip()
    return None


def resolve_rust_use(file_path: str, target: str) -> str:
    parts = file_path.split("/")
    target_parts = target.split("::")
    if target_parts and target_parts[0] == "crate":
        source_index = parts.index("src") if "src" in parts else max(0, len(parts) - 2)
        return ".".join([*parts[: source_index + 1], *target_parts[1:]])
    if target_parts and target_parts[0] == "self":
        return ".".join([*parts[:-1], *target_parts[1:]])
    return target.replace("::", ".")


def declarator_name(node: Any | None, source: bytes) -> str:
    if node is None:
        return ""
    if node.type in {"identifier", "field_identifier", "qualified_identifier", "operator_name"}:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    inner = field_child(node, "declarator")
    if inner is not None:
        return declarator_name(inner, source)
    for child in cursor_children(node):
        value = declarator_name(child, source)
        if value:
            return value
    return ""
