ARG PYTHON_BASE=python@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3
FROM ${PYTHON_BASE}

ARG DEBIAN_SNAPSHOT=20260429T000000Z
ARG BASH_VERSION=5.2.37-2+b8
ARG CA_CERTIFICATES_VERSION=20250419
ARG DOCKER_IO_VERSION=26.1.5+dfsg1-9+b12
ARG JQ_VERSION=1.7.1-6+deb13u1
ARG OPENSSH_CLIENT_VERSION=1:10.0p1-7+deb13u2
ARG RSYNC_VERSION=3.4.1+ds1-5+deb13u1
ARG TMUX_VERSION=3.5a-3

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/dwarf \
    ADA2_DWARF_RUNS_DIR=/var/dwarf/runs \
    ADA2_DWARF_STATE_DIR=/var/dwarf/state \
    ADA2_DWARF_BUNDLES_DIR=/var/dwarf/bundles \
    ADA2_PROFILE_MANAGER_CONFIG=/var/dwarf/state/config.yaml

RUN printf 'Acquire::Check-Valid-Until "false";\nAcquire::Check-Date "false";\n' >/etc/apt/apt.conf.d/90snapshot \
    && cat >/etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash=${BASH_VERSION} \
        ca-certificates=${CA_CERTIFICATES_VERSION} \
        docker.io=${DOCKER_IO_VERSION} \
        jq=${JQ_VERSION} \
        openssh-client=${OPENSSH_CLIENT_VERSION} \
        rsync=${RSYNC_VERSION} \
        tmux=${TMUX_VERSION} \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 dwarf \
    && mkdir -p /home/dwarf/.ssh /home/dwarf/dwarf-fw /var/dwarf/runs /var/dwarf/state /var/dwarf/bundles \
    && chmod 700 /home/dwarf/.ssh \
    && chown -R dwarf:dwarf /home/dwarf /var/dwarf

WORKDIR /home/dwarf/dwarf-fw

COPY dwarf/ /home/dwarf/dwarf-fw/dwarf/
COPY infrastructure/docker/dwarf-fw-entrypoint.sh /usr/local/bin/dwarf-fw-entrypoint
COPY infrastructure/docker/requirements-framework.txt /tmp/requirements-framework.txt

RUN chmod +x /usr/local/bin/dwarf-fw-entrypoint \
    && python3 -m pip install --no-compile --require-hashes -r /tmp/requirements-framework.txt \
    && rm /tmp/requirements-framework.txt

USER dwarf

VOLUME ["/var/dwarf/runs", "/var/dwarf/state", "/var/dwarf/bundles"]
EXPOSE 8787

ENTRYPOINT ["/usr/local/bin/dwarf-fw-entrypoint"]
CMD ["dashboard", "serve", "--bind", "0.0.0.0", "--port", "8787"]
