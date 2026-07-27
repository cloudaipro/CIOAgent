#!/bin/bash
# Three Man Team — handoff structure check
#
# The framework's own rule: machines check what machines can check, so human attention is
# spent only on judgment. This is that rule applied to the handoff files themselves.
#
# A brief missing its Out of Scope section, or carrying a Definition of Done nobody can run,
# is a bounced step waiting to happen — and a bounced step is the most expensive event in this
# methodology: it re-runs the Reviewer, the Builder fix, and the Architect re-brief, re-billing
# every agent's accumulated context across the whole loop. One command here costs less than one
# loop there.
#
# This check is structural, not semantic. It proves the sections exist, the placeholders are
# filled, and the Definition of Done carries something runnable. Whether the content is *right*
# is still the reading role's judgment — and still theirs to bounce.
#
# Usage: scripts/check-handoff.sh brief|review-request|review-feedback
# Exit 0 = structurally complete.  Exit 1 = fix it before handing off.

set -u
cd "$(dirname "$0")/.." || exit 1

FAIL=0
problem() { echo "✗ $*"; FAIL=1; }
ok()      { echo "  ✓ $*"; }

# Every heading level, so a project that reformats H3 to H4 does not silently pass.
has_section() {
  grep -qE "^#{1,4} +$2 *$" "$1"
}

need_section() {
  if has_section "$1" "$2"; then
    return 0
  fi
  problem "$1 has no '$2' section"
  return 1
}

# Body of a section: everything between its heading and the next heading of any level.
section_body() {
  awk -v h="$2" '
    $0 ~ "^#+ +" h " *$" { inside = 1; next }
    inside && /^#+ +/     { inside = 0 }
    inside                { print }
  ' "$1"
}

# Unfilled template placeholders — [N], [Decision or constraint], [command].
# First character must be alphanumeric, which excludes the "- [ ]" checkbox and "- [x]".
# Markdown links "](" are excluded too.
placeholders() {
  grep -nE '\[[A-Za-z0-9][^]]*\]' "$1" | grep -v '](' || true
}

check_placeholders() {
  local found
  found="$(placeholders "$1")"
  if [ -n "$found" ]; then
    problem "$1 still has unfilled template placeholders:"
    echo "$found" | sed 's/^/      /'
    return 1
  fi
  ok "$1 has no unfilled placeholders"
  return 0
}

need_file() {
  if [ -f "$1" ]; then
    return 0
  fi
  problem "$1 does not exist"
  return 1
}

# --- brief -------------------------------------------------------------------
# Read by: Architect (last line of the Pre-Flight Check, before spinning up the Builder)
#          Builder (session start, before writing any code)
check_brief() {
  F="handoff/ARCHITECT-BRIEF.md"
  need_file "$F" || return

  if grep -qE '^#{1,4} +Step +[0-9]' "$F"; then
    ok "$F names a numbered step"
  else
    problem "$F has no '## Step <N>' heading with a real step number"
  fi

  need_section "$F" "Decisions"
  need_section "$F" "Out of Scope"
  need_section "$F" "Flags"

  if need_section "$F" "Definition of Done"; then
    DOD="$(section_body "$F" "Definition of Done")"
    if [ -z "$(echo "$DOD" | tr -d '[:space:]')" ]; then
      problem "$F Definition of Done is empty"
    elif echo "$DOD" | grep -q '`'; then
      ok "Definition of Done carries a runnable command"
    else
      problem "$F Definition of Done has no command in backticks — a criterion you cannot express as a command, a click-path, or a diff assertion means the step is not specified well enough to build"
    fi
  fi

  check_placeholders "$F"
}

# --- review-request ----------------------------------------------------------
# Read by: Builder (after writing it, before signalling done)
#          Reviewer (session start — a structural bounce costs no review budget)
check_review_request() {
  F="handoff/REVIEW-REQUEST.md"
  need_file "$F" || return

  if grep -qE '^Ready for Review: *YES *$' "$F"; then
    ok "Ready for Review: YES"
  elif grep -qE '^Ready for Review: *NO *$' "$F"; then
    problem "$F says Ready for Review: NO — not ready to hand off"
  else
    problem "$F has no decided 'Ready for Review:' line (still YES / NO, or missing)"
  fi

  if need_section "$F" "Files Changed"; then
    if section_body "$F" "Files Changed" | grep -qE '\| *[0-9]+ *(-|–) *[0-9]+ *\|'; then
      ok "Files Changed cites line ranges"
    else
      problem "$F Files Changed has no line ranges — the Reviewer greps to ranges instead of reading whole files, so a missing range is extra context they pay for on every turn"
    fi
  fi

  if need_section "$F" "Mechanical Gate"; then
    # Anchored: the template ships "Gate: PASS / FAIL / NO GATE DEFINED", and an
    # undecided line must not read as a decided one.
    if grep -qE '^Gate: *FAIL *$' "$F"; then
      problem "$F reports Gate: FAIL — a failing gate never reaches the Reviewer (RULES.md Iron Rule 2). Fix it first"
    elif grep -qE '^Gate: *(PASS|NO GATE DEFINED) *$' "$F"; then
      ok "Mechanical Gate reported"
    else
      problem "$F has no decided 'Gate:' result (PASS, FAIL, or NO GATE DEFINED)"
    fi
  fi

  check_placeholders "$F"
}

# --- review-feedback ---------------------------------------------------------
# Read by: Reviewer (after writing it)
#          Builder (session start when resuming after review)
check_review_feedback() {
  F="handoff/REVIEW-FEEDBACK.md"
  need_file "$F" || return

  if grep -qE '^Ready for Builder: *(YES|NO) *$' "$F"; then
    ok "Ready for Builder is decided"
  else
    problem "$F has no decided 'Ready for Builder:' line (still YES / NO, or missing)"
  fi

  need_section "$F" "Must Fix"
  need_section "$F" "Should Fix"

  check_placeholders "$F"
}

case "${1:-}" in
  brief)           echo "— Architect brief";  check_brief ;;
  review-request)  echo "— Review request";   check_review_request ;;
  review-feedback) echo "— Review feedback";  check_review_feedback ;;
  *)
    echo "usage: scripts/check-handoff.sh brief|review-request|review-feedback" >&2
    exit 2
    ;;
esac

echo ""
if [ "$FAIL" = 1 ]; then
  echo "Handoff check FAILED — fix the problems above before handing off."
  exit 1
fi
echo "Handoff check clean."
