# Node 22 from the official tarball, hash-verified, per architecture. This
# stage is the only place the tarball is downloaded; the other stages copy
# from it. The checksum comes from the release's own SHASUMS256.txt rather
# than a hardcoded per-architecture hash, so a Renovate version bump needs
# no hash edits. The trixie nodejs package (20.x) is too old: ghost-cli
# 1.34.0 requires ^22.13.0 || ^24.0.0, and Ghost 6.73.0 requires
# ^22.23.1 || ^24.20.0 (v22.23.4 is the pinned 22 LTS patch).
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

COPY --from=node /usr/local /usr/local

# Pinned Ghost CLI and pnpm for reproducible builds (managed by Renovate).
# Ghost 6's release carries a pnpm lockfile, so ghost-cli installs it with
# pnpm and checks at install time that pnpm (or corepack) is available.
# Ghost pins pnpm 12 in its package; installing the same version here avoids
# corepack's package-signing key drift and an extra download. Both stay in
# this discarded stage.
RUN npm install -g --no-audit --no-fund ghost-cli@1.34.0 pnpm@12.10.1

# Ghost CLI refuses to run as root, and its install target must be an
# empty directory owned by the non-root user.
RUN useradd --create-home --shell /bin/sh ghost && install -d -o ghost -g ghost /opt/ghost

# Install the pinned Ghost application into the image. The container runs
# this tree directly at runtime; the persisted volume holds only the
# user's content and config, so a fresh container starts with no
# download and add-on updates ship new Ghost versions.
RUN runuser -u ghost -- ghost install 6.73.0 local --dir /opt/ghost --no-start --no-setup

# Ghost 6 depends on better-sqlite3 (an optional dependency whose install
# script downloads a prebuilt binary; the release's pnpm workspace allows
# that build script), so no Python and no C toolchain are needed. Verify
# the module loads before handover: a missing prebuild must fail the image
# build instead of crash-looping Ghost at boot.
#
# pnpm keeps its content-addressable store inside /opt/ghost (so it can
# hardlink into node_modules); node_modules keeps those hardlinks, so the
# store is a build artifact and is removed before the stage is handed over.
RUN set -e; \
    rm -rf /opt/ghost/.pnpm-store; \
    runuser -u ghost -- node -e "require('/opt/ghost/current/node_modules/better-sqlite3')"

# =============================================================================
# Final image: the Node binary plus the installed Ghost application. No
# Python, no Ghost CLI, no npm or pnpm, no package-install layers at all.
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
