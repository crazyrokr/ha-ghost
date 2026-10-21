FROM ghcr.io/hassio-addons/debian-base:32770e4

COPY root /

# Node 22 from the official tarball, hash-verified, per architecture.
# The trixie nodejs package (20.x) is too old: ghost-cli 1.33.7 pulls an
# execa release that needs Node >= 22.12, and Ghost 5.130.6 supports
# ^18.12.1 || ^20.11.1 || ^22.13.1.
ARG TARGETARCH
ARG NODE_VERSION=v22.23.3
RUN set -e; \
    TARGETARCH="${TARGETARCH:-amd64}"; \
    if [ "${TARGETARCH}" = "amd64" ]; then \
        ARCH=x64; \
        NODE_SHA256=1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af; \
    elif [ "${TARGETARCH}" = "aarch64" ]; then \
        ARCH=arm64; \
        NODE_SHA256=5ced2d48d1d7198739b7f86804de0171aefb6823b684b12341d3321afc3cb0b2; \
    else \
        echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; \
        exit 1; \
    fi; \
    curl -fsSL --retry 3 -o /tmp/node.tar.gz \
        "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-${ARCH}.tar.gz"; \
    echo "${NODE_SHA256}  /tmp/node.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1; \
    rm -f /tmp/node.tar.gz; \
    node --version

# ghost-cli's SQLite3 dependency check requires Python on Node 22 (it
# validates python3, and setuptools separately for Python 3.12+, which
# is what trixie ships). No C toolchain is needed: the sqlite3 native
# module is not compiled, the add-on downloads its prebuilt binary at
# first boot instead (see /etc/cont-init.d/99-ghost).
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-setuptools && rm -rf /var/lib/apt/lists/*

# Pinned Ghost CLI for reproducible builds (managed by Renovate)
RUN npm install -g --no-audit --no-fund ghost-cli@1.33.7

# Ghost CLI refuses to run as root, and the add-on container runs as root:
# a dedicated user owns the blog volume and the long-running process
# (switched with the built-in runuser, no su-exec package needed).
RUN useradd --create-home --shell /bin/sh ghost

LABEL \
  org.opencontainers.image.title="Home Assistant Ghost" \
  org.opencontainers.image.description="Runs Ghost within your Home Assistant setup providing integration of live-features" \
  org.opencontainers.image.source="https://github.com/crazyrokr/ha-ghost/tree/main/ghost" \
  org.opencontainers.image.licenses="MIT"
