# Node 22 from the official tarball, hash-verified, per architecture. This
# stage is the only place the tarball is downloaded; the other stages copy
# from it. The checksum comes from the release's own SHASUMS256.txt rather
# than a hardcoded per-architecture hash, so a Renovate version bump needs
# no hash edits. The trixie nodejs package (20.x) is too old: ghost-cli
# 1.33.7 pulls an execa release that needs Node >= 22.12, and Ghost 5.130.6
# supports ^18.12.1 || ^20.11.1 || ^22.13.1.
FROM ghcr.io/hassio-addons/debian-base:32770e4@sha256:c16befd0489d0f873f66b41c1d028b8978dea70c93b606e9b1cf237c17b7abca AS node

ARG BUILD_ARCH
ARG NODE_VERSION=v22.23.4
RUN set -e; \
    BUILD_ARCH="${BUILD_ARCH:-amd64}"; \
    if [ "${BUILD_ARCH}" = "amd64" ]; then \
        ARCH=x64; \
    elif [ "${BUILD_ARCH}" = "aarch64" ]; then \
        ARCH=arm64; \
    else \
        echo "unsupported BUILD_ARCH: ${BUILD_ARCH}" >&2; \
        exit 1; \
    fi; \
    TARBALL="node-${NODE_VERSION}-linux-${ARCH}.tar.gz"; \
    DIST="https://nodejs.org/dist/${NODE_VERSION}"; \
    cd /tmp; \
    curl -fsSL --retry 3 -o "${TARBALL}" "${DIST}/${TARBALL}"; \
    curl -fsSL --retry 3 -o SHASUMS256.txt "${DIST}/SHASUMS256.txt"; \
    CHECKSUM="$(grep "  ${TARBALL}\$" SHASUMS256.txt)" || { \
        echo "no sha256 entry for ${TARBALL} in SHASUMS256.txt" >&2; \
        exit 1; \
    }; \
    echo "${CHECKSUM}" | sha256sum -c -; \
    tar -xzf "${TARBALL}" -C /usr/local --strip-components=1; \
    rm -f "${TARBALL}" SHASUMS256.txt; \
    node --version

FROM ghcr.io/hassio-addons/debian-base:32770e4@sha256:c16befd0489d0f873f66b41c1d028b8978dea70c93b606e9b1cf237c17b7abca AS app

# ghost-cli's SQLite3 dependency check requires Python on Node 22 (it
# validates python3, and setuptools separately for Python 3.13, which is
# what trixie ships). Python stays in this stage only: the stage is
# discarded once the application is installed, so the published image
# carries no Python and no package-install layer.
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-setuptools && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local /usr/local

# Pinned Ghost CLI for reproducible builds (managed by Renovate)
RUN npm install -g --no-audit --no-fund ghost-cli@1.34.0

# Ghost CLI refuses to run as root, and its install target must be an
# empty directory owned by the non-root user.
RUN useradd --create-home --shell /bin/sh ghost && install -d -o ghost -g ghost /opt/ghost

# Install the pinned Ghost application into the image. The container runs
# this tree directly at runtime; the persisted volume holds only the
# user's content and config, so a fresh container starts with no
# download and add-on updates ship new Ghost versions.
RUN runuser -u ghost -- ghost install 5.130.6 local --dir /opt/ghost --no-start --no-setup --db client --port 2368 --ip 0.0.0.0

# Ghost's own install (run through yarn) pins prebuild-install 7.1.1,
# which cannot resolve its N-API target on Node 22.12+ and therefore
# silently drops the sqlite3 optionalDependency. A plain npm install of
# the same package resolves a current prebuild-install that downloads
# the prebuilt binary instead of compiling it, so no C toolchain is
# needed in any stage. The staging directory lives in the app directory,
# not in /tmp, and is removed before the stage is handed over.
RUN set -e; \
    STAGING=/opt/ghost/.sqlite3-staging; \
    install -d -o ghost -g ghost "$STAGING"; \
    runuser -u ghost -- npm install --prefix "$STAGING" sqlite3@5.1.7 --no-audit --no-fund --loglevel=error --no-update-notifier; \
    rm -rf /opt/ghost/current/node_modules/sqlite3; \
    mv "$STAGING/node_modules/sqlite3" /opt/ghost/current/node_modules/sqlite3; \
    rm -rf "$STAGING"; \
    runuser -u ghost -- node -e "require('/opt/ghost/current/node_modules/sqlite3')"

# =============================================================================
# Final image: the Node binary plus the installed Ghost application. No
# Python, no Ghost CLI, no npm, no package-install layers at all.
# =============================================================================
FROM ghcr.io/hassio-addons/debian-base:32770e4@sha256:c16befd0489d0f873f66b41c1d028b8978dea70c93b606e9b1cf237c17b7abca

# Only the node binary runs at runtime; npm, corepack, the headers and
# the share tree are build-time only and stay in the discarded stages.
COPY --from=node /usr/local/bin/node /usr/local/bin/node

# The blog runs as a dedicated user (the container runs as root): the
# user owns the persisted volume; the application in the image is
# handed over already owned by the same user.
RUN useradd --create-home --shell /bin/sh ghost

COPY --from=app --chown=ghost:ghost /opt/ghost /opt/ghost

COPY root /

LABEL \
  org.opencontainers.image.title="Home Assistant Ghost" \
  org.opencontainers.image.description="Runs Ghost within your Home Assistant setup providing integration of live-features" \
  org.opencontainers.image.source="https://github.com/crazyrokr/ha-ghost/tree/main/ghost" \
  org.opencontainers.image.licenses="MIT"
