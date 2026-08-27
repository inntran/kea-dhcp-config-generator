"""DHCPv4 JSON builder — assembles a Kea-ready Dhcp4 JSON dict.

Public API:
    build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict
        Returns {"Dhcp4": {...}} suitable for json.dumps(..., indent=2, ensure_ascii=False).

Key ordering follows Kea documentation examples (not alphabetical):
    Dhcp4 level:  valid-lifetime → renew-timer → rebind-timer → option-data
                  → client-classes → subnet4
    Subnet level: id → subnet → valid-lifetime → renew-timer → rebind-timer → client-classes
                  → option-data → pools → reservations
    Pool level:   pool → client-classes (only when set)
    Reservation:  hw-address → ip-address → [hostname] → [option-data]

Option fields on models (dns_servers, domain_name, ntp_servers, routers) are converted to
Kea option-data format at their respective scope; explicit option_data fields are merged
using merge-by-name semantics (most-specific wins).
"""

import ipaddress
from difflib import get_close_matches

from kea_dhcp_config_generator.builders.options import merge_option_data
from kea_dhcp_config_generator.builders.pools import allocate_next_block, parse_pool_range
from kea_dhcp_config_generator.fingerprints import DHCPFingerprint
from kea_dhcp_config_generator.models.input import (
    Dhcp4Config,
    GlobalConfig,
    HostReservationV4Model,
    OptionProfileModel,
    PoolV4Model,
    SubnetV4Model,
)
from kea_dhcp_config_generator.validation.errors import KeaConfigError
from kea_dhcp_config_generator.validation.semantic import _is_builtin_class


