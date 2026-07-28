# AGENT_EXECUTION.md

## Goal

Implement and operate KOS Code Intelligence MVP v0.1 for Python repositories.
The MVP extracts static structure from Python AST, builds a property-graph-like model, persists it in JSONL + SQLite, and exposes it through CLI and optional REST API.

## Environment

Preferred local Python:

```powershell
F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe
```

Set source path before running commands:

```powershell
$env:PYTHONPATH='src'
```

## Execution Steps

1. Initialize storage:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli init --repo sample_data\sample_shop
```

2. Index the sample repository:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli index --repo sample_data\sample_shop --repo-id sample_shop
```

3. Search a symbol:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli search verify_payment --repo sample_data\sample_shop
```

4. Inspect a node or edge:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli show node <node_id> --repo sample_data\sample_shop
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli show edge <edge_id> --repo sample_data\sample_shop
```

5. Query a local graph:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli neighborhood <node_id> --repo sample_data\sample_shop --hops 1
```

6. Ask for an agent context pack before editing code:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli agent-pack verify_payment --repo sample_data\sample_shop
```

For an external indexed repository:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m kos.cli agent-pack ModelManager --store-root '.\.kos_runs\Wan-Dancer-14B-batch'
```

7. Run tests:

```powershell
& 'F:\heimaAPP\Anaconda3\envs\General_Agent\python.exe' -m unittest discover -s tests -v
```

## Completion Criteria

- Full indexing completes without crashing on syntax-error fixtures.
- `verify_payment` is searchable.
- The local graph around `verify_payment` includes a `CALLS` edge from checkout flow.
- `agent-pack verify_payment` returns target, facts, and read-plan files.
- Reindexing after a rename marks missing previous entities as deleted.
- Unit tests pass.
