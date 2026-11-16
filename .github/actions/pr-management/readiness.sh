#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

case "$EVENT_NAME" in
  pull_request_target)
    # Same eligibility as the sweep: non-draft, not Dependabot's (dependabot-merge-triage.yaml owns those).
    if [[ "$PR_AUTHOR" == "dependabot[bot]" ]]; then
      echo '[]' > qualifying-numbers.json
    else
      echo "[$PR_NUMBER]" > qualifying-numbers.json
    fi
    ;;
  schedule|workflow_dispatch)
    echo 'sweep' > mode.txt
    ;;
  *)
    echo "::error::Unsupported readiness event: $EVENT_NAME"
    exit 1
    ;;
esac

if [[ -f mode.txt ]]; then
  mode=sweep
else
  mode=single
  echo "Processing PR(s): $(cat qualifying-numbers.json)"
fi


if [[ "$mode" == "sweep" ]]; then
# Cheap fields only — the full payload for all ~300+ open PRs times out the GraphQL API.
query='
  query($search: String!, $after: String) {
    search(query: $search, type: ISSUE, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on PullRequest {
          number
          isDraft
          closingIssuesReferences(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes { repository { nameWithOwner } milestone { title state } }
          }
        }
      }
    }
  }'
search="repo:${GITHUB_REPOSITORY} is:pr is:open draft:false -author:app/dependabot"

# milestone-pr: closes a same-repo issue with an open milestone matching the configured milestone_prefix. Only gates
# that label — every open PR found above gets a readiness check, milestone or not.
qualifies='
  [.closingIssuesReferences.nodes[]?
    | select(.repository.nameWithOwner == $repo
        and .milestone != null
        and .milestone.state == "OPEN"
        and ($prefix != "")
        and (.milestone.title | startswith($prefix)))]
  | length > 0'

echo '[]' > qualifying-numbers.json
echo '[]' > milestone-numbers.json
after=""
page=0
while :; do
  page=$((page + 1))
  if [[ -z "$after" ]]; then
    gh api graphql -f query="$query" -f search="$search" > response.json
  else
    gh api graphql -f query="$query" -f search="$search" -f after="$after" > response.json
  fi

  jq -e '.data.search | (.nodes | type == "array") and
    (.pageInfo.hasNextPage | type == "boolean")' response.json > /dev/null
  jq -c '[.data.search.nodes[] | select(.isDraft | not) | .number]' \
    response.json > page-numbers.json
  jq -s 'add' qualifying-numbers.json page-numbers.json > numbers-next.json \
    && mv numbers-next.json qualifying-numbers.json

  jq -c --arg repo "${GITHUB_REPOSITORY}" --arg prefix "$MILESTONE_PREFIX" \
    "[.data.search.nodes[] | select(.isDraft | not) | select($qualifies) | .number]" \
    response.json > page-milestone-numbers.json
  jq -s 'add' milestone-numbers.json page-milestone-numbers.json > milestone-next.json \
    && mv milestone-next.json milestone-numbers.json

  # A PR closing more than 100 issues isn't realistic, but flag it loudly rather than silently miss it.
  still_truncated=$(jq --arg repo "${GITHUB_REPOSITORY}" --arg prefix "$MILESTONE_PREFIX" \
    "[.data.search.nodes[] | select(.isDraft | not) | select(.closingIssuesReferences.pageInfo.hasNextPage and ($qualifies | not)) | .number]" \
    response.json)
  [[ "$still_truncated" != "[]" ]] && echo "::warning::PR(s) $still_truncated close more than 100 issues and may be wrongly excluded from milestone-pr."

  has_next=$(jq -r '.data.search.pageInfo.hasNextPage' response.json)
  [[ "$has_next" != "true" ]] && break
  after=$(jq -r '.data.search.pageInfo.endCursor' response.json)
  [[ -n "$after" && "$after" != "null" ]] || { echo "::error::Missing discovery cursor"; exit 1; }

  if [[ "$page" -ge 20 ]]; then
    echo "::error::Discovery still truncated after 20 pages (2000 PRs)."
    exit 1
  fi
done
echo "Found $(jq 'length' qualifying-numbers.json) open PR(s) to evaluate."
echo "Found $(jq 'length' milestone-numbers.json) milestone PR(s) to stamp."

