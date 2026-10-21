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
    # Given the Dockerfile exists
    # When its FROM line is checked
    # Then it builds on the official hassio debian-base image
    from_lines = [line for line in read_lines() if line.startswith("FROM ")]
    assert len(from_lines) == 1, "exactly one FROM stage expected"
    assert from_lines[0].startswith("FROM ghcr.io/hassio-addons/debian-base:"), from_lines[0]


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


def test_python_present_for_cli_dependency_check():
    # Given ghost-cli 1.33.x refuses to install Ghost 5 on Node 22 without
    # a usable Python (it checks python3, plus setuptools for 3.12+)
    # When the Dockerfile is checked
    # Then the interpreter is installed but no C toolchain is (the sqlite3
    # native module comes as a prebuilt binary, see the add-on cont-init)
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"apt-get install .*python3", content), "python3 must be installed"
    assert "python3-setuptools" in content, "setuptools is required for the trixie Python 3.13"
    assert "build-essential" not in content, (
        "no C toolchain is needed: sqlite3 is installed from its prebuilt binary"
    )


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


def test_node_install_covers_both_arches():
    # Given the add-on is published for amd64 and aarch64
    # When the Dockerfile TARGETARCH handling is checked
    # Then both architectures map to a known tarball (and unknown ones fail)
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "TARGETARCH" in content, "Node download must follow TARGETARCH"
    for arch in ("amd64", "aarch64"):
        assert arch in content, f"missing TARGETARCH case for {arch}"


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
