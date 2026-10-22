"""Checks for repository CI/CD configuration.

The original template was copied from another project; these tests pin
the configuration to what this repository actually is (default branch
'main', Ghost base image pin, no leftovers from the copied Obsidian
template).

Every test follows Given-When-Then.
"""

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CICD = REPO_ROOT / ".github" / "workflows" / "cicd.yaml"
RENOVATE = REPO_ROOT / ".github" / "renovate.json"
DOCKER_LOCK = REPO_ROOT / ".github" / "docker-lock.json"


def test_cicd_is_valid_yaml():
    # Given the CI workflow file exists
    # When it is parsed
    # Then it is valid YAML with a job definition
    assert CICD.is_file(), f"missing workflow: {CICD}"
    workflow = yaml.safe_load(CICD.read_text(encoding="utf-8"))
    assert workflow.get("jobs"), "workflow must define jobs"


def test_cicd_triggers_on_default_branch():
    # Given the repository default branch is 'main'
    # When the workflow push triggers are checked
    # Then they target 'main' so CI actually runs
    workflow = yaml.safe_load(CICD.read_text(encoding="utf-8"))
    # YAML 1.1 (PyYAML) parses the bare key `on:` as the boolean True
    triggers = workflow.get("on", workflow.get(True, {}))
    push_branches = triggers.get("push", {}).get("branches", [])
    assert "main" in push_branches, f"push must trigger on main, got: {push_branches}"
    assert "master" not in push_branches, "stale 'master' trigger: this repo has no master branch"


def test_docker_lock_pins_the_base_image():
    # Given the add-on builds on the hassio debian-base image
    # When docker-lock.json is checked
    # Then that exact base image reference is digest-pinned
    assert DOCKER_LOCK.is_file(), f"missing lock file: {DOCKER_LOCK}"
    lock = json.loads(DOCKER_LOCK.read_text(encoding="utf-8"))
    dockerfile = (REPO_ROOT / "ghost" / "Dockerfile").read_text(encoding="utf-8")
    from_match = re.search(r"FROM (\S+)", dockerfile)
    assert from_match, "Dockerfile must declare a base image"
    assert from_match.group(1) in lock, (
        f"base image {from_match.group(1)} is not pinned in docker-lock.json"
    )


def test_no_obsidian_template_leftovers():
    # Given the repository template was copied from an Obsidian add-on
    # When the build files are checked
    # Then no Obsidian image references remain anywhere
    for path in (DOCKER_LOCK, RENOVATE, REPO_ROOT / "ghost" / "Dockerfile"):
        content = path.read_text(encoding="utf-8").lower()
        assert "obsidian" not in content, f"stale obsidian reference in {path.name}"


def test_renovate_targets_existing_images():
    # Given renovate.json declares image pinning rules
    # When its matchPackageNames are checked
    # Then they reference image families this repository actually uses
    renovate = json.loads(RENOVATE.read_text(encoding="utf-8"))
    rules = renovate.get("packageRules", [])
    for rule in rules:
        for pattern in rule.get("matchPackageNames", []):
            if "linuxserver" in pattern:
                raise AssertionError(f"stale linuxserver rule: {pattern}")


def dockerfile_pins():
    dockerfile = (REPO_ROOT / "ghost" / "Dockerfile").read_text(encoding="utf-8")
    node = re.search(r"ARG NODE_VERSION=v(\d+\.\d+\.\d+)", dockerfile)
    cli = re.search(r"ghost-cli@(\d+\.\d+\.\d+)", dockerfile)
    app = re.search(r"ghost install (\d+\.\d+\.\d+) local", dockerfile)
    assert node and cli and app, "Dockerfile must pin Node, Ghost CLI and the Ghost app"
    return node.group(1), cli.group(1), app.group(1)


def test_renovate_manages_dockerfile_pins():
    # Given renovate.json declares regex custom managers for the Ghost
    # Dockerfile (Node, Ghost CLI, Ghost application version)
    # When each manager's matchString is run against the actual Dockerfile
    # Then the extracted values equal the real pins, so Renovate updates
    # them in place (the Node pin appears once per build stage)
    renovate = json.loads(RENOVATE.read_text(encoding="utf-8"))
    node_pin, cli_pin, app_pin = dockerfile_pins()
    dockerfile = (REPO_ROOT / "ghost" / "Dockerfile").read_text(encoding="utf-8")

    def managers_for(dep_name, expected_pin):
        matches = [
            m
            for m in renovate.get("customManagers", [])
            if m.get("depNameTemplate") == dep_name
        ]
        assert matches, f"no custom manager manages {dep_name}"
        assert m_files_match_dockerfile(matches), (
            f"{dep_name} manager must target ghost/Dockerfile"
        )
        # The pattern is written in JS syntax (Renovate); translate the
        # named-group syntax for Python's re module.
        pattern = matches[0]["matchStrings"][0].replace("(?<", "(?P<")
        found = [m.group("currentValue") for m in re.finditer(pattern, dockerfile)]
        assert found and set(found) == {expected_pin}, (
            f"{dep_name} matchString {pattern!r} extracts {found}, expected {expected_pin!r}"
        )
        return matches

    def m_files_match_dockerfile(managers):
        return any(
            re.search(p, "ghost/Dockerfile")
            for m in managers
            for p in m.get("managerFilePatterns", [])
        )

    node_manager = managers_for("node", node_pin)
    node_pattern = node_manager[0]["matchStrings"][0].replace("(?<", "(?P<")
    assert len(re.findall(node_pattern, dockerfile)) == 2, (
        "the Node pin appears once per stage; both must be updated together"
    )
    managers_for("ghost-cli", cli_pin)
    managers_for("ghost", app_pin)


def test_renovate_node_rule_is_limited_to_node_22():
    # Given Ghost requires Node 22 LTS
    # When the node package rule is checked
    # Then only 22.x updates are proposed (no jumps to other majors)
    renovate = json.loads(RENOVATE.read_text(encoding="utf-8"))
    rules = [
        r for r in renovate.get("packageRules", []) if "node" in r.get("matchPackageNames", [])
    ]
    assert rules, "no package rule for node"
    for rule in rules:
        assert rule.get("allowedVersions") in ("^22", "22.x", "22.*"), (
            f"node must be limited to the 22 line, got {rule.get('allowedVersions')!r}"
        )


def test_renovate_ghost_app_rule_is_limited_to_ghost_5():
    # Given Ghost 6 requires pnpm 12, which the image cannot provide
    # When the ghost package rule is checked
    # Then only 5.x updates are proposed
    renovate = json.loads(RENOVATE.read_text(encoding="utf-8"))
    rules = [
        r for r in renovate.get("packageRules", []) if "ghost" in r.get("matchPackageNames", [])
    ]
    assert rules, "no package rule for the ghost application"
    for rule in rules:
        assert rule.get("allowedVersions") in ("^5", "5.x", "5.*"), (
            f"the ghost application must be limited to the 5 line, got {rule.get('allowedVersions')!r}"
        )
