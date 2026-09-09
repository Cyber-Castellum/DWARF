# Dashboard Integrity Repair Implementation Plan

**Goal:** Repair every confirmed Operate/Learn defect and make the deployed dashboard provably identical to a public GitHub revision.

**Architecture:** Build an immutable dashboard image from a clean public source snapshot, record its revision, and deploy it without an application-source bind mount. Derive live documentation from runtime catalogs, use an explicit cached server-side attack-cost provider, and enforce navigation/accessibility/responsive contracts with automated tests.

**Tech Stack:** Python, Jinja, vanilla JavaScript/CSS, Docker Compose, pytest, Playwright.

---

### Task 1: Lock deployment provenance and SSH behavior

1. Add failing tests for the portable SSH default, Compose key destination,
   source-bind prohibition, and source-revision image metadata.
2. Run the focused tests and confirm the expected failures.
3. Align configuration, Compose, Dockerfile, and image-build logic.
4. Re-run focused tests and review the deployment diff.

### Task 2: Make Learn content current and honest

1. Add failing tests for live inventory totals, deployed-source provenance,
   complete API route documentation, and current mixed-network language.
2. Confirm the old static/zero-history behavior fails those tests.
3. Derive counts from catalogs, expose the build revision, update the route
   catalog, and correct the mixed-network scope statement.
4. Re-run focused tests and compare all displayed counts mechanically.

### Task 3: Replace the broken browser attack-cost feed

1. Add failing tests for server-side retrieval, caching, timeout fallback,
   explicit source labeling, and absence of cross-origin browser fetches.
2. Confirm the existing template fails.
3. Implement the smallest server-side provider and render its result.
4. Re-run provider and page tests.

### Task 4: Repair navigation and accessibility

1. Add failing tests for every known fragment family and missing label/H1.
2. Confirm scenario, glossary, example, and historical-profile failures.
3. Add stable destination IDs, graceful missing-catalog rendering, and labels.
4. Run the fragment/accessibility tests across every Operate/Learn page.

### Task 5: Harden new Operate pages

1. Add failing tests for missing-config rendering and 390px create-form width.
2. Confirm `/operate/config/edit` and `/operate/profiles/new` fail.
3. Fall back to typed configuration defaults and contain create-form fields.
4. Re-run focused and route-level tests.

### Task 6: Full review and public package

1. Review every changed file against the approved design and public baseline.
2. Run all relevant Python, shell, Compose, route, fragment, accessibility,
   secret, and Playwright checks.
3. Build a minimal public-repository tarball and inspect every member and mode.
4. Stop before live deployment until the archive is published to public main.

### Task 7: Exact-revision live deployment

1. Fetch the published public commit on `cardano-box` into a clean release
   directory and build with its revision.
2. Deploy through checked-in Compose without the source bind mount while
   preserving runs, bundles, state, and SSH material.
3. Prove the running image revision equals public GitHub main and inspect all
   container mounts.
4. Repeat the complete route, visual, functional, freshness, and secret audit
   against `https://dwarf.gainpalfam.com`.

### Post-deployment verification correction (2026-09-09)

The exact-revision cutover exposed a pre-existing incompatibility between the
dashboard lifecycle summary and the hardened SSH control channel. The summary
attempted to send inline Python over SSH, while the forced-command key accepts
only named control verbs, producing `bad-token-count`. In dashboard deployments
the authoritative lifecycle state is already mounted beneath
`ADA2_DWARF_STATE_DIR`; shim mode must read that mounted state directly and must
not attempt arbitrary remote execution. Regression coverage verifies both the
configured state-root selection and the absence of an SSH call in shim mode.
