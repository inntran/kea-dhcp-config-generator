# Bundled Kea JSON Schemas

These files are consumed at runtime by `validation/output_schema.py` to validate
the dict assembled by `builders/dhcp4.py` (and, later, `builders/dhcp6.py`)
before `writer.py` writes anything to disk.

## Files

- **`VERSION`** — pinned Kea version string. One line, trailing newline.
  Read at import time as `output_schema.SCHEMA_VERSION`.
- **`kea-dhcp4.json`** — JSON Schema (Draft 2020-12). Covers the subset of Kea
  DHCPv4 keys that `builders/dhcp4.py` currently emits. **Hand-authored as of
  Story 4.4** — Kea 3.x does not publish a single canonical JSON Schema file.
  Tight types on known keys; `additionalProperties: true` on objects so that
  forward-compatible Kea key additions do not falsely reject valid configs.
- **`kea-dhcp6.json`** — stub. DHCPv6 builder lands in Epic 5 (Story 5.2);
  this file exists so `importlib.resources` access succeeds today.

## Pinned version

`3.0.3` — selected per `_bmad-output/planning-artifacts/architecture.md`
("bundled Kea 3.x JSON schema") and PRD risk notes around Kea 3.x evolution.
Update both `VERSION` and the corresponding schema files together.

## Extending the schema

When `builders/dhcp4.py` learns to emit a new Kea key:

1. Add the key to the appropriate `$defs` entry in `kea-dhcp4.json`.
2. Add a test mutation in `tests/unit/test_validation_output_schema.py` that
   sets a wrong type for the new key and asserts `ConfigError`.
3. Bump `VERSION` only when re-targeting a different Kea stable release.

## Future automation (Story 8.3)

`scripts/update_kea_schema.py` will eventually fetch upstream Kea schema
material from a stable release and regenerate these files. Note: Kea does not
ship a standalone JSON Schema file in its release tarball; Story 8.3 will need
its own sourcing strategy (derive from YANG, hand-curate, or community source).
