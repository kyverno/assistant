#!/usr/bin/env bash
# Sets up the sandboxed terminal (NVIDIA OpenShell) and its audit trail.
#   setup    install OpenShell, build the image, create the sandbox, add the ssh entry
#   policy   re-apply profiles/maintainer/sandbox/policy.yaml to the running sandbox
#   status   one-line health check (exit 0 when the terminal is usable)
#   audit    collect | summary [DATE] | tail      (see docs/openshell.md)
#   rules    list | approve ID | reject ID [reason] | history   (access requests from the agent)
#   remove   delete the sandbox, ssh entry and collector (keeps OpenShell and audit logs)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX_DIR="$REPO_ROOT/profiles/maintainer/sandbox"      # Dockerfile + policy.yaml
OPENSHELL_VERSION="${OPENSHELL_VERSION:-v0.1.3}"
SANDBOX="${KYVERNO_SANDBOX:-kyverno-terminal}"
IMAGE="kyverno-assistant-terminal:local"
SSH_HOST="openshell-${SANDBOX}.default"
AUDIT_DIR="${KYVERNO_AUDIT_DIR:-$HOME/.kyverno-assistant/audit}"
SSH_CONFIG="$HOME/.ssh/config"
MARK_BEGIN="# BEGIN kyverno-assistant sandbox"
MARK_END="# END kyverno-assistant sandbox"
LABEL="io.kyverno.assistant.audit"

