# KOS Code Intelligence MVP

This repository is a runnable MVP based on `deep-research-report.md`.
It builds a static Python code knowledge graph from AST observations and stores the current graph in SQLite with JSONL history logs.

## What Works

- Python AST observation for files, modules, classes, functions, methods, imports, inheritance, and calls.
- Relationship discovery for `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `MAY_CALL`, and `INHERITS`.
- JSONL logs under `.kos/logs/` and SQLite current state under `.kos/graph.db`.
- CLI for init, index, search, show, history, neighborhood, and path queries.
- Agent-focused CLI commands for resolve, callers, callees, impact, read-plan, and one-shot context packs.
- Optional FastAPI app for REST access when `fastapi` and `uvicorn` are installed.
- A sample Python repository under `sample_data/sample_shop`.
- Standard-library `unittest` coverage for the core MVP path.

## Project Layout

```text
src/kos/
  observation.py   # Python ast -> observations
  discovery.py     # observations -> nodes and edges
  storage.py       # JSONL + SQLite storage
  indexer.py       # end-to-end indexing pipeline
  cli.py           # command line interface
  api.py           # optional FastAPI REST app
sample_data/sample_shop/
tests/
deep-research-report.md
```

## Run With Your Conda Python

On this machine, the most suitable existing environment is:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli index --repo sample_data\sample_shop --repo-id sample_shop
```

Search a symbol:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli search verify_payment --repo sample_data\sample_shop
```

Show a node:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli show node <node_id> --repo sample_data\sample_shop
```

Get a local graph:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli neighborhood <node_id> --repo sample_data\sample_shop --hops 1 --edge-types CALLS,IMPORTS,INHERITS
```

Index an external repository without writing `.kos` into that repository:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli index --repo 'F:\Wan-Dancer-14B' --repo-id Wan-Dancer-14B --store-root '.\.kos_runs\Wan-Dancer-14B'
```

Query that external index:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli search ModelManager --store-root '.\.kos_runs\Wan-Dancer-14B'
```

## Agent Tools

The `agent-*` commands return stable JSON for coding agents:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli agent-pack ModelManager --store-root '.\.kos_runs\Wan-Dancer-14B' --limit-files 10 --limit-facts 40
```

Available commands:

| Command | Purpose |
|---|---|
| `agent-resolve QUERY` | Resolve a symbol, fqname, or file path into a target node or candidates |
| `agent-who-calls QUERY` | Return incoming `CALLS` facts |
| `agent-calls QUERY` | Return outgoing `CALLS` facts |
| `agent-impact QUERY` | Return one-hop `CALLS`, `IMPORTS`, and `INHERITS` facts |
| `agent-read-plan QUERY` | Return the files an agent should read before editing |
| `agent-pack QUERY` | Return target, facts, read-plan files, and limits in one payload |

Agent JSON shape:

```json
{
  "status": "ok",
  "query": "ModelManager",
  "target": {"node_id": "...", "fqname": "...", "kind": "class", "file_path": "...", "span": null, "signature": null},
  "candidates": [],
  "files": [{"file_path": "...", "priority": 1, "reason": "target definition", "related_nodes": ["..."]}],
  "facts": [{"rel_type": "CALLS", "src": {}, "dst": {}, "confidence": 0.85, "evidence": []}],
  "limits": {"file_limit": 10, "fact_limit": 40}
}
```

Run tests:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m unittest discover -s tests -v
```

## REST API

The API module is implemented, but FastAPI is optional so the CLI/core can run without external packages.
After installing `fastapi` and `uvicorn`, start it with:

```powershell
$env:PYTHONPATH='src'
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli serve --repo sample_data\sample_shop --host 127.0.0.1 --port 8031
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

## Current Limits

- Python only.
- Static AST analysis only; runtime behavior is not traced.
- Import and call resolution is intentionally conservative and name-based.
- Tree-sitter, Neo4j export, React Flow UI, and embedding search are planned extension points, not part of this first runnable cut.
- Validation and revision are minimal: reindexing reconfirms known entities and marks missing active entities as deleted.

## Acceptance Snapshot

The sample repository currently indexes successfully with:

- 16 Python files scanned
- 54 nodes
- 90 edges
- 1 syntax error captured without crashing
