from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = """repo_id: sample_shop
language: python
storage:
  sqlite_path: .kos/graph.db
  jsonl_dir: .kos/logs
  wal: true
graph:
  backend: builtin
routing:
  akrt_enabled: true
validation:
  min_accept_confidence: 0.65
  may_call_threshold: 0.40
api:
  host: 127.0.0.1
  port: 8031
parsers:
  python_ast: true
  tree_sitter: false
"""


@dataclass(slots=True)
class KosConfig:
    repo_id: str = "sample_shop"
    language: str = "python"
    sqlite_path: str = ".kos/graph.db"
    jsonl_dir: str = ".kos/logs"
    api_host: str = "127.0.0.1"
    api_port: int = 8031


def init_kos_dir(root: Path) -> None:
    kos_dir = root / ".kos"
    (kos_dir / "logs").mkdir(parents=True, exist_ok=True)
    (kos_dir / "snapshots").mkdir(parents=True, exist_ok=True)
    (kos_dir / "cache").mkdir(parents=True, exist_ok=True)
    config_path = kos_dir / "config.yaml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")


def load_config(root: Path) -> KosConfig:
    path = root / ".kos" / "config.yaml"
    config = KosConfig()
    if not path.exists():
        return config
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        value = value.strip("'\"")
        if key == "repo_id":
            config.repo_id = value
        elif key == "language":
            config.language = value
        elif key == "sqlite_path":
            config.sqlite_path = value
        elif key == "jsonl_dir":
            config.jsonl_dir = value
        elif key == "host":
            config.api_host = value
        elif key == "port":
            try:
                config.api_port = int(value)
            except ValueError:
                pass
    return config