info() { printf '  %s\n' "$*"; }
pass() { printf '  ✓ %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*" >&2; }

sandbox_exists() { openshell sandbox get "$SANDBOX" >/dev/null 2>&1; }

sandbox_ready() {
  openshell sandbox list 2>/dev/null | awk -v n="$SANDBOX" '$1==n && $NF=="Ready"{f=1} END{exit !f}'
}

# Poll until the sandbox is Ready (default 5 minutes: the first start pulls and boots the image).
wait_sandbox_ready() {
  local i
  for i in $(seq 1 "${1:-150}"); do sandbox_ready && return 0; sleep 2; done
  return 1
}

# Say why the sandbox is not Ready: its real state and the create output. The Landlock hint is
# only shown when Docker Desktop really is older than 4.60.
explain_sandbox_failure() {
  local log="${1:-}" plat re='Docker Desktop ([0-9]+)\.([0-9]+)'
  fail "sandbox '$SANDBOX' did not become Ready"
  openshell sandbox get "$SANDBOX" 2>&1 | head -n 15 | sed 's/^/    /' >&2
  if [ -s "$log" ]; then
    info "last output of 'openshell sandbox create':"
    tail -n 15 "$log" | sed 's/^/    /' >&2
  fi
  plat=$(docker version --format '{{.Server.Platform.Name}}' 2>/dev/null)
  if [[ "$plat" =~ $re ]] && { [ "${BASH_REMATCH[1]}" -lt 4 ] \
     || { [ "${BASH_REMATCH[1]}" -eq 4 ] && [ "${BASH_REMATCH[2]}" -lt 60 ]; }; }; then
    info "Docker Desktop ${BASH_REMATCH[1]}.${BASH_REMATCH[2]} is older than 4.60 and has no Landlock: update it"
  fi
  info "to retry from scratch: openshell sandbox delete $SANDBOX, then re-run this script"
}

supported_platform() { [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; }

cmd_setup() {
  if ! supported_platform; then
    fail "the sandboxed terminal is only set up for Apple Silicon macOS so far; the terminal stays off"
    return 2
  fi
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 || { fail "Docker must be running"; return 1; }

  if ! command -v openshell >/dev/null 2>&1; then
    info "installing OpenShell $OPENSHELL_VERSION (Homebrew formula, background gateway service)..."
    curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh \
      | OPENSHELL_VERSION="$OPENSHELL_VERSION" sh || { fail "OpenShell install failed"; return 1; }
  fi
  pass "OpenShell $(openshell --version 2>/dev/null | head -1)"

  # Docker Desktop runs containers in a VM, so the sandbox cannot reach the gateway on the
  # Mac's 127.0.0.1 (OpenShell issue #3880); point it at host.docker.internal instead.
  local toml
  toml="$(brew --prefix 2>/dev/null)/var/openshell/gateway.toml"
  if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi "docker desktop" && [ -f "$toml" ] \
     && ! grep -q 'grpc_endpoint' "$toml"; then
    cp "$toml" "$toml.bak-kyverno"
    printf '\n[openshell.drivers.docker]\ngrpc_endpoint = "https://host.docker.internal:17670"\n' >> "$toml"
    brew services restart openshell >/dev/null 2>&1
    sleep 5
    pass "gateway pointed at host.docker.internal for Docker Desktop (backup: $toml.bak-kyverno)"
  fi
  openshell status >/dev/null 2>&1 || { fail "OpenShell gateway is not reachable: run 'openshell status'"; return 1; }

  info "building the sandbox image..."
  docker build -q -t "$IMAGE" "$SANDBOX_DIR" >/dev/null || { fail "image build failed"; return 1; }
  pass "sandbox image built"

  local create_log=""
  if sandbox_exists; then
    cmd_policy || return 1
    # Left stopped or half-started by an earlier run: try starting it before giving up.
    sandbox_ready || openshell sandbox start "$SANDBOX" >/dev/null 2>&1
  else
    create_log="$(mktemp)"
    info "creating sandbox '$SANDBOX' (the first start can take a few minutes)..."
    openshell sandbox create --name "$SANDBOX" --from "$IMAGE" \
      --policy "$SANDBOX_DIR/policy.yaml" --no-auto-providers -- sleep infinity \
      >"$create_log" 2>&1 &
  fi
  wait_sandbox_ready || { explain_sandbox_failure "$create_log"; return 1; }
  [ -n "$create_log" ] && rm -f "$create_log"
  openshell settings set --key ocsf_json_enabled --value true "$SANDBOX" >/dev/null 2>&1
  # The agent may ask for narrow network access; only a human approves, never automatically.
  openshell settings set --key agent_policy_proposals_enabled --value true "$SANDBOX" >/dev/null 2>&1
  openshell settings set --key proposal_approval_mode --value manual "$SANDBOX" >/dev/null 2>&1
  pass "sandbox '$SANDBOX' ready"

  add_ssh_entry || return 1
  cmd_status || return 1
  install_collector
}

add_ssh_entry() {
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  local block
  block="$(openshell sandbox ssh-config "$SANDBOX")" || { fail "could not read the sandbox ssh config"; return 1; }
  remove_ssh_entry
  { printf '\n%s\n%s\n%s\n' "$MARK_BEGIN" "$block" "$MARK_END"; } >> "$SSH_CONFIG"
  chmod 600 "$SSH_CONFIG"
  pass "ssh entry '$SSH_HOST' added to $SSH_CONFIG"
}

remove_ssh_entry() {
  [ -f "$SSH_CONFIG" ] || return 0
  python3 - "$SSH_CONFIG" "$MARK_BEGIN" "$MARK_END" <<'PY'
import sys
path, begin, end = sys.argv[1:4]
out, skip = [], False
for line in open(path).read().split("\n"):
    if line == begin: skip = True
    if not skip: out.append(line)
    if line == end: skip = False
open(path, "w").write("\n".join(out))
PY
}

cmd_policy() {
  sandbox_exists || { fail "sandbox '$SANDBOX' does not exist: scripts/openshell.sh setup"; return 1; }
  openshell policy set --policy "$SANDBOX_DIR/policy.yaml" --wait "$SANDBOX" >/dev/null 2>&1 \
    && pass "policy applied to '$SANDBOX'" \
    || { fail "policy update failed: openshell policy set --policy $SANDBOX_DIR/policy.yaml $SANDBOX"; return 1; }
}

cmd_status() {
  command -v openshell >/dev/null 2>&1 || { fail "OpenShell is not installed"; return 1; }
  sandbox_ready || { fail "sandbox '$SANDBOX' is not Ready"; return 1; }
  local out
  out="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "sandbox@$SSH_HOST" \
    'curl -s -o /dev/null -w "%{http_code}" https://api.github.com/zen; echo " $(touch /etc/x 2>&1 | grep -c denied)"' 2>/dev/null)"
  case "$out" in
    "200 1") pass "terminal works through ssh (GitHub reads allowed, system files protected)" ;;
    *) fail "the sandbox answered '$out' instead of '200 1'"; return 1 ;;
  esac
}

# --- audit -------------------------------------------------------------------------------

collector_plist="$HOME/Library/LaunchAgents/$LABEL.plist"

install_collector() {
  mkdir -p "$AUDIT_DIR"
  cat > "$collector_plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$REPO_ROOT/scripts/openshell.sh</string>
    <string>audit</string><string>collect</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)" "$collector_plist" >/dev/null 2>&1
  launchctl bootstrap "gui/$(id -u)" "$collector_plist" >/dev/null 2>&1 \
    && pass "audit collector running every 5 minutes (logs: $AUDIT_DIR)" \
    || fail "could not start the audit collector: scripts/openshell.sh audit collect"
}

