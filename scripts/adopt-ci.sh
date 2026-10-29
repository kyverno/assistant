#!/usr/bin/env bash
# Opens a pull request in each named repository that adds the shared CI actions (PR labelling, readiness and stale
# handling), in preview mode. Run it once, from a laptop, with your own `gh` login; nothing is stored anywhere.
#
#   scripts/adopt-ci.sh --reviewers @your-org/maintainers your-org/repo-a your-org/repo-b
#   scripts/adopt-ci.sh --reviewers @your-org/maintainers --dry-run your-org/repo-a
#   scripts/adopt-ci.sh --update-pin your-org/repo-a    # repos that already use the action: move only its pin
#
# Existing files are never overwritten. The action is pinned to the commit of this checkout, which must be pushed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_repo="kyverno/assistant"
ref=""
reviewers=""
dry_run=false
branch=""
update_pin=false

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat << 'EOF'
usage: adopt-ci.sh --reviewers @org/team [options] owner/repo [owner/repo ...]
       adopt-ci.sh --update-pin [options] owner/repo [owner/repo ...]

  --reviewers @org/team  existing team of the repo's organisation that the bot mentions when a PR needs a maintainer
                         (required, except with --update-pin)
  --update-pin           for repos that already use pr-management: rewrite only the pinned action reference in their
                         workflows, wherever it points now; add nothing and leave all config alone
  --dry-run              show which files would change; push nothing, open nothing
  --source owner/repo    where the actions live (default: kyverno/assistant)
  --ref SHA              full commit of the source to pin (default: this checkout's HEAD)
  --branch NAME          branch to open the PR from (default: ci/adopt-shared-actions; ci/update-pr-management-pin
                         with --update-pin)
EOF
}

repos=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reviewers) reviewers="${2:?--reviewers needs a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --update-pin) update_pin=true; shift ;;
    --source) source_repo="${2:?--source needs a value}"; shift 2 ;;
    --ref) ref="${2:?--ref needs a value}"; shift 2 ;;
    --branch) branch="${2:?--branch needs a value}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) usage >&2; die "unknown option $1" ;;
    *) repos+=("$1"); shift ;;
  esac
done

