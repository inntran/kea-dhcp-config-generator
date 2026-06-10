"""Semantic validation: subnet/pool/reservation checks and client-class checks.

This module is intentionally separate from validation/errors.py (types) and from
models/input.py (structural validation). It runs AFTER structural validation
succeeds, against a fully validated GlobalConfig.

Story 4.3 added `validate_classification()`; subnet/pool/reservation checks live
in `validate_semantic()`.
"""

from __future__ import annotations

import ipaddress
from difflib import get_close_matches

from ruamel.yaml.comments import CommentedMap

from kea_dhcp_config_generator.builders import pools as pool_utils
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import (
    GlobalConfig,
    PoolV6NaModel,
    PoolV6PdModel,
    SubnetV4Model,
    SubnetV6Model,
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
    """Run all subnet/pool/reservation semantic checks. Returns a flat error list.

    Collect-all: never raises; concatenates results from each check across both
    the DHCPv4 (Story 4.2) and DHCPv6 (Story 5.3) stacks.
    """
    errors: list[SubnetConfigError] = []
    profile_names = set(config.option_profiles)
    if config.dhcp4 is not None:
        subnets = config.dhcp4.subnets
        errors.extend(_check_subnet_overlaps(subnets, raw))
        errors.extend(_check_pool_bounds(subnets, raw))
        errors.extend(_check_duplicate_reservation_ips(subnets, raw))
        errors.extend(_check_option_profile_refs(subnets, "dhcp4", profile_names, raw))
    if config.dhcp6 is not None:
        subnets6 = config.dhcp6.subnets
        errors.extend(_check_subnet_overlaps_v6(subnets6, raw))
        errors.extend(_check_pool_bounds_v6(subnets6, raw))
        errors.extend(_check_duplicate_reservation_duids_v6(subnets6, raw))
        errors.extend(_check_duplicate_reservation_ips_v6(subnets6, raw))
        errors.extend(_check_option_profile_refs(subnets6, "dhcp6", profile_names, raw))
    return errors


def _check_option_profile_refs(
    subnets: list[SubnetV4Model] | list[SubnetV6Model],
    stack: str,
    profile_names: set[str],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    """Verify every subnet's `option_profile` names a defined profile.

    Resolving this here (rather than only in the builder) means a typoed
    reference surfaces through the collect-all validate() path with a YAML line
    and a fuzzy suggestion, instead of raising a bare KeaConfigError at build
    time and bypassing --analysis-only / validate().
    """
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        name = subnet.option_profile
        if name is None or name in profile_names:
            continue
        matches = get_close_matches(name, profile_names, n=1)
        suggestion = (
            f'did you mean "{matches[0]}"?'
            if matches
            else "define it under top-level option_profiles"
        )
        errors.append(
            SubnetConfigError(
                message=f'unknown option_profile "{name}"',
                yaml_path=f"{stack}.subnets[{i}].option_profile",
                line=_extract_line(raw, (stack, "subnets", i, "option_profile")),
                suggestion=suggestion,
            )
        )
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
                errors.append(
                    SubnetConfigError(
                        message=(
                            f"subnet {subnets[j].subnet} overlaps with "
                            f"earlier subnet {subnets[i].subnet}"
                        ),
                        yaml_path=f"dhcp4.subnets[{j}]",
                        line=_extract_line(raw, ("dhcp4", "subnets", j)),
                        suggestion=(f"change {subnets[j].subnet} to a non-overlapping prefix"),
                    )
                )
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
                errors.append(
                    SubnetConfigError(
                        message=f"invalid pool range: {exc}",
                        yaml_path=range_yaml_path,
                        line=_extract_line(raw, range_path),
                        suggestion=None,
                    )
                )
                continue

            try:
                start = ipaddress.ip_address(start_str)
                end = ipaddress.ip_address(end_str)
            except ValueError as exc:
                errors.append(
                    SubnetConfigError(
                        message=f"invalid pool range: {exc}",
                        yaml_path=range_yaml_path,
                        line=_extract_line(raw, range_path),
                        suggestion=None,
                    )
                )
                continue

            if has_reserved_endpoints and end == broadcast:
                errors.append(
                    SubnetConfigError(
                        message=(f"pool end {end} is the broadcast address of {subnet.subnet}"),
                        yaml_path=range_yaml_path,
                        line=_extract_line(raw, range_path),
                        suggestion=f"use {broadcast - 1} instead",
                    )
                )
                continue

            if start < first_usable or end > last_usable or start > end:
                errors.append(
                    SubnetConfigError(
                        message=(f"pool range {start} - {end} is outside subnet {subnet.subnet}"),
                        yaml_path=range_yaml_path,
                        line=_extract_line(raw, range_path),
                        suggestion=(
                            f"valid range for {subnet.subnet} is {first_usable} - {last_usable}"
                        ),
                    )
                )
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
            line_k = _extract_line(raw, ("dhcp4", "subnets", i, "reservations", k))
            if ip in seen:
                first_line = seen[ip]
                errors.append(
                    SubnetConfigError(
                        message=(
                            f"duplicate reservation ip-address {ip} "
                            f"at lines {first_line} and {line_k}"
                        ),
                        yaml_path=f"dhcp4.subnets[{i}].reservations[{k}]",
                        line=line_k,
                        suggestion=(
                            "each host reservation in a subnet must have a unique ip-address"
                        ),
                    )
                )
            else:
                seen[ip] = line_k
    return errors


# ---------------------------------------------------------------------------
# DHCPv6 semantic checks (Story 5.3)
# ---------------------------------------------------------------------------


def _check_subnet_overlaps_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    """Detect overlapping IPv6 prefixes among dhcp6 subnets.

    subnet.subnet is already an IPv6Network (parsed in models.input), so no
    string parsing is needed here.
    """
    errors: list[SubnetConfigError] = []
    for i in range(len(subnets)):
        net_i = subnets[i].subnet
        for j in range(i + 1, len(subnets)):
            net_j = subnets[j].subnet
            if net_i.overlaps(net_j):
                errors.append(
                    SubnetConfigError(
                        message=(f"subnet {net_j} overlaps with earlier subnet {net_i}"),
                        yaml_path=f"dhcp6.subnets[{j}]",
                        line=_extract_line(raw, ("dhcp6", "subnets", j)),
                        suggestion=f"change {net_j} to a non-overlapping prefix",
                    )
                )
    return errors


def _check_pool_bounds_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    """Verify each NA pool's explicit range falls within its parent IPv6 prefix.

    PD pools are not range-checked here (they delegate prefixes, not addresses);
    auto ranges are computed from the prefix and are in-bounds by construction.
    """
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        network = subnet.subnet
        first = network.network_address
        last = network.broadcast_address  # all-ones host address of the prefix
        for k, pool in enumerate(subnet.pools):
            if isinstance(pool, PoolV6NaModel):
                if pool.range == "auto":
                    continue
                range_path = ("dhcp6", "subnets", i, "pools", k, "range")
                range_yaml_path = f"dhcp6.subnets[{i}].pools[{k}].range"
                try:
                    start_str, end_str = pool_utils.parse_pool_range(pool.range)
                    start = ipaddress.IPv6Address(start_str)
                    end = ipaddress.IPv6Address(end_str)
                except ValueError as exc:
                    errors.append(
                        SubnetConfigError(
                            message=f"invalid pool range: {exc}",
                            yaml_path=range_yaml_path,
                            line=_extract_line(raw, range_path),
                            suggestion=None,
                        )
                    )
                    continue

                if start < first or end > last or start > end:
                    # Suggest the conventional usable window (excluding the subnet-router
                    # anycast address and the all-ones host), but never step outside the
                    # prefix — for tiny prefixes like ::/128 first+1/last-1 would under/
                    # overflow the address space, so clamp to the prefix bounds.
                    lo = first + 1 if first < last else first
                    hi = last - 1 if last > first else last
                    errors.append(
                        SubnetConfigError(
                            message=(f"pool range {start} - {end} is outside subnet {network}"),
                            yaml_path=range_yaml_path,
                            line=_extract_line(raw, range_path),
                            suggestion=f"valid range for {network} is {lo} - {hi}",
                        )
                    )
                continue

            if isinstance(pool, PoolV6PdModel):
                pool_path = ("dhcp6", "subnets", i, "pools", k)
                pool_yaml_path = f"dhcp6.subnets[{i}].pools[{k}]"
                prefix_line = _extract_line(raw, pool_path)

                # NOTE: Kea deliberately does NOT require a PD pool's prefix to be
                # contained within the parent subnet — "the IPv6 prefixes delegated
                # for the particular subnet don't need to match the prefix of the
                # subnet" (Kea src/lib/dhcpsrv/subnet.cc Subnet::addPool, which skips
                # inRange() for Lease::TYPE_PD). So we validate only that the prefix
                # parses and that delegated-len is a sane delegation of prefix-len;
                # we must NOT reject an out-of-subnet PD prefix or we would flag
                # valid Kea configs.
                try:
                    ipaddress.IPv6Network(
                        f"{pool.prefix}/{pool.prefix_len}",
                        strict=False,
                    )
                except ValueError as exc:
                    errors.append(
                        SubnetConfigError(
                            message=f"invalid pd-pool prefix: {exc}",
                            yaml_path=f"{pool_yaml_path}.prefix",
                            line=_extract_line(raw, (*pool_path, "prefix")),
                            suggestion=None,
                        )
                    )

                if pool.delegated_len < pool.prefix_len or pool.delegated_len > 128:
                    errors.append(
                        SubnetConfigError(
                            message=(
                                f"delegated-len {pool.delegated_len} must be between "
                                f"prefix-len {pool.prefix_len} and 128"
                            ),
                            yaml_path=pool_yaml_path,
                            line=prefix_line,
                            suggestion=None,
                        )
                    )
    return errors


def _check_duplicate_reservation_duids_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    """Detect duplicate DUIDs among reservations within the same dhcp6 subnet."""
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        seen: dict[str, int | None] = {}
        for k, res in enumerate(subnet.reservations):
            duid = res.duid
            line_k = _extract_line(raw, ("dhcp6", "subnets", i, "reservations", k))
            if duid in seen:
                first_line = seen[duid]
                errors.append(
                    SubnetConfigError(
                        message=(
                            f"duplicate reservation duid {duid} at lines {first_line} and {line_k}"
                        ),
                        yaml_path=f"dhcp6.subnets[{i}].reservations[{k}]",
                        line=line_k,
                        suggestion=("each host reservation in a subnet must have a unique duid"),
                    )
                )
            else:
                seen[duid] = line_k
    return errors


def _check_duplicate_reservation_ips_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
) -> list[SubnetConfigError]:
    errors: list[SubnetConfigError] = []
    for i, subnet in enumerate(subnets):
        seen: dict[str, int | None] = {}
        for k, res in enumerate(subnet.reservations):
            if res.ip_address is None:
                continue
            # Canonicalize so the same address written in different notations
            # (e.g. "2001:db8::1" vs "2001:db8:0:0:0:0:0:1") is treated as a
            # duplicate. A malformed address is left to the schema layer; skip it
            # here rather than raise, to keep this check focused on duplicates.
            try:
                ip = str(ipaddress.IPv6Address(res.ip_address))
            except ValueError:
                continue
            line_k = _extract_line(raw, ("dhcp6", "subnets", i, "reservations", k))
            if ip in seen:
                first_line = seen[ip]
                errors.append(
                    SubnetConfigError(
                        message=(
                            f"duplicate reservation ip-address {ip} "
                            f"at lines {first_line} and {line_k}"
                        ),
                        yaml_path=f"dhcp6.subnets[{i}].reservations[{k}]",
                        line=line_k,
                        suggestion=(
                            "each host reservation in a subnet must have a unique ip-address"
                        ),
                    )
                )
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
    skipped (catch-all check still runs). Covers both DHCPv4 (Story 4.3) and
    DHCPv6 (Story 5.3) pool/subnet client-class references.
    """
    errors: list[FingerprintError] = []
    warnings: list[ConfigWarning] = []
    if config.dhcp4 is not None:
        subnets = config.dhcp4.subnets
        errors.extend(_check_unknown_class_names(subnets, raw, library))
        warnings.extend(_check_no_catch_all_subnets(subnets, raw))
    if config.dhcp6 is not None:
        errors.extend(_check_unknown_class_names_v6(config.dhcp6.subnets, raw, library))
        warnings.extend(_check_no_catch_all_subnets_v6(config.dhcp6.subnets, raw))
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


def _check_unknown_class_names_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
    library: DHCPFingerprint | None,
) -> list[FingerprintError]:
    """Validate pool client-class references on dhcp6 subnets.

    DHCPv6 subnets carry no subnet-level client-class selector; only NA and PD
    pools reference fingerprint rules.
    """
    if library is None:
        return []
    errors: list[FingerprintError] = []
    for i, subnet in enumerate(subnets):
        for k, pool in enumerate(subnet.pools):
            if not pool.client_class:
                continue
            err = _classify_name(
                pool.client_class,
                library,
                yaml_path=f"dhcp6.subnets[{i}].pools[{k}].client-class",
                line_loc=("dhcp6", "subnets", i, "pools", k, "client-class"),
                raw=raw,
            )
            if err is not None:
                errors.append(err)
    return errors


def _check_no_catch_all_subnets_v6(
    subnets: list[SubnetV6Model],
    raw: CommentedMap,
) -> list[ConfigWarning]:
    warnings: list[ConfigWarning] = []
    for i, subnet in enumerate(subnets):
        if not subnet.pools:
            continue
        if any(not pool.client_class for pool in subnet.pools):
            continue
        warnings.append(
            ConfigWarning(
                message=(
                    f"subnet {subnet.subnet} has no catch-all pool: "
                    f"every pool is restricted by client-class"
                ),
                yaml_path=f"dhcp6.subnets[{i}]",
                line=_extract_line(raw, ("dhcp6", "subnets", i)),
                suggestion=(
                    "add a pool with no client-class to act as a catch-all; "
                    "clients matching none of the defined classes will receive "
                    "no address"
                ),
            )
        )
    return warnings


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
        warnings.append(
            ConfigWarning(
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
            )
        )
    return warnings