# Appends gateway-buffered events newer than the last stored one to a daily file. The gateway
# keeps only the latest 2,000 lines per sandbox in memory, so this is what makes the trail durable.
audit_collect() {
  sandbox_exists || return 0
  mkdir -p "$AUDIT_DIR"
  local state="$AUDIT_DIR/.last-$SANDBOX" last tmp
  last="$(cat "$state" 2>/dev/null || echo 0)"
  tmp="$(mktemp)"
  openshell logs "$SANDBOX" -n 2000 --source all 2>/dev/null > "$tmp" || { rm -f "$tmp"; return 1; }
  python3 - "$tmp" "$AUDIT_DIR" "$state" "$last" <<'PY'
import os, re, sys, time
src, outdir, state, last = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
newest = last
files = {}
for line in open(src, errors="replace"):
    m = re.match(r"\[(\d+\.\d+)\]", line)
    if not m:
        continue
    ts = float(m.group(1))
    if ts <= last:
        continue
    newest = max(newest, ts)
    if "[INFO ]" in line or "[DEBUG]" in line or "CONFIG:DETECTED" in line:
        continue
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    files.setdefault(day, []).append(line.rstrip("\n"))
for day, lines in files.items():
    with open(f"{outdir}/{day}.log", "a") as f:
        f.write("\n".join(lines) + "\n")
open(state, "w").write(str(newest))
cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 20 * 86400))
for name in os.listdir(outdir):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.log", name) and name[:10] < cutoff:
        os.remove(os.path.join(outdir, name))
PY
  rm -f "$tmp"
}

audit_summary() {
  local day="${1:-$(date +%F)}" file="$AUDIT_DIR/${1:-$(date +%F)}.log"
  [ -f "$file" ] || { echo "no audit log for $day in $AUDIT_DIR (run: scripts/openshell.sh audit collect)"; return 1; }
  python3 - "$file" "$day" <<'PY'
import collections, re, sys, time
path, day = sys.argv[1:3]
allowed, denied = collections.Counter(), collections.Counter()
denials, findings, cfg = [], [], []
for line in open(path, errors="replace"):
    m = re.match(r"\[(\d+)\.\d+\] \[\w+\] \[OCSF \] \[ocsf\] (\S+) \[(\w+)\]\s*(ALLOWED|DENIED)?\s*(.*)", line)
    if not m:
        continue
    ts, kind, sev, verdict, rest = m.groups()
    t = time.strftime("%H:%M:%S", time.localtime(int(ts)))
    if kind.startswith(("HTTP:", "NET:")):
        what = re.sub(r"\s*\[.*", "", rest)
        policy = re.search(r"policy:(\w+)", rest)
        reason = re.search(r"reason:([^\]]+)", rest)
        if verdict == "DENIED":
            denied[what.split(" ")[-1] if kind.startswith("NET") else " ".join(what.split(" ")[:2])] += 1
            denials.append((t, kind, what, policy.group(1) if policy else "-", reason.group(1)[:60] if reason else ""))
        else:
            allowed[kind] += 1
    elif kind.startswith("FINDING"):
        findings.append((t, rest))
    elif kind in ("CONFIG:UPDATED", "CONFIG:LOADED", "CONFIG:REJECTED", "CONFIG:FAILED", "CONFIG:PROPOSED", "CONFIG:APPROVED"):
        cfg.append((t, kind, rest[:100]))
print(f"Sandbox audit for {day}")
print(f"  allowed network/HTTP events: {sum(allowed.values())}   denied: {sum(denied.values())}")
if denied:
    print("\nMost denied")
    for what, n in denied.most_common(8):
        print(f"  {n:4d}  {what}")
    print("\nLatest denials (time, event, request, policy, reason)")
    for d in denials[-10:]:
        print("  " + "  ".join(d))
if findings:
    print("\nSecurity findings")
    for t, r in findings[-10:]:
        print(f"  {t}  {r[:120]}")
if cfg:
    print("\nAccess requests, policy and setting changes")
    for t, k, r in cfg[-10:]:
        print(f"  {t}  {k}  {r}")
PY
}

cmd_audit() {
  case "${1:-summary}" in
    collect) audit_collect ;;
    summary) audit_collect; audit_summary "${2:-}" ;;
    tail) openshell logs "$SANDBOX" --tail --source all ;;
    *) fail "usage: scripts/openshell.sh audit collect|summary [YYYY-MM-DD]|tail"; return 2 ;;
  esac
}

# --- access requests (OpenShell policy advisor) --------------------------------------------

