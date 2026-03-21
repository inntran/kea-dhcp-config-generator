"""Unit tests for builders/pools.py — pool range calculation and parsing."""

from kea_dhcp_config_generator.builders.pools import calculate_pool_range, parse_pool_range

# ---------------------------------------------------------------------------
# calculate_pool_range (AC #4, #5)
# ---------------------------------------------------------------------------


def test_calculate_pool_range_slash24_no_skip():
    """AC #4: /24 with no skip → .1 to .254 (network and broadcast excluded)."""
    start, end = calculate_pool_range("10.0.1.0/24")
    assert start == "10.0.1.1"
    assert end == "10.0.1.254"


def test_calculate_pool_range_slash24_with_skip():
    """AC #5: /24 with skip-start=5, skip-end=2 → .6 to .252."""
    start, end = calculate_pool_range("10.0.1.0/24", skip_start=5, skip_end=2)
    assert start == "10.0.1.6"
    assert end == "10.0.1.252"


def test_calculate_pool_range_slash24_skip_start_only():
    """Only skip_start shifts the start; end is .254."""
    start, end = calculate_pool_range("10.0.1.0/24", skip_start=10)
    assert start == "10.0.1.11"
    assert end == "10.0.1.254"


def test_calculate_pool_range_slash24_skip_end_only():
    """Only skip_end shifts the end; start is .1."""
    start, end = calculate_pool_range("10.0.1.0/24", skip_end=10)
    assert start == "10.0.1.1"
    assert end == "10.0.1.244"


def test_calculate_pool_range_slash16():
    """/16 subnet: usable range .0.1 to .255.254."""
    start, end = calculate_pool_range("192.168.0.0/16")
    assert start == "192.168.0.1"
    assert end == "192.168.255.254"


def test_calculate_pool_range_slash25():
    """/25 subnet (first half of /24): .1 to .126."""
    start, end = calculate_pool_range("10.0.1.0/25")
    assert start == "10.0.1.1"
    assert end == "10.0.1.126"


def test_calculate_pool_range_different_base_address():
    """Works correctly for subnets not starting at .0."""
    start, end = calculate_pool_range("172.16.5.0/24")
    assert start == "172.16.5.1"
    assert end == "172.16.5.254"


def test_calculate_pool_range_zero_skips_is_default():
    """Explicit zero skips produce same result as defaults."""
    r1 = calculate_pool_range("10.0.1.0/24")
    r2 = calculate_pool_range("10.0.1.0/24", skip_start=0, skip_end=0)
    assert r1 == r2


# ---------------------------------------------------------------------------
# parse_pool_range (AC #6)
# ---------------------------------------------------------------------------


def test_parse_pool_range_basic():
    """AC #6: explicit range string parsed to (start, end) tuple."""
    start, end = parse_pool_range("10.0.1.10 - 10.0.1.100")
    assert start == "10.0.1.10"
    assert end == "10.0.1.100"


def test_parse_pool_range_full_subnet_range():
    """Full usable range string parses correctly."""
    start, end = parse_pool_range("10.0.1.1 - 10.0.1.254")
    assert start == "10.0.1.1"
    assert end == "10.0.1.254"


def test_parse_pool_range_single_address_range():
    """A range where start == end parses correctly."""
    start, end = parse_pool_range("192.168.1.50 - 192.168.1.50")
    assert start == "192.168.1.50"
    assert end == "192.168.1.50"


def test_parse_pool_range_returns_strings():
    """parse_pool_range returns str tuples, not IP objects."""
    start, end = parse_pool_range("10.0.0.1 - 10.0.0.100")
    assert isinstance(start, str)
    assert isinstance(end, str)


def test_calculate_pool_range_returns_strings():
    """calculate_pool_range returns str tuples, not IP objects."""
    start, end = calculate_pool_range("10.0.1.0/24")
    assert isinstance(start, str)
    assert isinstance(end, str)


def test_calculate_pool_range_inverted_raises():
    """calculate_pool_range raises ValueError when skips exceed usable addresses (F2)."""
    import pytest

    # /30 has exactly 2 usable addresses (.1 and .2); any skip inverts the range
    with pytest.raises(ValueError, match="inverted range"):
        calculate_pool_range("10.0.0.0/30", skip_start=2)


def test_calculate_pool_range_excessive_combined_skip_raises():
    """Combined skip_start + skip_end exhausting usable addresses raises ValueError (F2)."""
    import pytest

    # /24 has 254 usable; skip 200 + 60 = 260 > 254
    with pytest.raises(ValueError, match="inverted range"):
        calculate_pool_range("10.0.1.0/24", skip_start=200, skip_end=60)


