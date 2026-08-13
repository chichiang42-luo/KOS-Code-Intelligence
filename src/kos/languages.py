from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    parser: str


LANGUAGES = (
    LanguageSpec("python", (".py", ".pyi"), "python_ast"),
    LanguageSpec("javascript", (".js", ".jsx", ".mjs", ".cjs"), "tree_sitter"),
    LanguageSpec("typescript", (".ts",), "tree_sitter"),
    LanguageSpec("tsx", (".tsx",), "tree_sitter"),
    LanguageSpec("css", (".css",), "tree_sitter"),
    LanguageSpec("bash", (".sh", ".bash"), "tree_sitter"),
    LanguageSpec("go", (".go",), "tree_sitter"),
    LanguageSpec("java", (".java",), "tree_sitter"),
    LanguageSpec("rust", (".rs",), "tree_sitter"),
    LanguageSpec("c", (".c", ".h"), "tree_sitter"),
    LanguageSpec("cpp", (".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"), "tree_sitter"),
)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".idea",
    ".kos",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}

_BY_EXTENSION = {extension: spec for spec in LANGUAGES for extension in spec.extensions}


def supported_languages() -> list[dict[str, object]]:
    return [
        {"name": spec.name, "extensions": list(spec.extensions), "parser": spec.parser}
        for spec in LANGUAGES
    ]


def detect_language(path: Path) -> str | None:
    spec = _BY_EXTENSION.get(path.suffix.lower())
    return spec.name if spec else None


def iter_source_files(repo_path: Path) -> list[Path]:
    root = repo_path.resolve()
    paths: list[Path] = []
    for path in root.rglob("*"):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if detect_language(path) is None:
            continue
        try:
            resolved = path.resolve()
            if os.path.commonpath((str(root), str(resolved))) != str(root):
                continue
        except (OSError, ValueError):
            continue
        if path.is_file():
            paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def language_counts(paths: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        language = detect_language(path)
        if language:
            counts[language] = counts.get(language, 0) + 1
    return dict(sorted(counts.items()))


def module_name_from_path(path: Path, language: str) -> str:
    parts = list(path.with_suffix("").parts)
    if language == "python" and parts[-1] == "__init__":
        parts.pop()
    elif language in {"javascript", "typescript", "tsx"} and parts[-1] == "index":
        parts.pop()
    return ".".join(parts) or path.stem
