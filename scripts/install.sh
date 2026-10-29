#!/bin/bash
# Idempotent installer for the kyverno-assistant Hermes profile. Re-run this
# same script after each step it asks you to do (fill in .env, etc.) — it
# detects what's already done and only does what's left.
set -uo pipefail

PROFILE="${1:-kyverno}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/profiles/maintainer"
PROFILE_DIR="$HOME/.hermes/profiles/$PROFILE"

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; }
info() { printf '  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

cd "$SRC" 2>/dev/null || { fail "$SRC not found"; exit 1; }
if [ ! -f distribution.yaml ]; then
  fail "$SRC/distribution.yaml not found"
  exit 1
fi

step "1. Prerequisites"

if ! command -v hermes >/dev/null 2>&1; then
  fail "hermes CLI not found on PATH — install it: https://hermes-agent.nousresearch.com"
  exit 1
fi
pass "hermes CLI found ($(hermes --version 2>&1 | head -1))"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found on PATH — the Slack MCP server and the sandboxed terminal run in Docker"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  fail "docker is installed but not running — start Docker and re-run this script"
  exit 1
fi
pass "docker is running"

# The helpers below parse YAML with python3. Use one that has PyYAML: the system python3 if it does,
# otherwise Hermes' own interpreter (which always does). Without this the model-keeping step fails
# silently and every re-run resets the model to the shipped default.
HERMES_DIR=$(hermes --version 2>/dev/null | sed -n 's/^Install directory: //p')
HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"
PY=""
for cand in python3 "$HERMES_DIR/venv/bin/python"; do
  cand_path=$(command -v "$cand" 2>/dev/null) || continue
  if "$cand_path" -c 'import yaml' >/dev/null 2>&1; then PY="$cand_path"; break; fi
done
if [ -z "$PY" ]; then
  fail "no Python with PyYAML found — run: python3 -m pip install pyyaml   then re-run this script"
  exit 1
fi
python3() { "$PY" "$@"; }
pass "python with PyYAML ($PY)"

step "2. Profile install"

# The update below resets config.yaml; remember the model the maintainer picked
# (hermes -p $PROFILE model) so step 3 can put it back.
cfg_get() {
  local v
  v=$(hermes -p "$PROFILE" config get "$1" 2>/dev/null </dev/null)
  case "$v" in "Config key not set"*) v="" ;; esac
  printf '%s' "$v"
}
# The update below resets config.yaml; keep what the maintainer chose with `hermes model` or /model.
MODEL_KEEP=""
PREV_TERMINAL=""
if [ -f "$PROFILE_DIR/distribution.yaml" ]; then
  MODEL_KEEP=$(python3 -c "
import yaml
m = (yaml.safe_load(open('$PROFILE_DIR/config.yaml')) or {}).get('model') or {}
for k, v in (m.items() if isinstance(m, dict) else []):
    if isinstance(v, (str, int, float, bool)):
        print(f'{k}\t{v}')
" 2>/dev/null)
  PREV_TERMINAL=$(cfg_get terminal.backend)
fi

# Installs made before the profiles/ layout recorded the repo root as their source, which no longer
# holds a distribution.yaml. Re-point those at profiles/maintainer (user data, .env and memory are kept).
RECORDED=""
[ -f "$PROFILE_DIR/distribution.yaml" ] && RECORDED=$(python3 -c "
import yaml
print((yaml.safe_load(open('$PROFILE_DIR/distribution.yaml')) or {}).get('source') or '')" 2>/dev/null)
MIGRATE=no
if [ -n "$RECORDED" ] && [ -d "$RECORDED" ] && [ ! -f "$RECORDED/distribution.yaml" ]; then MIGRATE=yes; fi

if [ "$MIGRATE" = "yes" ]; then
  info "profile '$PROFILE' was installed from $RECORDED, which has moved to profiles/maintainer — re-pointing it..."
  if ! hermes profile install . --name "$PROFILE" --force -y; then
    fail "hermes profile install --force failed — see output above"
    exit 1
  fi
  pass "profile '$PROFILE' re-installed from $SRC (user data kept)"
elif [ -f "$PROFILE_DIR/distribution.yaml" ]; then
  info "profile '$PROFILE' already installed — pulling in any repo changes (skills, config.yaml,"
  info "plugins/, cron) since the last install/update..."
  if ! hermes profile update "$PROFILE" --force-config -y; then
    fail "hermes profile update failed — see output above"
    exit 1
  fi
  pass "profile '$PROFILE' updated (config.yaml, skills, plugins/, cron refreshed; .env untouched)"
else
  info "installing profile '$PROFILE'..."
  # Hermes refuses to install under a name it has deleted before (it leaves a marker);
  # clear the marker so deleting and re-creating a profile works.
  rm -f "$HOME/.hermes/profiles/.deleted/$PROFILE"
  if ! hermes profile install . --name "$PROFILE" --alias -y; then
    fail "hermes profile install failed — see output above"
    exit 1
  fi
  pass "profile '$PROFILE' installed"
fi

# Hermes' own bundled-essentials seed only fires when a profile's skills/ is
# fully empty — ours ships 4 skills at install time, so that check never
# triggers and Hermes' one pinned "essential" skill (hermes-agent) never
# lands. Force it explicitly (confirmed via a real fresh-install test).
hermes -p "$PROFILE" skills reset hermes-agent --restore --yes >/dev/null 2>&1

# Hermes ships ~60 bundled skills; every enabled skill costs prompt tokens. Seed them once,
# keep this distribution's own skills plus a curated set of Hermes ones, and stop re-seeding.
hermes -p "$PROFILE" skills opt-in --sync >/dev/null 2>&1
python3 - "$PROFILE_DIR/skills" <<'PY'
import shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
keep = {p.parent.name for p in Path("skills").glob("*/SKILL.md")} | set("""
hermes-agent codebase-inspection dogfood github hermes-agent-skill-authoring
inspecting-hermes-desktop-dom node-inspect-debugger python-debugpy requesting-code-review
simplify-code spike systematic-debugging test-driven-development
arxiv competitor-news-monitor grounded-citations llm-wiki blocked-page-recovery sdlc-review
""".split())
for skill in sorted(root.rglob("SKILL.md")):
    if ".archive" not in skill.parts and skill.parent.name not in keep:
        shutil.rmtree(skill.parent)
for d in sorted((q for q in root.rglob("*") if q.is_dir()), reverse=True):
    if not any(d.iterdir()):
        d.rmdir()
PY
hermes -p "$PROFILE" skills opt-out >/dev/null 2>&1
pass "skills: this distribution's plus a curated set of Hermes ones (no other bundled skills)"

# Hermes runs hook commands relative to the directory it was started from, so a relative
# ./hooks/ path silently stops firing anywhere but the profile directory. Pin them.
python3 - "$PROFILE_DIR" <<'PY'
import sys
profile_dir = sys.argv[1]
path = profile_dir + "/config.yaml"
s = open(path).read()
open(path, "w").write(s.replace('command: "./hooks/', 'command: "' + profile_dir + '/hooks/'))
PY
pass "hook scripts pinned to $PROFILE_DIR/hooks"

step "3. Credentials"

ENV_FILE="$PROFILE_DIR/.env"
ENV_EXAMPLE="$PROFILE_DIR/.env.EXAMPLE"

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    info "created $ENV_FILE from .env.EXAMPLE"
  else
    fail "$ENV_EXAMPLE not found — something is wrong with the install"
    exit 1
  fi
fi

# --- Interactive credential prompts (skipped when stdin/stdout isn't a terminal) ---
INTERACTIVE=0
[ -t 0 ] && [ -t 1 ] && INTERACTIVE=1

env_get() {
  ENV_FILE="$ENV_FILE" KEY="$1" python3 -c "
import os
key = os.environ['KEY']
for line in open(os.environ['ENV_FILE']):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        if k.strip() == key:
            print(v.strip())
            break
"
}

env_set() {
  ENV_FILE="$ENV_FILE" KEY="$1" VALUE="$2" python3 -c "
import os
path, key, value = os.environ['ENV_FILE'], os.environ['KEY'], os.environ['VALUE']
lines = open(path).read().splitlines()
for i, line in enumerate(lines):
    if line.split('=', 1)[0].strip() == key and not line.lstrip().startswith('#'):
        lines[i] = f'{key}={value}'
        break
else:
    lines.append(f'{key}={value}')
open(path, 'w').write('\\n'.join(lines) + '\\n')
"
}

# prompt_for KEY "prompt" [secret]: asks only if KEY is empty; Enter skips.
prompt_for() {
  local key="$1" prompt="$2" secret="${3:-}" reply=""
  [ -n "$(env_get "$key")" ] && return 0
  if [ -n "$secret" ]; then
    read -r -s -p "  $prompt" reply </dev/tty
    echo
  else
    read -r -p "  $prompt" reply </dev/tty
  fi
  [ -n "$reply" ] && env_set "$key" "$reply"
  return 0
}

# Older installs called the repo token GITHUB_TOKEN; keep it working under the new name.
if [ -z "$(env_get REPO_GITHUB_TOKEN)" ] && [ -n "$(env_get GITHUB_TOKEN)" ]; then
  env_set REPO_GITHUB_TOKEN "$(env_get GITHUB_TOKEN)"
  env_set GITHUB_TOKEN ""
  info "renamed GITHUB_TOKEN to REPO_GITHUB_TOKEN in $ENV_FILE"
fi

if [ "$INTERACTIVE" = 1 ]; then
  info "Press Enter at any prompt to skip it and fill it in $ENV_FILE yourself."
  prompt_for REPO_GITHUB_TOKEN "GitHub token (fine-grained PAT, see README step 2): " secret
  prompt_for MAINTAINER_GITHUB_LOGIN "Your GitHub username: "
  prompt_for KYVERNO_REPO "Repo to manage (owner/repo): "

  if [ -z "$(env_get SLACK_BOT_TOKEN)" ] && [ -z "$(env_get SLACK_APP_TOKEN)" ]; then
    info ""
    read -r -p "  Set up Slack now? (README step 3) [y/N]: " SLACK_CHOICE </dev/tty
    if [[ "$SLACK_CHOICE" =~ ^[Yy] ]]; then
      prompt_for SLACK_BOT_TOKEN "Slack bot token (xoxb-...): " secret
      prompt_for SLACK_APP_TOKEN "Slack app-level token (xapp-...): " secret
      prompt_for SLACK_ALLOWED_USERS "Your Slack member ID: "
      prompt_for SLACK_HOME_CHANNEL "Maintainers channel ID: "
    fi
  fi
fi

MISSING=$(python3 -c "
import re, sys, yaml
manifest = yaml.safe_load(open('distribution.yaml'))
required = [v['name'] for v in manifest.get('env_requires', []) if v.get('required')]
values = {}
with open('$ENV_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        values[k.strip()] = v.strip()
slack_on = bool(values.get('SLACK_BOT_TOKEN') or values.get('SLACK_APP_TOKEN'))
missing = [n for n in required if not values.get(n) and (slack_on or not n.startswith('SLACK_'))]
print(' '.join(missing))
")

if [ -n "$MISSING" ]; then
  fail "$ENV_FILE is missing values for: $MISSING"
  info ""
  info "Fill those in, then re-run this script:"
  info "  \$EDITOR $ENV_FILE"
  info "  ./scripts/install.sh $PROFILE"
  exit 0
fi

pass "all required credentials are filled in ($ENV_FILE)"

# Catch a bad REPO_GITHUB_TOKEN now instead of as a 401 from the tools later.
check_github_token() {
  GH_TOKEN_VAL="$(env_get REPO_GITHUB_TOKEN)" GH_REPO_VAL="$(env_get KYVERNO_REPO)" python3 - <<'PY'
import os, re, ssl, urllib.error, urllib.request
tok = os.environ['GH_TOKEN_VAL']
repo = os.environ['GH_REPO_VAL'] or 'kyverno/kyverno'
if re.search(r'[\s"\']', tok):
    print('malformed: the token contains spaces or quotes')
    raise SystemExit
if len(re.findall(r'ghp_|github_pat_|gho_|ghu_', tok)) > 1:
    print('malformed: it looks like two tokens pasted together')
    raise SystemExit
try:
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    ctx = None
req = urllib.request.Request(f'https://api.github.com/repos/{repo}', headers={
    'Authorization': f'Bearer {tok}', 'User-Agent': 'kyverno-assistant-install'})
try:
    urllib.request.urlopen(req, timeout=15, context=ctx)
    print('ok')
except urllib.error.HTTPError as exc:
    print(f'http {exc.code}')
except Exception as exc:
    print(f'unreachable: {exc}')
PY
}
TOKEN_STATUS=$(check_github_token)
case "$TOKEN_STATUS" in
  ok) pass "REPO_GITHUB_TOKEN works for $(env_get KYVERNO_REPO)" ;;
  http\ 401) fail "GitHub rejected REPO_GITHUB_TOKEN (401): it is wrong, expired or revoked — create a new one and fix $ENV_FILE" ;;
  http\ 403|http\ 404) fail "REPO_GITHUB_TOKEN cannot read $(env_get KYVERNO_REPO) (${TOKEN_STATUS#http }): give the fine-grained token access to that repo, or get the org to approve it" ;;
  malformed*) fail "REPO_GITHUB_TOKEN in $ENV_FILE is malformed (${TOKEN_STATUS#malformed: }) — it must be just the one token, no quotes" ;;
  *) info "could not verify REPO_GITHUB_TOKEN (${TOKEN_STATUS:-no answer}) — continuing" ;;
