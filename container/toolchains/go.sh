# Go toolchain for kyverno-workspace. The version comes from the repository's own go.mod, so a build uses what its CI uses.
#   tc_want <repo dir>   the version to install
#   tc_have <version>    succeeds when it is already installed
#   tc_install <version> download and unpack it
#   tc_env               the shell lines that put it on PATH
tc_want() {
  local v
  v="$(awk '/^toolchain go/{sub("go","",$2); print $2; exit} /^go [0-9]/{print $2}' "$1/go.mod" | head -n 1)"
  # "go 1.27" names a release series, not a download: take its newest patch release.
  if [[ "$v" =~ ^[0-9]+\.[0-9]+$ ]]; then
    v="$(curl -fsSL 'https://go.dev/dl/?mode=json&include=all' | jq -r --arg s "$v." '[.[].version | ltrimstr("go") | select(startswith($s))][0]')"
  fi
  echo "$v"
}
tc_have() { [ "$("$TOOLS/go/bin/go" env GOVERSION 2>/dev/null)" = "go$1" ]; }
tc_install() {
  rm -rf "$TOOLS/go"
  curl -fsSL "https://go.dev/dl/go$1.linux-$ARCH.tar.gz" | tar -xz -C "$TOOLS"
}
# -p=2: the sandbox shares a laptop's memory with Docker, and a full parallel build of Kyverno can run it out.
tc_env() {
  echo "export PATH=$TOOLS/go/bin:$TOOLS/gopath/bin:\$PATH GOPATH=$TOOLS/gopath GOCACHE=$TOOLS/gocache GOFLAGS=-p=2 GOTOOLCHAIN=local"
}
