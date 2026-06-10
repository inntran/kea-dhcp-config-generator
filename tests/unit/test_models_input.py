"""Unit tests for models/input.py — Pydantic input models and structural validation.

Tests cover Story 1.3 acceptance criteria:
  AC1: valid YAML → GlobalConfig returned
  AC2: "24h" string duration → 86400 seconds
  AC3: integer duration passthrough
  AC4: invalid duration → ExceptionGroup[ConfigError] with line number
  AC5: hyphenated keys → snake_case Python attributes
  AC6: option_profiles reference stored as string
  AC7: neither dhcp4 nor dhcp6 → ExceptionGroup[ConfigError]

Tests cover Story 2.4 acceptance criteria:
  AC3: non-ASCII string in any user field → ExceptionGroup[ConfigError], exit 1
"""

from io import StringIO
from ipaddress import IPv6Network

import pytest
from ruamel.yaml import YAML

from kea_dhcp_config_generator.models.input import GlobalConfig, parse
from kea_dhcp_config_generator.validation.errors import ConfigError


def _load(yaml_text: str):
    """Parse YAML text into CommentedMap using round-trip mode."""
    yaml = YAML()
    return yaml.load(StringIO(yaml_text.strip()))


# ---------------------------------------------------------------------------
# AC1: valid YAML → GlobalConfig
# ---------------------------------------------------------------------------


def test_parse_valid_minimal_dhcp4():
    raw = _load("""
dhcp4:
  valid-lifetime: 3600
  subnets:
    - subnet: 10.0.1.0/24
""")
    config = parse(raw)
    assert isinstance(config, GlobalConfig)
    assert config.dhcp4 is not None
    assert config.dhcp4.valid_lifetime == 3600


def test_parse_valid_minimal_dhcp6():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
""")
    config = parse(raw)
    assert isinstance(config, GlobalConfig)
    assert config.dhcp6 is not None
    assert isinstance(config.dhcp6.subnets[0].subnet, IPv6Network)
    assert config.dhcp6.subnets[0].subnet == IPv6Network("2001:db8::/48")


def test_parse_valid_both_protocols():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
""")
    config = parse(raw)
    assert config.dhcp4 is not None
    assert config.dhcp6 is not None


# ---------------------------------------------------------------------------
# AC2: duration string "24h" → 86400
# ---------------------------------------------------------------------------


