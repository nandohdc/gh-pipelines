#!/usr/bin/env python3
"""Workflow contract tests for clean-runner Node package-manager setup (#189)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, read

SETUP_NODE_ACTION = ROOT / "actions" / "setup-node" / "action.yml"
CI_NODE = ROOT / ".github" / "workflows" / "ci-node.yml"
CI_WITH_SONAR_NODE = ROOT / ".github" / "workflows" / "ci-with-sonar-node.yml"
TEST_CALLERS = ROOT / ".github" / "workflows" / "test-callers.yml"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
USAGE_DOC = ROOT / "docs" / "USAGE.md"
CODEX_CONTEXT = ROOT / ".codex" / "CONTEXT.md"


def _assert_setup_before_corepack_before_cache(
    steps: List[Dict[str, Any]], context: str
) -> None:
    setup_indices: List[int] = []
    cached_indices: List[int] = []
    corepack_indices: List[int] = []

    for idx, step in enumerate(steps):
        uses = str(step.get("uses", ""))
        run = str(step.get("run", ""))
        if uses.startswith("actions/setup-node@"):
            setup_indices.append(idx)
            with_map = step.get("with") or {}
            if "cache" in with_map:
                cached_indices.append(idx)
        if "corepack enable" in run:
            corepack_indices.append(idx)

    assert setup_indices, f"{context}: missing actions/setup-node step"
    assert corepack_indices, f"{context}: missing corepack enable step"
    assert cached_indices, f"{context}: missing cached actions/setup-node step"

    uncached_indices = [idx for idx in setup_indices if idx not in cached_indices]
    assert uncached_indices, (
        f"{context}: must initialize Node without cache before enabling Corepack"
    )

    first_uncached = uncached_indices[0]
    first_corepack = corepack_indices[0]
    first_cached = cached_indices[0]

    assert first_uncached < first_corepack < first_cached, (
        f"{context}: expected step order uncached setup-node ({first_uncached}) "
        f"< corepack enable ({first_corepack}) < cached setup-node ({first_cached})"
    )

    corepack_step = steps[first_corepack]
    assert corepack_step.get("if") == "inputs.package-manager != 'npm'", (
        f"{context}: Corepack step must gate on inputs.package-manager != 'npm'"
    )
    assert "set -euo pipefail" in str(corepack_step.get("run", "")), (
        f"{context}: Corepack step must start with 'set -euo pipefail'"
    )
    assert "COREPACK_ENABLE_DOWNLOAD_PROMPT=0" in str(corepack_step.get("run", "")), (
        f"{context}: Corepack step must export COREPACK_ENABLE_DOWNLOAD_PROMPT=0"
    )


def test_actions_setup_node_enables_corepack_before_cache_step() -> None:
    action = yaml.safe_load(read(SETUP_NODE_ACTION))
    assert "inputs" in action
    # Keep public interface limited to documented inputs without test-only knobs.
    assert set(action["inputs"].keys()) == {"node-version", "package-manager", "cache"}
    steps = action["runs"]["steps"]
    _assert_setup_before_corepack_before_cache(steps, "actions/setup-node/action.yml")

    cached_step = next(
        s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node@") and "cache" in (s.get("with") or {})
    )
    assert cached_step.get("if") == "inputs.cache == 'true'", (
        "Cached setup-node step in action.yml must remain gated on inputs.cache == 'true'"
    )


def test_ci_node_jobs_enable_corepack_before_dependency_cache() -> None:
    workflow = yaml.safe_load(read(CI_NODE))
    jobs = workflow["jobs"]
    assert set(jobs.keys()) == {"lint", "test", "audit"}
    for job_name in ("lint", "test", "audit"):
        _assert_setup_before_corepack_before_cache(
            jobs[job_name]["steps"], f".github/workflows/ci-node.yml:{job_name}"
        )


def test_ci_with_sonar_node_delegates_package_manager_without_uncorepacked_cache() -> None:
    workflow = yaml.safe_load(read(CI_WITH_SONAR_NODE))
    ci_job = workflow["jobs"]["ci"]
    assert ci_job["uses"] == "./.github/workflows/ci-node.yml"
    assert ci_job["with"]["package-manager"] == "${{ inputs.package-manager }}"
    for step in workflow["jobs"]["sonar-scan"]["steps"]:
        if str(step.get("uses", "")).startswith("actions/setup-node@"):
            assert "cache" not in (step.get("with") or {})


def test_clean_environment_missing_tool_prerequisite_and_fixtures() -> None:
    fixtures = [
        ("node", "npm", "package-lock.json"),
        ("node-pnpm", "pnpm", "pnpm-lock.yaml"),
        ("node-yarn", "yarn", "yarn.lock"),
    ]
    for dirname, manager, lockfile in fixtures:
        fixture_dir = ROOT / "tests" / "fixtures" / dirname
        pkg_path = fixture_dir / "package.json"
        lock_path = fixture_dir / lockfile
        assert pkg_path.is_file(), f"Missing fixture package.json: {pkg_path}"
        assert lock_path.is_file(), f"Missing fixture lockfile: {lock_path}"
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        assert "lint" in pkg.get("scripts", {}), f"{dirname}: missing lint script"
        assert "test" in pkg.get("scripts", {}), f"{dirname}: missing test script"
        if manager != "npm":
            assert str(pkg.get("packageManager", "")).startswith(f"{manager}@"), (
                f"{dirname}: packageManager must pin {manager}@<version>"
            )

    # Verify explicitly on an isolated PATH without global pnpm/yarn that invoking
    # pnpm or yarn fails prior to Corepack activation.
    with tempfile.TemporaryDirectory() as empty_bin:
        for tool in ("pnpm", "yarn"):
            proc = subprocess.run(
                ["/bin/sh", "-c", f"{tool} --version"],
                env={"PATH": empty_bin},
                capture_output=True,
                text=True,
            )
            assert proc.returncode != 0, (
                f"Expected {tool} to be unavailable in clean PATH {empty_bin}"
            )


def test_lint_test_callers_and_docs_cover_clean_runner_node_setup() -> None:
    lint = read(LINT_WORKFLOW)
    assert_contains(
        lint,
        "python scripts/node_setup_workflow_contract_test.py",
        "lint.yml must execute node_setup_workflow_contract_test.py",
    )

    test_callers = yaml.safe_load(read(TEST_CALLERS))
    jobs = test_callers["jobs"]
    for required_job in (
        "test-ci-node",
        "test-ci-node-pnpm",
        "test-ci-node-yarn",
        "test-setup-node-clean-runner",
    ):
        assert required_job in jobs, f"test-callers.yml missing job: {required_job}"

    assert jobs["test-ci-node"]["with"]["package-manager"] == "npm"
    assert jobs["test-ci-node-pnpm"]["with"]["package-manager"] == "pnpm"
    assert jobs["test-ci-node-yarn"]["with"]["package-manager"] == "yarn"

    usage = read(USAGE_DOC)
    assert_contains(usage, "corepack", "docs/USAGE.md must document Corepack activation")
    codex = read(CODEX_CONTEXT)
    assert_contains(codex, "corepack", ".codex/CONTEXT.md must document Corepack activation")


def main() -> int:
    test_actions_setup_node_enables_corepack_before_cache_step()
    test_ci_node_jobs_enable_corepack_before_dependency_cache()
    test_ci_with_sonar_node_delegates_package_manager_without_uncorepacked_cache()
    test_clean_environment_missing_tool_prerequisite_and_fixtures()
    test_lint_test_callers_and_docs_cover_clean_runner_node_setup()
    print("Node setup workflow contract tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
