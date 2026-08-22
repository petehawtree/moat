#!/usr/bin/env bash

# Run the repository's independent Codex quality gate and retain its report.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: judge.sh must be run from a Git repository." >&2
  exit 1
fi

if [[ "$(git rev-parse --show-toplevel)" != "$SCRIPT_DIR" ]]; then
  echo "Error: judge.sh must live in and be run from the repository root." >&2
  exit 1
fi

if [[ ! -f "JUDGE_PROMPT.md" ]]; then
  echo "Error: JUDGE_PROMPT.md is missing from the repository root." >&2
  exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "Error: Codex CLI is not installed or is not on PATH." >&2
  echo "Install it with: npm install -g @openai/codex" >&2
  exit 127
fi

REPORT_DIR=".judge"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="$REPORT_DIR/judge-report-$TIMESTAMP.md"
mkdir -p "$REPORT_DIR"

# Include untracked files so an unintended write is visible, while .judge/
# itself is ignored and therefore does not create a false positive.
BEFORE_STATUS="$(git status --porcelain=v1 --untracked-files=all)"

echo "Running independent repository judge..."
echo "Report: $REPORT_FILE"
echo

set +e
codex exec --sandbox workspace-write "$(<JUDGE_PROMPT.md)" | tee "$REPORT_FILE"
CODEX_EXIT=${PIPESTATUS[0]}
set -e

AFTER_STATUS="$(git status --porcelain=v1 --untracked-files=all)"
if [[ "$BEFORE_STATUS" != "$AFTER_STATUS" ]]; then
  echo >&2
  echo "WARNING: repository state changed while the judge ran." >&2
  echo "Review these changes; the judge is not authorised to modify repository files:" >&2
  git status --short >&2
fi

if [[ "$CODEX_EXIT" -ne 0 ]]; then
  echo "Judge execution failed with exit code $CODEX_EXIT." >&2
  exit "$CODEX_EXIT"
fi

verdict_value() {
  local heading="$1"
  awk -v heading="### $heading" '
    $0 == heading { found = 1; next }
    found && /^### / { exit }
    found {
      value = $0
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (value != "") { print value; exit }
    }
  ' "$REPORT_FILE"
}

finding_count() {
  local severity="$1"
  awk -v severity="$severity" '
    $0 == "### Findings" { in_findings = 1; next }
    in_findings && /^### / { exit }
    in_findings && $0 ~ "^- " severity ":" {
      value = $0
      sub("^- " severity ":[[:space:]]*", "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      print value
      exit
    }
  ' "$REPORT_FILE"
}

FINAL_VERDICT="$(verdict_value "Overall verdict")"
CRITICAL_COUNT="$(finding_count "Critical")"
HIGH_COUNT="$(finding_count "High")"

if [[ ! "$FINAL_VERDICT" =~ ^(PASS|PASS\ WITH\ CONCERNS|FAIL)$ ]] || \
   [[ ! "$CRITICAL_COUNT" =~ ^[0-9]+$ ]] || \
   [[ ! "$HIGH_COUNT" =~ ^[0-9]+$ ]]; then
  echo "Judge report is missing or has an invalid machine-parseable verdict." >&2
  echo "Review: $REPORT_FILE" >&2
  exit 2
fi

echo
echo "Judge verdict: $FINAL_VERDICT"
echo "Critical findings: $CRITICAL_COUNT"
echo "High findings: $HIGH_COUNT"
echo "Report written to: $REPORT_FILE"

if [[ "$FINAL_VERDICT" == "FAIL" ]] || \
   (( CRITICAL_COUNT > 0 )) || \
   (( HIGH_COUNT > 0 )); then
  echo "Judge quality gate FAILED." >&2
  exit 1
fi

echo "Judge quality gate PASSED."
