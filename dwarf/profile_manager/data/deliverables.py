"""Pure deliverable/document catalog helpers for the dashboard data layer."""
from __future__ import annotations

from profile_manager.data.files import _best_existing


def _doc_links():
    from profile_manager.dashboard import PROJECT_ROOT

    candidates = [
        ("Delivery README", "README.md"),
        ("Install Guide", "INSTALL.md"),
        ("Operations Guide", "OPERATIONS.md"),
        ("Release Notes", "RELEASE-NOTES.md"),
        ("Test Outputs", "TEST-OUTPUTS.md"),
        ("Dwarf Framework README", "dwarf/README.md"),
        ("M2 Serialization/Deserialization Analysis", "dwarf/docs/m2-serdes/serialization-deserialization-analysis.md"),
        ("M2 Resource-Abuse Testing Plan", "dwarf/docs/m2-serdes/resource-abuse-testing-plan.md"),
        ("Forensic Bundle Format", "dwarf/docs/forensic-bundle-format.md"),
        ("Operator Handbook", "dwarf/docs/operator-handbook.md"),
    ]
    links = []
    for label, relative in candidates:
        path = PROJECT_ROOT / relative
        links.append((label, relative, path.exists()))
    return links


def _deliverable_entry(title, kind, status, paths=None, note=""):
    relative = _best_existing(paths or [])
    return {
        "title": title,
        "kind": kind,
        "status": status,
        "path": relative,
        "note": note,
    }


def _deliverable_catalog():
    return [
        {
            "id": "milestone-2",
            "title": "Milestone 2: Dwarf V3 Delivery",
            "target": "June 2026 V3 package",
            "items": [
                _deliverable_entry(
                    "Edge-case CBOR transaction-body scenarios",
                    "Deliverable",
                    "Included",
                    [
                        "dwarf/scenarios/edge-cases-cbor-tx-body-amaru.yaml",
                        "dwarf/scenarios/edge-cases-cbor-tx-body-cardano-node.yaml",
                    ],
                    "CBOR means Concise Binary Object Representation.",
                ),
                _deliverable_entry(
                    "Resource-abuse testing plan and first executions for RAM, disk, and sync abuse",
                    "Deliverable",
                    "Included",
                    [
                        "dwarf/docs/m2-serdes/resource-abuse-testing-plan.md",
                        "TEST-OUTPUTS.md",
                    ],
                ),
                _deliverable_entry(
                    "Dwarf framework, dashboard, and Docker deployment package",
                    "Deliverable",
                    "Included",
                    [
                        "dwarf/README.md",
                        "delivery/docker-compose.dwarf.yml",
                        "infrastructure/docker/dwarf-fw.Dockerfile",
                    ],
                    "The package includes the framework CLI, dashboard, deployment scripts, retained scenarios, profiles, bundles, and example run outputs.",
                ),
                _deliverable_entry(
                    "Serialization/deserialization analysis for transactions and blocks",
                    "Deliverable",
                    "Included",
                    ["dwarf/docs/m2-serdes/serialization-deserialization-analysis.md"],
                ),
                _deliverable_entry(
                    "Dashboard-ready preserved run examples and bundles",
                    "Deliverable",
                    "Included",
                    [
                        "dwarf/runs/20260419T020533Z-aa19a2d4/manifest.json",
                        "dwarf/bundles/20260419T020533Z-aa19a2d4.tar.gz",
                    ],
                    "Deploy scripts seed these examples into the runtime volume so they appear in Operate after deployment.",
                ),
            ],
        },
    ]


def _deliverable_rows():
    rows = []
    for milestone in _deliverable_catalog():
        for item in milestone["items"]:
            rows.append(
                {
                    "milestone": milestone["title"],
                    "target": milestone["target"],
                    **item,
                }
            )
    return rows


def _document_rows():
    return [
        {"label": label, "path": relative, "state": "present" if exists else "missing"}
        for label, relative, exists in _doc_links()
    ]
