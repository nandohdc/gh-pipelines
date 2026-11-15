#!/usr/bin/env python3
"""YAML-aware canary probe for active gh-pipelines and Sonar invocations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

import yaml

OWNER_PREFIX = "nandohdc/gh-pipelines/"
SONAR_WORKFLOW_RE = re.compile(
    r"^nandohdc/gh-pipelines/\.github/workflows/(?:ci-with-sonar-[^/@]+\.ya?ml|sonar-scan\.ya?ml)(?:@.*)?$"
)


class CanaryProbeError(RuntimeError):
    """Raised when canary workflow validation fails."""


@dataclass(frozen=True)
class CanaryProbeResult:
    """Summary of active gh-pipelines references discovered and rewritten."""

    workflow_files: list[Path]
    active_gh_pipelines_refs: list[str]
    active_sonar_workflows: list[str]
    rewritten_refs: int
    sonar_exempt: bool


@dataclass(frozen=True)
class _UsesOccurrence:
    start_line: int
    end_line: int
    start_col: int
    value: str


def _mapping_items(node: yaml.MappingNode) -> list[tuple[str, yaml.Node]]:
    items: list[tuple[str, yaml.Node]] = []
    for key_node, val_node in node.value:
        if isinstance(key_node, yaml.ScalarNode):
            items.append((str(key_node.value), val_node))
    return items


def _collect_step_uses(steps_node: yaml.SequenceNode) -> list[_UsesOccurrence]:
    occurrences: list[_UsesOccurrence] = []
    for step_node in steps_node.value:
        if not isinstance(step_node, yaml.MappingNode):
            continue
        for key, val in _mapping_items(step_node):
            if key == "uses" and isinstance(val, yaml.ScalarNode):
                occurrences.append(
                    _UsesOccurrence(
                        start_line=val.start_mark.line,
                        end_line=val.end_mark.line,
                        start_col=val.start_mark.column,
                        value=str(val.value),
                    )
                )
    return occurrences


def _extract_active_uses(text: str, workflow_path: Path) -> list[_UsesOccurrence]:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise CanaryProbeError(f"Failed to parse YAML in {workflow_path}: {exc}") from exc

    if not isinstance(root, yaml.MappingNode):
        raise CanaryProbeError(f"Expected top-level YAML mapping in {workflow_path}")

    occurrences: list[_UsesOccurrence] = []
    for top_key, top_val in _mapping_items(root):
        if top_key != "jobs":
            continue
        if not isinstance(top_val, yaml.MappingNode):
            raise CanaryProbeError(f"Expected 'jobs' mapping in {workflow_path}")
        for _job_id, job_node in _mapping_items(top_val):
            if not isinstance(job_node, yaml.MappingNode):
                continue
            for job_key, job_val in _mapping_items(job_node):
                if job_key == "uses" and isinstance(job_val, yaml.ScalarNode):
                    occurrences.append(
                        _UsesOccurrence(
                            start_line=job_val.start_mark.line,
                            end_line=job_val.end_mark.line,
                            start_col=job_val.start_mark.column,
                            value=str(job_val.value),
                        )
                    )
                elif job_key == "steps" and isinstance(job_val, yaml.SequenceNode):
                    occurrences.extend(_collect_step_uses(job_val))

    return [occ for occ in occurrences if occ.value.startswith(OWNER_PREFIX)]


def _rewrite_target(ref: str, target_ref: str) -> str:
    base = ref.split("@", 1)[0]
    return f"{base}@{target_ref}"


def _rewrite_workflow_file(workflow_path: Path, target_ref: str) -> tuple[list[str], int]:
    original = workflow_path.read_text(encoding="utf-8")
    occurrences = _extract_active_uses(original, workflow_path)
    if not occurrences:
        return [], 0

    lines = original.splitlines(keepends=True)
    rewritten_count = 0
    active_refs: list[str] = []

    for occ in sorted(occurrences, key=lambda item: (item.start_line, item.start_col), reverse=True):
        active_refs.append(occ.value)
        new_ref = _rewrite_target(occ.value, target_ref)
        if occ.start_line == occ.end_line and occ.start_line < len(lines):
            line = lines[occ.start_line]
            idx = line.find(occ.value, occ.start_col)
            if idx != -1:
                lines[occ.start_line] = line[:idx] + new_ref + line[idx + len(occ.value) :]
                rewritten_count += 1
                continue
        raise CanaryProbeError(
            f"Unable to locate active uses reference {occ.value!r} on line {occ.start_line + 1} of {workflow_path}"
        )

    active_refs.reverse()
    updated = "".join(lines)
    if updated != original:
        workflow_path.write_text(updated, encoding="utf-8")
    return active_refs, rewritten_count


def probe_consumer(
    consumer_dir: Path,
    *,
    target_ref: str = "main",
    enforce_sonar: bool = False,
) -> CanaryProbeResult:
    """Validate and rewrite active gh-pipelines references in a consumer checkout."""
    workflows_dir = consumer_dir / ".github" / "workflows"
    if not workflows_dir.is_dir():
        raise CanaryProbeError(f"No .github/workflows directory found in Canary consumer directory: {consumer_dir}")

    workflow_files = sorted(
        path for path in workflows_dir.iterdir() if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    if not workflow_files:
        raise CanaryProbeError(f"No workflow YAML files found under {workflows_dir}")

    active_refs: list[str] = []
    rewritten_total = 0
    for workflow_path in workflow_files:
        refs, rewritten = _rewrite_workflow_file(workflow_path, target_ref)
        active_refs.extend(refs)
        rewritten_total += rewritten

    if not active_refs:
        raise CanaryProbeError(
            "No active nandohdc/gh-pipelines references found in consumer .github/workflows/*.yml|*.yaml."
        )

    active_sonar = [ref for ref in active_refs if SONAR_WORKFLOW_RE.match(ref)]
    sonar_exempt = (consumer_dir / ".github" / "SONAR_EXEMPT").is_file()

    if enforce_sonar and not active_sonar and not sonar_exempt:
        raise CanaryProbeError(
            "No active Sonar workflow invocation found in consumer .github/workflows/*.yml|*.yaml "
            "and .github/SONAR_EXEMPT is absent."
        )

    return CanaryProbeResult(
        workflow_files=workflow_files,
        active_gh_pipelines_refs=active_refs,
        active_sonar_workflows=active_sonar,
        rewritten_refs=rewritten_total,
        sonar_exempt=sonar_exempt,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-dir", required=True, type=Path, help="Path to checked-out canary consumer repo.")
    parser.add_argument("--target-ref", default="main", help="Git ref to pin active gh-pipelines uses references to.")
    parser.add_argument(
        "--enforce-sonar",
        action="store_true",
        help="Fail when no active Sonar workflow is found unless .github/SONAR_EXEMPT exists.",
    )
    args = parser.parse_args(argv)

    try:
        result = probe_consumer(args.consumer_dir, target_ref=args.target_ref, enforce_sonar=args.enforce_sonar)
    except CanaryProbeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(f"Rewrote {result.rewritten_refs} active gh-pipelines reference(s) to @{args.target_ref}.")
    print(f"Active Sonar workflows: {len(result.active_sonar_workflows)}")
    if result.sonar_exempt:
        print("Sonar exemption file .github/SONAR_EXEMPT is present.")
    elif not result.active_sonar_workflows:
        print("No active Sonar workflows found; continuing because Sonar presence is not enforced during deprecation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
