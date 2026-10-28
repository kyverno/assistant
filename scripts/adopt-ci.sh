#!/usr/bin/env bash
# Opens a pull request in each named repository that adds the shared CI actions (PR labelling, readiness and stale
# handling), in preview mode. Run it once, from a laptop, with your own `gh` login; nothing is stored anywhere.
#
#   scripts/adopt-ci.sh --reviewers @your-org/maintainers your-org/repo-a your-org/repo-b
#   scripts/adopt-ci.sh --reviewers @your-org/maintainers --dry-run your-org/repo-a
#
# Existing files are never overwritten. The action is pinned to the commit of this checkout, which must be pushed.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_repo="kyverno/assistant"
ref=""
reviewers=""
dry_run=false
branch="ci/adopt-shared-actions"

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat << 'EOF'
usage: adopt-ci.sh --reviewers @org/team [options] owner/repo [owner/repo ...]

  --reviewers @org/team  existing team of the repo's organisation that the bot mentions when a PR needs a maintainer (required)
  --dry-run              show which files would be added; push nothing, open nothing
  --source owner/repo    where the actions live (default: kyverno/assistant)
  --ref SHA              full commit of the source to pin (default: this checkout's HEAD)
  --branch NAME          branch to open the PR from (default: ci/adopt-shared-actions)
EOF
}

repos=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reviewers) reviewers="${2:?--reviewers needs a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --source) source_repo="${2:?--source needs a value}"; shift 2 ;;
    --ref) ref="${2:?--ref needs a value}"; shift 2 ;;
    --branch) branch="${2:?--branch needs a value}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) usage >&2; die "unknown option $1" ;;
    *) repos+=("$1"); shift ;;
  esac
done

[[ ${#repos[@]} -gt 0 ]] || { usage >&2; die "name at least one owner/repo"; }
# Same rule as the action's own config check; a single @user is rejected there.
[[ "$reviewers" =~ ^@[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$ ]] || die "--reviewers must look like @org/team"
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

The scheduled and PR-event runs are **previews**: they print what they would do and write nothing.

1. Run **Sync labels** from the Actions tab with dry run off, so the lifecycle labels exist.
2. Run **PR lifecycle** (readiness, then stale) by hand with dry run off, and read the result.
3. When you are happy with the previews, a repository admin turns the automation on by setting the repository
   variable \`PR_AUTOMATION_LIVE\` to \`true\` (Settings, Secrets and variables, Actions, Variables). No PR is needed,
   and setting it back to \`false\` pauses it.

## What to check in this PR

- \`.github/pr-management.json\`: reviewers (\`${reviewers}\`), exempt labels, timings.
- \`.github/pr-readiness-checks.yml\`: the names of checks that should block, and any that are not the author's to fix.
- \`.github/labels.yml\`: add path rules for your areas. It must define the lifecycle labels \`needs-author-action\`,
  \`ready-for-review\`, \`workflow-approval-required\` and \`stale\` (an existing file is kept as it was).

## Keeping it current

The action is pinned to a full commit SHA so it never changes under you. To update, change the SHA in the four
workflow files (a Dependabot \`github-actions\` entry does this for you).

$(overlap_section)

Opened with \`scripts/adopt-ci.sh\`. The config check runs on this PR without a token.
EOF
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
  # The team must exist in the repository's own organisation: a mention of any other name notifies nobody.
  team_org=$(printf '%s' "${reviewers#@}" | cut -d/ -f1 | tr '[:upper:]' '[:lower:]')
  team_slug="${reviewers##*/}"
  [[ "$team_org" == "$(printf '%s' "${repo%%/*}" | tr '[:upper:]' '[:lower:]')" ]] \
    || { echo "  skipped: ${reviewers} is not a team of ${repo%%/*}" >&2; return 1; }
  gh api "orgs/${team_org}/teams/${team_slug}" --jq .slug > /dev/null 2>&1 \
    || { echo "  skipped: team ${reviewers} does not exist or is not visible to you" >&2; return 1; }
  default_branch=$(gh api "repos/${repo}" --jq .default_branch)
  writable=$(gh api "repos/${repo}" --jq .permissions.push)
  if [[ -n "$(gh pr list --repo "$repo" --head "$branch" --state open --json number --jq '.[].number')" ]]; then
    echo "  a PR from ${branch} is already open, skipped"
    return 0
  fi
  work="$tmp/${repo//\//_}"
  gh repo clone "$repo" "$work" -- --depth 1 --quiet
  (
    cd "$work"
    git checkout --quiet -b "$branch"
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
    for label in needs-author-action ready-for-review workflow-approval-required stale; do
      grep -q "^${label}:" .github/labels.yml || echo "  warning: .github/labels.yml has no '${label}' entry; add it from adopt-ci-templates/labels.yml"
    done

    [[ ${#added[@]} -gt 0 ]] || { echo "  already adopted, nothing to add"; exit 0; }
    [[ "$dry_run" == "false" ]] || { echo "  dry run: nothing pushed"; exit 0; }

    git add -- "${added[@]}"
    git commit --quiet -s -m "ci: adopt the shared PR automation" \
      -m "Pins ${source_repo}/actions/pr-management at ${ref}."
    head="$branch"
    if [[ "$writable" == "true" ]]; then
      git push --quiet origin "$branch"
    else
      gh repo fork "$repo" --remote --remote-name fork > /dev/null 2>&1 || true
      git push --quiet fork "$branch"
      head="$(gh api user --jq .login):${branch}"
    fi
    pr_body > "$tmp/body.md"
    gh pr create --repo "$repo" --base "$default_branch" --head "$head" \
      --title "ci: adopt the shared PR automation" --body-file "$tmp/body.md"
  )
}

failed=0
for repo in "${repos[@]}"; do
  [[ "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || { echo "skipping '$repo': not owner/repo" >&2; failed=1; continue; }
  adopt "$repo" || { echo "  FAILED: $repo" >&2; failed=1; }
done
exit "$failed"
