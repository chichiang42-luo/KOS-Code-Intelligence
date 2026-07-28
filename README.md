# KOS Code Intelligence

KOS Code Intelligence is a local code knowledge graph engine for coding agents.

Instead of asking an agent to repeatedly scan source files and infer relationships from raw text, KOS indexes a Python repository into structured nodes and edges: files, modules, classes, functions, methods, imports, calls, inheritance, and containment. Agents can then query this graph through stable JSON CLI commands before editing code.

This repository is an MVP. It is designed to validate whether a code knowledge graph can become a useful context engine for coding agents.

## Features

- Python AST indexing for files, modules, classes, functions, methods, imports, inheritance, and calls.
- Graph relationships: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `MAY_CALL`, and `INHERITS`.
- JSONL logs plus SQLite current-state storage under `.kos/`.
- Agent-friendly CLI commands for symbol resolution, caller/callee lookup, impact analysis, and read-plan generation.
- Optional FastAPI app for REST access.
- A small sample repository under `sample_data/sample_shop`.
- Standard-library `unittest` coverage for the core MVP path.

## Installation

Clone the repository:

```bash
git clone https://github.com/chichiang42-luo/KOS-Code-Intelligence.git
cd KOS-Code-Intelligence
```

Use Python 3.11+.

For local development without installation:

```bash
export PYTHONPATH=src
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH='src'
```

You can also install it in editable mode:

```bash
python -m pip install -e .
```

Optional API dependencies:

```bash
python -m pip install -e ".[api]"
```

## Quick Start

Index the included sample project:

```bash
python -m kos.cli index --repo sample_data/sample_shop --repo-id sample_shop
```

Search a symbol:

```bash
python -m kos.cli search verify_payment --repo sample_data/sample_shop
```

Ask for an agent-ready context pack:

```bash
python -m kos.cli agent-pack verify_payment --repo sample_data/sample_shop
```

Index an external repository without writing `.kos` into that repository:

```bash
python -m kos.cli index --repo /path/to/project --repo-id my_project --store-root .kos_runs/my_project
```

Query that external index:

```bash
python -m kos.cli agent-pack SomeSymbol --store-root .kos_runs/my_project
```

## Agent Tools

The `agent-*` commands return stable JSON intended for coding agents.

| Command | Purpose |
|---|---|
| `agent-resolve QUERY` | Resolve a symbol, fully qualified name, or file path into a target node or candidates |
| `agent-who-calls QUERY` | Return incoming `CALLS` facts |
| `agent-calls QUERY` | Return outgoing `CALLS` facts |
| `agent-impact QUERY` | Return one-hop `CALLS`, `IMPORTS`, and `INHERITS` facts |
| `agent-read-plan QUERY` | Return the files an agent should read before editing |
| `agent-pack QUERY` | Return target, facts, read-plan files, and limits in one payload |

Example:

```bash
python -m kos.cli agent-pack verify_payment --repo sample_data/sample_shop --limit-files 10 --limit-facts 40
```

JSON shape:

```json
{
  "status": "ok",
  "query": "verify_payment",
  "target": {
    "node_id": "...",
    "fqname": "app.payment.service.verify_payment",
    "kind": "function",
    "file_path": "app/payment/service.py",
    "span": {"start_line": 4, "start_col": 0, "end_line": 6, "end_col": 35},
    "signature": "verify_payment(order_id)"
  },
  "candidates": [],
  "files": [
    {
      "file_path": "app/payment/service.py",
      "priority": 1,
      "reason": "target definition",
      "related_nodes": ["..."]
    }
  ],
  "facts": [
    {
      "rel_type": "CALLS",
      "src": {},
      "dst": {},
      "confidence": 0.85,
      "evidence": []
    }
  ],
  "limits": {
    "candidate_limit": 10,
    "file_limit": 10,
    "fact_limit": 40
  }
}
```

## REST API

The REST API is optional. Install the API dependencies first:

```bash
python -m pip install -e ".[api]"
```

Start the API:

```bash
python -m kos.cli serve --repo sample_data/sample_shop --host 127.0.0.1 --port 8031
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/index/repo` | Index a repository |
| `GET` | `/search?q=verify_payment` | Search nodes |
| `GET` | `/nodes/{node_id}` | Fetch node details |
| `GET` | `/edges/{edge_id}` | Fetch edge details |
| `GET` | `/history/{entity_id}` | Fetch entity history |
| `GET` | `/graph/neighborhood` | Fetch local graph slice |
| `GET` | `/graph/path` | Find a short directed path |
| `GET` | `/healthz` | Health check |

## Development

Run tests:

```bash
export PYTHONPATH=src
python -m unittest discover -s tests -v
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
```

Project layout:

```text
src/kos/
  observation.py   # Python ast -> observations
  discovery.py     # observations -> nodes and edges
  storage.py       # JSONL + SQLite storage
  agent_tools.py   # agent-facing query layer
  cli.py           # command line interface
  api.py           # optional FastAPI REST app
sample_data/sample_shop/
tests/
deep-research-report.md
```

## Current Limits

- Python only.
- Static AST analysis only; runtime behavior is not traced.
- Import and call resolution is intentionally conservative and name-based.
- Tree-sitter, Neo4j export, React Flow UI, embedding search, and MCP integration are planned extension points.
- Validation and revision are minimal: reindexing reconfirms known entities and marks missing active entities as deleted.

## MVP Validation

The included sample repository indexes successfully with:

- 16 Python files scanned
- 54 nodes
- 90 edges
- 1 syntax error captured without crashing

The MVP has also been tested on a larger local Python project with 174 Python files, producing 3040 nodes and 9887 edges with zero parse errors.

