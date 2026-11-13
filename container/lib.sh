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

# --- the organisation's policy (container/assistant-policy.yaml) ---
# A small YAML subset: top-level "key: value" and "key:" followed by "  - item" lines (or "key: [a, b]").
# Where it comes from, in order: ASSISTANT_POLICY (a file, or an https URL) if set; else the policy the
# organisation publishes as <owner>/.github/assistant-policy.yaml, found from the repository you enter, which you
# confirm; else the file next to the launcher. A policy that cannot be read, or that is malformed, stops the launcher
# instead of being ignored.
POLICY_SET=0
POLICY_TEXT=""
POLICY_SOURCE=""
POLICY_EXPLICIT=0
POLICY_RESOLVED=0

# The policy text: the one chosen (or fetched), else the file next to the launcher, else nothing.
policy_text() {
  if [ "$POLICY_SET" = 1 ]; then printf '%s\n' "$POLICY_TEXT"; return 0; fi
  local f="${ASSISTANT_POLICY:-$HERE/assistant-policy.yaml}"
  [ -f "$f" ] && cat "$f"
  return 0
}

policy_scalar() {
  policy_text | awk -v k="$1" '
    { sub(/[ \t]*#.*/, "") }
    $0 ~ "^" k ":[ \t]*[^ \t[]" { sub(/^[^:]*:[ \t]*/, ""); gsub(/["\047]/, ""); print; exit }'
}

policy_list() {
  policy_text | awk -v k="$1" '
    { sub(/[ \t]*#.*/, "") }
    $0 ~ "^" k ":[ \t]*\\[" { sub(/^[^[]*\[/, ""); sub(/\].*/, ""); n = split($0, a, ","); for (i = 1; i <= n; i++) { gsub(/^[ \t"\047]+|[ \t"\047]+$/, "", a[i]); if (a[i] != "") print a[i] } exit }
    $0 ~ "^" k ":[ \t]*$" { f = 1; next }
    f && /^[ \t]+-[ \t]*/ { sub(/^[ \t]+-[ \t]*/, ""); gsub(/^["\047]+|["\047]+$/, ""); print; next }
    f && /[^ \t]/ { exit }'
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then printf '%s' "$1" | shasum -a 256; else printf '%s' "$1" | sha256sum; fi | cut -d' ' -f1
}

# An owner, then a repository name that may end in or contain a "*". An owner is never a wildcard.
valid_repo_pattern() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.*-]+$ && "$1" != *..* ]]; }

# Why the current policy is not usable, or nothing. Prints to stderr; the caller decides whether to stop.
policy_problem() {
  local src="${POLICY_SOURCE:-${ASSISTANT_POLICY:-$HERE/assistant-policy.yaml}}" p rc=0
  while IFS= read -r p; do
    [ -z "$p" ] || valid_repo_pattern "$p" || { fail "$src: '$p' under repos is not an owner/repo pattern (e.g. acme/* or acme/api)"; rc=1; }
  done <<<"$(policy_list repos)"
  case "$(policy_scalar token)" in ""|any|fine-grained) ;; *) fail "$src: token must be 'any' or 'fine-grained'"; rc=1 ;; esac
  p="$(policy_scalar default_repo)"
  [ -z "$p" ] || valid_repo "$p" || { fail "$src: default_repo '$p' is not an owner/repo name"; rc=1; }
  return $rc
}

# Use this text as the policy, unless it is not valid (then the previous one stays).
adopt_policy() {
  local old_text="$POLICY_TEXT" old_src="$POLICY_SOURCE" old_set="$POLICY_SET"
  POLICY_TEXT="$2" POLICY_SOURCE="$1" POLICY_SET=1
  policy_problem && return 0
  POLICY_TEXT="$old_text" POLICY_SOURCE="$old_src" POLICY_SET="$old_set"
  return 1
}

# Print a policy from an https URL or a file. A token is sent only to raw.githubusercontent.com, and then without
# following redirects, so it cannot be handed on to another host.
read_policy_source() {
  local src="$1" token="${2:-}" out
  case "$src" in
    https://raw.githubusercontent.com/*)
      if [ -n "$token" ]; then
        out="$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl -fs -m 15 --max-filesize 65536 -K - "$src")" || return 1
      else
        out="$(curl -fsL -m 15 --max-filesize 65536 "$src")" || return 1
      fi ;;
    https://*) out="$(curl -fsL -m 15 --max-filesize 65536 "$src")" || return 1 ;;
    *://*) fail "'$src': only https policy locations are supported"; return 1 ;;
    *) [ -f "$src" ] || return 1; out="$(head -c 65536 "$src")" ;;
  esac
  printf '%s\n' "$out"
}

# What the person decided for a repository, so the next run does not ask again: "<repo>|<source or none>|<sha256>".
choice_file() { printf '%s' "${ASSISTANT_POLICY_CHOICES:-${XDG_CONFIG_HOME:-$HOME/.config}/assistant/policy-choices}"; }
choice_get() {
  [ -f "$(choice_file)" ] || return 0
  awk -v k="$(lower "$1")|" 'index($0, k) == 1 { print substr($0, length(k) + 1); exit }' "$(choice_file)"
}
choice_set() {
  local f key; f="$(choice_file)"; key="$(lower "$1")"
  mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
  { [ -f "$f" ] && awk -v k="$key|" 'index($0, k) != 1' "$f"; printf '%s|%s|%s\n' "$key" "$2" "$3"; } > "$f.tmp" 2>/dev/null \
    && mv "$f.tmp" "$f"
  return 0
}

# Use a policy and remember it; a policy that is not valid stops the launcher (it does not fall back to none).
take_policy() {
  local repo="$1" src="$2" text="$3" hash
  adopt_policy "$src" "$text" || { info "($src is not a usable policy)"; exit 1; }
  hash="$(sha256_of "$text")"
  choice_set "$repo" "$src" "$hash"
  POLICY_RESOLVED=1
}

no_policy() { # repo, sha256 of a policy that was offered and declined (or nothing)
  POLICY_TEXT="" POLICY_SOURCE="" POLICY_SET=1 POLICY_RESOLVED=1
  choice_set "$1" none "${2:-}"
}

# Ask about a policy that was found or has changed: Y uses it, n goes without, anything else is another location.
offer_policy() {
  local repo="$1" src="$2" text="$3" why="$4" hash ans other
  hash="$(sha256_of "$text")"
  if ! { [ -t 0 ] && [ -t 1 ]; }; then info "$why $src; using it"; take_policy "$repo" "$src" "$text"; return 0; fi
  info "$why $src"
  read -r -p "  Use it? [Y]es, [n]o policy, or type another location (https URL or file): " ans </dev/tty
  case "$ans" in
    ""|[Yy]|[Yy][Ee][Ss]) take_policy "$repo" "$src" "$text" ;;
    [Nn]|[Nn][Oo]) no_policy "$repo" "$hash" ;;
    *) other="$(read_policy_source "$ans")" || { fail "could not read a policy from '$ans'"; exit 1; }
       take_policy "$repo" "$ans" "$other" ;;
  esac
}

