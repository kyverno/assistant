# shellcheck shell=bash
# Shared by the two launchers, container/kyverno (OpenShell) and container/kyverno-docker (--no-openshell).
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

# What is yours across an --upgrade: memory, chat history, your repo and username, the Copilot sign-in, the model you
# chose. Paths under the profile directory. The profile itself (skills, hooks, plugins, settings) comes from the new image.
# shellcheck disable=SC2034  # used by the launchers that source this file
STATE_FILES=".env config.yaml .kyverno-model-chosen mnemosyne/data memories sessions kyverno-dashboard kyverno-contributor state.db state.db-wal state.db-shm"

# Build the image from this checkout (--build), or pull it when it is missing or --upgrade asks for the newest.
ensure_image() {
  if [ "$BUILD" = 1 ]; then
    IMAGE="kyverno-assistant:dev"
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
  [ -z "$REPO" ] && read -r -p "  ${REPO_ASK:-Repo to manage (owner/repo) [kyverno/kyverno]: }" REPO
  REPO="${REPO:-kyverno/kyverno}"
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
