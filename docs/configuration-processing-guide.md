# Configuration Processing Guide

This guide explains how `kea-dhcp-config-generator` turns a declarative YAML
service definition into Kea DHCPv4 and DHCPv6 JSON. It is based on the current
loader, input models, builders, validators, bundled samples, and automated test
suite.

Use it when writing a new configuration, choosing a sample to copy, diagnosing
a validation failure, or integrating the generator into CI.

## Quickest safe workflow

Start with the smallest sample that resembles the target deployment, edit it,
inspect the analysis report, and generate stable filenames only after validation
succeeds.

```bash
# Install the project and development dependencies.
uv sync

# Inspect the interpreted configuration without writing JSON.
uv run kea-confgen \
  --config samples/office-dhcp4.yaml \
  --analysis-only

# Treat warnings as failures and write canonical filenames to ./generated/.
uv run kea-confgen \
  --config samples/office-dhcp4.yaml \
  --output generated \
  --overwrite \
  --strict \
  --analysis

# Finally, ask the installed Kea daemon to check the generated configuration.
kea-dhcp4 -t generated/kea-dhcp4.conf
```

With `--overwrite`, output names are `kea-dhcp4.conf`, `kea-dhcp6.conf`, and,
when requested, `kea-analysis.txt`. Without it, filenames contain a timestamp.
Generated paths go to stdout; diagnostics go to stderr.

## What happens to a configuration

```text
YAML file
  -> load YAML 1.2 and retain source line metadata
  -> parse into typed input models
  -> run subnet, pool, reservation, profile, and class checks
  -> resolve profiles, options, pool ranges, IDs, and fingerprints
  -> build Dhcp4 and/or Dhcp6 dictionaries
  -> validate each dictionary against the bundled Kea schema
  -> write formatted JSON with a generator metadata header
```

The stages have deliberately different failure behavior:

| Stage | Examples | Result |
| --- | --- | --- |
| Load | missing file, malformed YAML, empty document, list at the root | Fatal error; CLI exit `2` |
| Structural validation | invalid duration, missing pool discriminator, bad socket type | All Pydantic findings are collected; CLI exit `1` |
| Semantic validation | overlapping subnets, out-of-bounds pool, unknown profile or fingerprint | Findings are collected across both stacks; CLI exit `1` |
| Warning checks | all pools in a subnet are class-restricted | Generation continues, or exits `1` under `--strict` |
| Output schema validation | generated value violates the bundled Kea schema | No output file is written; CLI exit `1` |
| Write | output directory cannot be created or file cannot be written | Fatal error; CLI exit `2` |

Most validation diagnostics include the YAML path, original line number, and a
suggestion. Structural validation must succeed before semantic checks can run.

## Choosing a bundled sample

| Sample | Best starting point for |
| --- | --- |
| `samples/minimal-dhcp4.yaml` | One IPv4 subnet with an automatically sized pool |
| `samples/office-dhcp4.yaml` | Timers, common options, a reservation, and fingerprint classification |
| `samples/dual-stack.yaml` | A single input producing both DHCPv4 and DHCPv6 output |
| `samples/large-subnet-classified.yaml` | Sequential block allocation inside a large IPv4 subnet |
| `samples/enterprise-classified.yaml` | Multiple VLANs, option profiles, many client classes, and reservation-only subnets |
| `samples/pxe-multiboot.yaml` | Per-class PXE boot options and architecture-specific pools |
| `samples/pxe-dual-stack-ha.yaml` | Dual stack, PXE, HA hooks, PostgreSQL, sockets, and interfaces together |
| `samples/control-api.yaml` | HTTP control socket configuration |
| `samples/production-ha.yaml` | Kea HA hook parameters and interface binding |
| `samples/persistent-db.yaml` | PostgreSQL lease storage for both protocols |

The integration fixtures under `tests/integration/fixtures/valid/` are smaller
contract examples. Their matching files under `expected/` show the exact JSON
payload expected by the test suite.

## Configuration shape

At least one of `dhcp4` or `dhcp6` is required. The two stacks may coexist in a
single document and are built independently.

```yaml
fingerprint_library_version: "0.1.0"  # optional compatibility pin

option_profiles:                       # optional reusable subnet defaults
  office:
    valid-lifetime: 8h
    dns-servers: ["10.0.0.10"]

dhcp4:
  valid-lifetime: 12h
  subnets: []

dhcp6:
  valid-lifetime: 12h
  preferred-lifetime: 8h
  subnets: []
```

Durations accept a non-negative integer number of seconds or a string with one
of `d`, `h`, `m`, or `s`, such as `1d`, `8h`, or `30m`. Compound forms such as
`1h30m` are not accepted; use `5400` instead. User-facing string fields are
ASCII-only.