esac

# Hermes' Copilot provider takes the first of these it finds, ahead of anything it would sign in for.
for v in COPILOT_GITHUB_TOKEN GH_TOKEN GITHUB_TOKEN; do
  if [ -n "${!v:-}" ]; then
    info "warning: your shell exports $v — remove it from your shell startup files if Copilot login misbehaves (the profile .env is what Hermes should use)"
  fi
done

# GitHub Copilot signs in with a device-code login only, never with a PAT. Hermes picks the first valid
# token among COPILOT_GITHUB_TOKEN, GH_TOKEN and GITHUB_TOKEN, and the Copilot API rejects PATs. The repo PAT
# lives in REPO_GITHUB_TOKEN, a name Hermes doesn't look at, so the two never meet.
#   KYVERNO_COPILOT_LOGIN=yes  sign in (again) without asking, e.g. after the token expired
#   KYVERNO_COPILOT_LOGIN=no   skip it
WANT_COPILOT="${KYVERNO_COPILOT_LOGIN:-}"
if [ -z "$WANT_COPILOT" ] && [ "$INTERACTIVE" = 1 ] && [ -z "$(env_get COPILOT_GITHUB_TOKEN)" ]; then
  read -r -p "  Sign in to GitHub Copilot for the model (device code in your browser; skip with n) [Y/n]: " WANT_COPILOT </dev/tty
  case "$WANT_COPILOT" in [Nn]*) WANT_COPILOT=no ;; *) WANT_COPILOT=yes ;; esac
