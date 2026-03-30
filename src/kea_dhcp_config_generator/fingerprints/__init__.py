"""Fingerprint rule catalog for Kea client classification.

DHCPFingerprint loads bundled YAML rule files and provides name-based lookup.

Story scope:
  3.1 (this story): rule files + lookup()
  3.2: version pinning check + fuzzy_match()
  3.3: builder integration (passed as dependency to builders.dhcp4.build())
"""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class DHCPFingerprint:
    """Catalog of named DHCP fingerprint rules for Kea client classification.

    Rules are loaded from fingerprints/rules/*.yaml in alphabetical filename order.
    Within each file, rules are loaded in YAML file order (preserving definition order
    for Kea class priority — first match wins when classes are evaluated).
    """

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self._load_rules()

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
