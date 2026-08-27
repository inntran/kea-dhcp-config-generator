# kea-dhcp-config-generator

Generate [Kea DHCP](https://kea.readthedocs.io/) server configuration (`Dhcp4` /
`Dhcp6` JSON) from a small, declarative YAML service definition.

Instead of hand-writing verbose Kea JSON — option codes, pool boundary math,
client-class test expressions — you describe your network in a compact YAML file
and `kea-confgen` produces the Kea config for you, deterministically and with
validation.

```
                     kea-confgen
   service.yaml  ───────────────────▶  kea-dhcp4-<ts>.conf
  (declarative)    load → validate         kea-dhcp6-<ts>.conf
                   → build → write          (Kea-ready JSON)
```

## What it does

- **DHCPv4 and DHCPv6** from one file. A single run with both `dhcp4:` and
  `dhcp6:` sections writes both config files.
- **Pool math.** `range: auto` expands to the usable address span (network and
  broadcast excluded for IPv4); explicit ranges and `skip-start` / `skip-end`
  offsets are honoured. `block-size` / `block-count` carves N consecutive
  blocks of a given prefix length (e.g. ten /24s) without hand-computing
  octets — pools in a subnet pack back-to-back in declaration order, and a
  trailing `range: auto` picks up whatever's left as a catch-all. IPv6 NA
  pools and PD (prefix delegation) pools are supported.
- **Friendly options.** Convenience fields like `dns-servers`, `domain-name`,
  `routers`, and `ntp-servers` are translated into the correct Kea
  `option-data` entries. Options inherit most-specific-wins (host > pool >
  subnet > global).
- **Client classification by fingerprint.** Reference a named rule (e.g.
  `iOS_14_17`) on a pool and the tool emits the matching top-level Kea
  `client-classes` entry with its `test` expression. Unknown names are caught,
  with "did you mean…" suggestions.
- **Multi-layer validation.** Structural (types/shape), semantic (overlapping
  subnets, pools out of bounds, duplicate reservations), and output-schema
  checks run before anything is written. All errors are collected and reported
  in one pass, each with the offending YAML line number.
- **Deterministic output.** The same input yields byte-identical JSON across
  runs.
- **Analysis report.** `--analysis` produces a human-readable summary of your
  configuration (subnet inventory, pool sizes, classification coverage,
  reservations, validation results).
- **Library API.** Everything the CLI does is available as a Python library —
  `generate()` and `validate()` — for use in Ansible, CI, and other tooling.
- **Control sockets for API access.** Configure HTTP/HTTPS API listeners (`control-sockets`) for remote management.
- **Hook libraries for extensions.** Load plugins (`hooks-libraries`) for High Availability, RADIUS, DDNS, and other features.
- **Network interface binding.** Explicitly specify which interfaces to listen on (`interfaces-config`) and socket types.
- **Persistent lease storage.** Configure databases (`lease-database`) using memfile, MySQL, or PostgreSQL backends.

## Installation