# Nothing was found for the owner: ask whether the person has one.
ask_policy_location() {
  local repo="$1" url="$2" ans text
  if ! { [ -t 0 ] && [ -t 1 ]; }; then no_policy "$repo"; return 0; fi
  info "No policy found at $url, so nothing limits which repositories this assistant can be started on."
  while :; do
    read -r -p "  Do you have a policy to use? Enter its location (https URL or file), or n [n]: " ans </dev/tty
    case "$ans" in
      ""|[Nn]|[Nn][Oo]) no_policy "$repo"; return 0 ;;
      *) if text="$(read_policy_source "$ans")" && adopt_policy "$ans" "$text"; then take_policy "$repo" "$ans" "$text"; return 0; fi
         fail "could not use '$ans' as a policy" ;;
    esac
  done
}

# Decide which policy applies to REPO, then refuse the repository if the policy does not list it. Called before the
# token exists (FINAL=0, public lookup only) and again once it does (FINAL=1, which also sees a private .github repo and
# asks if nothing was found). A person who already chose is not asked again unless the organisation's file changed.
resolve_policy() {
  local repo="$1" token="${2:-}" final="${3:-0}" owner url text saved src prev
  [ "$POLICY_EXPLICIT" = 1 ] && POLICY_RESOLVED=1
  if [ "$POLICY_RESOLVED" != 1 ]; then
    owner="${repo%%/*}"
    url="https://raw.githubusercontent.com/$owner/.github/HEAD/assistant-policy.yaml"
    saved="$(choice_get "$repo")"; src="${saved%|*}"; prev="${saved##*|}"
    if [ -n "$saved" ] && [ "$src" != none ]; then
      text="$(read_policy_source "$src" "$token")" \
        || { fail "the policy chosen earlier for $repo ($src) could not be read; to choose again, delete its line in $(choice_file)"; exit 1; }
      if [ "$(sha256_of "$text")" = "$prev" ]; then
        info "using the policy from $src, as chosen before"; take_policy "$repo" "$src" "$text"
      else
        offer_policy "$repo" "$src" "$text" "The policy has changed since you last used it:"
      fi
    elif text="$(read_policy_source "$url" "$token" 2>/dev/null)"; then
      if [ -n "$saved" ] && [ "$(sha256_of "$text")" = "$prev" ]; then no_policy "$repo" "$prev"   # declined this very file before
      else offer_policy "$repo" "$url" "$text" "Found $owner's policy at"; fi
    elif [ "$final" = 1 ]; then
      if [ -n "$saved" ]; then no_policy "$repo"; else ask_policy_location "$repo" "$url"; fi
    fi
  fi
  [ "$POLICY_RESOLVED" = 1 ] && check_scope "$repo"
  return 0
}

