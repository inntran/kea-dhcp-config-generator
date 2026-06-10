"""Unit tests for analysis/report.py — Configuration Analysis Report Generator.

Tests cover Story 6.1 acceptance criteria:
  AC1: generate_report returns str, no stream writes
  AC2: DHCPv4 subnet inventory with pool counts and total IPs
  AC3: DHCPv6 NA pool address counts
  AC4: DHCPv6 PD pool delegable-prefix counts (2^(delegated_len - prefix_len))
  AC5: Classification section with pinned/unpinned library version
  AC6: Classification lists distinct named rules sorted and de-duplicated
  AC7: Classification flags subnets with no catch-all pool
  AC8: Host reservation section per-subnet MAC (v4) and DUID (v6) counts
  AC9: Validation results section (FAIL/WARN) when present, omitted when None
  AC10: Determinism — same input yields identical string
"""

from io import StringIO

import pytest
from ruamel.yaml import YAML

from kea_dhcp_config_generator.analysis import generate_report
from kea_dhcp_config_generator.models.input import GlobalConfig, parse
from kea_dhcp_config_generator.validation.errors import (
    ConfigError,
    ConfigWarning,
    ValidationResult,
)


def _load_yaml(yaml_text: str):
    """Parse YAML text into CommentedMap using round-trip mode."""
    yaml = YAML()
    return yaml.load(StringIO(yaml_text.strip()))


def _parse_config(yaml_text: str) -> GlobalConfig:
    """Parse YAML text into a validated GlobalConfig."""
    raw = _load_yaml(yaml_text)
    return parse(raw)


# ---------------------------------------------------------------------------
# AC1: Return type and no stream writes
# ---------------------------------------------------------------------------


def test_generate_report_returns_string():
    """generate_report returns str."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    result = generate_report(config)
    assert isinstance(result, str)


def test_generate_report_no_stream_writes(capsys):
    """generate_report writes nothing to stdout or stderr."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    generate_report(config)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# AC2: DHCPv4 subnet inventory
# ---------------------------------------------------------------------------


def test_dhcp4_subnet_inventory_simple():
    """DHCPv4 subnet inventory shows total count and per-subnet CIDR/pool-count/total-IPs."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
        - range: "10.0.1.100 - 10.0.1.200"
""")
    report = generate_report(config)
    assert "Subnet Inventory" in report
    assert "DHCPv4 subnets: 1" in report
    assert "10.0.1.0/24" in report
    assert "pools 2" in report
    # Pool 1: 10.0.1.10 to 10.0.1.50 = 41 addresses
    # Pool 2: 10.0.1.100 to 10.0.1.200 = 101 addresses
    # Total: 142
    assert "total IPs: 142" in report


def test_dhcp4_pool_range_auto():
    """DHCPv4 pool with 'auto' range resolves via calculate_pool_range."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: auto
""")
    report = generate_report(config)
    # 10.0.1.0/24 usable range is 10.0.1.1 - 10.0.1.254 = 254 addresses
    assert "total IPs: 254" in report


def test_dhcp4_multiple_subnets():
    """DHCPv4 multiple subnets listed separately."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
    - subnet: 10.0.2.0/24
      pools:
        - range: "10.0.2.10 - 10.0.2.100"
""")
    report = generate_report(config)
    assert "DHCPv4 subnets: 2" in report
    assert "10.0.1.0/24" in report
    assert "10.0.2.0/24" in report


def test_dhcp4_subnet_no_pools():
    """DHCPv4 subnet with no pools shows pool count 0."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    report = generate_report(config)
    assert "pools 0" in report
    assert "total IPs: 0" in report


# ---------------------------------------------------------------------------
# AC3: DHCPv6 NA subnet
# ---------------------------------------------------------------------------


def test_dhcp6_na_pool_inventory():
    """DHCPv6 NA pool labelled 'NA' with address count."""
    config = _parse_config("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: na
          range: "2001:db8::1 - 2001:db8::100"
""")
    report = generate_report(config)
    assert "DHCPv6 subnets: 1" in report
    assert "2001:db8::/48" in report
    assert "NA" in report
    # 2001:db8::1 to 2001:db8::100 = 256 addresses, rendered as an exact power.
    assert "2^8 addresses" in report


def test_dhcp6_na_pool_auto_range():
    """DHCPv6 NA pool with 'auto' range resolved."""
    config = _parse_config("""
dhcp6:
  subnets:
    - subnet: "2001:db8:1::/64"
      pools:
        - pool-type: na
          range: auto
""")
    report = generate_report(config)
    # 2001:db8:1::/64 usable span is 2^64 - 2 addresses; not an exact power, so it
    # renders against the nearest exponent as "~2^64" (never a 20-digit decimal).
    assert "NA: ~2^64 addresses" in report


# ---------------------------------------------------------------------------
# AC4: DHCPv6 PD pool
# ---------------------------------------------------------------------------