def build(config: GlobalConfig, fingerprint_library: DHCPFingerprint | None = None) -> dict:
    """Assemble a Kea-ready Dhcp4 JSON dict from a validated GlobalConfig.

    Args:
        config: Fully validated GlobalConfig (from models.input.parse()).
        fingerprint_library: Optional DHCPFingerprint instance. When provided,
            pool client-class names found in the library are resolved to
            top-level Kea client-classes entries (test or template-test).
            Never instantiated internally — always passed as a dependency.

    Returns:
        dict with structure {"Dhcp4": {...}}.
        Serialize with: json.dumps(result, indent=2, ensure_ascii=False)

    Raises:
        ValueError: if config.dhcp4 is None.
    """
    if config.dhcp4 is None:
        raise ValueError("GlobalConfig has no dhcp4 section; cannot build DHCPv4 config")

    dhcp4 = config.dhcp4
    dhcp4_dict: dict = {}

    # --- Timers (Kea-natural order) ---
    if dhcp4.valid_lifetime is not None:
        dhcp4_dict["valid-lifetime"] = dhcp4.valid_lifetime
    if dhcp4.renew_timer is not None:
        dhcp4_dict["renew-timer"] = dhcp4.renew_timer
    if dhcp4.rebind_timer is not None:
        dhcp4_dict["rebind-timer"] = dhcp4.rebind_timer

    # --- Control sockets (optional) ---
    if dhcp4.control_sockets:
        dhcp4_dict["control-sockets"] = [
            {
                "socket-type": cs.socket_type,
                **({"socket-name": cs.socket_name} if cs.socket_name else {}),
                **({"socket-address": cs.socket_address} if cs.socket_address else {}),
                **({"socket-port": cs.socket_port} if cs.socket_port is not None else {}),
            }
            for cs in dhcp4.control_sockets
        ]

    # --- Interfaces config (optional) ---
    if dhcp4.interfaces_config:
        interfaces_dict = {
            "interfaces": dhcp4.interfaces_config.interfaces,
        }
        if dhcp4.interfaces_config.dhcp_socket_type is not None:
            interfaces_dict["dhcp-socket-type"] = dhcp4.interfaces_config.dhcp_socket_type
        if dhcp4.interfaces_config.outbound_interface is not None:
            interfaces_dict["outbound-interface"] = dhcp4.interfaces_config.outbound_interface
        dhcp4_dict["interfaces-config"] = interfaces_dict

    # --- Lease database (optional) ---
    if dhcp4.lease_database:
        db_dict = {"type": dhcp4.lease_database.type}
        if dhcp4.lease_database.persist is not None:
            db_dict["persist"] = dhcp4.lease_database.persist
        if dhcp4.lease_database.name is not None:
            db_dict["name"] = dhcp4.lease_database.name
        if dhcp4.lease_database.host is not None:
            db_dict["host"] = dhcp4.lease_database.host
        if dhcp4.lease_database.port is not None:
            db_dict["port"] = dhcp4.lease_database.port
        if dhcp4.lease_database.user is not None:
            db_dict["user"] = dhcp4.lease_database.user
        if dhcp4.lease_database.password is not None:
            db_dict["password"] = dhcp4.lease_database.password
        dhcp4_dict["lease-database"] = db_dict

    # --- Global option-data (scalar fields merged with explicit option-data) ---
    global_option_data = _scope_option_data(dhcp4)
    if global_option_data:
        dhcp4_dict["option-data"] = global_option_data

    # --- Subnet list (client-classes immediately before subnet4, Kea-natural order) ---
    # --- Hooks libraries (optional) --- (must come before subnet4)
    if dhcp4.hooks_libraries:
        dhcp4_dict["hooks-libraries"] = [
            {
                "library": hl.library,
                **({"parameters": hl.parameters} if hl.parameters else {}),
            }
            for hl in dhcp4.hooks_libraries
        ]

    if dhcp4.subnets:
        client_classes = _collect_client_classes(dhcp4, fingerprint_library)
        # Names that will exist as top-level client-class definitions in the
        # output. A generated CatchAll may safely reference these (plus Kea
        # built-ins) via member(); an undefined reference is rejected in
        # _make_catchall_test.
        defined_classes = {c["name"] for c in client_classes}
        subnet4, catchall_classes = _build_subnet4(dhcp4, config.option_profiles, defined_classes)
        # Generated CatchAll classes use `not member(<device class>)`, so they must
        # be evaluated AFTER the device classes — append them last. They are emitted
        # even when no device class resolved to a library rule (e.g. all-custom
        # guards), so client-classes may exist solely because of CatchAll entries.
        all_classes = client_classes + catchall_classes
        if all_classes:
            dhcp4_dict["client-classes"] = all_classes
        dhcp4_dict["subnet4"] = subnet4

    return {"Dhcp4": dhcp4_dict}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _collect_client_classes(
    dhcp4: Dhcp4Config,
    fingerprint_library: DHCPFingerprint | None,
) -> list[dict]:
    """Build the top-level client-classes array for Kea Dhcp4 output.

    Scans all pools in YAML order (subnets top-to-bottom, pools top-to-bottom).
    For each pool.client_class:
      - Deduplicates by name (first occurrence wins; subsequent occurrences skipped)
      - Looks up the name in fingerprint_library
      - If found: emits {"name": ..., "test": ...} or {"name": ..., "template-test": ...}
      - If not found or no library: skips top-level entry
        (bare name still appears in pool's client-classes list — unchanged)

    Returns empty list if fingerprint_library is None or no pools have client_class.
    """
    if not fingerprint_library:
        return []

    seen: set[str] = set()
    entries: list[dict] = []

    for subnet in dhcp4.subnets:
        name = subnet.client_class
        if name and name not in seen:
            seen.add(name)
            rule = fingerprint_library.lookup(name)
            if rule is not None:
                entry: dict = {"name": name}
                if "test" in rule:
                    entry["test"] = rule["test"]
                else:
                    entry["template-test"] = rule["template-test"]
                entries.append(entry)
        for pool in subnet.pools or []:
            name = pool.client_class
            if not name or name in seen:
                continue
            seen.add(name)
            rule = fingerprint_library.lookup(name)
            if rule is None:
                continue  # custom class: pool still has it; no top-level entry
            entry: dict = {"name": name}
            if "test" in rule:
                entry["test"] = rule["test"]
            else:
                entry["template-test"] = rule["template-test"]
            entries.append(entry)

    return entries


