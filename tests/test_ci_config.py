"""Checks for repository CI/CD configuration.

The original template was copied from another project; these tests pin
the configuration to what this repository actually is (default branch
'main', Ghost base image pin, no leftovers from the copied Obsidian
template).

Every test follows Given-When-Then.
"""

import json
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
    from_match = __import__("re").search(r"FROM (\S+)", dockerfile)
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
