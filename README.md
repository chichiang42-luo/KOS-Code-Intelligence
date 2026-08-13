# KOS Code Intelligence

[![CI](https://github.com/chichiang42-luo/KOS-Code-Intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/chichiang42-luo/KOS-Code-Intelligence/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/kos-code-intelligence.svg)](https://pypi.org/project/kos-code-intelligence/)
[![Python](https://img.shields.io/pypi/pyversions/kos-code-intelligence.svg)](https://pypi.org/project/kos-code-intelligence/)
[![License](https://img.shields.io/github/license/chichiang42-luo/KOS-Code-Intelligence.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

KOS is a local multi-language code knowledge graph and MCP server for coding agents.

It indexes files, modules, classes, functions, methods, imports, calls, inheritance, and containment into SQLite. Agents can resolve symbols, inspect callers and dependencies, estimate impact, and receive a prioritized read plan with file-and-line evidence.

KOS runs locally, keeps the index beside the repository (or in a storage directory you choose), and does not require a hosted database or embedding service.

> [!IMPORTANT]
> KOS v0.3 is alpha software. Its index format and public interfaces may change between minor releases. See [Project status](#project-status) and [Current limits](#current-limits) before production use.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Installation](#installation)
- [Supported languages](#supported-languages)
- [Index lifecycle](#index-lifecycle)
- [Agent CLI](#agent-cli)
- [MCP server](#mcp-server)
- [Evaluation](#evaluation)
- [Development](#development)
- [Contributing and security](#contributing-and-security)
- [Project status](#project-status)
- [License](#license)

## Features

- Incremental indexing with file freshness checks across Python, JavaScript, TypeScript/TSX, CSS, Bash, Go, Java, Rust, C, and C++.
- Python AST analysis plus offline Tree-sitter grammar packages for the other supported languages.
- Stable node IDs that survive line movement.
- Transactional SQLite updates and versioned index metadata.
- Graph relationships: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `MAY_CALL`, and `INHERITS`.
- Agent-friendly CLI JSON and a repository-bound MCP stdio server.
- Evidence, confidence, ambiguity handling, limits, and truncation metadata.
- Versioned automated evaluation suites.
- Experimental REST API.

## Quick Start

Install KOS, index a repository, and ask for an agent-ready context pack:

```bash
pipx install kos-code-intelligence
kos update --repo /path/to/project
kos agent-pack ModelManager --repo /path/to/project
```

Check which languages KOS found and whether the index is current:

```bash
kos languages --repo /path/to/project
kos status --repo /path/to/project
```

KOS stores generated index data under `.kos/` by default. Add `.kos/` to the target repository's `.gitignore`; it is local, reproducible data and should not be committed.

## Installation

Install the command-line tool and MCP server:

```bash
pipx install kos-code-intelligence
```

Alternatively:

```bash
python -m pip install kos-code-intelligence
```

For development:

```bash
git clone https://github.com/chichiang42-luo/KOS-Code-Intelligence.git
cd KOS-Code-Intelligence
python -m pip install -e ".[test]"
```

KOS supports Python 3.11 through 3.13.

## Supported Languages

| Language | Extensions | Indexed concepts |
|---|---|---|
| Python | `.py`, `.pyi` | Modules, classes, functions, methods, imports, calls, inheritance |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | Classes, functions, methods, imports, calls, inheritance |
| TypeScript/TSX | `.ts`, `.tsx` | Classes, interfaces, functions, methods, imports, calls, inheritance |
| CSS | `.css` | Stylesheets, selectors, `@import` dependencies |
| Bash | `.sh`, `.bash` | Functions, `source` dependencies, command calls |
| Go | `.go` | Structs, interfaces, functions, methods, imports, calls |
| Java | `.java` | Classes, interfaces, enums, records, methods, imports, calls, inheritance |
| Rust | `.rs` | Structs, traits, enums, functions, methods, `use`, calls, trait implementations |
| C | `.c`, `.h` | Structs, functions, includes, calls |
| C++ | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx` | Classes, structs, functions, methods, includes, calls, inheritance |

Run `kos languages --repo /path/to/project` to see both supported languages and counts in the current repository.

## Index Lifecycle

Create or update an index:

```bash
kos update --repo /path/to/project
```

KOS writes `.kos/` inside the project by default. Keep storage elsewhere when the target repository should remain untouched:

```bash
kos update --repo /path/to/project --store-root /path/to/kos-store
```

Check freshness:

```bash
kos status --repo /path/to/project
```

Run diagnostics:

```bash
kos doctor --repo /path/to/project
```

`kos update` performs the first full index and then parses only added or changed files. Unchanged observations are loaded from the local cache before relationships are resolved globally.

## Agent CLI

Use `agent-pack` as the default entry point:

```bash
kos agent-pack ModelManager --repo /path/to/project
```

Available commands:

| Command | Purpose |
|---|---|
| `agent-resolve QUERY` | Resolve a symbol, qualified name, or file path |
| `agent-who-calls QUERY` | Return direct callers |
| `agent-calls QUERY` | Return direct called dependencies |
| `agent-impact QUERY` | Return one-hop call, import, and inheritance impact |
| `agent-read-plan QUERY` | Return prioritized files to read |
| `agent-pack QUERY` | Return the target, facts, evidence, and read plan |

Ambiguous and missing symbols are normal structured results. KOS does not choose an arbitrary target when several symbols have the same name.

## MCP Server

Start a repository-bound stdio server:

```bash
kos-mcp --repo /path/to/project
```

A generic MCP client configuration looks like:

```json
{
  "mcpServers": {
    "kos": {
      "command": "kos-mcp",
      "args": ["--repo", "/absolute/path/to/project"]
    }
  }
}
```

The server exposes:

- `kos_status`
- `kos_languages`
- `kos_update`
- `kos_resolve`
- `kos_who_calls`
- `kos_calls`
- `kos_impact`
- `kos_read_plan`
- `kos_pack`

The server is restricted to the repository and storage root selected at startup. Tool arguments cannot register or access another repository. Logs are written to stderr so stdout remains reserved for MCP messages.

Agents should call `kos_status` first, call `kos_update` when needed, and use `kos_pack` before reading or editing a target symbol.

## Source Reading Scope

KOS narrows source reading; it does not replace it. The graph answers where a symbol is defined, who calls it, what it calls, and which files are likely affected. The implementation body still answers how the behavior works.

An Agent normally reads only:

1. The target symbol span and its containing file.
2. Direct caller files that constrain inputs, outputs, and error handling.
3. Direct dependency files whose contracts may change.
4. Related base classes, traits, interfaces, imports, or registration points from the read plan.

The Agent should expand beyond that one-hop set only when evidence is ambiguous, dynamic dispatch or generated code is involved, tests reveal another dependency, or the requested change crosses an architectural boundary. It should not load every source file in the repository by default.

## Structured Output

Agent query results use schema version `1.0`:

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "query": "verify_payment",
  "target": {
    "node_id": "node_...",
    "fqname": "app.payment.service.verify_payment",
    "kind": "function",
    "file_path": "app/payment/service.py",
    "span": {
      "start_line": 4,
      "start_col": 0,
      "end_line": 6,
      "end_col": 35
    },
    "signature": "verify_payment(order_id)"
  },
  "candidates": [],
  "files": [],
  "facts": [],
  "index": {
    "state": "fresh",
    "repo_id": "sample_shop",
    "indexed_at": "2026-07-30T00:00:00+00:00",
    "changed_files": 0
  },
  "limits": {
    "candidate_limit": 10,
    "file_limit": 10,
    "fact_limit": 40,
    "candidates_truncated": false,
    "files_truncated": false,
    "facts_truncated": false
  },
  "error": null
}
```

Query status is `ok`, `ambiguous`, `not_found`, or `error`. Index freshness is reported separately as `fresh`, `stale`, `uninitialized`, or `incompatible`.

## Evaluation

Run the included evaluation suite:

```bash
kos eval \
  --repo sample_data/sample_shop \
  --store-root .kos_runs/sample-eval \
  --cases evals/sample_shop.json
```

The command reports case failures and p50/p95 query latency. It exits nonzero when any case fails, so it can run in CI.

Evaluation files use versioned JSON and can assert:

- Expected query status.
- Target fully qualified name.
- Required read-plan files.
- Required relation type, source, and destination.

## Index Compatibility

Indexes created by v0.1 or v0.2 are intentionally not modified in place. KOS reports them as `incompatible` because v0.3 records per-file language metadata.

Rebuild explicitly:

```bash
kos index --repo /path/to/project --rebuild
```

The previous SQLite files are copied to `.kos/backups/` before replacement.

## Experimental REST API

Install API dependencies:

```bash
python -m pip install "kos-code-intelligence[api]"
```

Start the server:

```bash
kos serve --repo /path/to/project --host 127.0.0.1 --port 8031
```

The REST API is experimental. CLI and MCP are the supported Agent interfaces for v0.3.

## Development

```bash
python -m unittest discover -s tests -v
ruff check .
python -m build
```

Run the source tree without installation:

```bash
export PYTHONPATH=src
python -m kos.cli --version
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m kos.cli --version
```

The full contributor workflow and pull-request expectations are documented in [CONTRIBUTING.md](CONTRIBUTING.md).

## Troubleshooting Tree-sitter

KOS v0.3.1 requires `tree-sitter>=0.25,<0.26`. Tree-sitter 0.26.0 can terminate Python with an access violation while traversing large syntax trees on Windows, so KOS rejects that runtime before indexing.

Repair an existing environment with:

```bash
python -m pip install --upgrade "kos-code-intelligence==0.3.1" "tree-sitter>=0.25,<0.26"
kos doctor --repo /path/to/project
```

KOS validates the PID stored in `.kos/index.lock`. A lock left by a crashed process is removed automatically; a lock owned by a live process is preserved.

## Current Limits

- Static analysis only; runtime dispatch is not traced.
- Cross-language runtime links, framework dependency injection, and general object type inference are not implemented.
- HTML/templates, C#, Kotlin, Swift, and generated code are not indexed yet.
- Impact analysis is limited to one hop.
- Multi-repository MCP servers, background watchers, embeddings, Neo4j, and UI are outside v0.3.

## Contributing and Security

Issues and pull requests are welcome. Before contributing, read [CONTRIBUTING.md](CONTRIBUTING.md) and run the test, lint, build, and evaluation commands described there.

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a public issue. Release history is maintained in [CHANGELOG.md](CHANGELOG.md).

## Project Status

KOS is an actively developed alpha project. The v0.3 line focuses on validating whether a maintained, polyglot code graph can improve context selection for coding agents. CLI and MCP are the primary supported interfaces; the REST API remains experimental.

## License

Licensed under the [Apache License 2.0](LICENSE).
