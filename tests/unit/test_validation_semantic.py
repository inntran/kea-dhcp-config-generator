"""Unit tests for validation/semantic.py — subnet/pool/reservation checks (Story 4.2)."""

from pathlib import Path

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation.errors import SubnetConfigError
from kea_dhcp_config_generator.validation.semantic import validate_semantic


def _run(tmp_path: Path, yaml_text: str):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml_text)
    raw = loader.load(cfg_path)
    config = input_models.parse(raw)
    return validate_semantic(config, raw)


# ---- AC #1: overlapping subnets ----


def test_overlapping_subnets_yields_one_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.4.0/23\n"
        "    - subnet: 10.0.4.0/24\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, SubnetConfigError)
    assert "10.0.4.0/23" in err.message
    assert "10.0.4.0/24" in err.message
    # Line of the second subnet's "- subnet: 10.0.4.0/24" entry.
    assert err.line == 4
    assert err.yaml_path == "dhcp4.subnets[1]"
    assert "non-overlapping" in (err.suggestion or "")


def test_non_overlapping_subnets_no_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "    - subnet: 10.0.2.0/24\n"
    )
    assert _run(tmp_path, yaml_text) == []


# ---- AC #2: pool end equals broadcast ----


def test_pool_end_at_broadcast_yields_specific_suggestion(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.255\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, SubnetConfigError)
    assert "broadcast" in err.message
    assert "10.0.1.255" in err.message
    assert err.suggestion == "use 10.0.1.254 instead"
    assert err.line == 5
    assert err.yaml_path == "dhcp4.subnets[0].pools[0].range"


# ---- AC #3: pool out of bounds ----


def test_pool_start_below_subnet_yields_out_of_bounds(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 9.255.255.1 - 10.0.1.50\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, SubnetConfigError)
    assert "outside" in err.message
    assert "10.0.1.0/24" in err.message
    assert "10.0.1.1 - 10.0.1.254" in (err.suggestion or "")


def test_pool_end_above_subnet_yields_out_of_bounds(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.2.5\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    assert "outside" in errors[0].message


def test_auto_pool_never_flagged(tmp_path):
    # /30 has only 2 usable addresses; an auto pool is still valid by construction.
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/30\n"
        "      pools:\n"
        "        - range: auto\n"
    )
    assert _run(tmp_path, yaml_text) == []


def test_malformed_pool_range_surfaces_as_subnet_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: not-a-range\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, SubnetConfigError)
    assert "invalid pool range" in err.message
    assert err.line == 5


# ---- AC #4: duplicate reservation IPs ----


def test_duplicate_reservation_ips_in_same_subnet_yields_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:01\n"
        "          ip-address: 10.0.1.50\n"
        "        - hw-address: aa:bb:cc:dd:ee:02\n"
        "          ip-address: 10.0.1.50\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, SubnetConfigError)
    assert "10.0.1.50" in err.message
    # Message includes both reservation line numbers.
    assert "5" in err.message and "7" in err.message
    assert err.line == 7
    assert err.yaml_path == "dhcp4.subnets[0].reservations[1]"


def test_same_ip_in_different_subnets_no_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:01\n"
        "          ip-address: 10.0.1.50\n"
        "    - subnet: 10.0.2.0/24\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:02\n"
        "          ip-address: 10.0.1.50\n"
    )
    assert _run(tmp_path, yaml_text) == []


def test_three_duplicates_yields_two_errors(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:01\n"
        "          ip-address: 10.0.1.50\n"
        "        - hw-address: aa:bb:cc:dd:ee:02\n"
        "          ip-address: 10.0.1.50\n"
        "        - hw-address: aa:bb:cc:dd:ee:03\n"
        "          ip-address: 10.0.1.50\n"
    )
    errors = _run(tmp_path, yaml_text)
    assert len(errors) == 2


# ---- AC #5: valid config ----


def test_valid_config_no_errors(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.100\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:01\n"
        "          ip-address: 10.0.1.200\n"
        "        - hw-address: aa:bb:cc:dd:ee:02\n"
        "          ip-address: 10.0.1.201\n"
        "    - subnet: 10.0.2.0/24\n"
    )
    assert _run(tmp_path, yaml_text) == []


# ---- AC #6: collect-all across check types ----


def test_collect_all_returns_multiple_errors(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.255\n"
        "      reservations:\n"
        "        - hw-address: aa:bb:cc:dd:ee:01\n"
        "          ip-address: 10.0.1.50\n"
        "        - hw-address: aa:bb:cc:dd:ee:02\n"
        "          ip-address: 10.0.1.50\n"
        "    - subnet: 10.0.1.0/25\n"
    )
    errors = _run(tmp_path, yaml_text)
    # 1 overlap + 1 broadcast-end + 1 duplicate reservation
    assert len(errors) >= 3
    messages = [e.message for e in errors]
    assert any("overlaps" in m for m in messages)
    assert any("broadcast" in m for m in messages)
    assert any("duplicate" in m for m in messages)


def test_no_dhcp4_returns_empty(tmp_path):
    yaml_text = "dhcp6:\n  subnets: []\n"
    assert _run(tmp_path, yaml_text) == []


def test_validate_semantic_never_raises(tmp_path):
    """Even with multiple error conditions, validate_semantic only returns; never raises."""
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: not-a-range\n"
    )
    # Should not raise.
    errors = _run(tmp_path, yaml_text)
    assert isinstance(errors, list)
