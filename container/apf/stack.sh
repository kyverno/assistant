#!/bin/bash
# stack.sh: the stack that enforces an organisation's access rules (docs/access-rules.md).
#
#   container/apf/stack.sh up RULES_JSON   write and sign the bundle for the rules, bring the stack up, wait until it is ready
#   container/apf/stack.sh down            stop it (keeps the keys, the revision counter and the gateway's sandboxes)
#   container/apf/stack.sh status          one line: running or not, the revision, the repositories
#   container/apf/stack.sh env             the two variables that point the OpenShell CLI at this stack's gateway
#
# RULES_JSON is what `assistant-access discover` prints: {"repos": ["acme/*", ...]}. It runs APF's policy service and policy
# gateway and a dedicated OpenShell gateway in Docker, on ports of their own, so the OpenShell gateway you already run is
# never touched or restarted. Everything it keeps is under ${ASSISTANT_ACCESS_DIR:-~/.config/assistant/access}, owner-only.
# Written for bash 3.2 (macOS) as well as bash 5.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${ASSISTANT_ACCESS_DIR:-$HOME/.config/assistant/access}"
# shellcheck source=/dev/null
. "$HERE/VERSION"

PROJECT=assistant-apf
GATEWAY_ID="$PROJECT"
KEY_ID=assistant-access
OPENSHELL_PORT="${ASSISTANT_ACCESS_PORT:-18080}"
OPENSHELL_HEALTH_PORT="${ASSISTANT_ACCESS_HEALTH_PORT:-18081}"
APF_EGRESS_PORT="${ASSISTANT_ACCESS_EGRESS_PORT:-19099}"
APF_ADMISSION_PORT="${ASSISTANT_ACCESS_ADMISSION_PORT:-18082}"
OPENSHELL_TAG="${OPENSHELL_IMAGE_TAG:-${OPENSHELL_VERSION:-v0.1.3}}"
OPENSHELL_TAG="${OPENSHELL_TAG#v}"
SERVICE_IMAGE="${ASSISTANT_APF_SERVICE_IMAGE:-$APF_IMAGE_PREFIX-policy-service:$APF_IMAGE_TAG}"
GATEWAY_IMAGE="${ASSISTANT_APF_GATEWAY_IMAGE:-$APF_IMAGE_PREFIX-policy-gateway:$APF_IMAGE_TAG}"
COMPILE_IMAGE="${ASSISTANT_APF_COMPILE_IMAGE:-$APF_IMAGE_PREFIX-compile:$APF_IMAGE_TAG}"
CONTAINER_SOCKET="${CONTAINER_SOCKET:-/var/run/docker.sock}"

ENV_FILE="$STATE/compose.env"
TOML="$STATE/gateway.toml"

say() { printf '  %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }

sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$HERE" --env-file "$ENV_FILE" -f "$HERE/compose.yaml" "$@"
}
volume_name() { printf '%s_%s' "$PROJECT" "$1"; }