def test_duration_string_24h():
    raw = _load("""
dhcp4:
  valid-lifetime: "24h"
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.valid_lifetime == 86400


def test_duration_string_30m():
    raw = _load("""
dhcp4:
  renew-timer: "30m"
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.renew_timer == 1800


def test_duration_string_1d():
    raw = _load("""
dhcp4:
  rebind-timer: "1d"
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.rebind_timer == 86400


def test_duration_string_seconds_only():
    raw = _load("""
dhcp4:
  valid-lifetime: "7200"
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.valid_lifetime == 7200


# ---------------------------------------------------------------------------
# AC3: integer duration passthrough
# ---------------------------------------------------------------------------


def test_duration_int_passthrough():
    raw = _load("""
dhcp4:
  valid-lifetime: 3600
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.valid_lifetime == 3600


def test_duration_int_zero():
    raw = _load("""
dhcp4:
  valid-lifetime: 0
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.valid_lifetime == 0


# ---------------------------------------------------------------------------
# AC4: invalid duration → ExceptionGroup[ConfigError] with line number
# ---------------------------------------------------------------------------


def test_invalid_duration_raises_exception_group():
    raw = _load("""
dhcp4:
  valid-lifetime: "2d3x"
  subnets: []
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    assert len(errors) >= 1
    assert all(isinstance(e, ConfigError) for e in errors)


def test_invalid_duration_error_message_describes_format():
    raw = _load("""
dhcp4:
  valid-lifetime: "2d3x"
  subnets: []
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    # Error message should mention the bad value
    assert any("2d3x" in e.message or "Invalid duration" in e.message for e in errors)


def test_invalid_duration_has_yaml_path():
    raw = _load("""
dhcp4:
  valid-lifetime: "2d3x"
  subnets: []
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    assert any("valid_lifetime" in e.yaml_path or "valid-lifetime" in e.yaml_path for e in errors)


def test_invalid_duration_has_line_number():
    raw = _load("""
dhcp4:
  valid-lifetime: "2d3x"
  subnets: []
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    duration_error = next(
        (e for e in errors if "valid_lifetime" in e.yaml_path or "valid-lifetime" in e.yaml_path),
        None,
    )
    assert duration_error is not None
    assert duration_error.line is not None
    assert duration_error.line >= 1


# ---------------------------------------------------------------------------
# AC5: hyphenated keys → snake_case Python attributes
# ---------------------------------------------------------------------------


def test_hyphenated_dns_servers_maps_to_snake_case():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      dns-servers: [8.8.8.8, 8.8.4.4]
""")
    config = parse(raw)
    assert config.dhcp4.subnets[0].dns_servers == ["8.8.8.8", "8.8.4.4"]


def test_hyphenated_valid_lifetime_maps_to_snake_case():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      valid-lifetime: 7200
""")
    config = parse(raw)
    assert config.dhcp4.subnets[0].valid_lifetime == 7200


def test_hyphenated_client_class_maps_to_snake_case():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: auto
          client-class: Windows_11
""")
    config = parse(raw)
    pool = config.dhcp4.subnets[0].pools[0]
    assert pool.client_class == "Windows_11"


def test_hyphenated_hw_address_maps_to_snake_case():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: aa:bb:cc:dd:ee:ff
          ip-address: 10.0.1.100
""")
    config = parse(raw)
    res = config.dhcp4.subnets[0].reservations[0]
    assert res.hw_address == "aa:bb:cc:dd:ee:ff"
    assert res.ip_address == "10.0.1.100"


# ---------------------------------------------------------------------------
# AC6: option_profiles reference stored as string
# ---------------------------------------------------------------------------


def test_option_profile_parsed_from_yaml():
    raw = _load("""
option_profiles:
  corporate:
    valid-lifetime: 86400
    dns-servers: [8.8.8.8]
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      option_profile: corporate
""")
    config = parse(raw)
    assert "corporate" in config.option_profiles
    assert config.option_profiles["corporate"].valid_lifetime == 86400
    assert config.dhcp4.subnets[0].option_profile == "corporate"


def test_option_profile_reference_is_string():
    raw = _load("""
option_profiles:
  my-profile:
    dns-servers: [1.1.1.1]
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      option_profile: my-profile
""")
    config = parse(raw)
    assert isinstance(config.dhcp4.subnets[0].option_profile, str)
    assert config.dhcp4.subnets[0].option_profile == "my-profile"


# ---------------------------------------------------------------------------
# AC7: neither dhcp4 nor dhcp6 → ExceptionGroup[ConfigError]
# ---------------------------------------------------------------------------


def test_missing_both_protocols_raises_exception_group():
    raw = _load("""
option_profiles: {}
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    assert len(errors) >= 1
    assert all(isinstance(e, ConfigError) for e in errors)


def test_missing_both_protocols_error_mentions_dhcp4_dhcp6():
    raw = _load("""
option_profiles: {}
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    combined_msg = " ".join(e.message for e in errors)
    assert "dhcp4" in combined_msg.lower() or "dhcp6" in combined_msg.lower()


# ---------------------------------------------------------------------------
# Additional: ConfigError dataclass structure
# ---------------------------------------------------------------------------


def test_config_error_is_exception():
    err = ConfigError(
        message="test error",
        yaml_path="dhcp4.subnets[0]",
        line=5,
        suggestion="fix this",
    )
    assert isinstance(err, Exception)
    assert err.message == "test error"
    assert err.yaml_path == "dhcp4.subnets[0]"
    assert err.line == 5
    assert err.suggestion == "fix this"


def test_config_error_str_with_line():
    err = ConfigError(
        message="invalid value",
        yaml_path="dhcp4.valid_lifetime",
        line=3,
        suggestion=None,
    )
    s = str(err)
    assert "3" in s
    assert "invalid value" in s


def test_config_error_str_without_line():
    err = ConfigError(
        message="at least one protocol required",
        yaml_path="",
        line=None,
        suggestion=None,
    )
    s = str(err)
    assert "at least one protocol required" in s


# ---------------------------------------------------------------------------
# Pool and reservation models
# ---------------------------------------------------------------------------


def test_pool_auto_range():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: auto
          skip-start: 5
          skip-end: 2
""")
    config = parse(raw)
    pool = config.dhcp4.subnets[0].pools[0]
    assert pool.range == "auto"
    assert pool.skip_start == 5
    assert pool.skip_end == 2


def test_pool_explicit_range():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      pools:
        - range: "10.0.1.100 - 10.0.1.200"
""")
    config = parse(raw)
    pool = config.dhcp4.subnets[0].pools[0]
    assert pool.range == "10.0.1.100 - 10.0.1.200"


def test_host_reservation_full():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: aa:bb:cc:dd:ee:ff
          ip-address: 10.0.1.100
          hostname: mydesktop
""")
    config = parse(raw)
    res = config.dhcp4.subnets[0].reservations[0]
    assert res.hw_address == "aa:bb:cc:dd:ee:ff"
    assert res.ip_address == "10.0.1.100"
    assert res.hostname == "mydesktop"


# ---------------------------------------------------------------------------
# DHCPv6 models
# ---------------------------------------------------------------------------


def test_dhcp6_pd_pool():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: pd
          prefix: "2001:db8:1::"
          prefix-len: 48
          delegated-len: 64
""")
    config = parse(raw)
    pd = config.dhcp6.subnets[0].pools[0]
    assert pd.pool_type == "pd"
    assert pd.prefix == "2001:db8:1::"
    assert pd.prefix_len == 48
    assert pd.delegated_len == 64


def test_dhcp6_na_pool_via_discriminator():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8:1::/64"
      pools:
        - pool-type: na
          range: "2001:db8:1::100 - 2001:db8:1::200"
          client-class: iOS_17
""")
    config = parse(raw)
    na = config.dhcp6.subnets[0].pools[0]
    assert na.pool_type == "na"
    assert na.range == "2001:db8:1::100 - 2001:db8:1::200"
    assert na.client_class == "iOS_17"


def test_dhcp6_mixed_na_pd_order_preserved():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: na
          range: auto
        - pool-type: pd
          prefix: "2001:db8:1::"
          prefix-len: 48
          delegated-len: 64
""")
    config = parse(raw)
    pools = config.dhcp6.subnets[0].pools
    assert [p.pool_type for p in pools] == ["na", "pd"]


def test_dhcp6_pool_missing_pool_type_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - range: auto
""")
    with pytest.raises(ExceptionGroup) as excinfo:
        parse(raw)
    errs = excinfo.value.exceptions
    assert all(isinstance(e, ConfigError) for e in errs)
    assert any("dhcp6.subnets[0].pools[0]" in e.yaml_path for e in errs)


def test_dhcp6_pool_invalid_pool_type_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      pools:
        - pool-type: bogus
          range: auto
""")
    with pytest.raises(ExceptionGroup) as excinfo:
        parse(raw)
    errs = excinfo.value.exceptions
    assert all(isinstance(e, ConfigError) for e in errs)
    assert any("dhcp6.subnets[0].pools[0]" in e.yaml_path for e in errs)


