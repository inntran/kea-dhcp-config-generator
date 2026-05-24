"""Semantic validation: subnet/pool/reservation checks and client-class checks.

This module is intentionally separate from validation/errors.py (types) and from
models/input.py (structural validation). It runs AFTER structural validation
succeeds, against a fully validated GlobalConfig.

Story 4.3 added `validate_classification()`; subnet/pool/reservation checks live
in `validate_semantic()`.
"""

from __future__ import annotations

import ipaddress

from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator.builders import pools as pool_utils
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import (
    GlobalConfig,
    SubnetV4Model,
    _extract_line,
)
from kea_dhcp_config_generator.validation.errors import (
    ConfigWarning,
    FingerprintError,
    SubnetConfigError,
)

KEA_BUILTIN_CLASS_NAMES: frozenset[str] = frozenset(
    {"ALL", "KNOWN", "UNKNOWN", "DROP", "SKIP_DDNS"}
)
KEA_BUILTIN_CLASS_PREFIXES: tuple[str, ...] = (
    "VENDOR_CLASS_",
    "HA_",
    "AFTER_",
    "EXTERNAL_",
)


def _is_builtin_class(name: str) -> bool:
    return name in KEA_BUILTIN_CLASS_NAMES or name.startswith(KEA_BUILTIN_CLASS_PREFIXES)


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
        has_reserved_endpoints = network.prefixlen <= 30
        if has_reserved_endpoints:
            first_usable = network.network_address + 1
            last_usable = network.broadcast_address - 1
        else:
            # RFC 3021 (/31) and /32: endpoint addresses are usable.
            first_usable = network.network_address
            last_usable = network.broadcast_address
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

            if has_reserved_endpoints and end == broadcast:
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


def validate_classification(
    config: GlobalConfig,
    raw: CommentedMap,
    library: DHCPFingerprint | None,
) -> tuple[list[FingerprintError], list[ConfigWarning]]:
    """Run client-class semantic checks. Returns (errors, warnings).

    Collect-all: never raises. If library is None, unknown-class checks are
    skipped (catch-all check still runs). DHCPv6 is out of scope (Epic 5).
    """
    if config.dhcp4 is None:
        return [], []
    subnets = config.dhcp4.subnets
    errors = _check_unknown_class_names(subnets, raw, library)
    warnings = _check_no_catch_all_subnets(subnets, raw)
    return errors, warnings


def _check_unknown_class_names(
    subnets: list[SubnetV4Model],
    raw: CommentedMap,
    library: DHCPFingerprint | None,
) -> list[FingerprintError]:
    if library is None:
        return []
    errors: list[FingerprintError] = []
    for i, subnet in enumerate(subnets):
        if subnet.client_class:
            err = _classify_name(
                subnet.client_class,
                library,
                yaml_path=f"dhcp4.subnets[{i}].client-class",
                line_loc=("dhcp4", "subnets", i, "client-class"),
                raw=raw,
            )
            if err is not None:
                errors.append(err)
        for k, pool in enumerate(subnet.pools):
            if not pool.client_class:
                continue
            err = _classify_name(
                pool.client_class,
                library,
                yaml_path=f"dhcp4.subnets[{i}].pools[{k}].client-class",
                line_loc=("dhcp4", "subnets", i, "pools", k, "client-class"),
                raw=raw,
            )
            if err is not None:
                errors.append(err)
    return errors


def _classify_name(
    name: str,
    library: DHCPFingerprint,
    *,
    yaml_path: str,
    line_loc: tuple,
    raw: CommentedMap,
) -> FingerprintError | None:
    if _is_builtin_class(name):
        return None
    if library.lookup(name) is not None:
        return None
    matches = library.fuzzy_match(name, n=1)
    suggestion = f'did you mean "{matches[0]}"?' if matches else None
    return FingerprintError(
        message=f'unknown client-class "{name}"',
        yaml_path=yaml_path,
        line=_extract_line(raw, line_loc),
        suggestion=suggestion,
    )


def _check_no_catch_all_subnets(
    subnets: list[SubnetV4Model],
    raw: CommentedMap,
) -> list[ConfigWarning]:
    warnings: list[ConfigWarning] = []
    for i, subnet in enumerate(subnets):
        if not subnet.pools:
            continue
        if any(not pool.client_class for pool in subnet.pools):
            continue
        warnings.append(ConfigWarning(
            message=(
                f"subnet {subnet.subnet} has no catch-all pool: "
                f"every pool is restricted by client-class"
            ),
            yaml_path=f"dhcp4.subnets[{i}]",
            line=_extract_line(raw, ("dhcp4", "subnets", i)),
            suggestion=(
                "add a pool with no client-class to act as a catch-all; "
                "clients matching none of the defined classes will receive "
                "no address"
            ),
        ))
    return warnings
