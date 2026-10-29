# Evidence

Recorded on 2026-10-29 in the project's Linux test environment (Python 3.13).

- `test-run.txt`: the full test run with coverage.
- `acceptance-transcript.txt`: `examples/acceptance.py`. A synthetic accounts folder is snapshotted twice, deleted, restored and compared; then one stored chunk is damaged and `verify`, `restore` and a partial restore are shown. The transcript is deterministic, and `tests/integration/test_acceptance.py` fails if a fresh run differs from this file.
- `scale.txt`: `examples/scale.py`, 5,000 small files plus a 256 MiB file. One machine, one run, not a benchmark.

What this does not show: a real disk failure, very large repositories, Windows, network drives, or CI. GitHub Actions had not been run when this was written.
