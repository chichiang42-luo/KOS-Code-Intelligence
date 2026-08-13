# Contributing

KOS Code Intelligence targets Python 3.11 or newer.

## Development

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
ruff check .
python -m build
```

Keep changes focused and add tests for graph behavior, JSON contracts, or indexing lifecycle changes.

## Pull Requests

- Explain the user-visible behavior and compatibility impact.
- Include evidence for new graph relationships.
- Do not commit `.kos`, `.kos_runs`, virtual environments, or local repository paths.
- Ensure tests, lint, package build, and the sample evaluation suite pass.
