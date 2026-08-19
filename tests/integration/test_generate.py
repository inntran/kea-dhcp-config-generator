"""Integration test for control-sockets, hooks-libraries, interfaces-config, lease-database features."""

from pathlib import Path
import json

import pytest

from kea_dhcp_config_generator import generate


def test_generate_control_hooks_db(tmp_path: Path):
    """Integration test for control-sockets, hooks-libraries, interfaces-config, lease-database."""
    result = generate(
        Path("tests/integration/fixtures/input/control-hooks-db.yaml"),
        output_dir=tmp_path,
        overwrite=True
    )

    # Verify outputs exist
    assert result.dhcp4_path.exists()
    assert result.dhcp6_path.exists()

    # Load and compare to expected
    with open("tests/integration/fixtures/expected/control-hooks-db-dhcp4.json") as f:
        expected_dhcp4 = json.load(f)
    with open(result.dhcp4_path) as f:
        actual_dhcp4 = json.load(f)

    assert actual_dhcp4 == expected_dhcp4, f"DHCPv4 mismatch"

    with open("tests/integration/fixtures/expected/control-hooks-db-dhcp6.json") as f:
        expected_dhcp6 = json.load(f)
    with open(result.dhcp6_path) as f:
        actual_dhcp6 = json.load(f)

    assert actual_dhcp6 == expected_dhcp6, f"DHCPv6 mismatch"
