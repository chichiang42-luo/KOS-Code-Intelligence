# Agent Execution Guide

## Goal

Use KOS as a maintained local code knowledge graph before reading or editing a supported source file.

## Install

```bash
python -m pip install -e .
```

## Standard Workflow

1. Check the index:

```bash
kos status --repo /path/to/project
```

2. Initialize or incrementally update it:

```bash
kos update --repo /path/to/project
```

3. Request a context pack:

```bash
kos agent-pack SymbolName --repo /path/to/project
```

4. Read files in ascending `priority` order and use `facts[].evidence` to verify relationships.

5. After changing supported source files, run `kos update` before the next graph query.

## Source Reading Boundary

KOS selects the source that matters; it does not explain every implementation body. Do not read the whole repository by default.

Start with the target span, then read files in the returned priority order. Expand beyond the one-hop read plan only for ambiguous relations, dynamic dispatch, generated code, failing tests, or changes that cross module boundaries.

## MCP Workflow

Configure an MCP client to start:

```bash
kos-mcp --repo /absolute/path/to/project
```

The Agent should:

1. Call `kos_status`.
2. Call `kos_languages` when the repository's language coverage is unclear.
3. Call `kos_update` when the index is stale or uninitialized.
4. Call `kos_pack` before investigating a symbol.
5. Use `kos_resolve` when `kos_pack` returns candidates.
6. Treat low-confidence `MAY_CALL` facts as leads that require source verification.

The MCP server is bound to one repository. It must not accept repository paths through tool arguments.

## Validation

```bash
python -m unittest discover -s tests -v
kos eval --repo sample_data/sample_shop --store-root .kos_runs/sample-eval --cases evals/sample_shop.json
```

Before publishing:

```bash
ruff check .
python -m build
```
