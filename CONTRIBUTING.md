# Contributing

Thanks for your interest in RestorePoint.

## Ways to help

- Report bugs, especially folder layouts, file names or filesystems where snapshot or restore misbehaves (describe them; do not attach real data).
- Improve the restore drill, verification or docs.

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy
python -m pytest --cov                            # must pass before you open a PR
python examples/acceptance.py --out /tmp/evidence # the disaster-recovery scenario
```

## Guidelines

- The core uses only the Python standard library.
- No network calls. RestorePoint reads folders and writes a repository.
- Treat the repository as untrusted input when reading: validate every manifest field and every path, cap decompression, and never write outside the restore target.
- Nothing is deleted except by `prune --apply`, and never a chunk that a readable snapshot uses.
- Changing the manifest format? Bump the schema deliberately.
- The acceptance transcript is compared byte for byte by a test. If behaviour changes on purpose, regenerate it with `python examples/acceptance.py --out docs/evidence` and review the diff.
- Test data must be synthetic. Never commit real backups.
- Do not add claims (performance, platform support, security) that a test or recorded evidence does not back up.
- Use clear commit messages and keep pull requests focused.
