# Dashboard Integrity Repair Design

## Goal

Make the deployed DWARF dashboard an exact, identifiable build of the public
repository while repairing the confirmed Operate/Learn correctness,
freshness, navigation, accessibility, and responsive defects.

## Root cause

The live `dwarf-fw` container was created outside the checked-in Compose
deployment. It uses an August 17 image and a read-only bind mount from a
selectively updated, non-Git application-source directory. That bind
mount replaces the image's packaged application source, so neither the image
nor public GitHub `main` is the live source of truth.

Several Learn pages also describe repository state while running in an image
that contains no `.git` directory. Static scenario and primitive totals then
drift independently from the runtime catalogs. Browser-side cross-origin data
fetches make the attack-cost page silently fall back to a snapshot.

## Design

Build and deploy only from an exact public-repository revision. The production
Compose contract must not mount application source. The image records the
source revision supplied by the build command, and the dashboard exposes it so
operators can compare the live revision with GitHub.

Keep mutable runs, bundles, state, and the SSH key outside the image. Align the
portable default SSH path with the Compose mount at
`/home/dwarf/.ssh/cardano-box`; retain the host-side `~/.ssh/cardano-box`
fallback.

Make Learn pages honest about their sources. Runtime inventory counts come
from the scenario and primitive catalogs. Repository-history sections render
only when Git history exists; otherwise the page presents deployed revision
and filesystem-derived evidence instead of claiming zero recent work. The
mixed Cardano/Amaru capability text reflects the locally proven scenario while
retaining the boundary that it is not yet a valid live Antithesis result.

Fetch attack-cost data server-side with a short timeout and bounded in-process
cache. Render the source and timestamp explicitly; use the baked snapshot only
when the upstream APIs are unavailable. No browser cross-origin request is
required.

Every generated deep link receives a matching stable ID. Historical references
whose catalog target no longer exists render as plain identifiers. Form
controls receive labels, the Learn landing page receives one H1, and create
forms remain within the mobile viewport. Configuration editing falls back to
typed defaults when no configuration file exists.

## Safety and verification

All changes are made against a clean export of public `main`, with the already
verified responsive-polish delta applied. Tests are written and observed
failing before implementation. The final gate covers unit behavior, full route
rendering, internal fragments, accessibility, secret-pattern scanning,
desktop/mobile visual contracts, Docker Compose rendering, source-mount
absence, source-revision provenance, and persistence of the existing runtime
mounts.

The public archive contains no runtime state, credentials, caches, build
outputs, Apple metadata, or unrelated local changes. Live deployment happens
only after the repaired archive is published, so the image can be built from
and identified with that exact public commit.
