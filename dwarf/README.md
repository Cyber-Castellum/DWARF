# DWARF Framework

This directory contains the Dwarf Version 3 (V3) framework code included in the June Milestone 2 (M2) delivery package.

The framework provides:

- `cardano-profile`, the command-line interface (CLI) entrypoint.
- The dashboard application rendered under `/operate` and `/learn`.
- The retained M2 scenario catalog under `scenarios/`.
- Primitive schemas and registry data under `primitives/`.
- Profile and profile-template examples under `profiles/`.
- Verified example runs under `runs/`.
- Preserved bundle archives under `bundles/`.
- M2 serialization/deserialization notes and retained first-execution evidence under `docs/` and `evidence/`.

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