def _scalar_to_option_data(model: Dhcp4Config | SubnetV4Model) -> list[dict]:
    """Convert scalar option fields to a list of Kea option-data entries.

    Scalar fields and their Kea option names:
        dns_servers  → domain-name-servers  (option 6)
        domain_name  → domain-name          (option 15)
        ntp_servers  → ntp-servers          (option 42)
        routers      → routers              (option 3)

    Multiple values are joined as a comma-separated string per Kea conventions.
    """
    entries: list[dict] = []
    if model.dns_servers:
        entries.append({"name": "domain-name-servers", "data": ", ".join(model.dns_servers)})
    if model.domain_name:
        entries.append({"name": "domain-name", "data": model.domain_name})
    if model.ntp_servers:
        entries.append({"name": "ntp-servers", "data": ", ".join(model.ntp_servers)})
    if model.routers:
        entries.append({"name": "routers", "data": ", ".join(model.routers)})
    return entries


def _scope_option_data(model: Dhcp4Config | SubnetV4Model) -> list[dict]:
    """Merge a model's scalar option fields with its explicit option-data list.

    Scalar-derived entries are the base; explicit option-data entries override
    by name (most-specific-wins via merge_option_data).
    """
    scalar = _scalar_to_option_data(model)
    return merge_option_data(scalar, model.option_data)


def _resolve_option_profile(
    name: str,
    profiles: dict[str, OptionProfileModel],
) -> OptionProfileModel:
    profile = profiles.get(name)
    if profile is not None:
        return profile
    suggestion = None
    if profiles:
        matches = get_close_matches(name, profiles.keys(), n=1)
        if matches:
            suggestion = f'did you mean "{matches[0]}"?'
    message = f'unknown option_profile "{name}"'
    if suggestion:
        message = f"{message} ({suggestion})"
    raise KeaConfigError(message)


def _resolve_pool_range(
    pool: PoolV4Model, subnet_cidr: str, cursor: ipaddress.IPv4Address | None
) -> tuple[str, ipaddress.IPv4Address | None]:
    """Return (pool range as 'start - end', next cursor for the following pool).

    Three pool forms:
      - explicit "x.x.x.x - y.y.y.y": parsed as-is; does not touch the cursor.
      - "auto": normally spans the subnet's full usable range (skip-start/
        skip-end applied), unchanged from prior behavior *unless* an earlier
        block-size/block-count pool in the same subnet already advanced the
        cursor — in that case "auto" starts from the cursor instead, so it
        naturally becomes "everything block pools didn't claim" (a trailing
        catch-all) rather than re-claiming the whole subnet and overlapping
        them. Still advances the cursor itself, in case a block pool follows.
      - block-size + block-count: claims the next block-aligned span of
        addresses starting at or after `cursor` (or the subnet's first usable
        address if this is the first block pool in the subnet), and returns
        the address just past the claimed span as the new cursor so a later
        pool in the same subnet continues from there.
    """
    network = ipaddress.ip_network(subnet_cidr, strict=False)
    has_reserved_endpoints = network.prefixlen <= 30
    last_usable = (
        network.broadcast_address - 1 if has_reserved_endpoints else network.broadcast_address
    )
    first_usable = (
        network.network_address + 1 if has_reserved_endpoints else network.network_address
    )

    if pool.range is not None:
        if pool.range == "auto":
            start_addr = cursor if cursor is not None else first_usable
            start_addr = start_addr + pool.skip_start
            end_addr = last_usable - pool.skip_end
            if start_addr > end_addr:
                raise ValueError(
                    f"auto pool range for {subnet_cidr!r} starting at {start_addr} "
                    f"(after prior block pools and skip-start={pool.skip_start}) with "
                    f"skip-end={pool.skip_end} produces an empty or inverted range "
                    f"({start_addr} > {end_addr}). Reduce skip values or block-count."
                )
            start, end = str(start_addr), str(end_addr)
            next_cursor = ipaddress.IPv4Address(int(end_addr) + 1)
        else:
            start, end = parse_pool_range(pool.range)
            next_cursor = cursor
        return f"{start} - {end}", next_cursor

    if cursor is None:
        cursor = first_usable

    assert pool.block_size is not None and pool.block_count is not None
    start, end, next_cursor = allocate_next_block(
        cursor, pool.block_size, pool.block_count, last_usable
    )
    return f"{start} - {end}", next_cursor


