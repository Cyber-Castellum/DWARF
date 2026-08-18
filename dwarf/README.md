# DWARF Framework

This directory contains the DWARF framework code.

The framework provides:

- `cardano-profile`, the command-line interface (CLI) entrypoint.
- The dashboard application rendered under `/operate` and `/learn`.
- The scenario catalog (239 scenarios) under `scenarios/`.
- Primitive schemas and registry data (206 primitives) under `primitives/`.
- Profile and profile-template examples under `profiles/`.
- Preserved bundle archives under `bundles/`.
- Documentation under `docs/`.

The delivery wrapper at the repository root is the intended operator entrypoint:

```bash
delivery/scripts/install.sh
delivery/scripts/build-image.sh
delivery/scripts/deploy.sh
delivery/scripts/status.sh
```

After deployment, open:

```text
http://127.0.0.1:8787/operate
http://<host-lan-ip>:8787/operate
```

The framework container is designed for any Docker-capable Linux host with Docker Compose v2. Runtime data is mounted through the package-local `var/` directory created by the delivery scripts.

For package-level install and operation instructions, use the root `INSTALL.md` and `OPERATIONS.md`.