# ---------------------------------------------------------------------------
# DHCPv6 subnet prefix typing (Story 5.1 AC #2)
# ---------------------------------------------------------------------------


def test_dhcp6_subnet_host_bits_tolerated():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8:1::1/64"
""")
    config = parse(raw)
    assert config.dhcp6.subnets[0].subnet == IPv6Network("2001:db8:1::/64")


def _assert_ipv6_prefix_error(raw):
    with pytest.raises(ExceptionGroup) as excinfo:
        parse(raw)
    errs = excinfo.value.exceptions
    assert len(errs) == 1
    err = errs[0]
    assert isinstance(err, ConfigError)
    assert err.yaml_path == "dhcp6.subnets[0].subnet"
    assert "IPv6 prefix" in err.message


def test_dhcp6_subnet_ipv4_cidr_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "10.0.0.0/24"
""")
    _assert_ipv6_prefix_error(raw)


def test_dhcp6_subnet_malformed_string_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "not-a-prefix"
""")
    _assert_ipv6_prefix_error(raw)


def test_dhcp6_subnet_bare_address_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::1"
""")
    _assert_ipv6_prefix_error(raw)


def test_dhcp6_subnet_missing_field_rejected():
    raw = _load("""
dhcp6:
  subnets:
    - {}
""")
    with pytest.raises(ExceptionGroup) as excinfo:
        parse(raw)
    errs = excinfo.value.exceptions
    assert any(e.yaml_path == "dhcp6.subnets[0].subnet" for e in errs)


