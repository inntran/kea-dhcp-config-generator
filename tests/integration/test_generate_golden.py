"""Integration tests for the public generate() API.

Comparison scope: the generated file headers are stripped and the Dhcp4/Dhcp6
payloads are aggregated into a single protocol-keyed JSON object that is
compared byte-for-byte against the committed golden text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kea_dhcp_config_generator import generate

from .conftest import EXPECTED_DIR, VALID_DIR, fixture_path

VALID_FIXTURES = [
    "minimal_v4",
    "minimal_v6",
    "full_v4_v6",
    "named_fingerprints",
    "auto_pools",
    "host_reservations",
    "option_inheritance",
]


def _payload_object(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {key: value for key, value in document.items() if key != "_kea-config-generator"}


@pytest.mark.parametrize("stem", VALID_FIXTURES)
def test_generate_matches_golden(tmp_path: Path, capsys: pytest.CaptureFixture[str], stem: str):
    fixture = fixture_path(VALID_DIR, stem)
    result = generate(fixture, output_dir=tmp_path, overwrite=True)

    golden = (EXPECTED_DIR / f"{stem}.json").read_text(encoding="utf-8")

    assert result.dhcp4_path is None or result.dhcp4_path.exists()
    assert result.dhcp6_path is None or result.dhcp6_path.exists()

    generated_payloads: dict[str, dict] = {}
    if result.dhcp4_path is not None:
        generated_payloads["Dhcp4"] = _payload_object(result.dhcp4_path)["Dhcp4"]
    if result.dhcp6_path is not None:
        generated_payloads["Dhcp6"] = _payload_object(result.dhcp6_path)["Dhcp6"]

    assert generated_payloads
    rendered = json.dumps(generated_payloads, indent=2, ensure_ascii=True, sort_keys=False) + "\n"
    assert rendered == golden

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