def _build_pool_entry(
    pool: PoolV4Model, subnet_cidr: str, cursor: ipaddress.IPv4Address | None
) -> tuple[dict, ipaddress.IPv4Address | None]:
    """Build a single Kea pool entry dict.

    Pool key order: pool → client-classes → option-data (Kea-natural, matches
    the bundled schema's poolEntry property order).
    client-class on a pool maps to "client-classes": [value] (list form, Kea 3.x).
    The deprecated singular "client-class" field is never emitted on pools.

    Returns (entry, next_cursor) — see _resolve_pool_range for cursor semantics.
    """
    pool_range, next_cursor = _resolve_pool_range(pool, subnet_cidr, cursor)
    entry: dict = {"pool": pool_range}
    if pool.client_class:
        entry["client-classes"] = [pool.client_class]
    option_data = merge_option_data([], pool.option_data)
    if option_data:
        entry["option-data"] = option_data
    return entry, next_cursor


def _build_reservation_entry(reservation: HostReservationV4Model) -> dict:
    """Build a single Kea host reservation dict.

    Key order (Kea-natural): hw-address → ip-address → hostname → option-data
    Omit hostname if None; omit option-data if empty.
    """
    entry: dict = {
        "hw-address": reservation.hw_address,
        "ip-address": reservation.ip_address,
    }
    if reservation.hostname is not None:
        entry["hostname"] = reservation.hostname
    option_data = merge_option_data([], reservation.option_data)
    if option_data:
        entry["option-data"] = option_data
    return entry


def _subnet_used_pool_classes(subnet: SubnetV4Model) -> list[str]:
    """Distinct pool-guard class names in a subnet, in first-encounter YAML order.

    Custom (non-library) names are included — they are still classes a catch-all
    must exclude. The subnet-level client-class selector is intentionally excluded;
    only pool guards participate in pool-level catch-all coverage.
    """
    seen: set[str] = set()
    used: list[str] = []
    for pool in subnet.pools or []:
        name = pool.client_class
        if name and name not in seen:
            seen.add(name)
            used.append(name)
    return used


def _make_catchall_test(used_classes: list[str], defined_classes: set[str]) -> str:
    """Build the CatchAll test expression: `not member('A') and not member('B')`.

    Mirrors the Kea KB catch-all pattern (understanding-client-classification.md):
    the synthesized class matches exactly the clients that belong to none of the
    subnet's guarded classes.

    `defined_classes` is the set of class names that will exist as top-level
    `client-classes` definitions in the output (the library-resolved guard names).
    A `member('X')` reference is only valid in Kea when X is a defined class or a
    Kea built-in (KNOWN/UNKNOWN/DROP/ALL/... — see `_is_builtin_class`); referencing
    an undefined class makes `kea-dhcp4 -t` reject the config. A guard that is
    neither is rejected here with a clear error rather than emitting an invalid
    config (NFR4).

    Class names are embedded inside single-quoted Kea expression string literals.
    Kea's eval lexer string rule is `'[^'\n]*'` with NO escape mechanism, so a
    name containing a single quote or newline cannot be represented and would
    produce an unparseable config. Reject such names with a clear error too.
    """
    for name in used_classes:
        if "'" in name or "\n" in name:
            raise KeaConfigError(
                f"client-class name {name!r} cannot be used in a generated catch-all "
                "expression: Kea class-expression string literals cannot contain a "
                "single quote or newline. Rename the class."
            )
        if name not in defined_classes and not _is_builtin_class(name):
            raise KeaConfigError(
                f"client-class {name!r} guards a pool but is not defined: a generated "
                "catch-all references it with member(), which Kea rejects for an "
                "undefined class. Add a fingerprint-library rule for it (or use a "
                "Kea built-in class)."
            )
    return " and ".join(f"not member('{name}')" for name in used_classes)


