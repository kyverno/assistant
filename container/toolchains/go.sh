# shellcheck shell=bash disable=SC2153,SC2034
# ARCH and TOOLS are set by kyverno-workspace, which sources this file.
# Go toolchain for kyverno-workspace. The version comes from the repository's own go.mod, so a build uses what its CI uses.
#   tc_want <repo dir>   the version to install
#   tc_have <version>    succeeds when it is already installed
#   tc_install <version> download (checksum verified) and unpack it
#   tc_env               the shell lines that put it on PATH
_go_releases() { curl -fsSL 'https://go.dev/dl/?mode=json&include=all'; }
tc_want() {
  local v
  v="$(awk '/^toolchain go/{sub("go","",$2); print $2; exit} /^go [0-9]/{print $2}' "$1/go.mod" | head -n 1)"
  # "go 1.27" names a release series, not a download: take its newest patch release.
  if [[ "$v" =~ ^[0-9]+\.[0-9]+$ ]]; then
    v="$(_go_releases | jq -r --arg s "$v." '[.[].version | ltrimstr("go") | select(startswith($s))][0]')"
  fi
  echo "$v"
}
tc_have() { [ "$("$TOOLS/go/bin/go" env GOVERSION 2>/dev/null)" = "go$1" ]; }
tc_install() {
  local file="go$1.linux-$ARCH.tar.gz" sha
  sha="$(_go_releases | jq -r --arg f "$file" '[.[].files[] | select(.filename == $f) | .sha256][0]')"
  [ -n "$sha" ] && [ "$sha" != null ] || { echo "  no checksum published for $file" >&2; return 1; }
  dl_verify "https://go.dev/dl/$file" "$sha" "$TOOLS/.downloads/$file" || return 1
  rm -rf "$TOOLS/go" && tar -xzf "$TOOLS/.downloads/$file" -C "$TOOLS"; local rc=$?
  rm -f "$TOOLS/.downloads/$file"; return $rc
}
# -p=2: the sandbox shares a laptop's memory with Docker, and a full parallel build of Kyverno can run it out.
# GOPROXY without "direct": modules come from the Go module mirror only, the one host the sandbox policy allows.
tc_env() {
  echo "export PATH=$TOOLS/go/bin:$TOOLS/gopath/bin:\$PATH GOPATH=$TOOLS/gopath GOCACHE=$TOOLS/gocache GOFLAGS=-p=2 GOTOOLCHAIN=local GOPROXY=https://proxy.golang.org"
}
