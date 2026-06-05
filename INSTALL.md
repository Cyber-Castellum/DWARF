# Install Guide

This guide is written for a new operator receiving Dwarf Version 3 (V3) as a delivery package.

The recommended path is to install on a Linux host with Docker. The package is not hard-coded to a specific host.

## 1. Check Requirements

Run:

```bash
docker version
docker compose version
df -h .
```

Expected:

- Docker daemon is reachable.
- `docker compose version` prints a Compose v2 version.
- At least 20 GB free disk is available.

If Docker is installed but permission is denied, add the deploying user to the Docker group or run from an account with Docker access. Do not run the Dwarf container privileged by default.

## 2. Copy The Package To The Target Host

Example:

```bash
rsync -a --delete dwarf-v3/ user@host:/opt/dwarf-v3/
```

Then connect with Secure Shell (SSH) to the target:

```bash
ssh user@host
cd /opt/dwarf-v3
```

For a different host, copy the directory anywhere the deploying user can write, for example:

```bash
scp -r dwarf-v3 user@host:/opt/dwarf-v3
ssh user@host
cd /opt/dwarf-v3
```

## 3. Prepare Runtime Directories

Run:

```bash
delivery/scripts/install.sh
```

This validates the package layout and creates:

```text
var/runs
var/state
var/bundles
```

The script does not install host packages, modify Docker daemon configuration, or start Cardano services.

## 4. Build The Framework Image

Run:

```bash
delivery/scripts/build-image.sh
```

This builds:

```text
dwarf/framework:june-20260604-m2
```

The build uses:

```text
infrastructure/docker/dwarf-fw.Dockerfile
```

The Dockerfile uses Debian snapshot repositories for reproducibility. V3 includes a regression check ensuring expired snapshot Release files do not break the build:

```text
Acquire::Check-Valid-Until "false";
Acquire::Check-Date "false";
```

## 5. Deploy The Dashboard

Run:

```bash
delivery/scripts/deploy.sh
```

Default dashboard:

```text
http://127.0.0.1:8787/operate
http://<host-local-area-network-ip>:8787/operate
```

The local area network (LAN) Internet Protocol (IP) address form is:

```text
http://<host-lan-ip>:8787/operate
```

If port `8787` is in use:

```bash
DWARF_DASHBOARD_PORT=8877 delivery/scripts/deploy.sh
```

Then open:

```text
http://127.0.0.1:8877/operate
http://<host-lan-ip>:8877/operate
```

## 6. Check Status

Run:

```bash
delivery/scripts/status.sh
```

Or, if deployed on a non-default port:

```bash
DWARF_DASHBOARD_PORT=8877 delivery/scripts/status.sh
```

The status command reports:

- package root
- image tag
- container name
- dashboard bind/port
- runtime root
- Docker image status
- Compose service status
- Docker container status
- mapped port
- in-container `cardano-profile dashboard status` output

Expected June M2 inventory:

```text
Profiles: 9
Evidence packages: 4
Smoke tests: 5
Fuzz tests: 0
Scenario catalog: 28 scenarios
```

## 7. Optional Browser Verification

By default, deployment binds Docker to `0.0.0.0`, so the dashboard is reachable on both loopback and the host LAN IP. If you deploy with:

```bash
DWARF_DASHBOARD_PORT=8877 delivery/scripts/deploy.sh
```

then open either:

```text
http://127.0.0.1:8877/operate
http://<host-lan-ip>:8877/operate
```

If you explicitly deploy loopback-only with `DWARF_DASHBOARD_BIND=127.0.0.1`, use SSH port forwarding:

```bash
ssh -N -L 8877:127.0.0.1:8877 user@host
```

Then open locally:

```text
http://127.0.0.1:8877/operate
```

The dashboard should render the Operate page and the Scenarios page should show the scoped June M2 scenario catalog.

For a recipient-facing visual overview of the expected graphical user interface (GUI), open:

```text
docs/index.html
```

That HyperText Markup Language (HTML) file is self-contained and is the GitHub Pages entry point for the delivery overview, so it can be viewed offline after the package is unpacked or hosted from the repository's `docs/` directory.

## Configuration

Override deployment defaults with environment variables:

```bash
export DWARF_IMAGE=dwarf/framework:june-20260604-m2
export DWARF_CONTAINER_NAME=dwarf-fw-june-m2
export DWARF_DASHBOARD_BIND=0.0.0.0
export DWARF_DASHBOARD_PORT=8787
export DWARF_RUNTIME_ROOT=/absolute/path/to/var
export ADA2_DWARF_TOKEN=dwarf
```

Most operators only need `DWARF_DASHBOARD_PORT`. Set `DWARF_DASHBOARD_BIND=127.0.0.1` only when you want loopback-only access.

## SSH Keys

The V3 delivery stack does not mount SSH keys by default. That is deliberate.

Some future or operator-specific substrate scenarios may require SSH fan-out to another machine. Add that only through an explicit Compose override after deciding which keys and hosts are in scope.

## Uninstall

Stop the stack and preserve runtime data and image:

```bash
delivery/scripts/uninstall.sh
```

Remove runtime data:

```bash
delivery/scripts/uninstall.sh --purge
```

Remove image:

```bash
delivery/scripts/uninstall.sh --remove-image
```

Remove both:

```bash
delivery/scripts/uninstall.sh --purge --remove-image
```