# Read ASSISTANT_POLICY, if set, and stop on a policy the launcher cannot trust.
policy_check() {
  local f="${ASSISTANT_POLICY:-}" text
  if [ -n "$f" ]; then
    POLICY_EXPLICIT=1
    text="$(read_policy_source "$f")" || { fail "ASSISTANT_POLICY: could not read a policy from '$f'"; exit 1; }
    POLICY_TEXT="$text" POLICY_SOURCE="$f" POLICY_SET=1
  fi
  policy_problem || exit 1
}

# Is this repository inside the policy's repos? GitHub names are case-insensitive, so the comparison is too.
repo_in_scope() {
  local repo pat patterns; repo="$(lower "$1")"; patterns="$(policy_list repos)"
  [ -z "$patterns" ] && return 0
  while IFS= read -r pat; do
    pat="$(lower "$pat")"
    # shellcheck disable=SC2053  # the pattern is meant as a glob; valid_repo_pattern allows only letters, digits, . _ - and *
    [[ "$repo" == $pat ]] && return 0
  done <<<"$patterns"
  return 1
}

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
      HERMES_PROFILE=maintainer; PROVIDER=assistant-repo-github; PROVIDER_FILE="repo-github.yaml"
      TOKEN_KEY=REPO_GITHUB_TOKEN; LOGIN_VAR=MAINTAINER_GITHUB_LOGIN
      DEFAULT_NAME="$NAME_M"
      REPO_ASK="Repo to manage"
      TOKEN_SCOPE="the assistant still cannot merge."
      TOKEN_PASTE_HELP="Paste your fine-grained GitHub token (see README step 1)." ;;
    contributor)
      HERMES_PROFILE=contributor; PROVIDER=assistant-contributor-github; PROVIDER_FILE="repo-github-contributor.yaml"
      TOKEN_KEY=CONTRIBUTOR_REPO_GITHUB_TOKEN; LOGIN_VAR=CONTRIBUTOR_GITHUB_LOGIN
      DEFAULT_NAME="$NAME_C"
      REPO_ASK="Repo you contribute to"
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
    fail "--org NAME bakes your pack into an image you build: add --build (the published image carries Kyverno's pack)"; exit 2
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

# Refuse a repository the organisation's policy does not list.
check_scope() {
  repo_in_scope "$1" && return 0
  fail "'$1' is not a repository this organisation's policy allows. Allowed:"
  policy_list repos | sed 's/^/      /' >&2
  info "(policy: ${POLICY_SOURCE:-the one next to the launcher})"
  exit 1
}

# With token: fine-grained in the policy, refuse a token that is not limited to selected repositories. GitHub sends an
# X-OAuth-Scopes header for a classic token and for the gh sign-in's, and none for a fine-grained one.
token_is_broad() {
  printf 'header = "Authorization: Bearer %s"\n' "$1" \
    | curl -s -m 10 -D - -o /dev/null -K - https://api.github.com/user | grep -qi '^x-oauth-scopes:'
}

check_token_scope() {
  [ "$(policy_scalar token)" = fine-grained ] || return 0
  if token_is_broad "$1"; then
    fail "this token can act on every repository you have access to, and the organisation's policy requires a fine-grained token (README step 1)"
    exit 1
  fi
}

# Ask which repo to manage and get a GitHub token, by a gh sign-in or a pasted token; check it against the repo.
# Sets PAT, REPO and (when it can be read from the token) LOGIN. The caller says what happens to the token by defining
# token_note_gh (printed under the gh option) and token_note_paste (printed before the paste prompt), and picks the
# default option in TOKEN_DEFAULT (1 = gh, 2 = paste).
acquire_github_token() {
  local default; default="$(policy_scalar default_repo)"
  [ -z "$REPO" ] && read -r -p "  ${REPO_ASK:-Repo to manage} (owner/repo)${default:+ [$default]}: " REPO
  REPO="${REPO:-$default}"
  [ -n "$REPO" ] || { fail "no repository: type owner/repo, or set ASSISTANT_REPO"; exit 1; }
  valid_repo "$REPO" || { fail "'$REPO' is not an owner/repo name (letters, digits, dots, dashes, underscores)"; exit 1; }
  resolve_policy "$REPO" "" 0
  PAT=""
  local method="paste" choice="" code
  if [ "$(policy_scalar token)" = fine-grained ]; then
    info "Your organisation's policy requires a fine-grained token limited to the repositories it needs, so there is no gh sign-in."
  elif command -v gh >/dev/null 2>&1; then
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
    200) pass "GitHub accepts the token for $REPO"; resolve_policy "$REPO" "$PAT" 1; check_token_scope "$PAT" ;;
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
