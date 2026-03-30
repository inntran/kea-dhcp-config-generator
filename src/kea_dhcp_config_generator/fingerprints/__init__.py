"""Fingerprint rule catalog for Kea client classification.

DHCPFingerprint loads bundled YAML rule files and provides name-based lookup,
fuzzy matching for typo correction, and version pinning validation.

Story scope:
  3.1: rule files + lookup()
  3.2 (this story): version() + fuzzy_match() + version pinning check in __init__()
  3.3: builder integration (passed as dependency to builders.dhcp4.build())
"""

import difflib
import importlib.metadata
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from kea_dhcp_config_generator.validation.errors import ConfigWarning


class DHCPFingerprint:
    """Catalog of named DHCP fingerprint rules for Kea client classification.

    Rules are loaded from fingerprints/rules/*.yaml in alphabetical filename order.
    Within each file, rules are loaded in YAML file order (preserving definition order
    for Kea class priority — first match wins when classes are evaluated).
    """

    def __init__(self, pinned_version: str | None = None) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self.warnings: list[ConfigWarning] = []
        self._load_rules()
        if pinned_version is not None:
            self._check_version(pinned_version)

    def _load_rules(self) -> None:
        """Load all *.yaml rule files from fingerprints/rules/ in alphabetical order."""
        rules_dir = Path(__file__).parent / "rules"
        yaml = YAML(typ="safe")
        for rule_file in sorted(rules_dir.glob("*.yaml")):
            with rule_file.open(encoding="utf-8") as f:
                rules = yaml.load(f)
            if rules:
                for rule in rules:
                    self._rules[rule["name"]] = rule

    def lookup(self, name: str) -> dict[str, Any] | None:
        """Look up a rule by name.

        Returns the rule dict if found, None otherwise (never raises).
        """
        return self._rules.get(name)

    def version(self) -> str:
        """Return the installed package version (used as fingerprint library version)."""
        return importlib.metadata.version("kea-dhcp-config-generator")

    def _check_version(self, pinned_version: str) -> None:
        """Compare pinned_version against installed; append ConfigWarning if different."""
        installed = self.version()
        if pinned_version != installed:
            self.warnings.append(
                ConfigWarning(
                    message=(
                        f"Fingerprint library version mismatch: "
                        f"pinned={pinned_version!r}, installed={installed!r}"
                    ),
                    yaml_path="fingerprint_library_version",
                    line=None,
                    suggestion=(
                        f"Update fingerprint_library_version to {installed!r} "
                        f"or pin to the version you validated against"
                    ),
                )
            )

    def fuzzy_match(self, name: str, *, n: int = 5, cutoff: float = 0.6) -> list[str]:
        """Return up to n rule names that are close matches to name.

        Uses difflib.get_close_matches (Ratcliff/Obershelp algorithm).
        Returns empty list if no close matches found (never raises).

        Args:
            name: The rule name to look up (potentially misspelled).
            n: Maximum number of suggestions to return (default 5).
            cutoff: Minimum similarity ratio 0–1 (default 0.6).
        """
        return difflib.get_close_matches(name, self._rules.keys(), n=n, cutoff=cutoff)
