# shellcheck shell=bash disable=SC2153,SC2034
# ARCH and TOOLS are set by kyverno-workspace, which sources this file.
# The Kyverno CLI (kyverno test / apply, which need no cluster) and Chainsaw, for repositories of policies and tests.
# Released binaries, not built: they are small and the releases are the supported way to get them. Each release
# publishes a checksums.txt, which the download is checked against.
_latest_tag() { curl -fsSI -o /dev/null -w '%{redirect_url}' "https://github.com/$1/releases/latest" | sed 's#.*/tag/##'; }
_release_file() {   # <repo> <tag> <file> <dest dir>
  local sha
  sha="$(curl -fsSL "https://github.com/$1/releases/download/$2/checksums.txt" | awk -v f="$3" '$2 == f {print $1}')"
  [ -n "$sha" ] || { echo "  no checksum published for $3" >&2; return 1; }
  dl_verify "https://github.com/$1/releases/download/$2/$3" "$sha" "$TOOLS/.downloads/$3" || return 1
  tar -xzf "$TOOLS/.downloads/$3" -C "$4"; local rc=$?
  rm -f "$TOOLS/.downloads/$3"; return $rc
}
tc_want() { _latest_tag kyverno/kyverno; }
tc_have() { [ "$("$TOOLS/kyverno-tools/kyverno" version 2>/dev/null | awk '/Version:/{print $2}')" = "${1#v}" ] && [ -x "$TOOLS/kyverno-tools/chainsaw" ]; }
tc_install() {
  local arch="$ARCH" tag; tag="$(_latest_tag kyverno/chainsaw)"
  mkdir -p "$TOOLS/kyverno-tools"
  [ "$arch" = amd64 ] && arch=x86_64
  _release_file kyverno/kyverno "$1" "kyverno-cli_${1}_linux_$arch.tar.gz" "$TOOLS/kyverno-tools" || return 1
  _release_file kyverno/chainsaw "$tag" "chainsaw_linux_$ARCH.tar.gz" "$TOOLS/kyverno-tools"
}
tc_env() { echo "export PATH=$TOOLS/kyverno-tools:\$PATH"; }
