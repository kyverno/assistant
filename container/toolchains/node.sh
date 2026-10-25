# Node.js toolchain for kyverno-workspace. The version comes from the repository (.node-version, .nvmrc or
# package.json "engines"); dependencies are the project's own `npm ci`, run by whoever needs them.
tc_want() {
  local d="$1" v=""
  for f in .node-version .nvmrc; do [ -f "$d/$f" ] && v="$(tr -d 'v \n' < "$d/$f")" && break; done
  [ -z "$v" ] && [ -f "$d/package.json" ] && v="$(jq -r '.engines.node // empty' "$d/package.json" | grep -oE '[0-9]+' | head -n 1)"
  # A major ("24") or a full version: take the newest release of that major.
  curl -fsSL https://nodejs.org/dist/index.json \
    | jq -r --arg v "${v:-}" '[.[].version | ltrimstr("v") | select($v == "" or . == $v or startswith($v + "."))][0]'
}
tc_have() { [ "$("$TOOLS/node/bin/node" --version 2>/dev/null)" = "v$1" ]; }
tc_install() {
  local arch="$ARCH"; [ "$arch" = amd64 ] && arch=x64
  rm -rf "$TOOLS/node" && mkdir -p "$TOOLS/node"
  curl -fsSL "https://nodejs.org/dist/v$1/node-v$1-linux-$arch.tar.xz" | tar -xJ -C "$TOOLS/node" --strip-components=1
}
tc_env() { echo "export PATH=$TOOLS/node/bin:\$PATH npm_config_cache=$TOOLS/npm-cache"; }