# A throwaway container of the policy-service image with the named volumes attached and nothing else: no network, read-only
# root, no privilege escalation, every capability dropped except the comma-separated ones (`-` for none). Reads stdin so a tar
# stream can be extracted into a volume. The same way APF's own harness fills them.
run_in_volumes() {
  local user="$1" caps="$2" mounts=() spec cap
  shift 2
  local opts=(--network none --read-only --cap-drop ALL --security-opt no-new-privileges:true)
  if [ "$caps" != - ]; then
    for cap in ${caps//,/ }; do opts+=(--cap-add "$cap"); done
  fi
  while [ $# -gt 0 ] && [ "$1" != -- ]; do
    spec="$1"; mounts+=(-v "$(volume_name "${spec%%:*}"):${spec#*:}"); shift
  done
  shift
  docker run --rm -i --user "$user" --entrypoint sh "${opts[@]}" "${mounts[@]}" "$SERVICE_IMAGE" -c "$1"
}

# apf-compile from the compile image. STATE is mounted at the same path, so every path passed in resolves unchanged.
apf_compile() {
  docker run --rm --network none --user "$(id -u):$(id -g)" -v "$STATE:$STATE" -w "$STATE" --entrypoint apf-compile "$COMPILE_IMAGE" "$@"
}

preflight() {
  { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; } || die "Docker is not running"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is needed (docker compose version)"
  command -v openssl >/dev/null 2>&1 || die "openssl is needed"
  command -v python3 >/dev/null 2>&1 || die "python3 is needed"
  [ -S "$CONTAINER_SOCKET" ] || [ -e "$CONTAINER_SOCKET" ] || die "no container engine socket at $CONTAINER_SOCKET (set CONTAINER_SOCKET)"
  local image
  for image in "$SERVICE_IMAGE" "$GATEWAY_IMAGE" "$COMPILE_IMAGE"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      say "pulling $image"
      docker pull -q "$image" >/dev/null 2>&1 || die "could not pull $image (set ASSISTANT_APF_*_IMAGE to use another)"
    fi
  done
  local port holder
  for port in "$OPENSHELL_PORT" "$OPENSHELL_HEALTH_PORT" "$APF_EGRESS_PORT"; do
    holder="$(docker ps --filter "publish=$port" --format '{{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null | head -n 1)"
    if [ -n "$holder" ] && [ "${holder##* }" != "$PROJECT" ]; then
      die "port 127.0.0.1:$port is used by container ${holder%% *}; set ASSISTANT_ACCESS_PORT, ASSISTANT_ACCESS_HEALTH_PORT or ASSISTANT_ACCESS_EGRESS_PORT"
    fi
  done
}

make_cert() { # <crt> <key>: a self-signed serving certificate for the two APF listeners
  [ -s "$1" ] && [ -s "$2" ] && return 0
  local cfg="$STATE/tls/openssl.cnf"
  mkdir -p "$STATE/tls"
  cat >"$cfg" <<CNF
[req]
distinguished_name = dn
x509_extensions = server
prompt = no
[dn]
CN = localhost
[server]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, DNS:host.openshell.internal, IP:127.0.0.1
CNF
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout "$2" -out "$1" -config "$cfg" 2>/dev/null || die "could not make the certificate"
  chmod 0600 "$2"
}

make_jwt() { # the key pair the dedicated gateway signs its extension tokens with
  local d="$STATE/jwt"
  [ -s "$d/signing.pem" ] && [ -s "$d/public.pem" ] && [ -s "$d/kid" ] && return 0
  mkdir -p "$d"
  openssl genpkey -algorithm ed25519 -out "$d/signing.pem" 2>/dev/null || die "could not make the gateway key"
  chmod 0600 "$d/signing.pem"
  openssl pkey -in "$d/signing.pem" -pubout -out "$d/public.pem" 2>/dev/null
  printf 'openshell-%s\n' "$(openssl pkey -pubin -in "$d/public.pem" -outform DER 2>/dev/null | sha256_of | cut -c1-16)" >"$d/kid"
}

write_trust() { # <out> <audience>
  cat >"$1" <<TOML
issuer = "openshell-gateway:$GATEWAY_ID"
audience = "$2"
kid = "$(tr -d '\r\n' <"$STATE/jwt/kid")"
public_key_pem = """
$(cat "$STATE/jwt/public.pem")
"""
max_lifetime_secs = 3600
leeway_secs = 60
TOML
}

# The signed bundle for the rules, written to bundles/<revision>/bundle. The revision only goes up: the policy service refuses a
# lower one than it has seen, which is what stops an old, wider rule set from being put back.
build_bundle() { # <rules-json> <revision>
  local rules="$1" rev="$2" stage="$STATE/bundles/$2" dir
  dir="$stage/bundle"
  rm -rf "$stage"; mkdir -p "$dir" "$STATE/keys"
  python3 -I "$HERE/../assistant-access" compile --out "$dir" --rules "$rules" --revision "$rev" || return 1
  apf_compile genfixture --template "$dir/bundle.template.yaml" --base-dir "$dir" --key "$STATE/keys/$KEY_ID.seed" \
    --generate-key --keyid "$KEY_ID" --out "$dir/manifest.yaml" >/dev/null || die "could not sign the bundle"
  rm -f "$dir/bundle.template.yaml"
  chmod 0600 "$STATE/keys/$KEY_ID.seed"      # the signing container wrote it with its own umask
  apf_compile compile --verify-only --bundle-path "$dir/manifest.yaml" --trust-root-dir "$STATE/keys" --key-id "$KEY_ID" >/dev/null \
    || die "the signed bundle does not verify against its own key"
  printf '%s\n' "$rev" >"$STATE/revision"
  # Keep this revision and the one before it.
  local old
  for old in "$STATE"/bundles/*; do
    [ -d "$old" ] || continue
    [ "$(basename "$old")" -lt $((rev - 1)) ] 2>/dev/null && rm -rf "$old"
  done
  return 0
}

write_env() { # <bundle-revision>
  cat >"$ENV_FILE" <<ENV
OPENSHELL_PORT=$OPENSHELL_PORT
OPENSHELL_HEALTH_PORT=$OPENSHELL_HEALTH_PORT
APF_EGRESS_PORT=$APF_EGRESS_PORT
APF_ADMISSION_PORT=$APF_ADMISSION_PORT
APF_KEY_ID=$KEY_ID
APF_SERVICE_IMAGE=$SERVICE_IMAGE
APF_GATEWAY_IMAGE=$GATEWAY_IMAGE
APF_BUNDLE_DIR=$STATE/bundles/$1/bundle
APF_TRUST_ROOT=$STATE/keys/$KEY_ID.pub
OPENSHELL_IMAGE_TAG=$OPENSHELL_TAG
OPENSHELL_STATE_DIR=$STATE/gateway-state
OPENSHELL_GATEWAY_TOML=$TOML
CONTAINER_SOCKET=$CONTAINER_SOCKET
ENV
}

render_toml() {
  sed -e "s|@GATEWAY_PORT@|$OPENSHELL_PORT|g" -e "s|@HEALTH_PORT@|$OPENSHELL_HEALTH_PORT|g" -e "s|@EGRESS_PORT@|$APF_EGRESS_PORT|g" \
      -e "s|@GATEWAY_ID@|$GATEWAY_ID|g" -e "s|@OPENSHELL_TAG@|$OPENSHELL_TAG|g" "$HERE/gateway.toml" >"$TOML"
}

fill_volumes() {
  local stage="$STATE/stage" s
  rm -rf "$stage"; mkdir -p "$stage/secrets" "$stage/apf/interceptor" "$stage/apf/middleware" "$stage/openshell/jwt"
  [ -s "$STATE/http-bearer-token" ] || { openssl rand -hex 32 >"$STATE/http-bearer-token"; chmod 0600 "$STATE/http-bearer-token"; }
  cp "$STATE/http-bearer-token" "$stage/secrets/http-bearer-token"
  cp "$STATE/keys/$KEY_ID.seed" "$stage/secrets/document-signing.seed"
  cp "$STATE/tls/apf.crt" "$stage/apf/ca.crt"; cp "$STATE/tls/apf.crt" "$stage/openshell/ca.crt"
  for s in interceptor middleware; do cp "$STATE/tls/apf.crt" "$stage/apf/$s/tls.crt"; cp "$STATE/tls/apf.key" "$stage/apf/$s/tls.key"; done
  write_trust "$stage/apf/interceptor/trust.toml" "urn:openshell:extension:interceptor:$GATEWAY_ID"
  write_trust "$stage/apf/middleware/trust.toml" "urn:openshell:extension:middleware:$GATEWAY_ID"
  cp "$STATE/jwt/signing.pem" "$STATE/jwt/public.pem" "$STATE/jwt/kid" "$stage/openshell/jwt/"
  # Fresh named volumes belong to root; hand the ones the APF containers use to their user before the first write.
  # shellcheck disable=SC2016  # the script runs in the container, where $d is meant to expand
  run_in_volumes 0:0 CHOWN,FOWNER,DAC_OVERRIDE apf-run:/run/apf apf-state:/var/lib/apf apf-audit:/var/log/apf/audit \
    apf-secrets:/etc/apf/secrets apf-openshell:/etc/apf/openshell -- '
    set -e
    for d in /run/apf /var/lib/apf /var/log/apf/audit /etc/apf/secrets /etc/apf/openshell; do
      [ "$(stat -c %u "$d")" = 65532 ] || chown 65532:65532 "$d"
    done
    chmod 0700 /etc/apf/secrets; chmod 0755 /etc/apf/openshell' </dev/null || die "could not prepare the volumes"
  (cd "$stage/secrets" && COPYFILE_DISABLE=1 tar -cf - .) | run_in_volumes 65532:65532 - apf-secrets:/etc/apf/secrets -- '
    set -e; umask 077; cd /etc/apf/secrets
    rm -f http-bearer-token document-signing.seed
    tar -xf - --no-same-owner --no-same-permissions --warning=no-unknown-keyword
    chmod 0400 http-bearer-token document-signing.seed' || die "could not write the secrets"
  (cd "$stage/apf" && COPYFILE_DISABLE=1 tar -cf - .) | run_in_volumes 65532:65532 - apf-openshell:/etc/apf/openshell -- '
    set -e; umask 077; cd /etc/apf/openshell
    rm -rf interceptor middleware ca.crt
    tar -xf - --no-same-owner --no-same-permissions --warning=no-unknown-keyword
    chmod 0755 interceptor middleware; chmod 0400 interceptor/tls.key middleware/tls.key
    chmod 0444 ca.crt interceptor/tls.crt middleware/tls.crt interceptor/trust.toml middleware/trust.toml' || die "could not write the listener material"
  (cd "$stage/openshell" && COPYFILE_DISABLE=1 tar -cf - .) | run_in_volumes 0:0 - openshell-jwt:/etc/openshell-extensions/apf -- '
    set -e; umask 077; cd /etc/openshell-extensions/apf
    rm -rf jwt ca.crt
    tar -xf - --no-same-owner --no-same-permissions --warning=no-unknown-keyword
    chmod 0755 jwt; chmod 0400 jwt/signing.pem; chmod 0444 ca.crt jwt/public.pem jwt/kid' || die "could not write the gateway material"
  rm -rf "$stage"
}

ready() { curl -fsS -o /dev/null -m 3 "http://127.0.0.1:$OPENSHELL_HEALTH_PORT/readyz" 2>/dev/null; }

up() {
  local rules="$1" sha prev rev toml_before="" toml_after=""
  [ -n "$rules" ] || die "up needs the rules as JSON"
  preflight
  umask 077
  mkdir -p "$STATE" "$STATE/gateway-state"
  sha="$(printf '%s' "$rules" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), sort_keys=True))' | sha256_of)" \
    || die "the rules are not valid JSON"
  prev="$(cat "$STATE/rules.sha" 2>/dev/null || true)"
  rev="$(cat "$STATE/revision" 2>/dev/null || echo 0)"
  # The same rules, and the stack already answering: nothing to do (every launch comes through here).
  if [ "$sha" = "$prev" ] && ready; then
    ok "access rules in force (revision $rev): $(python3 -c 'import json,sys; print(", ".join(json.load(sys.stdin)["repos"]))' <"$STATE/rules.json")"
    return 0
  fi
  if [ "$sha" != "$prev" ] || [ ! -s "$STATE/bundles/$rev/bundle/manifest.yaml" ]; then
    rev=$((rev + 1))
    say "signing the rules (revision $rev)"
    build_bundle "$rules" "$rev" || die "could not build the bundle"
    printf '%s\n' "$sha" >"$STATE/rules.sha"
    printf '%s\n' "$rules" >"$STATE/rules.json"
  fi
  make_cert "$STATE/tls/apf.crt" "$STATE/tls/apf.key"
  make_jwt
  [ -s "$TOML" ] && toml_before="$(sha256_of <"$TOML")"
  render_toml
  toml_after="$(sha256_of <"$TOML")"
  write_env "$rev"
  # The project's first start creates the containers and volumes and fills them. A later start only brings the changes in: a new
  # bundle is a new bind-mount path, so compose recreates the policy service by itself, and the gateway swaps to it without a restart.
  if [ -z "$(compose ps -a -q 2>/dev/null)" ] || ! docker volume inspect "$(volume_name apf-secrets)" >/dev/null 2>&1; then
    compose up --no-start --quiet-pull >/dev/null 2>&1 || compose up --no-start >/dev/null 2>&1 || die "could not create the stack"
    fill_volumes
  fi
  compose up -d -t 5 --remove-orphans pod policy-service policy-gateway >/dev/null 2>&1 \
    || die "the policy services did not start (docker compose -p $PROJECT logs)"
  if [ "$toml_before" != "$toml_after" ]; then
    compose up -d -t 5 --no-deps --force-recreate openshell-gateway >/dev/null 2>&1 || die "the gateway did not start"
  else
    compose up -d -t 5 openshell-gateway >/dev/null 2>&1 || die "the gateway did not start"
  fi
  for _ in $(seq 1 90); do ready && break; sleep 2; done
  ready || die "the gateway did not become ready (docker compose -p $PROJECT logs)"
  ok "access rules in force (revision $rev): $(python3 -c 'import json,sys; print(", ".join(json.load(sys.stdin)["repos"]))' <"$STATE/rules.json")"
}

down() {
  [ -s "$ENV_FILE" ] || { say "not running"; return 0; }
  compose down --remove-orphans >/dev/null 2>&1 || die "could not stop the stack"
  say "stopped. Sandboxes made on it are kept; 'openshell sandbox list' (with the variables from 'stack.sh env') shows them."
}

status() {
  local rev repos running="no"
  rev="$(cat "$STATE/revision" 2>/dev/null || echo -)"
  repos="$(python3 -c 'import json,sys; print(", ".join(json.load(sys.stdin)["repos"]))' <"$STATE/rules.json" 2>/dev/null || echo -)"
  ready && running="yes"
  printf 'running=%s revision=%s repos=%s endpoint=http://127.0.0.1:%s\n' "$running" "$rev" "$repos" "$OPENSHELL_PORT"
}

case "${1:-}" in
  up) shift; up "${1:-}" ;;
  down) down ;;
  status) status ;;
  env) printf 'OPENSHELL_GATEWAY_ENDPOINT=http://127.0.0.1:%s\nOPENSHELL_GATEWAY_INSECURE=true\n' "$OPENSHELL_PORT" ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
