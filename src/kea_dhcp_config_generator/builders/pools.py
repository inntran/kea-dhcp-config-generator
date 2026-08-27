"""Pool range calculation utilities for DHCPv4 and DHCPv6 builders.

Provides:
    calculate_pool_range(subnet_cidr, skip_start, skip_end) — compute usable range
        from a subnet CIDR, excluding network/broadcast, with optional skip offsets.
    parse_pool_range(range_str) — parse an explicit "x.x.x.x - y.y.y.y" range string.

Uses Python stdlib ipaddress only — no netaddr dependency.
"""

import ipaddress


def calculate_pool_range(
    subnet_cidr: str,
    skip_start: int = 0,
    skip_end: int = 0,
) -> tuple[str, str]:
    """Calculate the usable pool range for a subnet, excluding network/broadcast.

    For a /24 subnet 10.0.1.0/24 with no skips:
        → ("10.0.1.1", "10.0.1.254")

    With skip_start=5, skip_end=2:
        → ("10.0.1.6", "10.0.1.252")

    Args:
        subnet_cidr: Subnet in CIDR notation, e.g. "10.0.1.0/24".
        skip_start: Number of addresses to skip from the first usable address.
        skip_end: Number of addresses to skip before the last usable address.

    Returns:
        (start_ip, end_ip) as strings.
    """
    if skip_start < 0:
        raise ValueError(f"skip_start must be non-negative, got {skip_start}")
    if skip_end < 0:
        raise ValueError(f"skip_end must be non-negative, got {skip_end}")

    network = ipaddress.ip_network(subnet_cidr, strict=False)
    first_usable = network.network_address + 1
    last_usable = network.broadcast_address - 1

    if first_usable > last_usable:
        raise ValueError(
            f"Subnet {subnet_cidr!r} has no usable pool range "
            "(the prefix length leaves no usable addresses after excluding "
            "network and broadcast boundaries)."
        )

    start = first_usable + skip_start
    end = last_usable - skip_end
    if start > end:
        raise ValueError(
            f"Pool range for {subnet_cidr!r} with skip_start={skip_start}, "
            f"skip_end={skip_end} produces an empty or inverted range "
            f"({start} > {end}). Reduce skip values."
        )
    return str(start), str(end)


def parse_pool_range(range_str: str) -> tuple[str, str]:
    """Parse an explicit pool range string into a (start, end) tuple.

    Accepts the format used in Kea and in this tool's YAML:
        "10.0.1.10 - 10.0.1.100"

    Args:
        range_str: Range string in the form "x.x.x.x - y.y.y.y".

    Returns:
        (start_ip, end_ip) as strings, with whitespace stripped.
    """
    parts = range_str.split(" - ", maxsplit=1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid pool range string {range_str!r}. "
            "Expected format: 'x.x.x.x - y.y.y.y' (space-dash-space separator)."
        )
    start_str, end_str = parts[0].strip(), parts[1].strip()
    try:
        ipaddress.ip_address(start_str)
    except ValueError as err:
        raise ValueError(
            f"Invalid start IP address {start_str!r} in pool range {range_str!r}."
        ) from err
    try:
        ipaddress.ip_address(end_str)
    except ValueError as err:
        raise ValueError(
            f"Invalid end IP address {end_str!r} in pool range {range_str!r}."
        ) from err
    return start_str, end_str


def align_up(addr: ipaddress.IPv4Address, block_size: int) -> ipaddress.IPv4Address:
    """Round addr up to the next address whose block_size-prefix block it starts.

    e.g. align_up(10.0.1.37, 24) -> 10.0.2.0 (37 is inside 10.0.1.0/24, so the
    next block-aligned address is the start of 10.0.2.0/24). If addr already
    sits on a block boundary, it is returned unchanged.
    """
    block_len = 2 ** (32 - block_size)
    addr_int = int(addr)
    remainder = addr_int % block_len
    if remainder == 0:
        return addr
    return ipaddress.IPv4Address(addr_int + (block_len - remainder))


def allocate_next_block(
    cursor: ipaddress.IPv4Address,
    block_size: int,
    block_count: int,
    last_usable: ipaddress.IPv4Address,
) -> tuple[str, str, ipaddress.IPv4Address]:
    """Claim block_count consecutive block_size-prefix blocks starting at or
    after cursor, aligned to a block_size boundary.

    Returns (start_ip, end_ip, next_cursor) where next_cursor is the address
    immediately after the claimed span (for the following pool in the same
    subnet to continue from).

    Raises ValueError if the claimed span would exceed last_usable.
    """
    if block_size < 1 or block_size > 32:
        raise ValueError(f"block-size must be between 1 and 32, got {block_size}")
    if block_count < 1:
        raise ValueError(f"block-count must be positive, got {block_count}")

    start = align_up(cursor, block_size)
    block_len = 2 ** (32 - block_size)
    span = block_len * block_count
    end = ipaddress.IPv4Address(int(start) + span - 1)

    if end > last_usable:
        raise ValueError(
            f"block-size={block_size}, block-count={block_count} starting at {start} "
            f"needs {span} addresses through {end}, which exceeds the subnet's usable "
            f"range (ends at {last_usable})."
        )

    next_cursor = ipaddress.IPv4Address(int(end) + 1)
    return str(start), str(end), next_cursor
