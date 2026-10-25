#!/usr/bin/env bash
# CI gate for pip-audit: fail on known vulnerabilities that have a fix.
#
#   .github/scripts/pip-audit-gate.sh [report.json]
#
# Without a report path it audits requirements.txt against OSV first. With a
# path (argument or PIP_AUDIT_JSON) it only evaluates that existing report;
# tests/test_ci_gates.py uses this with the fixtures in tests/fixtures/.
#
# Exit codes:
#   0  no finding with a fix version (findings without one are warnings)
#   1  at least one finding has a fix version: upgrade the pin
#   2  the audit itself failed (no report, or no dependency list in it)
set -euo pipefail

# Accepted vulnerabilities. Every entry needs a comment with the reason and an
# expiry date. pip-audit cannot enforce the date, so remove or renew the entry
# by then. For example:
#   --ignore-vuln GHSA-xxxx-xxxx-xxxx  # why it does not apply; expires 2026-12-31
# Nothing is ignored today.
ignore_args=()

report="${1:-${PIP_AUDIT_JSON:-}}"

if [ -z "$report" ]; then
  report="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/pip-audit.json"
  rm -f "$report"
  # pip-audit exits 1 when it finds anything; the decision is made below,
  # from the report, so a tool failure can be told apart from a finding.
  pip-audit -r requirements.txt -s osv -f json -o "$report" \
    ${ignore_args[@]+"${ignore_args[@]}"} || true
fi

if [ ! -s "$report" ]; then
  echo "::error::pip-audit failed: no report at $report (see the log above)"
  exit 2
fi
if ! jq -e '(.dependencies | type) == "array" and (.dependencies | length) > 0' \
  "$report" >/dev/null 2>&1; then
  echo "::error::pip-audit failed: $report has no dependency list"
  exit 2
fi

# One row per finding. Skipped dependencies carry no "vulns" key.
findings=$(jq -r '
  .dependencies[] | . as $dep | (.vulns // [])[]
  | [$dep.name, ($dep.version // "-"), .id,
     (if (.fix_versions // []) | length > 0
      then .fix_versions | join(",") else "-" end)]
  | @tsv' "$report")

audited=$(jq '[.dependencies[] | select(.skip_reason | not)] | length' "$report")
fixable=0
unfixable=0

if [ -n "$findings" ]; then
  printf '%-24s %-14s %-24s %s\n' NAME VERSION ID FIX_VERSIONS
  while IFS=$'\t' read -r name version id fixes; do
    printf '%-24s %-14s %-24s %s\n' "$name" "$version" "$id" "$fixes"
  done <<<"$findings"
  echo
  while IFS=$'\t' read -r name version id fixes; do
    if [ "$fixes" = "-" ]; then
      unfixable=$((unfixable + 1))
      echo "::warning::$name $version: $id has no fixed version yet"
    else
      fixable=$((fixable + 1))
      echo "::error::$name $version: $id is fixed in $fixes"
    fi
  done <<<"$findings"
fi

jq -r '.dependencies[] | select(.skip_reason)
  | "::notice::pip-audit skipped \(.name): \(.skip_reason)"' "$report"

echo "pip-audit: $audited dependencies audited, $fixable finding(s) with a fix, $unfixable without one"
if [ "$fixable" -gt 0 ]; then
  echo "Upgrade the affected pins in requirements.txt to a fixed version."
  exit 1
fi