def test_dhcp6_subnet_non_string_rejected_via_collect_all():
    """A non-string subnet (e.g. a YAML list) must surface as a ConfigError, not a
    bare TypeError that escapes parse()'s ExceptionGroup collect-all path."""
    raw = _load("""
dhcp6:
  subnets:
    - subnet: [1, 2, 3]
""")
    _assert_ipv6_prefix_error(raw)


def test_dhcp6_host_reservation_duid():
    raw = _load("""
dhcp6:
  subnets:
    - subnet: "2001:db8::/48"
      reservations:
        - duid: "00:01:00:01:52:13:52:13:08:00:27:58:f1:e8"
          ip-address: "2001:db8::100"
          hostname: myhost6
""")
    config = parse(raw)
    res = config.dhcp6.subnets[0].reservations[0]
    assert res.duid == "00:01:00:01:52:13:52:13:08:00:27:58:f1:e8"
    assert res.ip_address == "2001:db8::100"
    assert res.hostname == "myhost6"


# ---------------------------------------------------------------------------
# fingerprint_library_version
# ---------------------------------------------------------------------------


def test_fingerprint_library_version_parsed():
    raw = _load("""
fingerprint_library_version: "1.0.0"
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    config = parse(raw)
    assert config.fingerprint_library_version == "1.0.0"


def test_fingerprint_library_version_optional():
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
""")
    config = parse(raw)
    assert config.fingerprint_library_version is None


# ---------------------------------------------------------------------------
# Story 2.4 AC #3: ASCII-only string validation
# ---------------------------------------------------------------------------


def test_non_ascii_domain_name_raises_config_error():
    """AC #3: non-ASCII domain-name → ExceptionGroup[ConfigError]."""
    raw = _load("""
dhcp4:
  domain-name: "中国.cn"
  subnets: []
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    assert any(isinstance(e, ConfigError) for e in errors)
    assert any("ASCII" in e.message for e in errors if isinstance(e, ConfigError))


def test_non_ascii_hostname_raises_config_error():
    """AC #3: non-ASCII hostname in reservation → ExceptionGroup[ConfigError]."""
    raw = _load("""
dhcp4:
  subnets:
    - subnet: 10.0.1.0/24
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.0.1.10"
          hostname: "sérver.local"
""")
    with pytest.raises(ExceptionGroup) as exc_info:
        parse(raw)
    errors = exc_info.value.exceptions
    assert any(isinstance(e, ConfigError) for e in errors)
    assert any("ASCII" in e.message for e in errors if isinstance(e, ConfigError))


def test_ascii_domain_name_accepted():
    """AC #3: ASCII-only domain-name passes validation."""
    raw = _load("""
dhcp4:
  domain-name: "example.com"
  subnets: []
""")
    config = parse(raw)
    assert config.dhcp4.domain_name == "example.com"
