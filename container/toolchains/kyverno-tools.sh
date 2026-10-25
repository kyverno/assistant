# The Kyverno CLI (kyverno test / apply, which need no cluster) and Chainsaw, for repositories of policies and tests.
# Released binaries, not built: they are small and the releases are the supported way to get them.
_latest_tag() { curl -fsSI -o /dev/null -w '%{redirect_url}' "https://github.com/$1/releases/latest" | sed 's#.*/tag/##'; }
tc_want() { _latest_tag kyverno/kyverno; }
tc_have() { [ "$("$TOOLS/kyverno-tools/kyverno" version 2>/dev/null | awk '/Version:/{print $2}')" = "${1#v}" ] && [ -x "$TOOLS/kyverno-tools/chainsaw" ]; }
tc_install() {
  local arch="$ARCH" tag; tag="$(_latest_tag kyverno/chainsaw)"
  mkdir -p "$TOOLS/kyverno-tools"
  [ "$arch" = amd64 ] && arch=x86_64
  curl -fsSL "https://github.com/kyverno/kyverno/releases/download/$1/kyverno-cli_${1}_linux_$arch.tar.gz" | tar -xz -C "$TOOLS/kyverno-tools" kyverno
  curl -fsSL "https://github.com/kyverno/chainsaw/releases/download/$tag/chainsaw_linux_$ARCH.tar.gz" | tar -xz -C "$TOOLS/kyverno-tools" chainsaw
}
tc_env() { echo "export PATH=$TOOLS/kyverno-tools:\$PATH"; }
