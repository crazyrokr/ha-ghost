"""Structural checks for the add-on Dockerfile.

Guards against the defects found in the original template: unpinned
packages, a COPY of a directory that does not exist, and missing
build context files.

Every test follows Given-When-Then.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO_ROOT / "ghost"
DOCKERFILE = ADDON_DIR / "Dockerfile"


def read_lines():
    return DOCKERFILE.read_text(encoding="utf-8").splitlines()


def test_dockerfile_exists():
    # Given the add-on directory exists
    # When checked for a Dockerfile
    # Then the Dockerfile is present
    assert DOCKERFILE.is_file(), f"missing Dockerfile: {DOCKERFILE}"


def test_base_image_is_hassio_debian_base():
    # Given the Dockerfile uses a multi-stage build
    # When its FROM lines are checked
    # Then every stage builds on the official hassio debian-base image
    from_lines = [line for line in read_lines() if line.startswith("FROM ")]
    assert from_lines, "Dockerfile must declare at least one stage"
    for line in from_lines:
        assert line.startswith("FROM ghcr.io/hassio-addons/debian-base:"), line


def final_stage():
    # The last FROM starts the final image; only that stage's instructions
    # end up in the published image (the earlier stages are discarded).
    lines = read_lines()
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    return "\n".join(lines[last_from:])


def test_copy_root_matches_build_context():
    # Given the Dockerfile copies the root directory (s6 services)
    # When the build context is checked
    # Then ghost/root exists so the COPY cannot fail
    copy_lines = [line for line in read_lines() if line.startswith("COPY root ")]
    assert copy_lines, "Dockerfile must copy the root/ service directory"
    assert (ADDON_DIR / "root").is_dir(), "ghost/root is missing but COPY root / references it"


def test_ghost_cli_is_pinned_not_latest():
    # Given the Dockerfile installs the Ghost CLI
    # When its version reference is checked
    # Then it is pinned to an explicit version (reproducible builds;
    # '@latest' would silently change the add-on on every rebuild)
    npm_lines = [line for line in read_lines() if "npm install" in line and "ghost-cli" in line]
    assert npm_lines, "Dockerfile must install ghost-cli"
    for line in npm_lines:
        assert "@latest" not in line, f"ghost-cli must not be unpinned: {line}"
        assert re.search(r"ghost-cli@\d+\.\d+\.\d+", line), f"ghost-cli version not pinned: {line}"


def test_python_installed_in_build_stage_only():
    # Given ghost-cli 1.33.x refuses to install Ghost 5 on Node 22 without
    # a usable Python (it checks python3, plus setuptools for 3.12+)
    # When the Dockerfile is checked
    # Then Python is installed in the (discarded) build stage only: the
    # final image carries no Python at all
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"apt-get install .*python3", content), "python3 must be installed (build stage)"
    assert "python3-setuptools" in content, "setuptools is required for the trixie Python 3.13"
    assert "apt-get" not in final_stage(), (
        "the final image must not install any packages (no Python)"
    )


def test_no_c_toolchain_anywhere():
    # Given the sqlite3 native module is used as a prebuilt binary
    # (downloaded at build time, never compiled)
    # When the Dockerfile is checked
    # Then no C toolchain package appears in any stage
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "build-essential" not in content and "build-base" not in content, (
        "no C toolchain is needed: sqlite3 is installed from its prebuilt binary"
    )


def test_ghost_app_is_installed_at_build_time():
    # Given the add-on ships the Ghost application in the image (so a fresh
    # container starts without any download, and no Python is needed at
    # runtime)
    # When the Dockerfile is checked
    # Then the pinned Ghost version is installed into /opt/ghost in a
    # discarded stage and handed over to the final image
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"ghost install \d+\.\d+\.\d+ local", content), (
        "the Ghost app must be installed at build time"
    )
    assert "--dir /opt/ghost" in content, "the app must be installed to /opt/ghost"
    assert "COPY --from=" in content, "the app must be copied into the final stage"
    assert "ghost install" not in final_stage(), (
        "the app must not be (re)installed in the final image"
    )
    assert "npm install" not in final_stage() or "sqlite3" in final_stage(), (
        "no runtime package installs in the final image"
    )


def test_build_time_sqlite3_staging_not_in_tmp():
    # Given the build-time heal runs 'prebuild-install' from the staging
    # directory (an executed file), mirroring the runtime lesson that
    # executed files must not live in /tmp
    # When the Dockerfile staging default is checked
    # Then it stages inside the app directory, not in /tmp
    content = DOCKERFILE.read_text(encoding="utf-8")
    staging_lines = [line for line in content.splitlines() if "sqlite3-staging" in line]
    assert staging_lines, "the build-time heal must use a named staging directory"
    default = staging_lines[0].split("sqlite3-staging", 1)[0]
    assert "/opt/ghost" in default and "/tmp" not in default, staging_lines[0]


def test_node_is_pinned_and_hash_verified():
    # Given the add-on needs a specific Node major (the trixie nodejs 20.x
    # package is too old for the pinned ghost-cli)
    # When the Dockerfile is checked
    # Then Node is installed from the official tarball with an explicit
    # version and a sha256 verification step
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"NODE_VERSION=v22\.\d+\.\d+", content), "Node version must be pinned"
    assert "nodejs.org/dist" in content, "Node must come from the official tarballs"
    assert "sha256sum -c" in content, "the Node tarball must be hash-verified"


def node_stage():
    # Everything between the shared node stage's header and the next FROM.
    content = DOCKERFILE.read_text(encoding="utf-8")
    return content.split(" AS node", 1)[1].split("\nFROM ", 1)[0]


def test_node_checksum_is_downloaded_from_the_dist_repository():
    # Given nodejs.org publishes SHASUMS256.txt alongside every release
    # When the node stage is checked
    # Then the checksum file is downloaded from the same versioned dist
    # directory as the tarball, so a Renovate Node bump needs no hash edits
    stage = node_stage()
    assert 'DIST="https://nodejs.org/dist/${NODE_VERSION}"' in stage, (
        "the checksum must come from the versioned nodejs.org dist directory"
    )
    assert '"${DIST}/SHASUMS256.txt"' in stage, "SHASUMS256.txt must be downloaded"


def test_node_checksum_is_matched_to_the_arch_tarball():
    # Given SHASUMS256.txt lists every artifact of the release
    # When the node stage selects its checksum line
    # Then it selects the exact architecture tarball from the pinned
    # version (an unanchored match could verify a different artifact)
    stage = node_stage()
    assert 'TARBALL="node-${NODE_VERSION}-linux-${ARCH}.tar.gz"' in stage, stage
    assert 'grep "  ${TARBALL}\\$" SHASUMS256.txt' in stage, stage
    assert "sha256sum -c -" in stage, stage


def test_node_stage_hardcodes_no_sha256():
    # Given the checksums now come from the release's SHASUMS256.txt
    # When the node stage is scanned for digests
    # Then no hardcoded sha256 remains: a pinned hash goes stale on every
    # Node update and must be refreshed by hand
    stage = node_stage()
    assert not re.search(r"\b[0-9a-f]{64}\b", stage), (
        "the node stage must not hardcode a sha256 digest"
    )
    assert "NODE_SHA256" not in stage, "no per-architecture Node hash should remain"


def test_node_install_covers_both_arches():
    # Given the add-on is published for amd64 and aarch64
    # When the Dockerfile BUILD_ARCH handling is checked
    # Then both architectures map to a known tarball (and unknown ones fail)
    # BUILD_ARCH is the build arg the Home Assistant builder passes
    # explicitly (build-image action); TARGETARCH is not part of its contract
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "BUILD_ARCH" in content, "Node download must follow BUILD_ARCH"
    assert "TARGETARCH" not in content, "the HA builder provides BUILD_ARCH, not TARGETARCH"
    for arch in ("amd64", "aarch64"):
        assert arch in content, f"missing BUILD_ARCH case for {arch}"


def test_non_root_user_is_created():
    # Given the Ghost CLI refuses to run as root, while the add-on
    # container runs as root
    # When the Dockerfile is checked
    # Then a dedicated ghost user is created for the blog and the service
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"useradd .*ghost\b", content), "a ghost user must be created"


def test_opencontainers_labels_present():
    # Given the Dockerfile exists
    # When its LABEL metadata is checked
    # Then the standard opencontainers labels are present
    content = DOCKERFILE.read_text(encoding="utf-8")
    for label in (
        "org.opencontainers.image.title",
        "org.opencontainers.image.description",
        "org.opencontainers.image.source",
        "org.opencontainers.image.licenses",
    ):
        assert label in content, f"missing label: {label}"