def _referenced_class_names(dhcp4: Dhcp4Config) -> set[str]:
    """Every client-class name the user references anywhere in the dhcp4 config.

    Used to keep generated CatchAll names from colliding with a user's own class
    (e.g. a custom class literally named `CatchAll_1`), which would otherwise
    rebind the user's pool selector to the synthesized `not member(...)` class.
    """
    names: set[str] = set()
    for subnet in dhcp4.subnets:
        if subnet.client_class:
            names.add(subnet.client_class)
        for pool in subnet.pools or []:
            if pool.client_class:
                names.add(pool.client_class)
    return names


def _catchall_name(assigned_id: int | str, referenced: set[str]) -> str:
    """Return the reserved CatchAll class name for a subnet: `CatchAll_<id>`.

    `CatchAll_<id>` is a reserved name the builder owns. The subnet id makes it
    unique across subnets (Kea's client-class namespace is global). If the user
    has manually defined a class with this exact reserved name, reject the config
    rather than silently rebinding their selector to the generated class.
    """
    name = f"CatchAll_{assigned_id}"
    if name in referenced:
        raise KeaConfigError(
            f"client-class name {name!r} is reserved: the tool generates a "
            f"catch-all class named {name!r} for subnet id {assigned_id}. "
            "Rename your class."
        )
    return name


