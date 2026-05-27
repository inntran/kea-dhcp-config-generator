"""Kea 3.x JSON schema conformance validation.

Compiles the bundled ``_schema/kea-dhcp{4,6}.json`` validators once at import
time and exposes :func:`validate_dhcp4` / :func:`validate_dhcp6`, which raise
:class:`ConfigError` (collected by callers) on the first violation per call.

This module is the sole owner of ``_schema/`` access; no other module should
read those files directly.
"""

from __future__ import annotations

import json
from importlib.resources import files

import jsonschema_rs

from kea_dhcp_config_generator.validation.errors import ConfigError

_SCHEMA_DIR = files("kea_dhcp_config_generator") / "_schema"

SCHEMA_VERSION: str = (_SCHEMA_DIR / "VERSION").read_text(encoding="utf-8").strip()

_DHCP4_VALIDATOR: jsonschema_rs.Validator = jsonschema_rs.validator_for(
    json.loads((_SCHEMA_DIR / "kea-dhcp4.json").read_text(encoding="utf-8"))
)
_DHCP6_VALIDATOR: jsonschema_rs.Validator = jsonschema_rs.validator_for(
    json.loads((_SCHEMA_DIR / "kea-dhcp6.json").read_text(encoding="utf-8"))
)


def _path_to_str(instance_path: list[object]) -> str:
    """Render a jsonschema-rs instance_path as a dotted/indexed path string.

    Example: ['Dhcp4', 'subnet4', 0, 'pools'] → 'Dhcp4.subnet4[0].pools'.
    Returns 'Dhcp4' for empty paths (the root we always expect to exist).
    """
    if not instance_path:
        return "Dhcp4"
    parts: list[str] = []
    for segment in instance_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(("." if parts else "") + str(segment))
    return "".join(parts)


def _first_error_to_config_error(
    validator: jsonschema_rs.Validator, config_dict: dict
) -> ConfigError | None:
    """Run the validator and convert the first schema error to a ConfigError.

    Returns None on success. The first-error semantics here are intentional
    (see Story 4.4 AC #4); callers accumulate across protocols themselves.
    """
    for err in validator.iter_errors(config_dict):
        path_str = _path_to_str(list(err.instance_path))
        return ConfigError(
            message=f"schema violation at {path_str}: {err.message}",
            yaml_path=path_str,
            line=None,
            suggestion=None,
        )
    return None


def validate_dhcp4(config_dict: dict) -> None:
    """Validate a built Kea DHCPv4 dict against the bundled schema.

    Args:
        config_dict: The dict returned by ``builders.dhcp4.build(...)`` —
            shape ``{"Dhcp4": {...}}``. **Must NOT include the
            ``_kea-config-generator`` metadata header that ``writer.py``
            injects later.**

    Raises:
        ConfigError: A single error describing the first schema violation
            found, with ``line=None`` and ``suggestion=None`` (output
            validation has no YAML source line).
    """
    err = _first_error_to_config_error(_DHCP4_VALIDATOR, config_dict)
    if err is not None:
        raise err


def validate_dhcp6(config_dict: dict) -> None:
    """Validate a built Kea DHCPv6 dict against the bundled schema.

    The DHCPv6 schema is a permissive stub today (Epic 5 will replace it);
    any object passes. The function exists so the call site can be uniform
    across protocols once the DHCPv6 builder lands.
    """
    err = _first_error_to_config_error(_DHCP6_VALIDATOR, config_dict)
    if err is not None:
        raise err
