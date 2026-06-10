"""Configuration analysis report generator.

Pure function generate_report walks a fully-validated GlobalConfig and renders
a deterministic plain-text report with sections:
  1. Subnet Inventory (DHCPv4 + DHCPv6 subnets, pools, address counts)
  2. Client Classification (library version, rules in use, catch-all coverage)
  3. Host Reservations (per-subnet MAC/DUID counts)
  4. Validation Results (optional, only when validation_result is supplied)

Returns a str with fixed section ordering and ASCII-only content.
"""

from ipaddress import IPv6Network, ip_address

from kea_dhcp_config_generator.builders.pools import (
    calculate_pool_range,
    parse_pool_range,
)
from kea_dhcp_config_generator.models.input import (
    GlobalConfig,
    PoolV6NaModel,
    PoolV6PdModel,
)
from kea_dhcp_config_generator.validation.errors import ValidationResult


def generate_report(
    config: GlobalConfig, validation_result: ValidationResult | None = None
) -> str:
    """Generate a human-readable analysis report from a validated GlobalConfig.

    Args:
        config: Fully validated GlobalConfig instance.
        validation_result: Optional ValidationResult with errors/warnings.
            When None, the Validation Results section is omitted entirely.

    Returns:
        A deterministic, ASCII-only plain-text report as a single str.
        Same input always yields byte-identical output.
        Pure function: performs no I/O, no side effects.
    """
    sections = []

    # --- Header ---
    sections.extend(_header(config))

    # --- Subnet Inventory ---
    sections.extend(_subnet_inventory(config))

    # --- Client Classification ---
    sections.extend(_classification(config))

    # --- Host Reservations ---
    sections.extend(_reservations(config))

    # --- Validation Results (optional) ---
    if validation_result is not None:
        sections.extend(_validation(validation_result))

    # Join all lines with newlines; trailing newline for cleanliness
    return "\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Private section builders
# ---------------------------------------------------------------------------


def _header(config: GlobalConfig) -> list[str]:
    """Return header lines (title and timestamp stub)."""
    return ["Configuration Analysis Report"]


def _subnet_inventory(config: GlobalConfig) -> list[str]:
    """Subnet inventory section: DHCPv4 count + details, DHCPv6 count + details."""
    lines = ["", "Subnet Inventory"]
    lines.append("-" * 40)

    # --- DHCPv4 ---
    dhcp4_subnets = config.dhcp4.subnets if config.dhcp4 else []
    lines.append(f"DHCPv4 subnets: {len(dhcp4_subnets)}")
    if dhcp4_subnets:
        for subnet in dhcp4_subnets:
            pool_count = len(subnet.pools)
            total_ips = _count_v4_pool_ips(subnet.pools, subnet.subnet)
            lines.append(f"  {subnet.subnet}: pools {pool_count}, total IPs: {total_ips}")
    else:
        lines.append("  (none)")

    # --- DHCPv6 ---
    dhcp6_subnets = config.dhcp6.subnets if config.dhcp6 else []
    lines.append(f"DHCPv6 subnets: {len(dhcp6_subnets)}")
    if dhcp6_subnets:
        for subnet in dhcp6_subnets:
            subnet_str = str(subnet.subnet)
            lines.append(f"  {subnet_str}:")
            if subnet.pools:
                for pool in subnet.pools:
                    if isinstance(pool, PoolV6NaModel):
                        pool_ips = _count_v6_na_pool_ips(pool, subnet.subnet)
                        lines.append(f"    NA: {pool_ips} addresses")
                    elif isinstance(pool, PoolV6PdModel):
                        prefix_count = _count_v6_pd_prefixes(pool)
                        lines.append(f"    PD: {prefix_count} delegable prefixes")
            else:
                lines.append("    (no pools)")
    else:
        lines.append("  (none)")

    return lines


def _count_v4_pool_ips(pools, subnet_cidr: str) -> int:
    """Count total addressable IPs across all DHCPv4 pools in a subnet."""
    total = 0
    for pool in pools:
        if pool.range == "auto":
            start, end = calculate_pool_range(
                subnet_cidr, skip_start=pool.skip_start, skip_end=pool.skip_end
            )
        else:
            start, end = parse_pool_range(pool.range)
        # Convert to integers and count inclusive range
        start_int = int(ip_address(start))
        end_int = int(ip_address(end))
        total += end_int - start_int + 1
    return total


def _count_v6_na_pool_ips(pool: PoolV6NaModel, subnet: IPv6Network) -> int:
    """Count addressable IPv6 addresses in an NA pool."""
    if pool.range == "auto":
        # For IPv6, calculate_pool_range expects a CIDR; use subnet's string representation
        subnet_cidr = str(subnet)
        start, end = calculate_pool_range(subnet_cidr)
    else:
        start, end = parse_pool_range(pool.range)
    # Convert to integers and count inclusive range
    start_int = int(ip_address(start))
    end_int = int(ip_address(end))
    return end_int - start_int + 1


