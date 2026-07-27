#!/usr/bin/env python3
"""Static contract tests for SonarQube consumer documentation.

These tests intentionally avoid contacting GitHub, SonarQube, Doppler, or any
network endpoint. They assert that docs and examples teach the scanner/public
URL split and local ARC availability semantics consumed by the reusable
SonarQube workflows.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.contract_lib import assert_contains, assert_not_contains, read
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "USAGE.md"
INTEGRATION_CONSUMERS = ROOT / "docs" / "INTEGRATION_CONSUMERS.md"
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint.yml"
EXAMPLES = [
    ROOT / "examples" / "ci-with-sonar-go.yml",
    ROOT / "examples" / "ci-with-sonar-node.yml",
    ROOT / "examples" / "ci-with-sonar-python.yml",
    ROOT / "examples" / "sonar-scan.yml",
]
DOCS = [README, USAGE, INTEGRATION_CONSUMERS]
LOCAL_KIND_SCANNER_URL = "http://sonarqube.sonarqube.svc.cluster.local:9000"


def test_docs_distinguish_scanner_public_legacy_and_runner_contract() -> None:
    for path in DOCS:
        text = read(path)
        for expected in (
            "SONAR_SCANNER_HOST_URL",
            "SONAR_PUBLIC_BASE_URL",
            "SONAR_HOST_URL",
            "RUNNER_LABEL",
            "runner-label",
            LOCAL_KIND_SCANNER_URL,
        ):
            assert_contains(text, expected, f"{path} missing Sonar URL/runner contract text: {expected}")

        lower = text.lower()
        assert_contains(lower, "scanner", f"{path} must identify the scanner server URL role")
        assert_contains(lower, "quality-gate", f"{path} must identify quality-gate scanner usage")
        assert_contains(lower, "public", f"{path} must identify the public base URL role")
        assert_contains(lower, "legacy", f"{path} must document legacy SONAR_HOST_URL compatibility")
        assert_contains(lower, "not used", f"{path} must state the public base URL is not used for scanner traffic")


def test_docs_state_local_arc_queue_and_fail_fast_behavior() -> None:
    for path in DOCS:
        text = read(path).lower()
        for expected in (
            "local arc",
            "queued",
            "pending",
            "unhealthy",
            "preflight fails",
            "green",
        ):
            assert_contains(text, expected, f"{path} missing local ARC availability behavior: {expected}")

    usage = read(USAGE).lower()
    assert_contains(usage, "untrusted fork", "USAGE.md must document fork secret safety")
    assert_contains(usage, "pull_request_target", "USAGE.md must warn examples do not use pull_request_target")


def test_usage_input_and_secret_tables_include_new_contract() -> None:
    usage = read(USAGE)
    for expected in (
        "| `sonar-scanner-host-url` | string | no | `\"\"` | Preferred non-secret scanner URL used by both scan and quality gate",
        "| `sonar-public-base-url` | string | no | `\"\"` | Optional public SonarQube UI/base URL for generated links; not used by scanner calls |",
        "| `pre-scan-command` | string | no | `\"\"` | Shell command executed after checkout and tool setup but before the scan; use it to generate coverage reports (e.g. `pytest --cov --cov-report=xml`) |",
        "| `runner-label` | string | no | `\"\"` | Self-hosted runner label; set to the local ARC label when using the local-kind scanner Service URL |",
        "| `SONAR_SCANNER_HOST_URL` | no | Optional scanner URL secret, preferred over legacy `SONAR_HOST_URL` when the input is empty |",
        "| `SONAR_PUBLIC_BASE_URL` | no | Optional public SonarQube UI/base URL secret; not used by scanner calls |",
        "| `SONAR_HOST_URL` | no | Legacy scanner URL fallback |",
        "| `DOPPLER_TOKEN` | **yes** | Read-only Doppler service token used to fetch Sonar credentials at runtime |",
        "| `SONAR_TOKEN` | **yes** | SonarQube token |",
    ):
        assert_contains(usage, expected, f"USAGE.md missing input/secret table contract row: {expected}")


def test_examples_show_fork_safe_local_kind_contract() -> None:
    for path in EXAMPLES:
        text = read(path)
        for expected in (
            "on:\n  push:\n  pull_request:",
            "sonar-scanner-host-url: ${{ vars.SONAR_SCANNER_HOST_URL }}",
            "sonar-public-base-url: ${{ vars.SONAR_PUBLIC_BASE_URL }}",
            "runner-label: ${{ vars.RUNNER_LABEL }}",
            LOCAL_KIND_SCANNER_URL,
            "pull_request_target",
        ):
            assert_contains(text, expected, f"{path} missing example contract text: {expected}")

        assert_not_contains(text, "pull_request_target:", f"{path} must not run on pull_request_target")
        assert_not_contains(text, "cloudflared tunnel --url", f"{path} must not teach Quick Tunnel")
        assert_not_contains(text, "trycloudflare.com", f"{path} must not teach Quick Tunnel hostnames")
        assert_not_contains(text.lower(), "digitalocean", f"{path} must not teach stale public scanner hosts")

    for path in EXAMPLES[:3]:
        assert_contains(
            read(path),
            "DOPPLER_TOKEN: ${{ secrets.DOPPLER_TOKEN }}",
            f"{path} bundle example should keep only Doppler token as the Sonar secret",
        )

    sonar_example = read(EXAMPLES[-1])
    assert_contains(
        sonar_example,
        'pre-scan-command: ""',
        "standalone example missing pre-scan-command input",
    )
    for expected in (
        "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
        "SONAR_SCANNER_HOST_URL: ${{ secrets.SONAR_SCANNER_HOST_URL }}",
        "SONAR_PUBLIC_BASE_URL: ${{ secrets.SONAR_PUBLIC_BASE_URL }}",
        "SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}",
    ):
        assert_contains(sonar_example, expected, f"standalone example missing fallback secret: {expected}")


def test_docs_avoid_ambiguous_app_url_and_header_injection_guidance() -> None:
    checked_paths = DOCS + EXAMPLES
    forbidden_phrases = (
        "GitHub App URL",
        "Cf-Access-Client-Id",
        "Cf-Access-Client-Secret",
        "Cloudflare-Access-Client-Id",
        "Cloudflare-Access-Client-Secret",
        "Authorization: Bearer",
        "trycloudflare.com",
        "cloudflared tunnel --url",
    )
    for path in checked_paths:
        text = read(path)
        for phrase in forbidden_phrases:
            assert_not_contains(text, phrase, f"{path} contains forbidden or ambiguous docs phrase: {phrase}")


def test_lint_workflow_runs_documentation_contract() -> None:
    lint = read(LINT_WORKFLOW)
    assert_contains(
        lint,
        "python scripts/sonar_documentation_contract_test.py",
        "lint workflow should run the Sonar documentation contract test",
    )


def test_lint_workflow_runs_sonar_coverage_tests() -> None:
    lint = read(LINT_WORKFLOW)
    assert_contains(
        lint,
        "python -m pytest scripts/test_contract_lib.py",
        "lint workflow should run the contract_lib unit tests",
    )


def main() -> None:
    test_docs_distinguish_scanner_public_legacy_and_runner_contract()
    test_docs_state_local_arc_queue_and_fail_fast_behavior()
    test_usage_input_and_secret_tables_include_new_contract()
    test_examples_show_fork_safe_local_kind_contract()
    test_docs_avoid_ambiguous_app_url_and_header_injection_guidance()
    test_lint_workflow_runs_documentation_contract()
    test_lint_workflow_runs_sonar_coverage_tests()
    print("sonar_documentation_contract_test: ok")


if __name__ == "__main__":
    main()
