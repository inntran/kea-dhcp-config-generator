# Kea Container Test Harness — Design

**Date:** 2026-06-10
**Status:** Design (spike-validated)
**Goal:** Validate generated configs against a real Kea 3.0 daemon, satisfying
NFR4/NFR5 and unlocking Epic 8's "live Kea instance" requirement.

## Problem

NFR4/NFR5 require every generated DHCPv4/DHCPv6 config to pass against a real
Kea 3.x instance with no manual modification. Today there is no Kea binary in
the test environment, so this requirement is unverified. We need a reproducible
way to run real `kea-dhcp4` / `kea-dhcp6` against generated configs.

## Decisions (settled in brainstorming)

- **Test boundary:** full DHCP daemon (serve) — assert the daemon **starts and
  stays up** on the generated config (process alive after a short wait, no
  ERROR/FATAL in its log). Not a bare `kea-dhcp4 -t`; not a full DISCOVER→ACK
  lease exchange.
- **Invocation:** per-config ephemeral `podman run` (hermetic, no shared state).
- **Availability:** **hard-fail** if podman or the image is missing — but only
  on the local dev path and a dedicated **Linux-only CI job**. The existing
  Ubuntu+macOS unit-test matrix stays podman-free and unchanged (macOS GitHub
  runners cannot run Linux containers).
- **Image distribution:** built from a bundled `Containerfile` and run as
  `localhost/kea-test:3.0` / `ghcr.io/inntran/kea-test:3.0`. **Local build only
  for now**; the ghcr build-and-push workflow is a deferred follow-up.
- **Tag:** rolling `:3.0` (always the latest Kea 3.0.x the repo serves).

## Spike findings (all observed, not assumed)

A spike built the image and ran the binaries under the local **rootless** podman
(5.8.2, uid 1000). Results that shape this design:

1. **Capabilities — `NET_BIND_SERVICE` + `NET_RAW` (v4), `NET_BIND_SERVICE`
   (v6). `NET_ADMIN` is NOT needed.** The Kea binaries are setcap'd
   (`kea-dhcp4`: `cap_net_bind_service,cap_net_raw=ep`; `kea-dhcp6`:
   `cap_net_bind_service=ep`). Under rootless podman a file-cap'd binary will
   not even `exec` — it fails with `Operation not permitted` — unless those
   exact caps are `--cap-add`ed. This applies even to `kea-dhcp4 -t`. The caps
   are an **exec requirement**, not just a socket-binding requirement.
2. **No network needed.** With `interfaces-config: { "interfaces": [] }` the
   daemon starts, stays up, and logs only a benign `WARN
   DHCPSRV_NO_SOCKETS_OPEN`. No podman network, no client, no `--network`
   tuning.
3. **Starts & stays up works.** `kea-dhcp4/6 -c <cfg>` → container
   `State.Running == true` after 3s, final log line `DHCP4_STARTED ... 3.0.3
   started` (resp. `DHCP6_STARTED`), no ERROR/FATAL.
4. **Exit-code contract (for `-t`, available as a secondary primitive):** valid
   config → exit 0; invalid → exit 1 with an `Error encountered:` line.
5. **UBI subscription repos must be disabled.** UBI10 enables `rhel-10-*` and
   `codeready-*` repos that return HTTP 403 without a Red Hat subscription. The
   `dnf install` must pass `--disablerepo='rhel-*' --disablerepo='codeready-*'`
   and rely on the entitlement-free `ubi-10-*` repos plus the ISC Kea repo.
6. **Version aligned to 3.0.3.** The ISC repo ships Kea **3.0.3**. Everything is
   pinned to match: `_schema/VERSION`, the schema JSON descriptions, the schema
   README, `SCHEMA_VERSION`, and each fingerprint rule's `validated_against`.
   The 15 fingerprint expressions were run through `kea-dhcp4 -t` in the 3.0.3
   container (all exit 0, no errors), so `validated_against: "3.0.3"` is a
   verified claim, not just a string bump.
7. **Image size:** ~258 MB.

## Architecture

Three units, each independently understandable and testable.

### 1. The image (`tests/integration/kea/Containerfile`)

```
FROM registry.access.redhat.com/ubi10/ubi
RUN curl -1sLf https://dl.cloudsmith.io/public/isc/kea-3-0/setup.rpm.sh | bash \
 && dnf install -y \
      --disablerepo='rhel-*' --disablerepo='codeready-*' \
      isc-kea-dhcp4 isc-kea-dhcp6 \
 && dnf clean all
```

