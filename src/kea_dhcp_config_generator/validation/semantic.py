"""Semantic validation: subnet overlap, pool bounds, duplicate reservation IPs.

This module is intentionally separate from validation/errors.py (types) and from
models/input.py (structural validation). It runs AFTER structural validation
succeeds, against a fully validated GlobalConfig.

Story 4.3 will add a sibling validate_classification() function for client-class
checks. Do not add classification logic here.
"""

from __future__ import annotations

import ipaddress

from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator.builders import pools as pool_utils
from kea_dhcp_config_generator.models.input import (
    GlobalConfig,
    SubnetV4Model,
    _extract_line,
)
from kea_dhcp_config_generator.validation.errors import SubnetConfigError


def validate_semantic(
    config: GlobalConfig,
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    """Run all semantic checks for Story 4.2. Returns a flat error list.

    Collect-all: never raises; concatenates results from each check.
    DHCPv6 is out of scope (Epic 5).
    """
    if config.dhcp4 is None:
        return []
    subnets = config.dhcp4.subnets
    errors: list[SubnetConfigError] = []
    errors.extend(_check_subnet_overlaps(subnets, raw))
    errors.extend(_check_pool_bounds(subnets, raw))
    errors.extend(_check_duplicate_reservation_ips(subnets, raw))
    return errors


def _check_subnet_overlaps(
    subnets: list[SubnetV4Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    errors: list[SubnetConfigError] = []
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network | None] = []
    for subnet in subnets:
        try:
            networks.append(ipaddress.ip_network(subnet.subnet, strict=False))
        except ValueError:
            networks.append(None)

    for i in range(len(subnets)):
        net_i = networks[i]
        if net_i is None:
            continue
        for j in range(i + 1, len(subnets)):
            net_j = networks[j]
            if net_j is None:
                continue
            if net_i.overlaps(net_j):
                errors.append(SubnetConfigError(
                    message=(
                        f"subnet {subnets[j].subnet} overlaps with "
                        f"earlier subnet {subnets[i].subnet}"
                    ),
                    yaml_path=f"dhcp4.subnets[{j}]",
                    line=_extract_line(raw, ("dhcp4", "subnets", j)),
                    suggestion=(
                        f"change {subnets[j].subnet} to a non-overlapping prefix"
                    ),
                ))
    return errors


def _check_pool_bounds(
    subnets: list[SubnetV4Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        try:
            network = ipaddress.ip_network(subnet.subnet, strict=False)
        except ValueError:
            continue
        first_usable = network.network_address + 1
        last_usable = network.broadcast_address - 1
        broadcast = network.broadcast_address

        for k, pool in enumerate(subnet.pools):
            if pool.range == "auto":
                continue
            range_path = ("dhcp4", "subnets", i, "pools", k, "range")
            range_yaml_path = f"dhcp4.subnets[{i}].pools[{k}].range"
            try:
                start_str, end_str = pool_utils.parse_pool_range(pool.range)
            except ValueError as exc:
                errors.append(SubnetConfigError(
                    message=f"invalid pool range: {exc}",
                    yaml_path=range_yaml_path,
                    line=_extract_line(raw, range_path),
                    suggestion=None,
                ))
                continue

            try:
                start = ipaddress.ip_address(start_str)
                end = ipaddress.ip_address(end_str)
            except ValueError as exc:
                errors.append(SubnetConfigError(
                    message=f"invalid pool range: {exc}",
                    yaml_path=range_yaml_path,
                    line=_extract_line(raw, range_path),
                    suggestion=None,
                ))
                continue

            if end == broadcast:
                errors.append(SubnetConfigError(
                    message=(
                        f"pool end {end} is the broadcast address "
                        f"of {subnet.subnet}"
                    ),
                    yaml_path=range_yaml_path,
                    line=_extract_line(raw, range_path),
                    suggestion=f"use {broadcast - 1} instead",
                ))
                continue

            if start < first_usable or end > last_usable or start > end:
                errors.append(SubnetConfigError(
                    message=(
                        f"pool range {start} - {end} is outside "
                        f"subnet {subnet.subnet}"
                    ),
                    yaml_path=range_yaml_path,
                    line=_extract_line(raw, range_path),
                    suggestion=(
                        f"valid range for {subnet.subnet} is "
                        f"{first_usable} - {last_usable}"
                    ),
                ))
    return errors


def _check_duplicate_reservation_ips(
    subnets: list[SubnetV4Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        seen: dict[str, int | None] = {}
        for k, res in enumerate(subnet.reservations):
            ip = res.ip_address
            if ip is None:
                continue
            line_k = _extract_line(
                raw, ("dhcp4", "subnets", i, "reservations", k)
            )
            if ip in seen:
                first_line = seen[ip]
                errors.append(SubnetConfigError(
                    message=(
                        f"duplicate reservation ip-address {ip} "
                        f"at lines {first_line} and {line_k}"
                    ),
                    yaml_path=f"dhcp4.subnets[{i}].reservations[{k}]",
                    line=line_k,
                    suggestion=(
                        "each host reservation in a subnet must have "
                        "a unique ip-address"
                    ),
                ))
            else:
                seen[ip] = line_k
    return errors
