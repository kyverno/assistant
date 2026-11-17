# shellcheck shell=bash
# Shared by the two launchers, container/assistant (OpenShell) and container/assistant-docker (--no-openshell).
# Sourced, never run. The caller sets HERE, REPO_ROOT, IMAGE, BUILD and UPGRADE first, and defines
# instance_exists (is the sandbox or container already there?) and INSTANCE (its name).

# Colour only on a terminal, and not when NO_COLOR is set (https://no-color.org).
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'; C_OFF=$'\033[0m'
else
  C_BOLD=""; C_DIM=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_CYAN=""; C_OFF=""
fi
pass() { printf '  %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
fail() { printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$1" >&2; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
info() { printf '  %s\n' "$1"; }
step() { printf '\n%s▌ %s%s\n' "$C_CYAN$C_BOLD" "$1" "$C_OFF"; }
# What this run is, once, at the top: the assistant, the profile and the sandbox.
banner() { printf '\n  %sKyverno assistant%s %s· %s · %s%s\n' "$C_BOLD" "$C_OFF" "$C_DIM" "$1" "$2" "$C_OFF"; }
# A short table of what is ready, printed before the chat opens. ready_row LABEL VALUE, then ready_box.
READY_ROWS=()
ready_row() { READY_ROWS+=("$(printf '%-10s %s' "$1" "$2")"); }
ready_box() {
  local r
  [ "${#READY_ROWS[@]}" -gt 0 ] || return 0
  printf '  %s┌─ Ready ──────────────────────────────────%s\n' "$C_DIM" "$C_OFF"
  for r in "${READY_ROWS[@]}"; do printf '  %s│%s %s\n' "$C_DIM" "$C_OFF" "$r"; done
  printf '  %s└──────────────────────────────────────────%s\n' "$C_DIM" "$C_OFF"
}
# Reads a token or key into the variable named by $1, asking with $2. What you paste stays visible until you press
# Enter, then the line is replaced by a masked one with a tick. A hidden prompt looks stuck (macOS Terminal shows a key
# symbol and nothing else), and people paste twice.
read_token() {
  local __t="" __n __cols
  read -r -p "  $2: " __t </dev/tty
  if [ -n "$__t" ]; then
    __cols=$(tput cols 2>/dev/null || echo 80)
    __n=$(( (${#2} + 4 + ${#__t}) / __cols + 1 ))
    printf '\033[%dA\r\033[J' "$__n" >/dev/tty
    printf '  %s: %s…%s  %s✓%s %d characters\n' "$2" "${__t:0:5}" "${__t: -4}" "$C_GREEN" "$C_OFF" "${#__t}" >/dev/tty
  fi
  printf -v "$1" '%s' "$__t"
}

# gh's own sign-in only: a GH_TOKEN or GITHUB_TOKEN in the environment is not what we mean to store.
gh_token() { env -u GH_TOKEN -u GITHUB_TOKEN gh auth token --hostname github.com 2>/dev/null; }

# An owner/repo as GitHub allows it.
valid_repo() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$ && "${1#*/}" != "." && "${1#*/}" != ".." ]]; }

# The environment variable a model provider's key travels in.
provider_env_var() {
  case "$1" in
    anthropic) echo ANTHROPIC_API_KEY ;;
    openai) echo OPENAI_API_KEY ;;
    nvidia) echo NVIDIA_API_KEY ;;
    *) return 1 ;;
  esac
}

