# Dwarf V3 June Milestone 2 (M2) Delivery

This directory is a Dwarf Version 3 (V3) delivery for Milestone 2. It contains the current Dwarf framework code path plus the catalog, documentation, Docker wrapper, and evidence needed for the M2 testing deliverables retained here.

## Scope

Included M2 material:

- Dwarf framework source and dashboard/command-line interface (CLI) support.
- M2 serialization/deserialization scenarios for Concise Binary Object Representation (CBOR) fuzzing, structured CBOR fuzzing, edge-case CBOR checks, and authored serdes substrate checks.
- M2 resource first-execution scenarios and evidence bundles.
- M2 prior-run test-output index in `TEST-OUTPUTS.md`.
- M2 primitive registry subset and primitive schemas needed by the included scenarios.
- Full existing profile/template catalog, with `profile-a-haskell-peersharing-disabled` providing the profile context for the retained M2 resource first executions.
- Docker delivery wrapper for a June M2-scoped framework image.
- Six chain-verified example runs under `dwarf/runs/` for the dashboard run inspector.
- Six exported example archives under `dwarf/bundles/` for the preserved-bundles catalog.
- One retained cross-implementation comparison evidence record for the Operate comparison view.

## Layout

```text
dwarf-v3/
├── README.md
├── INSTALL.md
├── OPERATIONS.md
├── RELEASE-NOTES.md
├── TEST-OUTPUTS.md
├── delivery/
│   ├── docker-compose.dwarf.yml
│   ├── scripts/
│   └── tests/
├── dwarf/
│   ├── cardano-profile
│   ├── profile_manager/
│   ├── primitives/
│   ├── scenarios/
│   ├── profiles/
│   ├── runs/
│   ├── bundles/
│   ├── docs/m2-serdes/
│   └── evidence/m2-first-executions/
└── infrastructure/docker/
```

## Docker Image

Default image tag:

```text
dwarf/framework:june-20260604-m2
```

To rebuild the June tag from this directory:

```bash
delivery/scripts/install.sh
delivery/scripts/build-image.sh
delivery/scripts/deploy.sh
delivery/scripts/status.sh
```

The delivery stack is the portable DWARF framework/dashboard container. It is intended to run on any Docker-capable Linux host with Docker Compose v2 and is not tied to any specific host. Target-node services for live campaigns are selected separately by the operator when a campaign requires them.

## M2 Catalog Counts

The scoped catalog in this delivery contains:

- 28 YAML Ain't Markup Language (YAML) scenario files.
- 18 registered primitive entries.
- 18 primitive schema files under `dwarf/primitives/`, plus the registry and README.
- 9 configured profiles and 9 profile templates under `dwarf/profiles/`.
- 19 selected evidence bundles, indexed in `TEST-OUTPUTS.md`.
- 6 dashboard-ready example runs under `dwarf/runs/`.
- 6 preserved example archives under `dwarf/bundles/`.
- 1 retained cross-implementation comparison evidence record.

Run `delivery/tests/test_delivery_contract.sh` to verify the package layout.