# Stamp milestone-pr on qualifying PRs. Removing it from PRs that stop qualifying is a follow-up.
for number in $(jq -r '.[]' milestone-numbers.json); do
  current_labels=$(gh pr view "$number" --repo "${GITHUB_REPOSITORY}" --json labels --jq '[.labels[].name]')
  has_milestone_label=$(jq 'any(. == "milestone-pr")' <<< "$current_labels")
  if [[ "$has_milestone_label" == "false" ]]; then
    gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --add-label "milestone-pr"
  fi
done
fi


set -euo pipefail

detail_query='
  query($owner: String!, $name: String!, $number: Int!) {
    repository(owner: $owner, name: $name) {
      pullRequest(number: $number) {
        number
        state
        isDraft
        author { login }
        labels(first: 100) { pageInfo { hasNextPage endCursor } nodes { name } }
        commits(last: 1) {
          nodes { commit {
            oid
            committedDate
            statusCheckRollup { contexts(first: 100) { pageInfo { hasNextPage endCursor } nodes {
              ... on CheckRun { name conclusion status startedAt }
              ... on StatusContext { context state createdAt }
            } } }
          } }
        }
        reviewThreads(first: 100) {
          pageInfo { hasNextPage endCursor }
          nodes { isResolved comments(last: 1) { nodes { author { login } createdAt } } }
        }
        # Only the newest 100 of each (the API page maximum), without pagination: a check about "the latest comment"
        # never needs older ones, but a PR with over 100 newer events could hide an unanswered maintainer comment.
        # For `unanswered` and the plain-comment nudge that only means a missed check or nudge, which is the quiet side.
        # A wrong nudge would need a maintainer review in view while the later reply of the author, in the comments
        # connection, was pushed out by more than 100 newer comments. Paginating would add API cost to every PR in the
        # sweep for that rare case.
        comments(last: 100) { nodes { id url bodyText author { login __typename } authorAssociation createdAt } }
        reviews(last: 100) { nodes { id url author { login __typename } authorAssociation state submittedAt bodyText commit { oid } inlineComments: comments(first: 1) { totalCount } } }
      }
    }
  }'
# Continuation queries for the three connections above, used to finish any that didn't fit in 100.
labels_page_query='
  query($owner: String!, $name: String!, $number: Int!, $after: String!) {
    repository(owner: $owner, name: $name) { pullRequest(number: $number) {
      labels(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { name } }
    } }
  }'
checks_page_query='
  query($owner: String!, $name: String!, $number: Int!, $after: String!) {
    repository(owner: $owner, name: $name) { pullRequest(number: $number) {
      commits(last: 1) { nodes { commit {
        statusCheckRollup { contexts(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes {
          ... on CheckRun { name conclusion status startedAt }
          ... on StatusContext { context state createdAt }
        } } }
      } } }
    } }
  }'
threads_page_query='
  query($owner: String!, $name: String!, $number: Int!, $after: String!) {
    repository(owner: $owner, name: $name) { pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved comments(last: 1) { nodes { author { login } createdAt } } }
      }
    } }
  }'
owner="${GITHUB_REPOSITORY%/*}"
name="${GITHUB_REPOSITORY#*/}"

# Paginates one connection on $pr_file to completion, given its jq path and *_page_query. 20-page cap
# as a last-resort safety valve; the truncated-connection handling below still covers that case.
paginate_connection() {
  local pr_file="$1" conn_path="$2" query="$3" extra_page=0
  validate_connection "$pr_file" "$conn_path" || return 1
  while [[ "$(jq -r "${conn_path}.pageInfo.hasNextPage" "$pr_file")" == "true" ]]; do
    extra_page=$((extra_page + 1))
    if [[ "$extra_page" -gt 20 ]]; then
      echo "::warning::$conn_path on PR in $pr_file still has more pages after 20 extra fetches — leaving it truncated."
      break
    fi
    after=$(jq -r "${conn_path}.pageInfo.endCursor" "$pr_file")
    [[ -n "$after" && "$after" != "null" ]] || return 1
    gh api graphql -f query="$query" -f owner="$owner" -f name="$name" -F number="$number" -f after="$after" > page.json
    validate_connection page.json ".data.repository.pullRequest${conn_path}" || return 1
    jq --argjson extra "$(jq -c ".data.repository.pullRequest${conn_path}.nodes" page.json)" \
      "${conn_path}.nodes += \$extra" "$pr_file" > pr-next.json && mv pr-next.json "$pr_file"
    jq --argjson pi "$(jq -c ".data.repository.pullRequest${conn_path}.pageInfo" page.json)" \
      "${conn_path}.pageInfo = \$pi" "$pr_file" > pr-next.json && mv pr-next.json "$pr_file"
  done
}

