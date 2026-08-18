# Install Guide

This guide is written for a new operator setting up Dwarf.

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

## 2. Get The Package Onto The Target Host

Clone the repository directly on the target host:

```bash
git clone https://github.com/Cyber-Castellum/DWARF.git
cd DWARF
```

Or, if you already have the tree on another machine, copy it over and connect with Secure Shell (SSH):

```bash
rsync -a --delete DWARF/ user@host:/opt/DWARF/
ssh user@host
cd /opt/DWARF
```

The package is not hard-coded to a specific host or path; put it anywhere the deploying user can write.

## 3. Install (one command)

Run:

```bash
delivery/scripts/install.sh
```

This performs the full bring-up: it validates the package layout, seeds the
scenario/manifest/profile catalog, creates the runtime directories, builds the
framework image, and starts the dashboard container. In other words, `install.sh`
runs sections 4 and 5 below for you — they document what it does under the hood and
how to run each step standalone (e.g. to rebuild or restart without re-seeding).

```text
var/runs
var/state
var/bundles
```

Optional capabilities (off by default):

```bash
delivery/scripts/install.sh --control-channel   # drive substrate deploy/teardown from the dashboard
delivery/scripts/install.sh --afl                # build the host-side AFL coverage harness
delivery/scripts/install.sh --all                # both of the above
delivery/scripts/install.sh --prepare-only       # seed the catalog only (no build, no start)
```

The script does not install host packages, modify Docker daemon configuration, or start Cardano services.

## 4. Build The Framework Image

Run:

```bash
delivery/scripts/build-image.sh
```

This builds:

```text
dwarf/framework:current
```

The build uses:

```text
infrastructure/docker/dwarf-fw.Dockerfile
```

The Dockerfile uses Debian snapshot repositories for reproducibility. The build includes a regression check ensuring expired snapshot Release files do not break the build:

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

Expected inventory:

```text
Profiles: 13
Evidence packages: 4
Smoke tests: 5
Fuzz tests: 0
Scenario catalog: 239 scenarios
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

The dashboard should render the Operate page and the Scenarios page should show the scenario catalog.

For a recipient-facing visual overview of the expected graphical user interface (GUI), open:

```text
docs/index.html
```

That HyperText Markup Language (HTML) file is self-contained and is the GitHub Pages entry point for the delivery overview, so it can be viewed offline after the package is unpacked or hosted from the repository's `docs/` directory.

## Configuration

Override deployment defaults with environment variables:

```bash
export DWARF_IMAGE=dwarf/framework:current
export DWARF_CONTAINER_NAME=dwarf-fw
export DWARF_DASHBOARD_BIND=0.0.0.0
export DWARF_DASHBOARD_PORT=8787
export DWARF_RUNTIME_ROOT=/absolute/path/to/var
export ADA2_DWARF_TOKEN=dwarf
```

Most operators only need `DWARF_DASHBOARD_PORT`. Set `DWARF_DASHBOARD_BIND=127.0.0.1` only when you want loopback-only access.

## Moog, GitHub, And Antithesis Setup Values

The dashboard exposes a browser-based setup form at:

```text
/operate/config
```

Values can be entered through that form and saved into `var/state/config.yaml`, or provided as Docker environment variables at startup. The delivery scripts source a package-local `.env` file before running Compose, and the Compose file passes through Moog/GitHub/Antithesis variables including:

```bash
MOOG_GITHUB_USER=
MOOG_GITHUB_REPO=
MOOG_GITHUB_PAT=
MOOG_TARGET_DIRECTORY=
MOOG_TARGET_COMMIT=
MOOG_ANTITHESIS_LAUNCH_URL=
MOOG_ANTITHESIS_USER=
MOOG_ANTITHESIS_PASSWORD=
MOOG_REGISTRY=
MOOG_ANTITHESIS_API_KEY=
MOOG_AGENT_EMAIL_USER=
MOOG_AGENT_EMAIL_PASSWORD=
```

Environment values take display precedence over saved config. Secret fields are masked after save; leave a secret field blank in the form to keep the current saved value. In this client-prep mode, PATs and Antithesis credentials may be saved, so treat `var/state/config.yaml` as private.

## Optional Moog Bootstrap

Moog setup is not automatic. The delivery scripts deploy Dwarf by default and leave Moog binaries, wallets, PATs, Antithesis credentials, and services untouched.

To preview the Moog bootstrap plan from inside the running Dwarf container:

```bash
DWARF_MOOG_BOOTSTRAP=plan delivery/scripts/deploy.sh
```

To apply the safe skeleton setup, both variables are required:

```bash
DWARF_MOOG_BOOTSTRAP=approve \
DWARF_MOOG_BOOTSTRAP_APPROVE=1 \
delivery/scripts/deploy.sh
```

The approved path creates only Moog deploy/state/ops and requester/oracle secret directories, then writes an operator plan file. It does not download Moog release artifacts, create wallet files, read wallet JSON, store GitHub PATs, write Antithesis credentials, enable services, or start oracle/agent processes.

After any bootstrap or manual Moog change, run:

```bash
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog healthcheck --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile wallet healthcheck moog-requester --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog readiness --repo <org/repo> --github-user <user> --json
docker exec dwarf-fw /home/dwarf/dwarf-fw/dwarf/cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json
```

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
