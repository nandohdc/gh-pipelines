#!/usr/bin/env python3
"""Unit and contract tests for scripts/canary_probe.py and integration-smoke."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import canary_probe  # type: ignore[attr-defined]


def _write_workflow(consumer_dir: Path, name: str, content: str) -> Path:
    workflows_dir = consumer_dir / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    path = workflows_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def test_rewrites_active_job_and_step_references_preserving_comments(tmp_path: Path) -> None:
    wf = _write_workflow(
        tmp_path,
        "ci.yml",
        """# Leading workflow comment
name: CI
on: [push]
jobs:
  # Commented out job must stay untouched:
  # old-sonar:
  #   uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-go.yml@v1
  go-ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1.9.0 # keep inline comment
    with:
      go-version: "1.26.2"
  helper:
    runs-on: ubuntu-latest
    env:
      SAMPLE_TEXT: "nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"
    steps:
      - uses: actions/checkout@v7
      # - uses: nandohdc/gh-pipelines/actions/setup-node@v1
      - name: Setup Go via composite action
        uses: "nandohdc/gh-pipelines/actions/setup-go@v1"
      - name: Unrelated shell text mentioning uses and sonar
        run: |
          echo "uses: nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"
""",
    )

    result = canary_probe.probe_consumer(tmp_path, target_ref="main")

    assert result.rewritten_refs == 2
    assert result.active_gh_pipelines_refs == [
        "nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1.9.0",
        "nandohdc/gh-pipelines/actions/setup-go@v1",
    ]
    assert result.active_sonar_workflows == []

    updated = wf.read_text(encoding="utf-8")
    assert "uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@main # keep inline comment" in updated
    assert 'uses: "nandohdc/gh-pipelines/actions/setup-go@main"' in updated
    # Comments and unrelated string literals remain byte-for-byte untouched:
    assert "#   uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-go.yml@v1" in updated
    assert "# - uses: nandohdc/gh-pipelines/actions/setup-node@v1" in updated
    assert 'SAMPLE_TEXT: "nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"' in updated
    assert 'echo "uses: nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"' in updated


def test_detects_active_sonar_invocations_and_ignores_comments_or_samples(tmp_path: Path) -> None:
    wf = _write_workflow(
        tmp_path,
        "quality.yaml",
        """name: Quality
on: [pull_request]
jobs:
  # ci-commented:
  #   uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-python.yml@v1
  scan:
    uses: nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1
    with:
      sonar-scanner-host-url: https://sonar.example.invalid
""",
    )

    result = canary_probe.probe_consumer(tmp_path, target_ref="main", enforce_sonar=True)
    assert result.rewritten_refs == 1
    assert result.active_sonar_workflows == ["nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"]
    assert "uses: nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@main" in wf.read_text(encoding="utf-8")


def test_rejects_commented_only_or_text_only_gh_pipelines_references(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "ci.yml",
        """name: Commented Only
on: [push]
jobs:
  local:
    runs-on: ubuntu-latest
    # uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1
    steps:
      - run: |
          echo "nandohdc/gh-pipelines/.github/workflows/sonar-scan.yml@v1"
""",
    )

    with pytest.raises(canary_probe.CanaryProbeError, match="No active nandohdc/gh-pipelines"):
        canary_probe.probe_consumer(tmp_path, target_ref="main")


def test_rejects_malformed_yaml_in_workflows_directory(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "broken.yml",
        """name: Broken
on: [push
jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-go.yml@v1
""",
    )

    with pytest.raises(canary_probe.CanaryProbeError, match="Failed to parse YAML"):
        canary_probe.probe_consumer(tmp_path, target_ref="main")


def test_sonar_enforcement_modes_and_exemption_file(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "ci.yml",
        """name: CI
on: [push]
jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-node.yml@v1
""",
    )

    # Default enforce_sonar=False succeeds during deprecation even without .github/SONAR_EXEMPT
    relaxed = canary_probe.probe_consumer(tmp_path, target_ref="main", enforce_sonar=False)
    assert relaxed.active_sonar_workflows == []
    assert relaxed.sonar_exempt is False

    # Opt-in enforce_sonar=True fails when no active Sonar workflow and no exemption exist
    with pytest.raises(canary_probe.CanaryProbeError, match="No active Sonar"):
        canary_probe.probe_consumer(tmp_path, target_ref="main", enforce_sonar=True)

    # Commented-only Sonar still fails enforce_sonar=True when not exempt
    _write_workflow(
        tmp_path,
        "ci.yml",
        """name: CI
on: [push]
jobs:
  # old:
  #   uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-node.yml@main
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-node.yml@v1
""",
    )
    with pytest.raises(canary_probe.CanaryProbeError, match="No active Sonar"):
        canary_probe.probe_consumer(tmp_path, target_ref="main", enforce_sonar=True)

    # Adding .github/SONAR_EXEMPT satisfies enforce_sonar=True
    (tmp_path / ".github" / "SONAR_EXEMPT").write_text("Migrated off Sonar\n", encoding="utf-8")
    exempted = canary_probe.probe_consumer(tmp_path, target_ref="main", enforce_sonar=True)
    assert exempted.sonar_exempt is True


def test_cli_exit_codes_and_output(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "ci.yml",
        """name: CI
on: [push]
jobs:
  ci:
    uses: nandohdc/gh-pipelines/.github/workflows/ci-with-sonar-go.yml@v1
""",
    )

    ok_proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "canary_probe.py"),
            "--consumer-dir",
            str(tmp_path),
            "--target-ref",
            "main",
            "--enforce-sonar",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok_proc.returncode == 0, ok_proc.stderr
    assert "Rewrote 1 active gh-pipelines reference(s) to @main" in ok_proc.stdout
    assert "Active Sonar workflows: 1" in ok_proc.stdout

    missing_dir = tmp_path / "missing"
    fail_proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "canary_probe.py"),
            "--consumer-dir",
            str(missing_dir),
            "--target-ref",
            "main",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert fail_proc.returncode != 0


def test_integration_smoke_lint_and_docs_wire_canary_probe() -> None:
    smoke = (ROOT / ".github" / "workflows" / "integration-smoke.yml").read_text(encoding="utf-8")
    lint = (ROOT / ".github" / "workflows" / "lint.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "INTEGRATION_CONSUMERS.md").read_text(encoding="utf-8")

    assert "uses: actions/checkout@v7" in smoke
    assert "uses: actions/setup-python@v7" in smoke
    assert "PyYAML==6.0.2" in smoke
    assert "scripts/canary_probe.py" in smoke
    assert "perl " not in smoke
    assert "grep -REn" not in smoke
    assert 'issue_label="drift"' in smoke
    assert '--repo "${REPOSITORY}"' in smoke
    assert smoke.index("scripts/canary_probe.py") < smoke.index("actionlint -shellcheck=")

    assert "python -m pytest scripts/canary_probe_test.py" in lint
    assert "YAML" in docs
    assert "input compatibility" in docs