# Prints one tab-separated line per pending request: id, rule, level, endpoints, binaries, why.
pending_requests() {
  openshell rule get "$SANDBOX" --status pending 2>/dev/null | python3 -c '
import re, sys
chunk = {}
def flush():
    if chunk:
        print("\t".join(chunk.get(k, "") for k in ("id", "rule", "level", "endpoints", "binaries", "why", "prover")))
for line in sys.stdin:
    line = line.rstrip()
    m = re.match(r"\s+(Chunk|Rule|Rationale|Prover|Endpoints|Binaries):\s*(.*)", line)
    if not m:
        continue
    key, val = m.groups()
    if key == "Chunk":
        flush(); chunk.clear(); chunk["id"] = val
    elif key == "Rule": chunk["rule"] = val
    elif key == "Rationale": chunk["why"] = val
    elif key == "Prover": chunk["prover"] = val
    elif key == "Endpoints":
        chunk["endpoints"] = val
        chunk["level"] = "L7" if "[L7" in val else "L4"
    elif key == "Binaries": chunk["binaries"] = val
flush()'
}

# Approved requests from the agent default to audit-only; turn enforcement on for every
# REST endpoint that lacks it so a rule allowing GET / cannot be used for other paths.
enforce_rest_endpoints() {
  openshell policy get "$SANDBOX" --full -o json 2>/dev/null | python3 -c '
import json, sys
pol = json.load(sys.stdin).get("policy", {})
for name, rule in (pol.get("network_policies") or {}).items():
    bins = [b["path"] for b in rule.get("binaries") or []]
    for ep in rule.get("endpoints") or []:
        if ep.get("protocol") == "rest" and ep.get("enforcement") != "enforce":
            print("\t".join([name, "%s:%s" % (ep["host"], ep.get("port", 443)), ",".join(bins)]))' |
  while IFS=$'\t' read -r name target bins; do
    args=(--add-endpoint "$target:read-only:rest:enforce" --rule-name "$name")
    IFS=, read -ra list <<< "$bins"
    for b in "${list[@]}"; do args+=(--binary "$b"); done
    openshell policy update "$SANDBOX" "${args[@]}" --wait >/dev/null 2>&1 \
      && pass "enforcement switched on for $target ($name)" \
      || fail "could not switch on enforcement for $target ($name)"
  done
}

cmd_rules() {
  local action="${1:-list}"
  case "$action" in
    list)
      local rows; rows="$(pending_requests)"
      [ -n "$rows" ] || { echo "no pending access requests for '$SANDBOX'"; return 0; }
      echo "$rows" | while IFS=$'\t' read -r id rule level endpoints bins why prover; do
        printf '\n  %s  (%s)\n    asks for : %s\n    program  : %s\n    reason   : %s\n    risk check: %s\n' \
          "$id" "$rule" "$endpoints" "$bins" "$why" "$prover"
        [ "$level" = "L4" ] && printf '    note     : whole-host access, not limited to methods or paths\n'
      done
      printf '\n  approve: scripts/openshell.sh rules approve <id>    reject: scripts/openshell.sh rules reject <id> [reason]\n'
      ;;
    approve|reject)
      local id="${2:-}" row full
      [ -n "$id" ] || { fail "usage: scripts/openshell.sh rules $action <id>"; return 2; }
      row="$(pending_requests | awk -F'\t' -v i="$id" 'index($1, i)==1' | head -1)"
      [ -n "$row" ] || { fail "no pending request starting with '$id'"; return 1; }
      full="${row%%$'\t'*}"
      if [ "$action" = "reject" ]; then
        openshell rule reject "$SANDBOX" --chunk-id "$full" --reason "${3:-rejected by maintainer}" >/dev/null 2>&1 \
          && pass "rejected $full" || fail "reject failed"
        return
      fi
      if [ "$(echo "$row" | cut -f3)" = "L4" ] && [ "${3:-}" != "--allow-whole-host" ]; then
        fail "this request opens a whole host, not specific methods and paths; ask the agent for a narrower one (or add --allow-whole-host)"
        return 1
      fi
      openshell rule approve "$SANDBOX" --chunk-id "$full" >/dev/null 2>&1 \
        && pass "approved $full" || { fail "approve failed"; return 1; }
      enforce_rest_endpoints
      ;;
    history) openshell rule history "$SANDBOX" ;;
    *) fail "usage: scripts/openshell.sh rules list|approve <id>|reject <id> [reason]|history"; return 2 ;;
  esac
}

cmd_remove() {
  launchctl bootout "gui/$(id -u)" "$collector_plist" >/dev/null 2>&1; rm -f "$collector_plist"
  remove_ssh_entry
  sandbox_exists && openshell sandbox delete "$SANDBOX" >/dev/null 2>&1
  pass "sandbox, ssh entry and audit collector removed (audit logs kept in $AUDIT_DIR)"
}

case "${1:-}" in
  setup) cmd_setup ;;
  policy) cmd_policy ;;
  status) cmd_status ;;
  audit) shift; cmd_audit "$@" ;;
  rules) shift; cmd_rules "$@" ;;
  remove) cmd_remove ;;
  *) echo "usage: scripts/openshell.sh setup|policy|status|audit|rules|remove"; exit 2 ;;
esac
