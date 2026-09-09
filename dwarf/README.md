# DWARF Framework

This directory contains the DWARF framework code.

The framework provides:

- `cardano-profile`, the command-line interface (CLI) entrypoint.
- The dashboard application rendered under `/operate` and `/learn`.
- The current scenario catalog under `scenarios/`.
- Primitive schemas and registry data under `primitives/`.
- Profile and profile-template examples under `profiles/`.
- Preserved bundle archives under `bundles/`.
- Documentation under `docs/`.

The delivery wrapper at the repository root is the intended operator entrypoint:

```bash
bash delivery/scripts/install.sh
bash delivery/scripts/build-image.sh
bash delivery/scripts/deploy.sh
bash delivery/scripts/status.sh
```

After deployment, open:

```text
http://127.0.0.1:8787/operate
http://<host-lan-ip>:8787/operate
```

The framework container is designed for any Docker-capable Linux host with Docker Compose v2. Runtime data is retained under `~/.local/share/dwarf/` by default; set `DWARF_RUNTIME_ROOT` to use another location.

For package-level install and operation instructions, use the root `INSTALL.md` and `OPERATIONS.md`.
