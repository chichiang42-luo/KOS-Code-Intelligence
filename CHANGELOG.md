# Changelog

All notable changes to KOS Code Intelligence are documented here.

## [Unreleased]

## [0.3.1] - 2026-08-07

- Pinned `tree-sitter` to the stable `>=0.25,<0.26` runtime after reproducing a Windows access violation in 0.26.0.
- Switched AST child traversal to `TreeCursor` and explicitly retained each parsed `Tree` for the observer lifetime.
- Added runtime compatibility diagnostics so unsafe Tree-sitter versions fail with an actionable error instead of parsing.
- Added stale index-lock recovery by validating the lock owner PID before waiting or failing.

## [0.3.0] - 2026-08-04

- Added incremental indexing for JavaScript, TypeScript/TSX, CSS, Bash, Go, Java, Rust, C, and C++.
- Added offline Tree-sitter grammar dependencies while retaining Python AST analysis.
- Added language-aware symbol resolution to prevent arbitrary links between same-named symbols in different languages.
- Added `kos languages`, `kos_languages`, per-file language metadata, and schema version 3.
- Added a polyglot fixture covering imports, calls, inheritance, selectors, and non-Python incremental updates.
- Documented selective source reading: KOS narrows the relevant code set but does not replace implementation-level reading.

## [0.2.0] - 2026-07-30

- Added file-level incremental indexing with freshness checks.
- Added stable node identifiers and transactional graph updates.
- Added a repository-bound MCP stdio server with eight structured tools.
- Added a unified service layer for CLI, MCP, and REST.
- Added versioned evaluation suites, diagnostics, packaging, and CI.
- Improved Python import alias, relative import, and `self`/`cls` call resolution.

## [0.1.0] - 2026-07-29

- Initial Python AST code graph MVP.
- Added SQLite/JSONL storage and agent-oriented CLI queries.