def test_dhcp6_pd_pool_delegable_prefix_count():
    """DHCPv6 PD pool labelled 'PD' with delegable-prefix count."""
    config = _parse_config("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: pd
          prefix: "2001:db8:0::/48"
          prefix-len: 48
          delegated-len: 64
""")
    report = generate_report(config)
    assert "PD" in report
    # Delegable prefixes: 2^(64 - 48) = 2^16 = 65536
    assert "65536" in report


def test_dhcp6_pd_pool_small_range():
    """DHCPv6 PD pool with smaller delegation range."""
    config = _parse_config("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: pd
          prefix: "2001:db8:0::/48"
          prefix-len: 48
          delegated-len: 56
""")
    report = generate_report(config)
    # Delegable prefixes: 2^(56 - 48) = 2^8 = 256
    assert "256" in report


# ---------------------------------------------------------------------------
# AC5: Classification — library version
# ---------------------------------------------------------------------------


def test_classification_pinned_library_version():
    """Classification section shows pinned fingerprint_library_version."""
    config = _parse_config("""
fingerprint_library_version: "0.1.0"
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    report = generate_report(config)
    assert "Client Classification" in report
    assert "fingerprint_library_version: 0.1.0" in report


def test_classification_unpinned_library_version():
    """Classification section shows 'unpinned' when fingerprint_library_version absent."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    report = generate_report(config)
    assert "Client Classification" in report
    assert "unpinned" in report


# ---------------------------------------------------------------------------
# AC6: Classification — named rules in use
# ---------------------------------------------------------------------------


def test_classification_rules_in_use():
    """Classification lists distinct named rules referenced in pools, sorted, de-duplicated."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
          client-class: "Android_12_14"
        - range: "10.0.1.100 - 10.0.1.200"
          client-class: "macOS"
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: na
          range: "2001:db8::1 - 2001:db8::100"
          client-class: "Android_12_14"
""")
    report = generate_report(config)
    assert "rules in use:" in report
    assert "Android_12_14" in report
    assert "macOS" in report


def test_classification_no_rules():
    """Classification shows 'none' when no rules in use."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
""")
    report = generate_report(config)
    assert "rules in use: none" in report


# ---------------------------------------------------------------------------
# AC7: Classification — catch-all flag
# ---------------------------------------------------------------------------


def test_classification_no_catch_all_flag():
    """Classification flags subnet where all pools are class-restricted."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
          client-class: "Android_12_14"
""")
    report = generate_report(config)
    assert "no catch-all pool" in report or "all pools class-restricted" in report


def test_classification_with_catch_all():
    """Classification does not flag when subnet has unrestricted pool."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
          client-class: "Android_12_14"
        - range: "10.0.1.100 - 10.0.1.200"
""")
    report = generate_report(config)
    # This subnet has a catch-all pool (second one without client-class)
    # so it shouldn't be flagged
    for line in report.split("\n"):
        if "no catch-all pool" in line.lower() and "10.0.1.0/24" in line:
            pytest.fail("Subnet with catch-all pool should not be flagged")


# ---------------------------------------------------------------------------
# AC8: Host reservations
# ---------------------------------------------------------------------------


def test_host_reservations_dhcp4_mac():
    """Host reservation section shows DHCPv4 MAC reservation counts."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.0.1.10"
        - hw-address: "11:22:33:44:55:66"
          ip-address: "10.0.1.11"
""")
    report = generate_report(config)
    assert "Host Reservations" in report
    assert "10.0.1.0/24" in report
    assert "2" in report  # 2 reservations


def test_host_reservations_dhcp6_duid():
    """Host reservation section shows DHCPv6 DUID reservation counts."""
    config = _parse_config("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      reservations:
        - duid: "00:03:00:01:aa:bb:cc:dd:ee:ff"
        - duid: "00:03:00:01:11:22:33:44:55:66"
""")
    report = generate_report(config)
    assert "Host Reservations" in report
    assert "2001:db8::/48" in report
    assert "2" in report  # 2 reservations


def test_host_reservations_mixed():
    """Host reservation section splits DHCPv4 (MAC) and DHCPv6 (DUID)."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.0.1.10"
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      reservations:
        - duid: "00:03:00:01:aa:bb:cc:dd:ee:ff"
""")
    report = generate_report(config)
    assert "Host Reservations" in report
    # Should have separate lines for v4 and v6
    assert "10.0.1.0/24" in report
    assert "2001:db8::/48" in report


def test_host_reservations_empty():
    """Host reservation section shows 'none' when no reservations."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    report = generate_report(config)
    assert "Host Reservations" in report
    assert "none" in report or "0" in report


def test_host_reservations_zero_count_subnets_reported():
    """Per-subnet counts include zero-count subnets so mixed configs are unambiguous.

    Regression for codex review [P2]: a subnet with no reservations must still
    appear with an explicit 0 count, distinct from a subnet that was never listed.
    """
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.0.1.10"
    - subnet: 10.0.2.0/24
""")
    report = generate_report(config)
    # The subnet with a reservation shows 1; the empty subnet shows 0 explicitly.
    assert "10.0.1.0/24: 1 MAC (DHCPv4)" in report
    assert "10.0.2.0/24: 0 MAC (DHCPv4)" in report


# ---------------------------------------------------------------------------
# AC9: Validation results
# ---------------------------------------------------------------------------


def test_validation_results_with_errors():
    """Validation results section lists errors with FAIL status."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    validation_result = ValidationResult(
        errors=[
            ConfigError(
                message="Overlapping subnet",
                yaml_path="dhcp4.subnets[0].subnet",
                line=5,
                suggestion="Use a different CIDR",
            )
        ]
    )
    report = generate_report(config, validation_result)
    assert "Validation Results" in report
    assert "FAIL" in report
    assert "Overlapping subnet" in report
    assert "Line 5" in report