# A check can run several times on one commit (a re-run, or a workflow triggered by more than one event), and the rollup
# lists every run. Only the newest run of each check says whether it passes now, so an earlier red run must not keep
# blocking a PR whose later run is green. A run that has not finished counts as the newest, so a queued re-run is
# never hidden behind an older result. Entries with no name are kept as they are: the pending check treats them as unknown.
# Pending wins over a newer completed run on purpose, as it does everywhere else in readiness: a PR is not ready while
# any run of a check is still going. That holds the PR only until the older run finishes (a hung one ends at its job
# timeout), after which the newest run decides. Ordering by start time instead would mark a PR ready under a run that
# is still in progress.
latest_check_per_name() {
  jq -c '
    def key: .name // .context;
    def stamp:
      if ((.status // "COMPLETED") != "COMPLETED") or .state == "PENDING" or .state == "EXPECTED" then "~"
      else (.startedAt // .createdAt // "") end;
    if .commits.nodes[0].commit.statusCheckRollup == null then .
    else .commits.nodes[0].commit.statusCheckRollup.contexts.nodes |= (
      [.[] | select(key == null)]
      + ([.[] | select(key != null)] | group_by(key) | map(max_by(stamp)))
    ) end' one-pr.json > one-pr-next.json && mv one-pr-next.json one-pr.json
}

fetch_pr_details() {
  gh api graphql -f query="$detail_query" -f owner="$owner" -f name="$name" -F number="$number" \
    | jq -c '.data.repository.pullRequest' > one-pr.json || return 1

  # A qualifying PR can close between discovery and here — skip rather than process a null result.
  if [[ "$(jq -r 'type' one-pr.json)" != "object" ]]; then
    echo "PR #$number: detail fetch returned nothing (closed mid-run?) — skipping."
    return 0
  fi
  [[ "$(jq -r '.state' one-pr.json)" == "OPEN" ]] || return 0
  [[ "$(jq -r '.author.login' one-pr.json)" == "dependabot[bot]" ]] && return 0
  if [[ "$(jq -r '.isDraft' one-pr.json)" == "true" ]]; then
    # A converted draft must not retain readiness; do not evaluate or comment on it.
    for label in ready-for-review needs-author-action workflow-approval-required; do
      gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --remove-label "$label" || return 1
    done
    return 0
  fi

  paginate_connection one-pr.json ".labels" "$labels_page_query" || return 1
  # A missing rollup is pending CI, not a proven empty/successful connection.
  if [[ "$(jq -r '.commits.nodes[0].commit.statusCheckRollup' one-pr.json)" != "null" ]]; then
    paginate_connection one-pr.json ".commits.nodes[0].commit.statusCheckRollup.contexts" "$checks_page_query" || return 1
  fi
  paginate_connection one-pr.json ".reviewThreads" "$threads_page_query" || return 1
  latest_check_per_name || return 1

  cat one-pr.json >> pr-details.jsonl
}

# Written straight to a file, never through a bash variable — PR content can contain arbitrary text.
# Called in an `if` context (errexit suppressed), so every fallible command returns explicitly; a failed
# PR is skipped (never evaluated or mutated) and the run still fails after the remaining PRs are handled.
# A maintainer's plain comment the author has not answered: a PR comment, or the summary of a review that only comments.
# Inline comments are never nudged, and neither is the summary of a review that has inline comments: those are review
# threads, which the `threads` check already tracks. Only "Request changes" blocks readiness, so a plain comment may be
# a requested change the reviewer never marked as one. Prints the
# newest such comment as {id, url, who, text}, or null; always null unless the repository opts in with
# nudge_plain_comments. It only decides whether to ask the reviewer once, so it errs towards staying quiet:
#  - answered: the author commented, reviewed or replied in a review thread afterwards, or pushed. A review is for one
#    commit, so a different head means a push; a PR comment has no commit, so the head commit's date stands in.
#  - not a request: a `/command` (Prow), a bare LGTM or thanks, or an approval by the same reviewer afterwards.
#  - too old: only comments from the last 7 days, so enabling this never digs up old threads.
plain_comment_to_nudge() {
  if [[ "${NUDGE_PLAIN_COMMENTS:-false}" != "true" ]]; then echo null; return 0; fi
  jq -c --argjson days "$NUDGE_WINDOW_DAYS" '
    def human: (.author.__typename // "") != "Bot" and ((.author.login // "") | endswith("[bot]") | not);
    def maintainer: .authorAssociation == "OWNER" or .authorAssociation == "MEMBER" or .authorAssociation == "COLLABORATOR";
    def routine: (.bodyText // "") | test("^\\s*/") or test("^\\s*(lgtm|looks good( to me)?|thanks|thank you|ty|\\+1|👍)[\\s.!]*$"; "i");
    # Quoted back to the reviewer, so it must not ping anyone (@), cross-link an issue or PR (#), or carry HTML or a
    # marker comment (< >). One line, at most 100 characters.
    def excerpt:
      ((.bodyText // "") | gsub("[@#<>]"; "") | gsub("\\s+"; " ") | gsub("^ | $"; "")) as $t
      | if ($t | length) > 100 then $t[0:100] + "…" else $t end;
    .author.login as $author
    | (.commits.nodes[0].commit.oid // "") as $head
    | (.commits.nodes[0].commit.committedDate // "") as $pushed
    | (now - $days * 86400) as $since
    | ([.reviews.nodes[]? | select(human and .state == "APPROVED") | {who: (.author.login // ""), at: .submittedAt}]) as $approvals
    | ([.comments.nodes[]? | select(human) | {who: (.author.login // ""), at: .createdAt}]
       + [.reviews.nodes[]? | select(human) | select(.state != "PENDING") | {who: (.author.login // ""), at: .submittedAt}]
       + [.reviewThreads.nodes[]?.comments.nodes[]? | {who: (.author.login // ""), at: .createdAt}]) as $events
    | ([$events[] | select(.who == $author) | .at] | max // "") as $answered
    | [(.comments.nodes[]? | select(human and maintainer and .author.login != $author)
          | {id, url, text: excerpt, who: .author.login, at: .createdAt, routine: routine, pushed: ($pushed > .createdAt)}),
       (.reviews.nodes[]? | select(human and maintainer and .author.login != $author)
          | select(.state == "COMMENTED" and ((.bodyText // "") != ""))
          | select((.inlineComments.totalCount // 1) == 0)
          | {id, url, text: excerpt, who: .author.login, at: .submittedAt, routine: routine, pushed: ((.commit.oid // "") != $head)})]
    | map(. as $c | select(
        ($c.at > $answered) and ($c.pushed | not) and ($c.routine | not)
        and (($c.at | fromdateiso8601) > $since)
        and ([$approvals[] | select(.who == $c.who and .at > $c.at)] | length == 0)
        and ($c.id | test("^[A-Za-z0-9_=-]+$")) and ($c.who | test("^[A-Za-z0-9-]+$"))))
    | sort_by(.at) | last // null | if . == null then null else {id, url, who, text} end' pr.json
}

: > pr-details.jsonl
fetch_failures=0
NUDGE_WINDOW_DAYS=7
for number in $(jq -r '.[]' qualifying-numbers.json); do
  if ! fetch_pr_details; then
    echo "::warning::PR #$number: detail fetch failed — skipping this PR."
    fetch_failures=$((fetch_failures + 1))
  fi
done
jq -s '.' pr-details.jsonl > prs.json
returned_count=$(jq 'length' prs.json)

for ((i = 0; i < returned_count; i++)); do
  jq -c ".[$i]" prs.json > pr.json

  number=$(jq -r '.number' pr.json)
  author=$(jq -r '.author.login' pr.json)
  findings=$(jq -c -f readiness-checks.jq pr.json)
  # infra_checks aren't the author's to fix — FYI comment below, not a reason to withhold ready-for-review.
  blocking=$(jq -c '[.[] | select(.id != "infra_checks")]' <<< "$findings")
  infra_finding=$(jq -c '[.[] | select(.id == "infra_checks")]' <<< "$findings")
  has_label=$(jq -r '[.labels.nodes[].name] | any(. == "needs-author-action")' pr.json)
  has_ready_label=$(jq -r '[.labels.nodes[].name] | any(. == "ready-for-review")' pr.json)
  has_approval_label=$(jq -r '[.labels.nodes[].name] | any(. == "workflow-approval-required")' pr.json)

  # A maintainer's exempt label (e.g. hold) means hands off: leave every label and comment as it is.
  if jq -e --argjson exemptions "$EXEMPT_LABELS" \
    'any(.labels.nodes[]; .name as $l | $exemptions | index($l) != null)' pr.json > /dev/null; then
    echo "PR #$number: exempt label present — leaving labels as-is."
    continue
  fi

  # Gated workflows are absent from the rollup, so the PR would look clean. Matched by head SHA
  # (`pull_requests` is empty on fork runs); on lookup failure, skip the PR.
  head_sha=$(jq -r '.commits.nodes[0].commit.oid' pr.json)
  if [[ ! "$head_sha" =~ ^[0-9a-f]{40,64}$ ]]; then
    echo "::warning::PR #$number: missing head SHA — leaving labels as-is."
    continue
  fi
  if ! gated_count=$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head_sha}&status=action_required&per_page=1" --jq '.total_count') \
    || [[ ! "$gated_count" =~ ^[0-9]+$ ]]; then
    echo "::warning::PR #$number: could not check for workflow runs awaiting approval — leaving labels as-is."
    continue
  fi
  gated=false
  [[ "$gated_count" -gt 0 ]] && gated=true

  # A truncated connection means "no findings" is unproven — leave labels as-is rather than clear them.
  truncated=$(jq -r '
    [.labels.pageInfo.hasNextPage,
     (.commits.nodes[0].commit.statusCheckRollup.contexts.pageInfo.hasNextPage // false),
     .reviewThreads.pageInfo.hasNextPage]
    | any' pr.json)

  # A check still running isn't a finding — don't mark ready until it settles. No check data at all
  # (rollup absent, or empty — e.g. right after a PR opens) counts as pending too, not "clean."
  pending=$(jq -r '
    (.commits.nodes[0].commit.statusCheckRollup.contexts.nodes | length == 0) or
    ([.commits.nodes[0].commit.statusCheckRollup.contexts.nodes[]?
      | select((.name == null and .context == null) or
          (.status == null and .state == null) or
          (.status == "COMPLETED" and .conclusion == null) or
          (.status? and .status != "COMPLETED") or .state? == "PENDING" or .state? == "EXPECTED")]
     | length > 0)' pr.json)

  # Do not clear labels or report readiness for data from a head that changed during pagination.
  gh api "repos/${GITHUB_REPOSITORY}/pulls/$number" > live-pr.json
  if ! jq -e --arg head "$head_sha" \
    '.state == "open" and .draft == false and .head.sha == $head' live-pr.json > /dev/null; then
    echo "::warning::PR #$number: state/draft/head changed during evaluation — leaving labels as-is."
    continue
  fi

  if [[ "$truncated" == "true" ]]; then
    # Never clear author-action/approval labels based on a partial connection.
    [[ "$has_ready_label" == "true" ]] && gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --remove-label ready-for-review
    echo "::warning::PR #$number: truncated connection — not evaluating readiness."
    continue
  fi

  if [[ "$gated" == "true" && "$has_approval_label" == "false" ]]; then
    gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --add-label "workflow-approval-required"
  elif [[ "$gated" == "false" && "$has_approval_label" == "true" ]]; then
    gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "workflow-approval-required"
  fi

  if [[ "$blocking" == "[]" ]]; then
    if [[ "$gated" == "true" ]]; then
      # Not the author's to fix, but never ready while CI hasn't run.
      [[ "$has_ready_label" == "true" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "ready-for-review"
      [[ "$has_label" == "true" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "needs-author-action"
      echo "PR #$number: $gated_count workflow run(s) awaiting approval — not ready-for-review."
    elif [[ "$pending" == "true" ]]; then
      # A new commit's checks aren't settled yet — don't let an old ready-for-review keep claiming so.
      [[ "$has_ready_label" == "true" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "ready-for-review"
      echo "PR #$number: checks still pending — removed ready-for-review if present."
    else
      nudge_target=$(plain_comment_to_nudge)
      if [[ "$infra_finding" != "[]" ]]; then
        infra_message=$(jq -r '.[0].message' <<< "$infra_finding")
        infra_marker="<!-- pr-readiness-check-infra: $(printf '%s' "$infra_message" | sha1sum | cut -c1-12) -->"
      fi
      if [[ "$infra_finding" != "[]" || "$nudge_target" != "null" ]]; then
        gh api "repos/${GITHUB_REPOSITORY}/issues/$number/comments" --paginate --jq '.[].body' > comments.txt
      fi
      [[ "$has_label" == "true" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "needs-author-action"
      [[ "$has_ready_label" == "false" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --add-label "ready-for-review"

      if [[ "$infra_finding" != "[]" ]]; then
        # Marker hashes the message, so a different red check later gets its own comment.
        if ! grep -qF -- "$infra_marker" comments.txt; then
          gh pr comment "$number" --repo "${GITHUB_REPOSITORY}" --body "$infra_marker
Everything else here looks ready, so this is labelled \`ready-for-review\`. ${infra_message}"
        fi
      fi

      if [[ "$nudge_target" != "null" ]]; then
        # One per head commit, not per comment: reviewers talking to each other must not trigger a nudge per message.
        # A push answers the comment, so the same comment is never nudged under two heads.
        nudge_marker="<!-- pr-readiness-check-nudge: $head_sha -->"
        if ! grep -qF -- "$nudge_marker" comments.txt; then
          nudge_quote=$(jq -r '.text' <<< "$nudge_target")
          # Added after the substitution: $(...) would strip the blank line that ends the quote.
          [[ -n "$nudge_quote" ]] && nudge_quote="> ${nudge_quote}"$'\n\n'
          gh pr comment "$number" --repo "${GITHUB_REPOSITORY}" --body "$nudge_marker
@$(jq -r '.who' <<< "$nudge_target"), in [your comment]($(jq -r '.url' <<< "$nudge_target")):

${nudge_quote}This PR is ready for review and that comment has had no reply or push since. Is it a requested change? If so, please submit it as **Request changes**. The bot then tracks it until the author replies or pushes. If you'd rather not use a formal review, add \`hold\` to stop the bot changing labels on this PR, and remove it once the comment is resolved."
        fi
      fi
    fi
    continue
  fi

  ids=$(jq -r '[.[].id] | sort | join(",")' <<< "$blocking")
  marker="<!-- pr-readiness-check: $ids -->"
  # REST, fully paginated — a GraphQL comments(last: 100) cap could miss the marker on a busy PR.
  # Fetched to a file first, then searched — piping straight into `grep -q` would let grep close the
  # pipe on an early match, SIGPIPE-ing gh mid-fetch and (with pipefail) reporting failure even though
  # the marker was genuinely found.
  gh api "repos/${GITHUB_REPOSITORY}/issues/$number/comments" --paginate --jq '.[].body' > comments.txt
  already_reported=false
  grep -qF -- "$marker" comments.txt && already_reported=true

  [[ "$has_label" == "false" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --add-label "needs-author-action"
  [[ "$has_ready_label" == "true" ]] && gh pr edit "$number" --repo "${GITHUB_REPOSITORY}" --remove-label "ready-for-review"
  [[ "$already_reported" == "true" ]] && continue

  items=$(jq -r '.[] | "- " + .message' <<< "$blocking")
  body=$(printf '%s\n\n@%s this PR needs a bit more before it can be reviewed:\n%s' "$marker" "$author" "$items")

  gh pr comment "$number" --repo "${GITHUB_REPOSITORY}" --body "$body"
done

if [[ "$fetch_failures" -gt 0 ]]; then
  echo "::error::$fetch_failures PR detail fetch(es) failed; those PRs were skipped."
  exit 1
fi