fi
if [ "$WANT_COPILOT" = "yes" ]; then
  if [ -x "$HERMES_DIR/venv/bin/python" ] && PYTHONPATH="$HERMES_DIR" HERMES_HOME="$PROFILE_DIR" "$HERMES_DIR/venv/bin/python" -c "
from hermes_cli.copilot_auth import copilot_device_code_login
from hermes_cli.config import save_env_value
token = copilot_device_code_login()
if not token:
    raise SystemExit(1)
save_env_value('COPILOT_GITHUB_TOKEN', token)
"; then
    pass "Copilot signed in (COPILOT_GITHUB_TOKEN saved). Pick it with: hermes -p $PROFILE model"
  else
    fail "Copilot sign-in did not finish — retry with: KYVERNO_COPILOT_LOGIN=yes ./scripts/install.sh"
  fi
fi

# Put back the maintainer's model choice (the profile update reset it).
if [ -n "$MODEL_KEEP" ]; then
  while IFS=$'\t' read -r key value; do
    hermes -p "$PROFILE" config set "model.$key" "$value" >/dev/null 2>&1
  done <<< "$MODEL_KEEP"
fi

# Hermes only substitutes ${HERMES_SKILL_DIR}/${HERMES_SESSION_ID} in SKILL.md
# content — ${KYVERNO_REPO} etc. are never resolved by Hermes itself, so the
# model would otherwise read that literal placeholder text. Do it ourselves.
python3 -c "
import glob
values = {}
with open('$ENV_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        values[k.strip()] = v.strip()
for var in ('KYVERNO_REPO', 'MAINTAINER_GITHUB_LOGIN', 'SLACK_HOME_CHANNEL'):
    val = values.get(var)
    if not val:
        continue
    token = '\${' + var + '}'
    for path in glob.glob('$PROFILE_DIR/skills/*/SKILL.md'):
        text = open(path).read()
        if token in text:
            open(path, 'w').write(text.replace(token, val))
"
pass "skill instructions resolved to real env values"

step "4. AGENTS.md cache"

# Discovers every AGENTS.md/ARCHITECTURE.md in the repo (not just api/AGENTS.md — kyverno/kyverno
# has one per major package: pkg/engine, pkg/cel, pkg/webhooks, etc., plus root AGENTS.md and
# ARCHITECTURE.md) via one git-tree call, then caches each as its own mnemosyne entry so
# kyverno-context can recall the one relevant to whatever package is in play instead of a fresh
# search_code every session.
AGENTS_SEED_FILE="$(mktemp)"
python3 -c "
import base64, json, ssl, urllib.request, urllib.error

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None  # falls back to the interpreter's default cert store

values = {}
with open('$ENV_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        values[k.strip()] = v.strip()

repo = values.get('KYVERNO_REPO')
token = values.get('REPO_GITHUB_TOKEN')

def api_get(path):
    req = urllib.request.Request(
        f'https://api.github.com/{path}',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'kyverno-assistant-install',
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        return json.load(resp)

try:
    default_branch = api_get(f'repos/{repo}')['default_branch']
    tree = api_get(f'repos/{repo}/git/trees/{default_branch}?recursive=true')
except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError, KeyError) as exc:
    print(f'SKIP: could not list {repo}\'s tree: {exc}')
    print('HINT: if this is an SSL cert error, try: python3 -m pip install certifi')
    raise SystemExit(0)

import re
doc_paths = [n['path'] for n in tree.get('tree', [])
             if re.search(r'(?i)agents\.md$|architecture\.md$', n['path'])]
if not doc_paths:
    print('SKIP: no AGENTS.md/ARCHITECTURE.md files found')
    raise SystemExit(0)

entries = []
failed = []
for path in doc_paths:
    try:
        payload = api_get(f'repos/{repo}/contents/{path}')
        content = base64.b64decode(payload['content']).decode('utf-8')
    except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError, KeyError) as exc:
        failed.append(path)
        continue
    entries.append({
        'id': f'kyverno-doc:{path}',
        'content': f'--- {path} ---\n{content}',
        'source': 'install-seed',
        'timestamp': '2026-01-01T00:00:00',
        'session_id': 'install-seed',
        'importance': 0.9,
        'scope': 'global',
        'pinned': 1,
    })

if not entries:
    print('SKIP: found doc paths but could not fetch any of them')
    raise SystemExit(0)

seed = {'mnemosyne_export': {'version': '1.3'}, 'working_memory': entries}
with open('$AGENTS_SEED_FILE', 'w') as f:
    json.dump(seed, f)
print(f'OK: {len(entries)} cached' + (f', {len(failed)} failed: {failed}' if failed else ''))
" > "$AGENTS_SEED_FILE.status" 2>&1

if grep -q "^OK:" "$AGENTS_SEED_FILE.status"; then
  if hermes -p "$PROFILE" mnemosyne import -i "$AGENTS_SEED_FILE" --force >/dev/null 2>&1; then
    pass "$(sed 's/^OK: //' "$AGENTS_SEED_FILE.status") (recall by 'kyverno-doc:<path>' any session)"
  else
    info "docs fetched but mnemosyne import failed — run: hermes -p $PROFILE mnemosyne import -i $AGENTS_SEED_FILE --force"
  fi
else
  info "$(cat "$AGENTS_SEED_FILE.status") — skipping cache, kyverno-context will fall back to search_code"
fi
rm -f "$AGENTS_SEED_FILE" "$AGENTS_SEED_FILE.status"

SLACK_CONFIGURED=$(python3 -c "
values = {}
with open('$ENV_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        values[k.strip()] = v.strip()
print('yes' if values.get('SLACK_BOT_TOKEN') and values.get('SLACK_APP_TOKEN') else 'no')
")

step "5. Messaging gateway"

if [ "$SLACK_CONFIGURED" = "yes" ]; then
  # gateway install/restart only work against the 'default' profile — switch
  # to it, then switch back, so this script doesn't silently change which
  # profile is sticky-active for the maintainer's own `hermes chat`.
  ORIGINAL_ACTIVE=$(hermes profile list 2>/dev/null | awk '$1 ~ /◆/ {gsub(/◆/,"",$1); print $1; exit}')
  hermes profile use default >/dev/null 2>&1
  GATEWAY_OUT=$(hermes gateway install 2>&1)
  if echo "$GATEWAY_OUT" | grep -qi "already installed"; then
    pass "gateway service already installed"
    info "if the bot doesn't respond, it may need: hermes gateway restart"
  elif echo "$GATEWAY_OUT" | grep -qi "install"; then
    pass "gateway service installed"
  else
    fail "gateway install had an unexpected result — run: hermes gateway install"
    info "$GATEWAY_OUT"
  fi
  if [ -n "$ORIGINAL_ACTIVE" ] && [ "$ORIGINAL_ACTIVE" != "default" ]; then
    hermes profile use "$ORIGINAL_ACTIVE" >/dev/null 2>&1
  fi
else
  info "no Slack credentials set — skipping gateway (CLI-only use is fine)"
fi

step "6. Sandboxed terminal"

# The terminal runs inside an NVIDIA OpenShell sandbox: no tokens, read-only GitHub, every
# decision audited. Without the sandbox the profile has no terminal at all.
SANDBOX_TERMINAL="no"
WANT_SANDBOX="${KYVERNO_SANDBOX_TERMINAL:-}"
[ -z "$WANT_SANDBOX" ] && [ "$PREV_TERMINAL" = "ssh" ] && WANT_SANDBOX=yes
if [ -z "$WANT_SANDBOX" ] && [ "$INTERACTIVE" = 1 ]; then
  info "Optional: a sandboxed terminal for the assistant (installs NVIDIA OpenShell and builds a"
  info "small Docker image). It can read GitHub and run commands, but holds no tokens and cannot"
  info "write anywhere you haven't allowed (profiles/maintainer/sandbox/policy.yaml)."
  read -r -p "  Set it up? [Y/n]: " WANT_SANDBOX </dev/tty
  case "$WANT_SANDBOX" in [Nn]*) WANT_SANDBOX=no ;; *) WANT_SANDBOX=yes ;; esac
fi
if [ "$WANT_SANDBOX" = "yes" ]; then
  # The sandbox needs Landlock, which Docker Desktop only has from 4.60.
  DOCKER_PLATFORM=$(docker version --format '{{.Server.Platform.Name}}' 2>/dev/null)
  DESKTOP_RE='Docker Desktop ([0-9]+)\.([0-9]+)'
  if [[ "$DOCKER_PLATFORM" =~ $DESKTOP_RE ]]; then
    if [ "${BASH_REMATCH[1]}" -lt 4 ] || { [ "${BASH_REMATCH[1]}" -eq 4 ] && [ "${BASH_REMATCH[2]}" -lt 60 ]; }; then
      fail "Docker Desktop ${BASH_REMATCH[1]}.${BASH_REMATCH[2]} is older than 4.60 and has no Landlock — update it and re-run; skipping the sandbox for now"
      WANT_SANDBOX=no
    fi
  fi
fi
if [ "$WANT_SANDBOX" = "yes" ]; then
  if "$REPO_ROOT/scripts/openshell.sh" setup; then
    python3 - "$PROFILE_DIR/config.yaml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
for tool in ("terminal", "file"):
    if not re.search(r"(?m)^    - %s$" % tool, s):
        s = s.replace("    - cronjob\n", "    - cronjob\n    - %s\n" % tool, 1)
open(p, "w").write(s)
PY
    for kv in terminal.backend=ssh terminal.ssh_host=openshell-kyverno-terminal.default \
              terminal.ssh_user=sandbox terminal.cwd=/sandbox terminal.degraded_mode=fail; do
      hermes -p "$PROFILE" config set "${kv%%=*}" "${kv#*=}" >/dev/null 2>&1
    done
    SANDBOX_TERMINAL="yes"
    pass "terminal and file tools enabled, running only in the sandbox"
    info "Audit: ./scripts/openshell.sh audit summary   (daily logs in ~/.kyverno-assistant/audit)"
  else
    fail "sandbox setup failed — the terminal stays off. Fix the error above and re-run this script"
  fi
else
  info "skipped — no terminal (re-run with KYVERNO_SANDBOX_TERMINAL=yes to add it later)"
fi

step "7. Sanity checks"

MCP_OUT=$(hermes -p "$PROFILE" mcp list 2>&1)
if echo "$MCP_OUT" | grep -q "github .*enabled"; then
  pass "github MCP server enabled"
else
  fail "github MCP server not enabled — run: hermes -p $PROFILE mcp list"
fi

if [ "$SLACK_CONFIGURED" = "yes" ]; then
  if echo "$MCP_OUT" | grep -q "slack .*enabled"; then
    pass "slack MCP server enabled"
  else
    fail "slack MCP server not enabled — run: hermes -p $PROFILE mcp list"
  fi
fi

if python3 -c "
import sys, yaml
g = yaml.safe_load(open('$PROFILE_DIR/config.yaml'))['mcp_servers']['github']
sys.exit(0 if set(g['headers']['X-MCP-Tools'].split(',')) == set(g['tools']['include']) else 1)
"; then
  pass "github tool allowlist header matches tools.include"
else
  fail "mcp_servers.github.headers.X-MCP-Tools and tools.include differ in config.yaml — they must list the same tools"
fi

if hermes -p "$PROFILE" mcp test github >/dev/null 2>&1; then
  pass "github MCP server reachable (this doesn't verify the token itself — ask it a real question to confirm)"
else
  fail "github MCP server check failed — run: hermes -p $PROFILE mcp test github"
fi

if [ "$SLACK_CONFIGURED" = "yes" ]; then
  if hermes -p "$PROFILE" mcp test slack >/dev/null 2>&1; then
    pass "slack tokens work"
  else
    fail "slack connection failed — check SLACK_BOT_TOKEN/SLACK_APP_TOKEN, then: hermes -p $PROFILE mcp test slack"
  fi
fi

if hermes -p "$PROFILE" hooks doctor 2>&1 | grep -q "All shell hooks look healthy"; then
  pass "shell hooks healthy"
else
  info "shell hooks need approval on first real use — run: hermes -p $PROFILE hooks doctor"
fi

TOOLS_OUT=$(hermes -p "$PROFILE" tools list 2>&1)
if [ "$SANDBOX_TERMINAL" = "yes" ]; then
  TERMINAL_RE="browser|code_execution|computer_use|connections|delegation"
  if [ "$(cfg_get terminal.backend)" = "ssh" ] && cfg_get terminal.ssh_host | grep -q '^openshell-' \
     && echo "$TOOLS_OUT" | grep -q "✓ enabled  terminal  "; then
    pass "terminal enabled and bound to the sandbox"
  else
    fail "terminal is not bound to the sandbox — see config.yaml terminal section"
  fi
else
  TERMINAL_RE="terminal|file|browser|code_execution|computer_use|connections|delegation"
fi
LEAKED=$(echo "$TOOLS_OUT" | grep -E "✓ enabled  ($TERMINAL_RE)  " || true)
if [ -z "$LEAKED" ]; then
  pass "no laptop-level tool access (only the sandboxed terminal, if set up)"
else
  fail "unexpected tool access enabled — this profile can reach beyond GitHub/Slack:"
  info "$LEAKED"
fi

step "Done"

info "Model: choose one in the chat with /model, or run: hermes -p $PROFILE model  (or put a key such as ANTHROPIC_API_KEY in $ENV_FILE)"
info "Talk to it: hermes -p $PROFILE chat  (or 'kyverno chat' if the alias took)"
info "Dashboard: hermes -p $PROFILE dashboard  (Kyverno tab, http://127.0.0.1:9119/kyverno)"
info ""
info "Cron jobs ship paused — review before turning any on:"
info "  hermes -p $PROFILE cron list"
info "  hermes -p $PROFILE cron resume <job-id>"
