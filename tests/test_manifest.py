"""Validate the add-on manifest the way hassfest does.

These checks mirror the hassfest add-on validation (required fields,
slug/directory match, image reference, arch list, map/ports/ingress
consistency). The real hassfest still runs in the repository CI; this
suite makes the same guarantees available locally.

Every test follows Given-When-Then.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO_ROOT / "ghost"
MANIFEST = ADDON_DIR / "config.yaml"

VALID_ARCHES = {"amd64", "aarch64", "armv7", "armhf", "i386"}
VALID_MAP_TYPES = {"addon_config", "addon_data", "share", "ssl"}
VALID_STARTUP = {"application", "services"}
VALID_BOOT = {"auto", "manual"}


@pytest.fixture(name="manifest")
def manifest_fixture():
    assert MANIFEST.is_file(), f"missing manifest: {MANIFEST}"
    with MANIFEST.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_manifest_parses_as_mapping(manifest):
    # Given the add-on manifest file exists
    # When it is parsed as YAML
    # Then it yields a non-empty mapping
    assert isinstance(manifest, dict)
    assert manifest


def test_required_fields_present(manifest):
    # Given the manifest (hassfest requires these fields)
    # When checked against the required field list
    # Then every required field is present and non-empty
    for field in ("name", "version", "slug", "description", "image", "arch"):
        assert manifest.get(field) not in (None, ""), f"missing or empty field: {field}"


def test_slug_matches_addon_directory(manifest):
    # Given the manifest declares a slug
    # When compared with the add-on folder name
    # Then they match (hassfest rejects the repo otherwise)
    assert manifest["slug"] == ADDON_DIR.name


def test_image_has_no_unknown_placeholders(manifest):
    # Given the manifest declares an image: a plain multi-arch reference,
    # or an {arch} template (the Supervisor substitutes known placeholders)
    # When its placeholders are inspected
    # Then no placeholder other than {arch} appears
    placeholders = set(re.findall(r"\{[^}]+\}", manifest["image"]))
    assert placeholders <= {"{arch}"}, f"unknown image placeholders: {sorted(placeholders)}"


def test_image_template_is_well_formed(manifest):
    # Given the manifest declares an image
    # When it is rendered for each supported arch (the {arch} placeholder
    # is optional; without it one multi-arch image serves every arch)
    # Then the result looks like a docker image reference
    for arch in manifest["arch"]:
        rendered = manifest["image"].replace("{arch}", arch)
        assert "://" not in rendered, "image reference must not contain a scheme"
        assert "/" in rendered, "image reference must contain a repository path"


def test_arch_list_is_non_empty_and_known(manifest):
    # Given the manifest declares arches
    # When each entry is checked
    # Then the list is non-empty and contains only known arches
    assert manifest["arch"]
    unknown = set(manifest["arch"]) - VALID_ARCHES
    assert not unknown, f"unknown arches: {sorted(unknown)}"


def test_version_is_a_string(manifest):
    # Given the manifest declares a version
    # When its type is checked
    # Then it is a string (hassfest rejects numeric versions)
    assert isinstance(manifest["version"], str)


def test_ingress_port_consistent(manifest):
    # Given the add-on exposes its web UI through ingress
    # When the ingress_port is checked
    # Then it is an integer within the valid port range
    if manifest.get("ingress") is True:
        port = manifest.get("ingress_port")
        assert isinstance(port, int), "ingress_port must be an integer when ingress is true"
        assert 0 < port < 65536


def test_exposed_ports_are_well_formed(manifest):
    # Given the manifest declares exposed ports
    # When each key/value is checked
    # Then keys match 'port' or 'port/proto' and values are int or null
    for key, value in manifest.get("ports", {}).items():
        port_part = key.split("/", 1)[0]
        assert port_part.isdigit(), f"bad port key: {key}"
        assert 0 < int(port_part) < 65536
        assert value is None or isinstance(value, int)


def test_webui_placeholder_format(manifest):
    # Given the manifest declares a webui URL
    # When its placeholders are checked
    # Then it uses the [HOST]/[PORT] template form
    webui = manifest.get("webui")
    if webui:
        assert webui.startswith(("http://", "https://"))
        if "PORT" in webui:
            assert "[PORT" in webui and "]" in webui, f"malformed PORT placeholder: {webui}"
            assert "[HOST]" in webui, "webui with a port must reference [HOST]"


def test_map_entries_are_valid(manifest):
    # Given the manifest declares volume maps
    # When each entry is checked
    # Then type is known, path is absolute, read_only is a boolean
    assert manifest.get("map"), "add-on must map its config directory"
    for entry in manifest["map"]:
        assert entry["type"] in VALID_MAP_TYPES, f"unknown map type: {entry['type']}"
        assert entry["path"].startswith("/"), f"map path must be absolute: {entry['path']}"
        if "read_only" in entry:
            assert isinstance(entry["read_only"], bool)


def test_config_directory_is_mapped_read_write():
    # Given Ghost stores its content in the add-on config directory
    # When the map entries are checked
    # Then addon_config is mapped to /config and writable
    with MANIFEST.open(encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle)
    config_maps = [
        entry
        for entry in manifest["map"]
        if entry["type"] == "addon_config" and entry["path"] == "/config"
    ]
    assert config_maps, "addon_config must be mapped to /config"
    for entry in config_maps:
        assert entry.get("read_only", False) is False, "Ghost needs write access to /config"


def test_panel_icon_is_material_design_icon(manifest):
    # Given the manifest declares a panel icon
    # When its prefix is checked
    # Then it is a material design icon name
    icon = manifest.get("panel_icon")
    if icon:
        assert icon.startswith("mdi:"), f"panel_icon must be an mdi icon: {icon}"


def test_options_and_schema_keys_agree(manifest):
    # Given the manifest declares options and a schema
    # When their key sets are compared
    # Then every schema key names a declared option (and vice versa)
    options = manifest.get("options", {})
    schema = manifest.get("schema", {})
    assert set(schema) <= set(options), "schema keys must exist in options"
    assert set(options) <= set(schema), "options keys must exist in schema"
