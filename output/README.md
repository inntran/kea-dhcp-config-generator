# output/

Default destination for generated Kea config files. With no `--output` flag,
`kea-confgen` writes here:

```bash
kea-confgen -c samples/dual-stack.yaml
# -> output/kea-dhcp4-<ts>.conf
# -> output/kea-dhcp6-<ts>.conf
```

With `--analysis` a `kea-analysis-<ts>.txt` report is written here too. Use
`--output <dir>` (or `-o`) to write somewhere else; the directory is created
automatically if it does not exist.

Generated `kea-*` files here are git-ignored; this `README.md` is kept so the
directory exists in the repository.