# ---------------------------------------------------------------------------
# IPv6 pool ranges (F6)
# ---------------------------------------------------------------------------


def test_calculate_pool_range_ipv6_slash64():
    """IPv6 /64 subnet: first usable is ::1, last usable excludes the all-ones address."""
    start, end = calculate_pool_range("2001:db8::/64")
    assert start == "2001:db8::1"
    assert end == "2001:db8::ffff:ffff:ffff:fffe"


def test_calculate_pool_range_ipv6_slash48():
    """IPv6 /48 subnet: usable range from ::1 to last-minus-one."""
    start, end = calculate_pool_range("2001:db8::/48")
    assert start == "2001:db8::1"
    assert end == "2001:db8:0:ffff:ffff:ffff:ffff:fffe"


def test_calculate_pool_range_ipv6_with_skip():
    """IPv6 /64 with skip_start=1 advances start by one address."""
    start, end = calculate_pool_range("2001:db8::/64", skip_start=1)
    assert start == "2001:db8::2"
    assert end == "2001:db8::ffff:ffff:ffff:fffe"


# ---------------------------------------------------------------------------
# parse_pool_range — error handling (F3)
# ---------------------------------------------------------------------------


def test_parse_pool_range_missing_separator_raises():
    """parse_pool_range raises ValueError when separator is absent (F3)."""
    import pytest

    with pytest.raises(ValueError, match="Invalid pool range string"):
        parse_pool_range("10.0.1.1-10.0.1.100")  # dash without spaces


def test_parse_pool_range_empty_string_raises():
    """parse_pool_range raises ValueError on empty input (F3)."""
    import pytest

    with pytest.raises(ValueError, match="Invalid pool range string"):
        parse_pool_range("")


# ---------------------------------------------------------------------------
# parse_pool_range — IP address validation (P-2)
# ---------------------------------------------------------------------------


def test_parse_pool_range_invalid_start_ip_raises():
    """parse_pool_range raises ValueError for a non-IP start address."""
    import pytest

    with pytest.raises(ValueError, match="Invalid start IP address"):
        parse_pool_range("not-an-ip - 10.0.1.100")


def test_parse_pool_range_invalid_end_ip_raises():
    """parse_pool_range raises ValueError for a non-IP end address."""
    import pytest

    with pytest.raises(ValueError, match="Invalid end IP address"):
        parse_pool_range("10.0.1.10 - garbage")


def test_parse_pool_range_out_of_range_octet_raises():
    """parse_pool_range raises ValueError when an octet exceeds 255."""
    import pytest

    with pytest.raises(ValueError, match="Invalid"):
        parse_pool_range("10.0.1.10 - 10.0.1.9999")


# ---------------------------------------------------------------------------
# calculate_pool_range — negative skip validation (P-3)
# ---------------------------------------------------------------------------


def test_calculate_pool_range_negative_skip_start_raises():
    """calculate_pool_range raises ValueError for negative skip_start."""
    import pytest

    with pytest.raises(ValueError, match="skip_start must be non-negative"):
        calculate_pool_range("10.0.1.0/24", skip_start=-1)


def test_calculate_pool_range_negative_skip_end_raises():
    """calculate_pool_range raises ValueError for negative skip_end."""
    import pytest

    with pytest.raises(ValueError, match="skip_end must be non-negative"):
        calculate_pool_range("10.0.1.0/24", skip_end=-5)


# ---------------------------------------------------------------------------
# calculate_pool_range — too-small subnets (P-4)
# ---------------------------------------------------------------------------


def test_calculate_pool_range_slash32_raises():
    """calculate_pool_range raises ValueError for /32 (no usable addresses)."""
    import pytest

    with pytest.raises(ValueError, match="no usable pool range"):
        calculate_pool_range("10.0.1.1/32")


def test_calculate_pool_range_slash31_raises():
    """calculate_pool_range raises ValueError for /31 (no usable addresses after excl.)."""
    import pytest

    with pytest.raises(ValueError, match="no usable pool range"):
        calculate_pool_range("10.0.1.0/31")


def test_calculate_pool_range_slash32_error_does_not_blame_skips():
    """Error for /32 mentions subnet size, not skip values."""
    import pytest

    with pytest.raises(ValueError, match="no usable pool range") as exc_info:
        calculate_pool_range("10.0.1.1/32", skip_start=0, skip_end=0)

    assert "skip" not in str(exc_info.value).lower() or "skip_start=0" not in str(exc_info.value)