def test_validation_results_with_warnings():
    """Validation results section lists warnings with WARN status."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    validation_result = ValidationResult(
        warnings=[
            ConfigWarning(
                message="Version mismatch",
                yaml_path="fingerprint_library_version",
                line=1,
                suggestion="Update to latest version",
            )
        ]
    )
    report = generate_report(config, validation_result)
    assert "Validation Results" in report
    assert "WARN" in report
    assert "Version mismatch" in report


def test_validation_results_pass():
    """Validation results section shows single PASS line when clean."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    validation_result = ValidationResult(errors=[], warnings=[])
    report = generate_report(config, validation_result)
    assert "Validation Results" in report
    assert "PASS" in report


def test_validation_results_none():
    """Validation results section omitted entirely when validation_result is None."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    report = generate_report(config, validation_result=None)
    assert "Validation Results" not in report


# ---------------------------------------------------------------------------
# AC10: Determinism
# ---------------------------------------------------------------------------


def test_determinism_identical_calls():
    """Same input yields byte-identical output."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
          client-class: "Android"
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: pd
          prefix: "2001:db8:0::/48"
          prefix-len: 48
          delegated-len: 64
fingerprint_library_version: "0.1.0"
""")
    result1 = generate_report(config)
    result2 = generate_report(config)
    assert result1 == result2
    assert isinstance(result1, str)
    assert len(result1) > 0


# ---------------------------------------------------------------------------
# AC: Section ordering
# ---------------------------------------------------------------------------


def test_section_ordering():
    """Sections appear in fixed order: header, Inventory, Classification, Reservations,
    [Validation]."""
    config = _parse_config("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.10 - 10.0.1.50"
          client-class: "Android"
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.0.1.10"
""")
    validation_result = ValidationResult(errors=[])
    report = generate_report(config, validation_result)

    # Find positions of sections
    subnet_pos = report.find("Subnet Inventory")
    class_pos = report.find("Client Classification")
    res_pos = report.find("Host Reservations")
    val_pos = report.find("Validation Results")

    assert subnet_pos < class_pos < res_pos < val_pos


def test_empty_config_sections():
    """Empty config sections render with 'none' line."""
    config = _parse_config("""
dhcp4:
  subnets: []
""")
    report = generate_report(config)
    # No DHCPv4 subnets, no DHCPv6
    assert "Subnet Inventory" in report
    assert "DHCPv4 subnets: 0" in report
    assert "DHCPv6 subnets: 0" in report


# ---------------------------------------------------------------------------
# Power-of-two NA address-count rendering
# ---------------------------------------------------------------------------


def test_format_pow2_exact_approx_and_minus():
    """Counts render against the next power up: exact 2^n, ~2^n within 4, else 2^n - k."""
    from kea_dhcp_config_generator.analysis.report import _format_pow2

    # Exact powers of two.
    assert _format_pow2(1) == "2^0"
    assert _format_pow2(256) == "2^8"
    assert _format_pow2(2**64) == "2^64"

    # Gap <= 4 below the next power -> "~2^n".
    assert _format_pow2(2**8 - 1) == "~2^8"  # gap 1
    assert _format_pow2(2**8 - 4) == "~2^8"  # gap 4 (boundary, inclusive)
    assert _format_pow2(2**64 - 2) == "~2^64"  # auto /64 span

    # Gap >= 5 below the next power -> "2^n - k" (always minus, never plus).
    assert _format_pow2(2**8 - 5) == "2^8 - 5"  # gap 5 (boundary)
    assert _format_pow2(236) == "2^8 - 20"  # 256 - 20
    assert _format_pow2(500) == "2^9 - 12"  # 512 - 12
    assert _format_pow2(100) == "2^7 - 28"  # 128 - 28

    # Degenerate counts.
    assert _format_pow2(0) == "0"