Requires Python 3.12+. The project uses [uv](https://docs.astral.sh/uv/).

```bash
# From a checkout of this repo
uv sync                      # create the venv and install deps
uv run kea-confgen --help    # run the CLI

# Or install into the current environment
pip install -e .
kea-confgen --help
```

This installs the `kea-confgen` console command.

## Quick start

```bash
# Generate from one of the bundled samples.
# Files are written to ./output/ by default (created if missing), with a
# timestamped filename; the path is printed to stdout.
kea-confgen -c samples/office-dhcp4.yaml
# -> output/kea-dhcp4-20260610-0042.conf

# Write to a stable, canonical filename instead of a timestamped one,
# into a directory of your choosing.
kea-confgen -c samples/dual-stack.yaml --output out/ --overwrite
# -> out/kea-dhcp4.conf
# -> out/kea-dhcp6.conf

# Validate the generated file with Kea itself (Kea 3.x)
kea-dhcp4 -t out/kea-dhcp4.conf
```

Generated file paths are written to **stdout**, one per line (pipeline-friendly);
errors and warnings go to **stderr**.

## Usage

```
kea-confgen --config <file.yaml> [options]
```

| Option | Description |
|--------|-------------|
| `-c`, `--config PATH` | Path to the YAML service definition (required). |
| `-o`, `--output PATH` | Directory to write generated files into (created if missing; default: `./output`). |
| `--overwrite` | Write the canonical `kea-<protocol>.conf` (overwriting it) instead of a timestamped filename. |
| `--strict` | Promote warnings (e.g. a subnet with no catch-all pool) to errors. |
| `--analysis` | Also write a human-readable analysis report to `kea-analysis-<ts>.txt`; its path is printed to stdout alongside the JSON paths. |
| `--analysis-only` | Print the analysis report to stdout and skip JSON generation. |

**Exit codes:** `0` success · `1` validation error · `2` fatal error (file not
found, YAML syntax error).

### Analysis report

```bash
kea-confgen -c samples/office-dhcp4.yaml --analysis-only
```

```
Configuration Analysis Report

Subnet Inventory
----------------------------------------
DHCPv4 subnets: 1
  10.0.10.0/24: pools 2, total IPs: 294
DHCPv6 subnets: 0
  (none)

Client Classification
----------------------------------------
fingerprint_library_version: 0.1.0
rules in use: iOS_14_17
subnets with no catch-all pool: none

Host Reservations
----------------------------------------
  10.0.10.0/24: 1 MAC (DHCPv4)

Validation Results
----------------------------------------
PASS
```

## Input format

The YAML mirrors Kea's vocabulary (hyphenated keys) with a few conveniences.

### Top level

| Key | Meaning |
|-----|---------|
| `dhcp4` | DHCPv4 configuration section (optional). |
| `dhcp6` | DHCPv6 configuration section (optional). |
| `fingerprint_library_version` | Pin the fingerprint rule library version you validated against; a mismatch warns. |

At least one of `dhcp4` / `dhcp6` must be present.

### Common fields (global or per-subnet)

- `valid-lifetime`, `renew-timer`, `rebind-timer` — durations. Accept raw
  seconds (`3600`) or a unit suffix: `d`, `h`, `m`, `s` (e.g. `24h`, `30m`).
  DHCPv6 also accepts `preferred-lifetime`.
- `dns-servers`, `ntp-servers`, `routers` — lists; rendered into the right Kea
  `option-data`. `domain-name` — a single string.
- `option-data` — an explicit Kea `option-data` list when you need full control;
  merged by option name over the convenience fields (most-specific wins).

### Control and API Configuration (optional, global or per-protocol)

- `control-sockets` — list of control socket configurations for API access (HTTP/HTTPS/Unix)
- `hooks-libraries` — list of hook library plugins to load
- `interfaces-config` — network interfaces to listen on
- `lease-database` — persistent lease storage configuration (memfile/mysql/postgresql)

All are optional; omit if using defaults or file-only configuration.

### Subnets

```yaml
subnets:
  - subnet: 10.0.10.0/24      # IPv4 CIDR  (IPv6 CIDR under dhcp6)
    id: 10                    # optional; auto-assigned 1,2,3… in YAML order if omitted
    client-class: KNOWN       # optional subnet-level class guard
    pools: [ ... ]
    reservations: [ ... ]
```

> Subnet `id` is **all-or-none**: assign explicit IDs to every subnet in a stack
> or to none. DHCPv4 and DHCPv6 IDs are independent sequences.

### Pools

DHCPv4 pool:

```yaml
pools:
  - range: auto                       # or "10.0.10.20 - 10.0.10.200"
    skip-start: 10                    # leave the first N usable addresses free
    skip-end: 0
    client-class: iOS_14_17           # optional fingerprint rule name
```

A pool can instead claim N consecutive blocks of a given prefix length
(`block-size` + `block-count`, mutually exclusive with `range`) — useful when
carving a large subnet into per-class chunks without computing octets by
hand. Pools in the same subnet pack back-to-back in declaration order:

```yaml
subnets:
  - subnet: 10.0.0.0/16
    pools:
      - block-size: 24                # ten /24s: 10.0.1.0/24 - 10.0.10.0/24
        block-count: 10
        client-class: Windows_10_11
      - block-size: 24                # next 20 /24s: 10.0.11.0/24 - 10.0.30.0/24
        block-count: 20
        client-class: macOS
      - range: auto                   # everything left over: 10.0.31.0 - 10.0.255.254
```

DHCPv6 pools use a `pool-type` discriminator:

```yaml
pools:
  - pool-type: na                     # non-temporary address pool
    range: auto                       # or "2001:db8:1::100 - 2001:db8:1::200"
    client-class: iOS_14_17           # optional
  - pool-type: pd                     # prefix-delegation pool
    prefix: "2001:db8:1::"
    prefix-len: 48
    delegated-len: 64
```

### Reservations

```yaml
# DHCPv4 — MAC-based
reservations:
  - hw-address: "aa:bb:cc:dd:ee:ff"
    ip-address: 10.0.10.50
    hostname: printer-01

# DHCPv6 — DUID-based
reservations:
  - duid: "00:03:00:01:aa:bb:cc:dd:ee:ff"
    ip-address: "2001:db8:1::100"
    hostname: nas-01
```

> All user-supplied string fields are restricted to ASCII.

## Example: input → output

`samples/office-dhcp4.yaml` (abridged):

```yaml
fingerprint_library_version: "0.1.0"
dhcp4:
  valid-lifetime: 24h
  dns-servers: ["10.0.0.1", "8.8.8.8"]
  routers: ["10.0.0.1"]
  subnets:
    - subnet: 10.0.10.0/24
      pools:
        - range: 10.0.10.50 - 10.0.10.99
          client-class: iOS_14_17
        - range: auto
          skip-start: 10
      reservations:
        - hw-address: "aa:bb:cc:dd:ee:ff"
          ip-address: 10.0.10.50
          hostname: printer-01
```

produces (abridged):

```json
{
  "Dhcp4": {
    "valid-lifetime": 86400,
    "option-data": [
      { "name": "domain-name-servers", "data": "10.0.0.1, 8.8.8.8" },
      { "name": "routers", "data": "10.0.0.1" }
    ],
    "client-classes": [
      { "name": "iOS_14_17", "test": "..." }
    ],
    "subnet4": [
      {
        "id": 1,
        "subnet": "10.0.10.0/24",
        "pools": [
          { "pool": "10.0.10.50 - 10.0.10.99", "client-classes": ["iOS_14_17"] },
          { "pool": "10.0.10.11 - 10.0.10.254" }
        ],
        "reservations": [
          { "hw-address": "aa:bb:cc:dd:ee:ff", "ip-address": "10.0.10.50", "hostname": "printer-01" }
        ]
      }
    ]
  }
}
```

(The real output also carries a `_kea-config-generator` provenance header.)

## Sample files

The [`samples/`](samples/) directory contains ready-to-run service definitions:

| File | Demonstrates |
|------|--------------|
| [`minimal-dhcp4.yaml`](samples/minimal-dhcp4.yaml) | The smallest useful config: one subnet, one `auto` pool. |
| [`office-dhcp4.yaml`](samples/office-dhcp4.yaml) | Timers, options, a fingerprint-classified pool, a catch-all pool, a MAC reservation. |
| [`dual-stack.yaml`](samples/dual-stack.yaml) | DHCPv4 + DHCPv6 in one run, with NA + PD pools and a DUID reservation. |
| [`control-api.yaml`](samples/control-api.yaml) | HTTP API socket for remote management. |
| [`production-ha.yaml`](samples/production-ha.yaml) | High Availability setup with HA hook library and control socket. |
| [`persistent-db.yaml`](samples/persistent-db.yaml) | PostgreSQL lease database backend for both DHCPv4 and DHCPv6. |

```bash
kea-confgen -c samples/dual-stack.yaml --overwrite
```

## Available fingerprint rules

Reference these names in a pool's `client-class`:

`Android_12_14`, `HomeRouter`, `HuaweiAndroid`, `IoT_Generic`, `LocalMAC`,
`OldAndroid`, `Tesla`, `VivoAndroid`, `WRT`, `Windows_10_11`, `Windows_10_Old`,
`Windows_7`, `iOS_9_13`, `iOS_14_17`, `macOS`.

## Use as a Python library

```python
from pathlib import Path
from kea_dhcp_config_generator import generate, validate

# Validate without writing anything; never raises for validation failures.
result = validate(Path("service.yaml"))
if not result.is_valid:
    for err in result.errors:
        print(err.line, err.yaml_path, err.message)

# Generate files; raises a typed error (ConfigError subclass / KeaConfigError)
# on failure.
gen = generate(Path("service.yaml"), output_dir=Path("out/"), overwrite=True)
print(gen.dhcp4_path, gen.dhcp6_path)
```

Exported API: `generate`, `validate`, `GenerationResult`, `ValidationResult`,
`ConfigError`, `ConfigWarning`, `KeaConfigError`, `SubnetConfigError`,
`OptionDataError`, `FingerprintError`.

## Development

```bash
uv sync
uv run pytest          # run the test suite
uv run ruff check      # lint
```

## License

See the repository for license details.