### Option inheritance

Values are resolved from least specific to most specific:

```text
protocol global -> option profile -> subnet -> pool/reservation
```

- A subnet references a profile with `option-profile`; profiles are defined in
  the top-level `option_profiles` mapping.
- Inline subnet values override values from its profile.
- Lists replace the less-specific list; they are not concatenated.
- Explicit `option-data` entries replace entries with the same `name` while
  retaining unrelated inherited entries.
- `option-data` identity is currently the `name` only. Entries without `name`,
  or duplicate names intended for different Kea option spaces, are unsupported.
- Pool and reservation `option-data` are emitted at those Kea scopes. They are
  not flattened into the subnet's `option-data` list.

DHCPv4 convenience fields map as follows:

| YAML field | Kea option name |
| --- | --- |
| `dns-servers` | `domain-name-servers` |
| `domain-name` | `domain-name` |
| `ntp-servers` | `ntp-servers` |
| `routers` | `routers` |

For DHCPv6, `dns-servers` maps to the DHCPv6 `dns-servers` option. The DHCPv6
models do not expose the DHCPv4-only `routers`, `domain-name`, or `ntp-servers`
convenience fields.

### DHCPv4 subnets, pools, and reservations

```yaml
dhcp4:
  valid-lifetime: 12h
  dns-servers: ["10.10.0.10", "10.10.0.11"]
  routers: ["10.10.20.1"]
  subnets:
    - subnet: 10.10.20.0/24
      pools:
        - range: "10.10.20.20 - 10.10.20.99"
          client-class: Windows_10_11
        - range: auto
          skip-start: 9
          skip-end: 4
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: "10.10.20.10"
          hostname: printer-01
```

An IPv4 pool uses exactly one of these forms:

- `range: "start - end"` for an explicit inclusive range. The parser requires
  the space-dash-space separator.
- `range: auto` to use the subnet's available range, adjusted by `skip-start`
  and `skip-end`.
- `block-size` plus `block-count` to claim consecutive prefix-sized blocks.

For ordinary prefixes up to `/30`, automatic allocation excludes the network
and broadcast addresses. Explicit `/31` and `/32` endpoint ranges are allowed.
`range: auto` cannot produce a usable range for `/31` or `/32`.

Block pools are cursor-based and processed in YAML order. Each block begins on
the next requested prefix boundary. A later `range: auto` begins after preceding
block pools, making it useful as a trailing catch-all. Explicit ranges do not
advance that cursor, so do not mix explicit and calculated ranges without
checking for overlap yourself.

Subnet IDs are either explicit for every subnet in a protocol stack or omitted
for every subnet. When omitted, the builder assigns IDs `1`, `2`, ... in YAML
order. DHCPv4 and DHCPv6 have independent ID sequences.

### DHCPv6 address and delegated-prefix pools

Each DHCPv6 pool requires a `pool-type` discriminator:

```yaml
dhcp6:
  valid-lifetime: 12h
  preferred-lifetime: 8h
  dns-servers: ["2001:db8::53"]
  subnets:
    - subnet: "2001:db8:20::/64"
      pools:
        - pool-type: na
          range: "2001:db8:20::100 - 2001:db8:20::ffff"
        - pool-type: pd
          prefix: "2001:db8:1000::"
          prefix-len: 48
          delegated-len: 56
      reservations:
        - duid: "00:03:00:01:aa:bb:cc:dd:ee:ff"
          ip-address: "2001:db8:20::10"
          hostname: nas-01
```

- `pool-type: na` emits a Kea address pool. Its range may be explicit or
  `auto`; explicit ranges must lie inside the parent subnet.
- `pool-type: pd` emits a Kea `pd-pools` entry. `delegated-len` must be between
  `prefix-len` and `128`.
- Kea allows a PD prefix outside the parent subnet prefix, and the semantic
  validator intentionally permits it.
- DHCPv6 reservations use DUIDs. The output uses Kea's `ip-addresses` list even
  though the input convenience field is singular `ip-address`.
- NA and PD entries may both appear in the input `pools` list; the builder
  separates them into Kea's `pools` and `pd-pools` arrays.

### Client classification

A pool's `client-class` may reference a rule shipped in
`src/kea_dhcp_config_generator/fingerprints/rules/`. Used rules are deduplicated
and expanded into top-level Kea `client-classes`; the pool receives Kea's list
form, `client-classes: [name]`.

Pin `fingerprint_library_version` after validating a deployment. A mismatch
with the installed rule library produces a warning. Unknown class names are
errors, with a fuzzy suggestion when a close rule exists. Kea built-ins such as
`ALL`, `KNOWN`, `UNKNOWN`, `DROP`, and `SKIP_DDNS`, plus the supported built-in
prefix families, are accepted without a generated rule definition.