# What differs between the two assistants, for both launchers. They share the image, not the sandbox (or container),
# the GitHub token or the state. Sets HERMES_PROFILE, PROVIDER and PROVIDER_FILE (OpenShell), TOKEN_KEY (the name the
# token has in the secrets file without OpenShell), LOGIN_VAR, DEFAULT_NAME (the default name of that profile's instance) and the wording. 
# Picks the profile when --profile was not given: the one you already have a sandbox for, else it asks (on a terminal)
# or falls back to maintainer. Args: names of the existing sandboxes or containers, then the maintainer and the
# contributor default names (NAME_M and NAME_C, which select_profile uses).
choose_profile() {
  local existing="$1" m="$2" c="$3" has_m=0 has_c=0 choice
  NAME_M="$m"; NAME_C="$c"
  grep -qx "$m" <<<"$existing" && has_m=1
  grep -qx "$c" <<<"$existing" && has_c=1
  [ -n "$KIND" ] && return 0
  if [ "$has_m$has_c" = 10 ]; then
    KIND=maintainer; info "starting the maintainer assistant, the only one you have (--profile contributor for the other)"; return 0
  fi
  if [ "$has_m$has_c" = 01 ]; then
    KIND=contributor; info "starting the contributor assistant, the only one you have (--profile maintainer for the other)"; return 0
  fi
  KIND=maintainer
  if ! { [ -t 0 ] && [ -t 1 ]; }; then
    info "starting the maintainer assistant (no terminal to ask on; --profile contributor for the other)"; return 0
  fi
  printf '\n  Which assistant do you want?\n'
  printf '    1) maintainer   review the PR queue of a repo you maintain\n'
  printf '    2) contributor  find work, understand the code and get your PRs merged\n'
  read -r -p "  Choose [1]: " choice </dev/tty
  [ "$choice" = 2 ] && KIND=contributor
  return 0
}

# shellcheck disable=SC2034  # each launcher uses some of these, not all
select_profile() {
  case "$1" in
    maintainer)
      HERMES_PROFILE=maintainer; PROVIDER=assistant-repo-github; PROVIDER_FILE=repo-github.yaml
      TOKEN_KEY=REPO_GITHUB_TOKEN; LOGIN_VAR=MAINTAINER_GITHUB_LOGIN
      DEFAULT_NAME="$NAME_M"
      REPO_ASK="Repo to manage (owner/repo): "
      TOKEN_SCOPE="the assistant still cannot merge."
      TOKEN_PASTE_HELP="Paste your fine-grained GitHub token (see README step 1)." ;;
    contributor)
      HERMES_PROFILE=contributor; PROVIDER=assistant-contributor-github; PROVIDER_FILE=repo-github-contributor.yaml
      TOKEN_KEY=CONTRIBUTOR_REPO_GITHUB_TOKEN; LOGIN_VAR=CONTRIBUTOR_GITHUB_LOGIN
      DEFAULT_NAME="$NAME_C"
      REPO_ASK="Repo you contribute to (owner/repo): "
      TOKEN_SCOPE="the assistant can only read, comment, and open a PR from your fork."
      TOKEN_PASTE_HELP="Paste a fine-grained GitHub token of your own account (public repositories, read-only, is enough to read; commenting, and opening a PR, on the repository need a token that can write there)." ;;
    *) echo "unknown profile '$1' (maintainer or contributor)" >&2; exit 2 ;;
  esac
}

# The repository an existing instance is set up for. On later runs the host does not know it: it is in the instance's
# profile .env (ASSISTANT_REPO). Fills REPO if it is empty; leaves it empty if it
# cannot be read. Needs instance_exec and instance_exists.
repo_from_instance() {
  [ -z "${REPO:-}" ] || return 0
  instance_exists || return 0
  REPO="$(instance_exec sh -c "grep -E '^ASSISTANT_REPO=' /opt/hermes-home/.hermes/profiles/$HERMES_PROFILE/.env 2>/dev/null | head -n 1 | cut -d= -f2-" 2>/dev/null)" || REPO=""
  valid_repo "$REPO" 2>/dev/null || REPO=""
  return 0
}

# What is yours across an --upgrade: memory, chat history, your repo and username, the Copilot sign-in, the model you
# chose. Paths under the profile directory. The profile itself (skills, hooks, plugins, settings) comes from the new image.
# shellcheck disable=SC2034  # used by the launchers that source this file
STATE_FILES=".env config.yaml .kyverno-model-chosen mnemosyne/data memories sessions maintainer-dashboard contributor-state state.db state.db-wal state.db-shm"

