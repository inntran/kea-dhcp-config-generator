"""Option inheritance resolution for DHCPv4 and DHCPv6 builders.

Resolves DHCP option inheritance across scopes using most-specific-wins semantics:
    host_reservation > pool > subnet > shared_network > global

Two public functions:
    resolve_options(*layers) — merges scalar/dict fields; last layer wins
    merge_option_data(base, override) — merges option-data lists by name key;
        override entries replace base entries with the same name (not appended)
"""

import copy

from deepmerge import Merger

# Custom merger: dicts are merged recursively; lists and all other types are
# replaced (last/most-specific layer wins entirely). This implements the
# "most-specific-wins" semantics required for DHCP option inheritance where a
# subnet's dns-servers list should fully replace the global list, not extend it.
_option_merger = Merger(
    [(list, "override"), (dict, "merge"), (set, "override")],
    ["override"],  # fallback for other types
    ["override"],  # type conflict resolution
)


def resolve_options(*layers: dict) -> dict:
    """Merge option layers left-to-right; rightmost (most-specific) wins.

    Uses a replace-list strategy so that:
      - Scalar values: last layer wins
      - Dict values: recursively merged, last-layer keys win
      - List values: last layer wins entirely (replace, not append)

    NOTE: option-data lists require merge-by-name semantics; use
    merge_option_data() for those rather than including them in layers here.

    Args:
        *layers: Option dicts from least-specific to most-specific scope.

    Returns:
        Merged dict with most-specific values taking precedence.
    """
    result: dict = {}
    for layer in layers:
        _option_merger.merge(result, copy.deepcopy(layer))
    return result


def merge_option_data(base: list[dict], override: list[dict]) -> list[dict]:
    """Merge two option-data lists using replace-by-name semantics.

    Kea option-data entries are identified by their 'name' key. When the same
    option name appears at both scopes, the more-specific (override) entry
    replaces the less-specific (base) entry — it does NOT append a duplicate.

    Limitation: identity is keyed on 'name' only. Kea also supports entries
    identified by (code, space) instead of name; such entries must carry a
    'name' key when passed to this function, or a ValueError is raised.
    Multi-space configurations sharing the same name across different option
    spaces are not supported by this merger.

    Args:
        base: option-data list from the less-specific scope (e.g. global).
        override: option-data list from the more-specific scope (e.g. subnet).

    Returns:
        Merged list preserving base order; override entries either replace
        matching base entries or are appended if their name is new.

    Raises:
        ValueError: if any entry in base or override is missing the 'name' key.
    """
    merged: dict[str, dict] = {}
    for entry in base:
        if "name" not in entry:
            raise ValueError(
                f"option-data entry missing required 'name' key: {entry!r}"
            )
        merged[entry["name"]] = entry
    for entry in override:
        if "name" not in entry:
            raise ValueError(
                f"option-data entry missing required 'name' key: {entry!r}"
            )
        merged[entry["name"]] = entry
    return list(merged.values())