def _build_subnet4(
    dhcp4: Dhcp4Config,
    option_profiles: dict[str, OptionProfileModel],
    defined_classes: set[str],
) -> tuple[list[dict], list[dict]]:
    """Build the subnet4 list and any generated per-subnet CatchAll classes.

    Subnet ID rules (all-or-none):
        - If ANY subnet has an explicit id, ALL subnets must have explicit IDs.
          Raises ValueError if the mix is detected (would cause collisions).
        - If NO subnet has an explicit id, IDs are auto-assigned sequentially
          from 1 in YAML order.

    CatchAll generation (DHCPv4): when a subnet has both class-restricted pools
    and at least one unguarded pool, a per-subnet `CatchAll_<id>` class is
    synthesized with test `not member(<each used class>)` and attached to the
    formerly-unguarded pool(s). This stops the catch-all pool from also serving
    clients that match a restricted class (Kea KB
    understanding-client-classification.md:188-235). The generated class dicts are
    returned for the caller to append AFTER the device classes (member() requires
    its referents to be evaluated first). `defined_classes` is the set of names
    that will exist as top-level client-class definitions (library-resolved guard
    names); a CatchAll guard outside this set and not a Kea built-in is rejected,
    since member() on an undefined class makes kea-dhcp4 -t fail.

    Subnet key order (Kea-natural):
        id → subnet → valid-lifetime → renew-timer → rebind-timer → client-classes
        → option-data → pools → reservations

    Returns:
        (subnet4_list, catchall_class_dicts)
    """
    # Validate all-or-none ID consistency.
    # If any subnet has an explicit id, all must — mixing is not allowed because
    # auto-assigned IDs starting from 1 would collide with user-assigned IDs.
    has_any_explicit = any(s.id is not None for s in dhcp4.subnets)
    if has_any_explicit:
        missing = [i + 1 for i, s in enumerate(dhcp4.subnets) if s.id is None]
        if missing:
            raise ValueError(
                f"Mixed subnet ID assignment: subnet(s) at position(s) {missing} "
                "lack an explicit 'id' while other subnets define one. "
                "Either assign explicit IDs to all subnets or to none."
            )

    subnets: list[dict] = []
    catchall_classes: list[dict] = []
    next_id = 1
    # Names the user references anywhere — used to reject a config that manually
    # uses a reserved `CatchAll_<id>` name (which the builder owns) rather than
    # silently rebinding the user's selector to the generated class.
    referenced_class_names = _referenced_class_names(dhcp4)

    for subnet in dhcp4.subnets:
        profile: OptionProfileModel | None = None
        if subnet.option_profile is not None:
            profile = _resolve_option_profile(subnet.option_profile, option_profiles)

        if subnet.id is not None:
            assigned_id = subnet.id
        else:
            assigned_id = next_id
            next_id += 1

        # Decide whether this subnet qualifies for a generated CatchAll: it must
        # have at least one guarded pool AND at least one unguarded pool. The
        # unguarded pool(s) then get guarded by the synthesized CatchAll class.
        used_classes = _subnet_used_pool_classes(subnet)
        has_unguarded_pool = any(not pool.client_class for pool in (subnet.pools or []))
        catchall_name: str | None = None
        if used_classes and has_unguarded_pool:
            catchall_name = _catchall_name(assigned_id, referenced_class_names)
            catchall_classes.append(
                {
                    "name": catchall_name,
                    "test": _make_catchall_test(used_classes, defined_classes),
                }
            )

        subnet_dict: dict = {
            "id": assigned_id,
            "subnet": subnet.subnet,
        }

        # Per-subnet timers (omit if not set)
        valid_lifetime = subnet.valid_lifetime
        renew_timer = subnet.renew_timer
        rebind_timer = subnet.rebind_timer
        if profile is not None:
            if valid_lifetime is None:
                valid_lifetime = profile.valid_lifetime
            if renew_timer is None:
                renew_timer = profile.renew_timer
            if rebind_timer is None:
                rebind_timer = profile.rebind_timer
        if valid_lifetime is not None:
            subnet_dict["valid-lifetime"] = valid_lifetime
        if renew_timer is not None:
            subnet_dict["renew-timer"] = renew_timer
        if rebind_timer is not None:
            subnet_dict["rebind-timer"] = rebind_timer

        # Subnet-level client-class selector. Kea 3.0 renamed the singular
        # "client-class" string to the list-form "client-classes"; the singular
        # form is deprecated (BaseNetworkParser::getClientClassesElem logs
        # DHCPSRV_CLIENT_CLASS_DEPRECATED and rejects setting both), so we emit
        # the list form only — consistent with pools and host reservations.
        if subnet.client_class:
            subnet_dict["client-classes"] = [subnet.client_class]

        # Subnet-specific option-data; option profiles contribute the base.
        subnet_option_data = _scope_option_data(subnet)
        if profile is not None:
            subnet_option_data = merge_option_data(
                _scalar_to_option_data(profile),
                subnet_option_data,
            )
        if subnet_option_data:
            subnet_dict["option-data"] = subnet_option_data

        # Pools. When a CatchAll class was synthesized for this subnet, attach it
        # to each unguarded pool so the catch-all range serves only unmatched
        # clients (Kea evaluates the generated `not member(...)` test).
        if subnet.pools:
            pool_entries = []
            cursor: ipaddress.IPv4Address | None = None
            for pool in subnet.pools:
                entry, cursor = _build_pool_entry(pool, subnet.subnet, cursor)
                if catchall_name is not None and not pool.client_class:
                    # Insert before option-data (if present) to preserve
                    # Kea-natural key order: pool → client-classes → option-data.
                    option_data = entry.pop("option-data", None)
                    entry["client-classes"] = [catchall_name]
                    if option_data is not None:
                        entry["option-data"] = option_data
                pool_entries.append(entry)
            subnet_dict["pools"] = pool_entries

        # Reservations (after pools, Kea-natural order)
        if subnet.reservations:
            subnet_dict["reservations"] = [_build_reservation_entry(r) for r in subnet.reservations]

        subnets.append(subnet_dict)

    return subnets, catchall_classes
