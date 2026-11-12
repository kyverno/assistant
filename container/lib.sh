# shellcheck shell=bash
# Shared by the two launchers, container/assistant (OpenShell) and container/assistant-docker (--no-openshell).
# Sourced, never run. The caller sets HERE, REPO_ROOT, IMAGE, BUILD and UPGRADE first, and defines
# instance_exists (is the sandbox or container already there?) and INSTANCE (its name).

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }
info() { printf '  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

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
# token has in the secrets file without OpenShell), LOGIN_VAR, DEFAULT_NAME (the existing instance's name, else the new
# default) and the wording. The provider names keep their kyverno- prefix: they hold the stored token, which cannot be moved.
# Picks the profile when --profile was not given: the one you already have a sandbox for, else it asks (on a terminal)
# or falls back to maintainer. Args: names of the existing sandboxes or containers, then the maintainer and the
# contributor default names.
# New instances get neutral names. One made before the rename (kyverno-assistant, kyverno-contributor) keeps working
# under the name it has: FOUND_M and FOUND_C are the existing instance's name, whichever of the two it is.
# Args: the existing names, then the maintainer's new and old name, then the contributor's new and old name.
choose_profile() {
  local existing="$1" m="$2" m_old="$3" c="$4" c_old="$5" has_m=0 has_c=0 choice
  NEW_M="$m"; NEW_C="$c"; FOUND_M=""; FOUND_C=""
  if grep -qx "$m" <<<"$existing"; then FOUND_M="$m"; elif grep -qx "$m_old" <<<"$existing"; then FOUND_M="$m_old"; fi
  if grep -qx "$c" <<<"$existing"; then FOUND_C="$c"; elif grep -qx "$c_old" <<<"$existing"; then FOUND_C="$c_old"; fi
  [ -n "$KIND" ] && return 0
  [ -n "$FOUND_M" ] && has_m=1
  [ -n "$FOUND_C" ] && has_c=1
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
      HERMES_PROFILE=maintainer; PROVIDER=kyverno-repo-github; PROVIDER_FILE=repo-github.yaml
      TOKEN_KEY=REPO_GITHUB_TOKEN; LOGIN_VAR=MAINTAINER_GITHUB_LOGIN
      DEFAULT_NAME="${FOUND_M:-${NEW_M:-assistant-maint}}"
      REPO_ASK="Repo to manage (owner/repo): "
      TOKEN_SCOPE="the assistant still cannot merge."
      TOKEN_PASTE_HELP="Paste your fine-grained GitHub token (see README step 1)." ;;
    contributor)
      HERMES_PROFILE=contributor; PROVIDER=kyverno-contributor-github; PROVIDER_FILE=repo-github-contributor.yaml
      TOKEN_KEY=CONTRIBUTOR_REPO_GITHUB_TOKEN; LOGIN_VAR=CONTRIBUTOR_GITHUB_LOGIN
      DEFAULT_NAME="${FOUND_C:-${NEW_C:-assistant-contrib}}"
      REPO_ASK="Repo you contribute to (owner/repo): "
      TOKEN_SCOPE="the assistant can only read, comment, and open a PR from your fork."
      TOKEN_PASTE_HELP="Paste a fine-grained GitHub token of your own account (public repositories, read-only, is enough to read; commenting, and opening a PR, on the repository need a token that can write there)." ;;
    *) echo "unknown profile '$1' (maintainer or contributor)" >&2; exit 2 ;;
  esac
}

# The Hermes profile folder an existing instance really has. One made before the rename calls it kyverno or
# kyverno-contributor, and an --upgrade has to read the state from there; the new image's own name is HERMES_PROFILE.
# The launcher defines instance_exec (run a command inside the instance). Sets INSTANCE_PROFILE.
instance_profile() {
  local old have
  case "$KIND" in maintainer) old=kyverno ;; *) old=kyverno-contributor ;; esac
  INSTANCE_PROFILE="$HERMES_PROFILE"
  instance_exists || return 0
  have="$(instance_exec ls /opt/hermes-home/.hermes/profiles 2>/dev/null)" || return 0
  if ! grep -qx "$HERMES_PROFILE" <<<"$have" && grep -qx "$old" <<<"$have"; then INSTANCE_PROFILE="$old"; fi
  return 0
}

# What is yours across an --upgrade: memory, chat history, your repo and username, the Copilot sign-in, the model you
# chose. Paths under the profile directory. The profile itself (skills, hooks, plugins, settings) comes from the new image.
# shellcheck disable=SC2034  # used by the launchers that source this file
STATE_FILES=".env config.yaml .kyverno-model-chosen mnemosyne/data memories sessions kyverno-dashboard maintainer-dashboard kyverno-contributor contributor-state state.db state.db-wal state.db-shm"

# Build the image from this checkout (--build), or pull it when it is missing or --upgrade asks for the newest.
ensure_image() {
  if [ "$BUILD" = 1 ]; then
    [ -f "$REPO_ROOT/container/Dockerfile" ] || { fail "--build needs a checkout of the repository (this launcher was taken from an image)"; exit 1; }
    IMAGE="assistant:dev"
    info "building $IMAGE from this checkout (the first build takes several minutes)..."
    docker build -f "$REPO_ROOT/container/Dockerfile" -t "$IMAGE" "$REPO_ROOT" || { fail "image build failed"; exit 1; }
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
    read -r -s -p "  GitHub token: " PAT </dev/tty
    echo
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
id: kyverno-git-push
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