- Pure binary carrier: no baked-in config, no auto-start entrypoint. Tests
  supply the config (bind-mounted) and the command.
- Built locally as `localhost/kea-test:3.0`. (ghcr push: deferred.)

### 2. The runner (`tests/integration/kea_runner.py`)

A thin, dependency-free helper that owns all podman knowledge so tests stay
declarative.

- `KEA_IMAGE = "localhost/kea-test:3.0"` (single source of truth for the ref).
- `require_kea_container()` — **hard-fail** (raise, not skip) if `podman` is
  absent or the image is not present locally. Invoked only by the Kea-marked
  tests, so it never affects the podman-free matrix.
- `run_daemon(config_path, *, family)` — builds the argv and runs it:
  - family `"v4"` → caps `NET_BIND_SERVICE,NET_RAW`, binary `kea-dhcp4`.
  - family `"v6"` → cap `NET_BIND_SERVICE`, binary `kea-dhcp6`.
  - `podman run -d --rm` with `-v <config>:/cfg/<name>:ro,Z`, no `--network`
    flag needed (default is fine; daemon uses `interfaces: []`).
  - Waits a short fixed interval, captures `State.Running`, `State.ExitCode`,
    and `podman logs`, then force-removes the container.
  - Returns a small result object: `running: bool`, `exit_code: int`,
    `log: str`, and `fatal_lines: list[str]` (log lines matching `ERROR|FATAL`).
- The config handed to Kea must carry the runtime-only scaffolding the generator
  does not emit — `interfaces-config: { interfaces: [] }` and an in-memory
  `lease-database` (`memfile`, `persist: false`). The runner wraps the
  generator's `Dhcp4`/`Dhcp6` output with this scaffolding before mounting, so
  the daemon can start without a real NIC or lease store. (The generator output
  itself is unchanged; the scaffolding lives only in the runner.)

### 3. The tests (`tests/integration/test_kea_runtime.py`)

- A registered `kea` pytest marker (declared in `pyproject.toml`
  `[tool.pytest.ini_options] markers`), so the suite is selectable via
  `pytest -m kea` and excludable via `-m "not kea"`.
- Parametrized over the `samples/` configs (`minimal-dhcp4`, `office-dhcp4`,
  `dual-stack`): for each, generate the config, run the daemon for the relevant
  family/families, and assert `result.running is True`, `result.exit_code == 0`
  (for any that exit), and `result.fatal_lines == []`.
- The whole module is gated by `require_kea_container()` at collection/setup, so
  on a machine without podman+image the tests **fail** (the chosen hard-fail
  semantics) rather than silently skipping.

## CI wiring (Story 8.1 reconciliation)

- **Unchanged:** the existing `tests` matrix (Python 3.12/3.14 × Ubuntu/macOS)
  runs `pytest -m "not kea"` — no podman, no image, macOS stays green.
- **New:** a dedicated `kea-integration` job, **Ubuntu-only**, that:
  1. builds the image (`podman build -t localhost/kea-test:3.0
     tests/integration/kea`),
  2. runs `pytest -m kea`,
  3. hard-fails if the image build or any Kea-backed test fails.
- The ghcr build-and-push workflow (so CI pulls a cached image instead of
  building each run) is a **follow-up**, not in this slice.

## Error handling

- Missing podman / missing image → `require_kea_container()` raises a clear,
  actionable error naming the build command. (Hard-fail by design.)
- Daemon crash on a generated config → `running is False`; the test fails and
  surfaces `result.fatal_lines` + the tail of `result.log` so the offending Kea
  error is visible.
- Container leak prevention → `podman run --rm` plus an explicit force-remove in
  a `finally` so no containers survive a failed assertion.

## Testing

- The runner is exercised by the runtime tests themselves (it has no logic worth
  unit-testing in isolation beyond argv assembly).
- A negative self-check (optional): feed the runner a deliberately invalid
  config and assert `running is False` / non-zero exit, proving the harness
  actually detects a bad config rather than always passing.

## Out of scope (explicit follow-ups)

- ghcr.io build-and-push workflow and CI pull-or-build caching.
- Story 8.3's `update_kea_schema.py` (upstream-derived schema generation). The
  3.0.3 version bump here is manual; the automated sourcing script is still its
  own story.
- Full lease-exchange (DISCOVER→ACK) testing with a DHCP client.
- Duplicate-subnet-id validation (separate pre-existing gap noted during the
  CatchAll review).
