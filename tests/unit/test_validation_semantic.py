"""Unit tests for validation/semantic.py — subnet checks (4.2) + classification (4.3)."""

from pathlib import Path

import pytest

from kea_dhcp_config_generator import loader
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models import input as input_models
from kea_dhcp_config_generator.validation.errors import (
    ConfigWarning,
    FingerprintError,
    SubnetConfigError,
)
from kea_dhcp_config_generator.validation.semantic import (
    validate_classification,
    validate_semantic,
)


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


def test_pool_range_within_slash31_is_allowed(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/31\n"
        "      pools:\n"
        "        - range: 10.0.1.0 - 10.0.1.1\n"
    )
    assert _run(tmp_path, yaml_text) == []


def test_pool_range_within_slash32_is_allowed(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.7/32\n"
        "      pools:\n"
        "        - range: 10.0.1.7 - 10.0.1.7\n"
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


# ---- Story 4.3 tests: validate_classification() ----


def _run_class(tmp_path: Path, yaml_text: str, *, library=None):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml_text)
    raw = loader.load(cfg_path)
    config = input_models.parse(raw)
    lib = library if library is not None else DHCPFingerprint()
    return validate_classification(config, raw, lib)


def test_unknown_class_no_close_match_yields_error_with_no_suggestion(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
        "          client-class: zZqXX_no_match_here\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, FingerprintError)
    assert err.suggestion is None
    assert err.yaml_path == "dhcp4.subnets[0].pools[0].client-class"
    assert err.line == 6
    assert 'unknown client-class' in err.message
    assert '"zZqXX_no_match_here"' in err.message


def test_unknown_class_with_close_match_yields_did_you_mean(tmp_path):
    # Verify our canonical rule actually exists.
    lib = DHCPFingerprint()
    assert lib.lookup("Android_12_14") is not None

    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
        "          client-class: Android_12_15\n"  # one-char off
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert len(errors) == 1
    assert errors[0].suggestion == 'did you mean "Android_12_14"?'


@pytest.mark.parametrize(
    "name",
    [
        "ALL", "KNOWN", "UNKNOWN", "DROP", "SKIP_DDNS",
        "VENDOR_CLASS_MSFT", "HA_server1", "AFTER_phase1", "EXTERNAL_radius",
    ],
)
def test_builtin_class_names_never_flagged(tmp_path, name):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
        f"          client-class: {name}\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert errors == []
    # Also no catch-all warning because single restricted pool.
    # This subnet has every pool class-restricted → would normally trigger AC #2.
    assert len(warnings) == 1  # catch-all warning is independent of class identity


def test_subnet_level_unknown_class_yields_error(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      client-class: zZqXX_no_match_here\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert len(errors) == 1
    assert errors[0].yaml_path == "dhcp4.subnets[0].client-class"


def test_no_catch_all_subnet_yields_warning(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: Android_12_14\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: macOS\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert errors == []
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, ConfigWarning)
    assert w.yaml_path == "dhcp4.subnets[0]"
    assert w.line == 3
    assert "catch-all" in w.suggestion
    assert "10.0.1.0/24" in w.message


def test_catch_all_pool_present_no_warning(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: Android_12_14\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"  # no client-class → catch-all
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert warnings == []


def test_empty_pools_no_warning(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert warnings == []


def test_single_unrestricted_pool_no_warning(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: auto\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert warnings == []


def test_collect_all_classification_findings(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        # subnet 0: no catch-all + unknown class
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: zZqXX_unknown_a\n"
        "        - range: 10.0.1.60 - 10.0.1.100\n"
        "          client-class: Android_12_14\n"
        # subnet 1: no catch-all + unknown class
        "    - subnet: 10.0.2.0/24\n"
        "      pools:\n"
        "        - range: 10.0.2.10 - 10.0.2.50\n"
        "          client-class: zZqXX_unknown_b\n"
        "        - range: 10.0.2.60 - 10.0.2.100\n"
        "          client-class: macOS\n"
    )
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert len(errors) == 2
    assert len(warnings) == 2


def test_library_none_skips_unknown_check_but_runs_catch_all(tmp_path):
    yaml_text = (
        "dhcp4:\n"
        "  subnets:\n"
        "    - subnet: 10.0.1.0/24\n"
        "      pools:\n"
        "        - range: 10.0.1.10 - 10.0.1.50\n"
        "          client-class: zZqXX_unknown\n"
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml_text)
    raw = loader.load(cfg_path)
    config = input_models.parse(raw)
    errors, warnings = validate_classification(config, raw, None)
    assert errors == []
    assert len(warnings) == 1


def test_dhcp6_only_short_circuit(tmp_path):
    yaml_text = "dhcp6:\n  subnets: []\n"
    errors, warnings = _run_class(tmp_path, yaml_text)
    assert errors == []
    assert warnings == []
