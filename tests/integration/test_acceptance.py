"""The recorded disaster-recovery transcript must match a fresh run, byte for byte."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_scenario():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "rp_acceptance", ROOT / "examples" / "acceptance.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["rp_acceptance"] = module
    spec.loader.exec_module(module)
    return module


def test_transcript_matches_the_recorded_evidence(tmp_path: Path) -> None:
    fresh = load_scenario().run_scenario(tmp_path.resolve())
    recorded = (ROOT / "docs" / "evidence" / "acceptance-transcript.txt").read_text(
        encoding="utf-8"
    )
    assert fresh == recorded


def test_the_scenario_ends_where_it_claims(tmp_path: Path) -> None:
    text = load_scenario().run_scenario(tmp_path.resolve())
    assert "recovered tree identical to the lost one: yes" in text
    assert "untouched ledgers were recovered intact: yes" in text
    assert "integrity: FAILED\n[exit 2]" in text and "integrity: OK\n[exit 0]" in text
