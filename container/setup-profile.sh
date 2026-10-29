#!/bin/bash
# Runs once per profile, at image build time, after `hermes profile install` and the mnemosyne install:
# the one-time profile setup that used to run on every user's first start. Doing it here means a user's
# first start only handles what is theirs (repo, username, sign-in), and each Hermes command costs
# nothing extra at runtime.
#   kyverno-setup-profile <maintainer|contributor> <hermes profile name>
set -uo pipefail

KIND="${1:?profile kind}"
PROFILE="${2:?profile name}"
export HERMES_HOME=/opt/hermes-home/.hermes
PROFILE_DIR="$HERMES_HOME/profiles/$PROFILE"
SRC=/opt/kyverno/profiles/$KIND

step() { printf '\n== %s\n' "$1"; }
# Hermes asks for confirmation on some commands when a terminal is attached; never wait on one.
hq() { timeout 300 hermes -p "$PROFILE" "$@" </dev/null >/dev/null 2>&1 || echo "   (note: hermes $* did not finish cleanly)"; }

step "skills: this distribution's plus a small curated set of Hermes ones (same as scripts/install.sh)"
hq skills reset hermes-agent --restore --yes
hq skills opt-in --sync
(cd "$SRC" && python3 - "$PROFILE_DIR/skills" <<'PY'
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
)
hq skills opt-out

step "hooks: run them from the read-only copy in /opt/kyverno"
# Hermes runs hook commands relative to where it was started, so the config needs absolute paths. Point them at
# the baked copies under /opt/kyverno: root-owned, and read-only to the agent under the sandbox policy. The
# copies the profile install put in the profile folder are writable by the agent, so they would be a weaker home
# for the checks that stop it merging.
# A hook written in Python runs under Hermes' own interpreter: the system python3 is not the one the proxy lets
# talk to GitHub with the token placeholder swapped in.
PYTHON="$(compgen -G "$HERMES_HOME/tools/python-*/bin/python3" | head -n 1)"
python3 - "$PROFILE_DIR" "$SRC/hooks" "$PYTHON" <<'PY'
import re, sys
profile_dir, hooks_dir, python = sys.argv[1:4]
p = profile_dir + "/config.yaml"
s = open(p).read()
s = re.sub(r'command: "\./hooks/([^"]+\.py)"', lambda m: 'command: "%s %s/%s"' % (python, hooks_dir, m.group(1)), s)
s = s.replace('command: "./hooks/', 'command: "' + hooks_dir + '/')
open(p, "w").write(s)
PY

step "tools: terminal and file run locally inside the sandbox; Slack is left out for now"
python3 - "$PROFILE_DIR/config.yaml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
for tool in ("terminal", "file"):
    if not re.search(r"(?m)^    - %s$" % tool, s):
        s = s.replace("    - cronjob\n", "    - cronjob\n    - %s\n" % tool, 1)
s = re.sub(r"(?m)^    - mcp-slack\n", "", s)
open(p, "w").write(s)
PY
[ "$KIND" = maintainer ] && hq mcp remove slack
hq config set terminal.backend local
hq config set terminal.cwd /sandbox

step "hooks: approve our own hooks up front"
# Hermes asks "Allow this hook to run? [y/N]" the first time a shell hook is used, and an unapproved
# hook does not run at all (including the merge-block hook). The chat screen hides that question, so
# approve them here. These are the profile's own scripts, inside the sandbox.
hq config set hooks_auto_accept true

step "memory plugin: never go online for its embedding model (it is already in the profile's cache)"
# Without this, HuggingFace can still be asked whether the cached model is current, and in the sandbox that
# request is refused and retried. Hermes loads the profile's .env into every process (the image's own ENV
# does not reach the sandbox). Append: the terminal settings above already wrote TERMINAL_ENV here, and
# kyverno-start adds the user's repo and login later.
echo 'HF_HUB_OFFLINE=1' >> "$PROFILE_DIR/.env"

step "check"
# hq() only reports a failed command, so make the settings that matter fail the build if they did not land.
CFG="$PROFILE_DIR/config.yaml"
grep -E '^\s+command: ".*/hooks/' "$CFG" | head -3 | sed 's/^ */   /'
hermes -p "$PROFILE" mcp list </dev/null 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E 'github|slack' | sed 's/^ */   /'
bad=0
require() { local what="$1"; shift; "$@" || { echo "   FAILED: $what" >&2; bad=1; }; }
forbid() { local what="$1"; shift; if "$@"; then echo "   FAILED: $what" >&2; bad=1; fi; }
forbid "a hook command still points at ./hooks/" grep -qE 'command: ".*\./hooks/' "$CFG"
require "hook commands point at $SRC/hooks" grep -qE "command: \".*$SRC/hooks/" "$CFG"
forbid "the Slack tool is still in the config" grep -q 'mcp-slack' "$CFG"
require "hooks_auto_accept is on" grep -qE '^hooks_auto_accept: *true' "$CFG"
require "the terminal backend is local" grep -qE '^ +backend: *local' "$CFG"
require "terminal is enabled" grep -qE '^    - terminal$' "$CFG"
require "file is enabled" grep -qE '^    - file$' "$CFG"
[ "$bad" = 0 ] || exit 1
