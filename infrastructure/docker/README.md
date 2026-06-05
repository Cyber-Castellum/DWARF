# Dwarf Framework Docker Assets

This directory contains only the Docker files required by the Dwarf Version 3 (V3) delivery package.

## Files

- `dwarf-fw.Dockerfile`: builds the framework image `dwarf/framework:june-20260604-m2`.
- `dwarf-fw-entrypoint.sh`: forwards container commands to `python3 dwarf/cardano-profile`.
- `requirements-framework.txt`: hash-locked Python dependencies used by the image build.

## Build

Use the delivery wrapper from the repository root:

```bash
delivery/scripts/build-image.sh
```

Equivalent direct command:

```bash
docker build \
  -f infrastructure/docker/dwarf-fw.Dockerfile \
  -t dwarf/framework:june-20260604-m2 \
  .
```

## Deploy

Use the delivery Compose file and lifecycle scripts:

```bash
delivery/scripts/install.sh
delivery/scripts/build-image.sh
delivery/scripts/deploy.sh
delivery/scripts/status.sh
```

The container serves the dashboard on port `8787` inside the container. The default host bind is `0.0.0.0:8787`.

## Scope

This V3 framework/dashboard delivery is intended to run on any Docker-capable Linux host with Docker Compose v2. It is not tied to any specific host.

The included Compose file starts the DWARF framework dashboard and mounts runtime data directories. It does not automatically start every possible Cardano target-node topology; those target services are selected by the operator when running campaigns.