When a subnet mixes restricted pools with one unrestricted pool, the DHCPv4
builder creates a `CatchAll_<subnet-id>` class so the unrestricted pool excludes
clients matching the restricted classes. If every pool is restricted, the
validator warns that unmatched clients receive no address; `--strict` promotes
that warning to an error. A subnet with no pools, such as a reservation-only
subnet, does not trigger the warning.

### Service integration settings

The following optional fields are accepted independently under `dhcp4` and
`dhcp6`:

```yaml
control-sockets:
  - socket-type: http       # unix, http, or https
    socket-address: 127.0.0.1
    socket-port: 8004

interfaces-config:
  interfaces: ["eth0"]
  dhcp-socket-type: raw     # udp or raw

lease-database:
  type: postgresql          # memfile, mysql, or postgresql
  name: kea
  host: db.example.com
  port: 5432
  user: kea
  password: replace-me

hooks-libraries:
  - library: libdhcp_ha.so
    parameters:
      high-availability: []
```

A Unix control socket requires `socket-name`; HTTP and HTTPS require
`socket-address`. Ports must be between `1` and `65535`. Hook parameters are
passed through as plain nested JSON-compatible data and are ultimately checked
by the bundled output schema.

Do not commit real database passwords or HA secrets. The generator currently
accepts literal strings and does not interpolate environment variables or
resolve secret stores.

## Validation coverage and boundaries

Current semantic validation detects:

- overlapping subnets within each protocol stack;
- explicit NA/address pools outside their parent subnet or inverted ranges;
- an IPv4 pool ending at a broadcast address for prefixes up to `/30`;
- duplicate IPv4 reservation addresses within a subnet;
- duplicate DHCPv6 DUIDs or canonicalized IPv6 reservation addresses within a
  subnet;
- malformed PD prefixes and invalid delegated lengths;
- references to missing option profiles or unknown client classes; and
- class-restricted subnets without an unrestricted pool.

Important current boundaries:

- Duplicate reservation identifiers are checked within a subnet, not globally.
- The semantic layer does not currently detect overlap between two explicit
  pools in the same subnet.
- Explicit IPv4 reservation addresses are not semantically checked for subnet
  containment or collision with dynamic pools.
- Custom class definitions cannot be declared in the input format. Apart from
  accepted Kea built-ins, `client-class` must name a shipped fingerprint rule.
- Successful generator validation means the emitted structure matches the
  bundled schema; it does not prove that local interfaces, hook libraries,
  database credentials, or runtime services exist.

For those reasons, use `--strict`, review `--analysis`, and run the appropriate
Kea daemon's `-t` check in deployment CI.

## Library API

The public API is useful for Ansible modules, test harnesses, and other Python
automation:

```python
from pathlib import Path

from kea_dhcp_config_generator import generate, validate

source = Path("service.yaml")
validation = validate(source, strict=True)

if not validation.is_valid:
    for error in validation.errors:
        print(error.yaml_path, error.line, error.message)
else:
    result = generate(
        source,
        output_dir=Path("generated"),
        overwrite=True,
        strict=True,
    )
    print(result.dhcp4_path, result.dhcp6_path)
```

`validate()` does not write files. `generate()` raises an
`ExceptionGroup[ConfigError]` for collected validation failures and
`KeaConfigError` for fatal loading or unexpected processing failures. Unlike
the CLI, the library caller must create `output_dir` before calling
`generate()`.

## Testing changes to processing behavior

Run the complete suite before relying on a changed configuration contract:

```bash
uv run pytest
uv run ruff check .
```

The tests are organized by responsibility:

- `tests/unit/test_loader.py` covers YAML parsing and source locations.
- `tests/unit/test_models_input.py` covers structural types and duration
  conversion.
- `tests/unit/test_validation_semantic.py` covers network, reservation, profile,
  and classification diagnostics.
- `tests/unit/test_dhcp4.py`, `test_dhcp6.py`, and `test_options.py` cover exact
  transformation rules.
- `tests/unit/test_validation_output_schema.py` checks the bundled Kea schemas.
- `tests/unit/test_cli.py` covers streams, exit codes, strict mode, analysis,
  and file behavior.
- `tests/integration/test_generate_golden.py` compares generated protocol
  payloads byte-for-byte with committed golden JSON.
- `tests/integration/test_validate_invalid.py` verifies representative invalid
  fixtures and diagnostic types.

When adding a field or changing its meaning, update the input model, semantic
checks if needed, the appropriate builder, schema expectations, one focused
unit test, and a valid or invalid integration fixture. If generated JSON changes
intentionally, update the matching golden file and review the diff rather than
blindly regenerating it.

