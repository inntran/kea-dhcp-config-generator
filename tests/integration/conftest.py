from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
VALID_DIR = FIXTURES_DIR / "valid"
EXPECTED_DIR = FIXTURES_DIR / "expected"
INVALID_DIR = FIXTURES_DIR / "invalid"


def fixture_path(directory: Path, stem: str, suffix: str = ".yaml") -> Path:
    return directory / f"{stem}{suffix}"