[[ ${#repos[@]} -gt 0 ]] || { usage >&2; die "name at least one owner/repo"; }
if [[ -z "$branch" ]]; then
  if [[ "$update_pin" == "true" ]]; then branch="ci/update-pr-management-pin"; else branch="ci/adopt-shared-actions"; fi
fi
[[ "$source_repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "--source must look like owner/repo"
if [[ "$update_pin" == "false" ]]; then
  # Same rule as the action's own config check; a single @user is rejected there.
  [[ "$reviewers" =~ ^@[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$ ]] || die "--reviewers must look like @org/team"
fi
for tool in gh git jq; do command -v "$tool" > /dev/null || die "$tool is required"; done
gh auth status > /dev/null 2>&1 || die "run 'gh auth login' first"

if [[ -z "$ref" ]]; then
  ref=$(git -C "$here" rev-parse HEAD)
fi
[[ "$ref" =~ ^[0-9a-f]{40}$ ]] || die "--ref must be a full 40-character commit SHA"
gh api "repos/${source_repo}/commits/${ref}" --jq .sha > /dev/null 2>&1 \
  || die "commit ${ref} is not in ${source_repo}; push this checkout or pass --ref with a pushed commit"

templates="$here/adopt-ci-templates"
workflows=(pr-management check-pr-management pr-labelling sync-labels)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

overlap_section() {
  [[ -n "$overlap" ]] || return 0
  cat << EOS

## Existing automation to retire first

These workflows already touch the same labels or run a labeler or stale bot. Once the previews are switched on, both
would act on the same PRs and could add duplicate or conflicting labels and comments. Keep one owner for each job:

$(printf '%s\n' "$overlap" | sed 's/^/- /')
EOS
}

pr_body() {
  cat << EOF
## What this does

Adds the shared PR automation from [${source_repo}](https://github.com/${source_repo}/tree/${ref}/actions/pr-management),
pinned to commit \`${ref}\`:

- **Labels** PRs from the paths they change, and flags merge conflicts.
- **Readiness**: marks a PR \`ready-for-review\` or \`needs-author-action\` from its checks and review threads.
- **Stale handling**: warns, then closes PRs the author has left quiet (14 days inactive, at least 7 days after the warning).

## Nothing changes when this merges

The scheduled and PR-event runs are **previews**: they print what they would do (log lines start with \`[dry-run]\`)
and write nothing. A repository admin turns the automation on afterwards.

**Before the switch**

1. Run **Sync labels** from the Actions tab with dry run off, so the lifecycle labels exist. Readiness and stale
   can fail without them.
2. Read a few preview runs, and optionally run **PR lifecycle** by hand (readiness, then stale) with dry run off to
   see real effects once.
3. Adjust the config (see below) to match the repository.
4. If the organisation restricts which actions may run (Settings, Actions, General), allow \`${source_repo}@*\`,
   \`actions/labeler@*\` and \`eps1lon/actions-label-merge-conflict@*\`.

**The switch**: set the repository variable \`PR_AUTOMATION_LIVE\` to \`true\` (Settings, Secrets and variables, Actions,
Variables), or run \`gh variable set PR_AUTOMATION_LIVE --body true --repo ${repo}\`. Creating a variable needs admin
access. No PR is needed.

**After it**: readiness and path labels act on the next PR event (readiness also hourly), and stale in its daily run.
The first live stale run can mark old quiet PRs stale; they cannot be closed until at least 7 days after the warning.
Setting the variable to \`false\`, or deleting it, returns every scheduled and PR-event run to preview.

## What to check in this PR

- \`.github/pr-management.json\`: reviewers (\`${reviewers}\`), exempt labels, timings.
- \`.github/pr-readiness-checks.yml\`: the names of checks that should block, and any that are not the author's to fix.
- \`.github/labels.yml\`: add path rules for your areas. It must define the lifecycle labels \`needs-author-action\`,
  \`ready-for-review\`, \`workflow-approval-required\`, \`stale\` and \`merge-conflicts\`; any missing from an existing file are appended to it by this PR.

## Keeping it current

The action is pinned to a full commit SHA so it never changes under you. To update, change the SHA in the four
workflow files (a Dependabot \`github-actions\` entry does this for you), or run
\`scripts/adopt-ci.sh --update-pin ${repo}\` from ${source_repo}, which opens a PR that changes only those lines.

$(overlap_section)

Opened with \`scripts/adopt-ci.sh\`. The config check runs on this PR without a token.
EOF
}

# A pinned reference to the action, under whichever repository and path it is published from.
pin_path_re='[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*/pr-management@[0-9a-f]{40}'

pin_body() { # old pins (one per line), changed files (one per line)
  local files_md from_md
  # The backticks are Markdown, not command substitution.
  # shellcheck disable=SC2016
  files_md=$(printf '%s\n' "$2" | sed 's/^/- `/; s/$/`/')
  # shellcheck disable=SC2016
  from_md=$(printf '%s\n' "$1" | sed -E 's#^([^/]+/[^/]+)/(.*)@([0-9a-f]{40})$#- [`&`](https://github.com/\1/tree/\3/\2)#')
  cat << EOF
## What this does

Points the PR automation at \`${source_repo}/actions/pr-management@${ref}\`. Only the \`uses:\` lines in the files
below change. The config files and every other line of the workflows stay as they are.

Files:

${files_md}

Moved from:

${from_md}

Moved to: [\`${source_repo}/actions/pr-management@${ref}\`](https://github.com/${source_repo}/tree/${ref}/actions/pr-management)

## What to check

- Read the action code at both links above if the source or the commit differs.
- If the organisation restricts which actions may run (Settings, Actions, General), allow \`${source_repo}@*\` first,
  or the workflows fail at start.
- After merging: run **Sync labels** by hand, then read the next hourly readiness run.

To roll back, revert this PR; the old pin keeps working.

Opened with \`scripts/adopt-ci.sh --update-pin\`.
EOF
}

publish() { # title; runs in the clone with the branch committed; reads repo, writable and default_branch from adopt
  local head="$branch"
  if [[ "$writable" == "true" ]]; then
    git push --quiet origin "$branch" || return 1
  else
    gh repo fork "$repo" --remote --remote-name fork > /dev/null 2>&1 || true
    git push --quiet fork "$branch" || return 1
    head="$(gh api user --jq .login):${branch}"
  fi
  gh pr create --repo "$repo" --base "$default_branch" --head "$head" --title "$1" --body-file "$tmp/body.md"
}

update_pins() { # runs in the clone
  local files old new f
  new="${source_repo}/actions/pr-management@${ref}"
  files=$(grep -lE "uses:[[:space:]]*${pin_path_re}" .github/workflows/* 2> /dev/null || true)
  if [[ -z "$files" ]]; then
    echo "  does not use pr-management yet; run without --update-pin to adopt it"
    return 0
  fi
  old=$(printf '%s\n' "$files" | while IFS= read -r f; do grep -ohE "uses:[[:space:]]*${pin_path_re}" "$f"; done \
    | sed -E 's/^uses:[[:space:]]*//' | sort -u)
  printf '%s\n' "$files" | while IFS= read -r f; do
    sed -E -i.bak "s#(uses:[[:space:]]*)${pin_path_re}#\1${new}#" "$f" && rm -f "$f.bak"
  done
  if git diff --quiet; then
    echo "  already pinned to ${new}, nothing to do"
    return 0
  fi
  git diff --stat | sed 's/^/  /'
  [[ "$dry_run" == "false" ]] || { echo "  dry run: nothing pushed"; return 0; }
  git add -- .github/workflows
  git commit --quiet -s -m "ci: update the pr-management pin" -m "Points the PR automation at ${new}."
  pin_body "$old" "$(git diff --name-only HEAD~1)" > "$tmp/body.md"
  publish "ci: update the pr-management pin"
}

add_file() { # source, destination (relative to repo root); never overwrites
  local src="$1" dest="$2"
  if [[ -e "$dest" ]]; then
    echo "  kept existing $dest"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  sed "s#kyverno/assistant/actions/pr-management@<full-commit-SHA>#${source_repo}/actions/pr-management@${ref}#" "$src" > "$dest"
  echo "  add $dest"
  added+=("$dest")
}

adopt() {
  local repo="$1" default_branch writable work
  echo "== $repo"
  [[ "$(gh api "repos/${repo}" --jq .archived)" == "false" ]] || { echo "  archived, skipped"; return 0; }
  if [[ "$update_pin" == "false" ]]; then
    # The team must exist in the repository's own organisation: a mention of any other name notifies nobody.
    team_org=$(printf '%s' "${reviewers#@}" | cut -d/ -f1 | tr '[:upper:]' '[:lower:]')
    team_slug="${reviewers##*/}"
    [[ "$team_org" == "$(printf '%s' "${repo%%/*}" | tr '[:upper:]' '[:lower:]')" ]] \
      || { echo "  skipped: ${reviewers} is not a team of ${repo%%/*}" >&2; return 1; }
    gh api "orgs/${team_org}/teams/${team_slug}" --jq .slug > /dev/null 2>&1 \
      || { echo "  skipped: team ${reviewers} does not exist or is not visible to you" >&2; return 1; }
  fi
  default_branch=$(gh api "repos/${repo}" --jq .default_branch)
  writable=$(gh api "repos/${repo}" --jq .permissions.push)
  if [[ -n "$(gh pr list --repo "$repo" --head "$branch" --state open --json number --jq '.[].number')" ]]; then
    echo "  a PR from ${branch} is already open, skipped"
    return 0
  fi
  work="$tmp/${repo//\//_}"
  gh repo clone "$repo" "$work" -- --depth 1 --quiet
  (
    cd "$work" || exit 1
    git checkout --quiet -b "$branch" || exit 1
    if [[ "$update_pin" == "true" ]]; then
      update_pins || exit 1
      exit 0
    fi
    added=()
    overlap=$(grep -lE 'needs-author-action|ready-for-review|actions/stale|actions/labeler' .github/workflows/* 2> /dev/null || true)
    if [[ -n "$overlap" ]]; then
      echo "  warning: existing workflows overlap with this automation; retire one before switching it on:" >&2
      printf '%s\n' "$overlap" | sed 's/^/    /' >&2
    fi
    for name in "${workflows[@]}"; do add_file "$templates/workflows/$name.yaml" ".github/workflows/$name.yaml"; done
    if [[ -e .github/pr-management.json ]]; then
      echo "  kept existing .github/pr-management.json"
    else
      mkdir -p .github
      jq --arg r "$reviewers" '.fallback_reviewers = $r' "$templates/pr-management.json" > .github/pr-management.json
      echo "  add .github/pr-management.json"
      added+=(.github/pr-management.json)
    fi
    add_file "$templates/pr-readiness-checks.yml" .github/pr-readiness-checks.yml
    add_file "$templates/labels.yml" .github/labels.yml
    # An existing labels.yml keeps everything it has; only the lifecycle labels the action needs are appended to it.
    labels_appended=false
    for label in needs-author-action ready-for-review workflow-approval-required stale merge-conflicts; do
      grep -qE "^[\"']?${label}[\"']?:" .github/labels.yml && continue
      [[ -z "$(tail -c1 .github/labels.yml)" ]] || echo >> .github/labels.yml
      awk -v key="${label}:" '$0 == key { on = 1; print; next } on && /^[ \t]/ { print; next } { on = 0 }' "$templates/labels.yml" >> .github/labels.yml
      echo "  append '${label}' to .github/labels.yml"
      labels_appended=true
    done
    if [[ "$labels_appended" == "true" ]]; then added+=(.github/labels.yml); fi

    [[ ${#added[@]} -gt 0 ]] || { echo "  already adopted, nothing to add"; exit 0; }
    [[ "$dry_run" == "false" ]] || { echo "  dry run: nothing pushed"; exit 0; }

    git add -- "${added[@]}"
    git commit --quiet -s -m "ci: adopt the shared PR automation" \
      -m "Pins ${source_repo}/actions/pr-management at ${ref}."
    pr_body > "$tmp/body.md"
    publish "ci: adopt the shared PR automation" || exit 1
  )
}

failed=0
for repo in "${repos[@]}"; do
  [[ "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || { echo "skipping '$repo': not owner/repo" >&2; failed=1; continue; }
  adopt "$repo" || { echo "  FAILED: $repo" >&2; failed=1; }
done
exit "$failed"
