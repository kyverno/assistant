#!/usr/bin/env bash
# Offline check of how `adopt-ci.sh --issues` fills .github/labels.yml: it appends only the default-named labels the
# config uses and the file lacks, keeps whatever the repository already has, and leaves no file behind when there is
# nothing to add. Runs the real functions from adopt-ci.sh in throwaway repositories.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC2034 # read by the functions evaluated below
templates="$here/../adopt-ci-templates"
# shellcheck disable=SC2034
source_repo=kyverno/assistant
# shellcheck disable=SC2034
ref=$(printf 'a%.0s' {1..40})
for fn in add_file append_issue_labels issue_body overlap_section; do
  eval "$(sed -n "/^${fn}()/,/^}/p" "$here/adopt-ci.sh")"
done
# shellcheck disable=SC2034
repo=acme/widgets
# shellcheck disable=SC2034
overlap=""
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0
check() { # description, condition result (0 = holds)
  [[ "$2" == "0" ]] || { echo "FAIL: $1"; fail=1; }
}
has() { grep -qE "$1" "$2" 2> /dev/null; echo $?; }
lacks() { if grep -qE "$1" "$2" 2> /dev/null; then echo 1; else echo 0; fi; }

run_case() { # name, config json, existing labels.yml content (or - for none); runs in a fresh repo, sets `added`
  local dir="$tmp/$1"
  mkdir -p "$dir/.github"
  printf '%s' "$2" > "$dir/.github/issue-management.json"
  [[ "$3" == "-" ]] || printf '%b' "$3" > "$dir/.github/labels.yml"
  cd "$dir"
  added=()
  append_issue_labels > /dev/null
}

# 1. fresh repository, starter config: needs-info and stale-issue, the Sync labels caller, no possibly-resolved
run_case fresh '{"needs_info_labels": ["needs-info"]}' -
check "fresh: needs-info added" "$(has '^needs-info:' .github/labels.yml)"
check "fresh: stale-issue added" "$(has '^stale-issue:' .github/labels.yml)"
check "fresh: possibly-resolved not added" "$(lacks '^possibly-resolved:' .github/labels.yml)"
check "fresh: sync-labels workflow added" "$(has 'uses: kyverno/assistant/' .github/workflows/sync-labels.yaml)"
check "fresh: color kept with its label" "$(has '^  color: d876e3' .github/labels.yml)"

body=$(issue_body)
check "fresh: the text tells the adopter to run Sync labels first" "$(grep -qF '1. Run **Sync labels**' <<< "$body" && echo 0 || echo 1)"
check "fresh: the next step is numbered 2" "$(grep -qF '2. Run **Issue management**' <<< "$body" && echo 0 || echo 1)"

# 2. the config names possibly-resolved: it is added too
run_case resolved '{"resolved_label": "possibly-resolved"}' -
check "resolved: possibly-resolved added" "$(has '^possibly-resolved:' .github/labels.yml)"

# 3. any other resolved label is the repository's own: nothing added for it
run_case ownresolved '{"resolved_label": "triage:verify-closure"}' -
check "own resolved label: not in the catalog" "$(lacks 'verify-closure' .github/labels.yml)"

# 4. an existing catalog keeps its entries; only what it lacks is appended, even without a trailing newline
run_case existing '{}' 'bug:\n  color: d73a4a\nneeds-info:\n  color: 123456\n  description: mine'
check "existing: own entry kept" "$(has '^bug:' .github/labels.yml)"
check "existing: own needs-info colour kept" "$(has '^  color: 123456' .github/labels.yml)"
check "existing: needs-info not duplicated" "$([[ $(grep -c '^needs-info:' .github/labels.yml) == 1 ]] && echo 0 || echo 1)"
check "existing: stale-issue appended on its own line" "$(has '^stale-issue:' .github/labels.yml)"
check "existing: labels.yml is reported as changed" "$([[ " ${added[*]} " == *" .github/labels.yml "* ]] && echo 0 || echo 1)"

# 5. every name is the repository's own: the catalog it did not have is not created, and no Sync labels caller is added
run_case custom '{"needs_info_labels": ["cannot reproduce"], "stale_label": "stale-x"}' -
check "custom: no labels.yml left behind" "$([[ ! -e .github/labels.yml ]] && echo 0 || echo 1)"
check "custom: no sync-labels workflow" "$([[ ! -e .github/workflows/sync-labels.yaml ]] && echo 0 || echo 1)"
check "custom: nothing recorded as added" "$([[ ${#added[@]} == 0 ]] && echo 0 || echo 1)"
body=$(issue_body)
check "custom: the text does not mention a Sync labels workflow that is not there" "$(grep -qF 'Run **Sync labels**' <<< "$body" && echo 1 || echo 0)"
check "custom: the text says the labels must already exist" "$(grep -qF 'make sure they exist' <<< "$body" && echo 0 || echo 1)"
check "custom: the steps start at 1" "$(grep -qF '1. Run **Issue management**' <<< "$body" && echo 0 || echo 1)"

# 6. every name is the repository's own and it already has a catalog: that file is left exactly as it was
run_case customexisting '{"needs_info_labels": ["cannot reproduce"], "stale_label": "stale-x"}' 'bug:\n  color: d73a4a\n'
check "custom with catalog: file untouched" "$([[ "$(cat .github/labels.yml)" == $'bug:\n  color: d73a4a' ]] && echo 0 || echo 1)"
check "custom with catalog: labels.yml not recorded as changed" "$([[ " ${added[*]} " != *" .github/labels.yml "* ]] && echo 0 || echo 1)"

[[ "$fail" == "0" ]] && echo "ok"
exit "$fail"
