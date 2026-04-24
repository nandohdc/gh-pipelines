#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/enforce-sonar-branch-protection.sh [owner/repo] [status-check-context] [branch]

Defaults:
  owner/repo            -> current gh repo (if available)
  status-check-context  -> ci / sonar-scan
  branch                -> main

This script appends a required status-check context to branch protection
without replacing existing required checks.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required." >&2
  exit 1
fi

repo="${1:-}"
status_context="${2:-ci / sonar-scan}"
branch="${3:-main}"

if [ -z "${repo}" ]; then
  repo="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || true)"
fi

if [ -z "${repo}" ]; then
  echo "Could not resolve repository. Pass owner/repo explicitly." >&2
  usage
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

if ! gh api "repos/${repo}/branches/${branch}/protection" >/dev/null 2>&1; then
  echo "Branch protection is unavailable for ${repo}:${branch} (or token lacks admin rights)." >&2
  exit 1
fi

existing_contexts="$(gh api "repos/${repo}/branches/${branch}/protection/required_status_checks/contexts" --jq '.[]' || true)"
if printf '%s\n' "${existing_contexts}" | grep -Fxq "${status_context}"; then
  echo "Required context already present on ${repo}:${branch}: ${status_context}"
  exit 0
fi

payload="$(jq -nc --arg context "${status_context}" '[$context]')"
printf '%s' "${payload}" | gh api \
  -X POST \
  "repos/${repo}/branches/${branch}/protection/required_status_checks/contexts" \
  --input - >/dev/null

echo "Added required status-check context on ${repo}:${branch}: ${status_context}"