# Build the image from this checkout (--build), or pull it when it is missing or --upgrade asks for the newest.
# --org NAME (or ASSISTANT_ORG) picks the organisation pack, container/org/NAME/, to bake into a build; the default is
# kyverno's, the worked example.
ensure_image() {
  ORG_PACK="${ORG_PACK:-${ASSISTANT_ORG:-kyverno}}"
  if [[ ! "$ORG_PACK" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then fail "'$ORG_PACK' is not a valid pack name"; exit 2; fi
  if [ "$BUILD" != 1 ] && [ "$ORG_PACK" != kyverno ]; then
    fail "--org NAME bakes your pack into an image you build: add --build (the published image reads your pack from <owner>/.github/assistant-org.yaml instead)"; exit 2
  fi
  if [ "$BUILD" = 1 ] && [ ! -f "$REPO_ROOT/container/org/$ORG_PACK/org.yaml" ]; then
    fail "no pack at container/org/$ORG_PACK/org.yaml (see docs/organisation-pack.md)"; exit 2
  fi
  if [ "$BUILD" = 1 ]; then
    [ -f "$REPO_ROOT/container/Dockerfile" ] || { fail "--build needs a checkout of the repository (this launcher was taken from an image)"; exit 1; }
    IMAGE="assistant:dev"
    info "building $IMAGE from this checkout (the first build takes several minutes)..."
    docker build -f "$REPO_ROOT/container/Dockerfile" -t "$IMAGE" --build-arg "ORG_PACK=$ORG_PACK" "$REPO_ROOT" \
      || { fail "image build failed"; exit 1; }
    if [ "$UPGRADE" != 1 ] && instance_exists; then
      info "note: '$INSTANCE' already exists and keeps running the image it was made from; add --upgrade to switch it to this build"
    fi
  elif [ "$UPGRADE" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    info "pulling $IMAGE..."
    if ! docker pull "$IMAGE"; then
      # An image that only exists on this machine (built with --build) cannot be pulled; that is fine.
      if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        info "could not pull it; using the copy already on this machine"
      else
        fail "could not pull $IMAGE — use --build, or --image with an image you have"
        exit 1
      fi
    fi
  fi
  pass "image $IMAGE"
}

# Ask which repo to manage and get a GitHub token, by a gh sign-in or a pasted token; check it against the repo.
# Sets PAT, REPO and (when it can be read from the token) LOGIN. The caller says what happens to the token by defining
# token_note_gh (printed under the gh option) and token_note_paste (printed before the paste prompt), and picks the
# default option in TOKEN_DEFAULT (1 = gh, 2 = paste).
acquire_github_token() {
  [ -z "$REPO" ] && read -r -p "  ${REPO_ASK:-Repo to manage (owner/repo): }" REPO
  [ -n "$REPO" ] || { fail "no repository: type owner/repo, or set ASSISTANT_REPO"; exit 1; }
  valid_repo "$REPO" || { fail "'$REPO' is not an owner/repo name (letters, digits, dots, dashes, underscores)"; exit 1; }
  PAT=""
  local method="paste" choice="" code
  if command -v gh >/dev/null 2>&1; then
    info "How do you want to authorize GitHub?"
    info "  1) Sign in with GitHub (gh): a browser sign-in, nothing to create. gh always asks for the 'repo'"
    info "     scope, so the token can act on every repository you have access to, private ones included, not"
    token_note_gh
    info "  2) Paste a fine-grained token (README step 1): limited to the one repository you scope it to."
    read -r -p "  Choose [$TOKEN_DEFAULT]: " choice </dev/tty
    case "${choice:-$TOKEN_DEFAULT}" in
      1) method="gh" ;;
      2) ;;
      *) fail "'$choice' is not 1 or 2"; exit 1 ;;
    esac
  else
    info "The GitHub CLI (gh) is not installed, so there is no browser sign-in (https://cli.github.com)."
  fi
  if [ "$method" = gh ]; then
    PAT=$(gh_token) || PAT=""
    if [ -z "$PAT" ]; then
      info "gh is not signed in. Starting its browser sign-in..."
      env -u GH_TOKEN -u GITHUB_TOKEN gh auth login --hostname github.com --git-protocol https --web </dev/tty \
        || { fail "the gh sign-in did not finish (re-run, or choose 2 and paste a token)"; exit 1; }
      PAT=$(gh_token) || PAT=""
      [ -z "$PAT" ] && { fail "gh did not give a token after signing in"; exit 1; }
    else
      pass "using your existing gh sign-in"
    fi
  else
    token_note_paste
    read_token PAT "GitHub token"
  fi
  [ -z "$PAT" ] && { fail "no token entered (this step needs a terminal to ask for it)"; exit 1; }
  # Check it before storing it. The token goes in through stdin, so it never shows in a process list.
  code=$(printf 'header = "Authorization: Bearer %s"\n' "$PAT" \
         | curl -s -o /dev/null -w '%{http_code}' -K - "https://api.github.com/repos/$REPO")
  case "$code" in
    200) pass "GitHub accepts the token for $REPO" ;;
    401) fail "GitHub rejected the token (401): wrong, expired or revoked"; exit 1 ;;
    403|404) fail "the token cannot read $REPO ($code): scope it to that repo, or get the org to approve it"; exit 1 ;;
    *) info "could not verify the token (HTTP ${code:-none}) — continuing" ;;
  esac
  if [ -z "$LOGIN" ]; then
    LOGIN=$(printf 'header = "Authorization: Bearer %s"\n' "$PAT" \
            | curl -s -m 10 -K - https://api.github.com/user | sed -n 's/.*"login": *"\([^"]*\)".*/\1/p' | head -n 1)
    if [[ "$LOGIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]]; then pass "signed in to GitHub as $LOGIN"; else LOGIN=""; fi
  fi
}

# --- pushing to your own fork (container/assistant --push-to-fork) ---
# The login a GitHub token belongs to. The token goes in through stdin, so it never shows in a process list.
token_login() {
  printf 'header = "Authorization: Bearer %s"\n' "$1" \
    | curl -s -K - https://api.github.com/user | sed -n 's/.*"login": *"\([^"]*\)".*/\1/p' | head -n 1
}

# The OpenShell provider profile for the push credential, written for a list of owner/repo targets (your forks, and for
# a maintainer the repository they manage). OpenShell checks each request outside the sandbox: a git push may only
# reach /<owner>/<repo> for these, so the credential cannot be used to push anywhere else. It is a second credential, apart from the read-only token, and the sandbox
# only ever holds a placeholder for it. Git reads the repository over a path with or without ".git", hence both.
git_push_profile() {
  local login="$1" t; shift
  cat <<EOF
id: assistant-git-push
display_name: assistant git push
description: Lets the sandbox push branches to ${login}'s own forks (and a maintainer's own repository), and nowhere else
category: source_control
credentials:
  - name: push_token
    description: GitHub token that can push to your fork (the gh sign-in's token, or a fine-grained token with Contents write on it)
    env_vars: [GIT_PUSH_TOKEN]
    required: true
    auth_style: basic
    header_name: authorization
discovery:
  credentials: [push_token]
endpoints:
  - host: github.com
    port: 443
    protocol: rest
    enforcement: enforce
    rules:
EOF
  for t in "$@"; do
    for suffix in "" ".git"; do
      printf '      - allow: { method: GET, path: "/%s%s/info/refs" }\n' "$t" "$suffix"
      printf '      - allow: { method: POST, path: "/%s%s/git-receive-pack" }\n' "$t" "$suffix"
    done
  done
  cat <<EOF
binaries:
  - /usr/bin/git
  - /usr/lib/git-core/git-remote-http
  - /usr/lib/git-core/git-remote-https
EOF
}

# --- the Slack review digest (container/assistant --slack, also offered on a maintainer's first run) ---
# The bot token goes to OpenShell as a provider; where and when to post are not secret and are kept in the sandbox's .env.
SLACK_PROVIDER=assistant-slack
SLACK_MANIFEST_URL='https://api.slack.com/apps?new_app=1&manifest_json=%7B%22display_information%22%3A%7B%22name%22%3A%22Assistant%20digest%22%2C%22description%22%3A%22Posts%20the%20daily%20review%20digest%22%7D%2C%22features%22%3A%7B%22bot_user%22%3A%7B%22display_name%22%3A%22assistant%22%2C%22always_online%22%3Afalse%7D%7D%2C%22oauth_config%22%3A%7B%22scopes%22%3A%7B%22bot%22%3A%5B%22chat%3Awrite%22%2C%22im%3Awrite%22%5D%7D%7D%2C%22settings%22%3A%7B%22org_deploy_enabled%22%3Afalse%2C%22socket_mode_enabled%22%3Afalse%2C%22token_rotation_enabled%22%3Afalse%7D%7D'

# This machine's time zone (IANA name), so "09:00" means 09:00 for you and not for the sandbox's clock.
slack_tz() {
  local z
  z=$(readlink /etc/localtime 2>/dev/null | sed -n 's#.*/zoneinfo/##p')
  [ -z "$z" ] && z=$(cat /etc/timezone 2>/dev/null)
  [[ "$z" =~ ^[A-Za-z0-9_+/-]+$ ]] || z=UTC
  echo "$z"
}

# Asks what the digest needs. Sets SLACK_TOKEN (empty when one is already stored in OpenShell), SLACK_CHANNEL,
# SLACK_TIME and SLACK_TZ. ASSISTANT_SLACK_TOKEN, ASSISTANT_SLACK_CHANNEL and ASSISTANT_SLACK_TIME skip the questions.
acquire_slack() {
  local reply
  SLACK_TOKEN=""
  if ! openshell provider get "$SLACK_PROVIDER" >/dev/null 2>&1; then
    SLACK_TOKEN="${ASSISTANT_SLACK_TOKEN:-}"
    if [ -z "$SLACK_TOKEN" ]; then
      info "The assistant will post a short review digest on weekdays. It needs a Slack bot token (about two minutes):"
      info ""
      info "  ${C_BOLD}1${C_OFF}  Open this link, choose your workspace and click Create:"
      info "     $SLACK_MANIFEST_URL"
      info "  ${C_BOLD}2${C_OFF}  Click Install to Workspace, then Allow."
      info "  ${C_BOLD}3${C_OFF}  Under OAuth & Permissions, copy the Bot User OAuth Token (it starts with xoxb-)."
      info ""
      info "${C_DIM}The token goes to OpenShell, never into the sandbox. No permission to add apps? Ask a workspace admin.${C_OFF}"
      read_token SLACK_TOKEN "Slack bot token"
    fi
    [ -z "$SLACK_TOKEN" ] && { fail "no Slack token entered (this step needs a terminal to ask for it)"; exit 1; }
    # Check it before storing it. The token goes in through stdin, so it never shows in a process list.
    reply=$(printf 'header = "Authorization: Bearer %s"\n' "$SLACK_TOKEN" | curl -s -m 15 -X POST -K - https://slack.com/api/auth.test)
    case "$reply" in
      *'"ok":true'*) pass "Slack accepts the token (workspace: $(printf '%s' "$reply" | sed -n 's/.*"team":"\([^"]*\)".*/\1/p'))" ;;
      *'"ok":false'*) fail "Slack rejected the token: $(printf '%s' "$reply" | sed -n 's/.*"error":"\([^"]*\)".*/\1/p')"; exit 1 ;;
      *) info "could not check the token with Slack — continuing" ;;
    esac
  fi
  SLACK_CHANNEL="${ASSISTANT_SLACK_CHANNEL:-}"
  if [ -z "$SLACK_CHANNEL" ]; then
    info ""
    info "Where should it post? Your Slack member ID (U...) sends you a DM: profile menu, Copy member ID."
    info "Or a channel ID (C...): invite the bot there first with /invite @assistant."
    read -r -p "  Member or channel ID: " SLACK_CHANNEL </dev/tty
  fi
  [[ "$SLACK_CHANNEL" =~ ^[CDGUW][A-Z0-9]{7,}$ ]] || { fail "'$SLACK_CHANNEL' is not a Slack member or channel ID (it starts with U, W, C, G or D)"; exit 1; }
  SLACK_TIME="${ASSISTANT_SLACK_TIME:-}"
  if [ -z "$SLACK_TIME" ]; then
    read -r -p "  What time, on weekdays, in your time zone ($(slack_tz))? [09:00]: " SLACK_TIME </dev/tty
    SLACK_TIME="${SLACK_TIME:-09:00}"
  fi
  [[ "$SLACK_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { fail "'$SLACK_TIME' is not a time like 09:00 (24 hours)"; exit 1; }
  # shellcheck disable=SC2034  # used by the launcher that sources this file
  SLACK_TZ="$(slack_tz)"
}

# Writes KEY=VALUE lines into the profile's .env inside the sandbox (values are checked by the caller). Needs instance_exec.
sandbox_env_set() {
  # shellcheck disable=SC2016  # the variables are expanded by the shell inside the sandbox
  instance_exec sh -c 'f=/opt/hermes-home/.hermes/profiles/$1/.env; shift
    for kv in "$@"; do k=${kv%%=*}; { grep -v "^$k=" "$f" 2>/dev/null; echo "$kv"; } > "$f.new"; cat "$f.new" > "$f"; rm -f "$f.new"; done' sh "$HERMES_PROFILE" "$@"
}