def _count_v6_pd_prefixes(pool: PoolV6PdModel) -> int:
    """Count delegable prefixes in a PD pool: 2^(delegated_len - prefix_len)."""
    return 2 ** (pool.delegated_len - pool.prefix_len)


def _classification(config: GlobalConfig) -> list[str]:
    """Client classification section: version, rules in use, catch-all coverage."""
    lines = ["", "Client Classification"]
    lines.append("-" * 40)

    # --- Library version ---
    if config.fingerprint_library_version:
        lines.append(f"fingerprint_library_version: {config.fingerprint_library_version}")
    else:
        lines.append("fingerprint_library_version: unpinned")

    # --- Rules in use (distinct, sorted, de-duplicated) ---
    rules_in_use = _collect_rules_in_use(config)
    if rules_in_use:
        sorted_rules = sorted(rules_in_use)
        lines.append(f"rules in use: {', '.join(sorted_rules)}")
    else:
        lines.append("rules in use: none")

    # --- Subnets with no catch-all pool ---
    no_catchall_subnets = _find_no_catchall_subnets(config)
    if no_catchall_subnets:
        lines.append("subnets with no catch-all pool:")
        for subnet_cidr in sorted(no_catchall_subnets):
            lines.append(f"  {subnet_cidr} (all pools class-restricted)")
    else:
        lines.append("subnets with no catch-all pool: none")

    return lines


def _collect_rules_in_use(config: GlobalConfig) -> set[str]:
    """Collect distinct named rules referenced across all v4+v6 pools."""
    rules = set()

    # DHCPv4 pools
    if config.dhcp4:
        for subnet in config.dhcp4.subnets:
            for pool in subnet.pools:
                if pool.client_class:
                    rules.add(pool.client_class)

    # DHCPv6 pools
    if config.dhcp6:
        for subnet in config.dhcp6.subnets:
            for pool in subnet.pools:
                if pool.client_class:
                    rules.add(pool.client_class)

    return rules


def _find_no_catchall_subnets(config: GlobalConfig) -> set[str]:
    """Find subnets where all pools are class-restricted (no catch-all)."""
    no_catchall = set()

    # DHCPv4
    if config.dhcp4:
        for subnet in config.dhcp4.subnets:
            if subnet.pools and all(pool.client_class for pool in subnet.pools):
                no_catchall.add(subnet.subnet)

    # DHCPv6
    if config.dhcp6:
        for subnet in config.dhcp6.subnets:
            if subnet.pools and all(pool.client_class for pool in subnet.pools):
                no_catchall.add(str(subnet.subnet))

    return no_catchall


def _reservations(config: GlobalConfig) -> list[str]:
    """Host reservations section: per-subnet counts, split DHCPv4 (MAC) vs DHCPv6 (DUID).

    Every subnet is reported, including those with zero reservations, so the
    per-subnet counts are unambiguous in mixed configs (a 0-count subnet is
    visibly distinct from one that was never reported).
    """
    lines = ["", "Host Reservations"]
    lines.append("-" * 40)

    any_subnets = False

    # DHCPv4 reservations — one line per subnet, including zero counts.
    if config.dhcp4:
        for subnet in config.dhcp4.subnets:
            any_subnets = True
            lines.append(
                f"  {subnet.subnet}: {len(subnet.reservations)} MAC (DHCPv4)"
            )

    # DHCPv6 reservations — one line per subnet, including zero counts.
    if config.dhcp6:
        for subnet in config.dhcp6.subnets:
            any_subnets = True
            lines.append(
                f"  {str(subnet.subnet)}: {len(subnet.reservations)} DUID (DHCPv6)"
            )

    if not any_subnets:
        lines.append("  (none)")

    return lines


def _validation(validation_result: ValidationResult) -> list[str]:
    """Validation results section: errors (FAIL), warnings (WARN), or PASS."""
    lines = ["", "Validation Results"]
    lines.append("-" * 40)

    if validation_result.errors or validation_result.warnings:
        # List errors
        for error in validation_result.errors:
            if error.line is not None:
                lines.append(f"FAIL  Line {error.line}: {error.message}")
            else:
                lines.append(f"FAIL  {error.yaml_path}: {error.message}")

        # List warnings
        for warning in validation_result.warnings:
            if warning.line is not None:
                lines.append(f"WARN  Line {warning.line}: {warning.message}")
            else:
                lines.append(f"WARN  {warning.yaml_path}: {warning.message}")
    else:
        # Clean result
        lines.append("PASS")

    return lines
