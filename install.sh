#!/bin/sh
# Run the Kyverno assistant without cloning this repository:
#
#   curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/kyverno/assistant/main/install.sh | sh -s -- --profile contributor
#
# It copies the launcher out of the image, so the launcher and the image it starts come from the same release, runs it,
# and removes the copy. Every argument goes to the launcher (container/kyverno --help). Besides the arguments, the
# image comes from --image or $KYVERNO_IMAGE, else ghcr.io/kyverno/assistant:latest.
set -eu

main() {
  image="${KYVERNO_IMAGE:-ghcr.io/kyverno/assistant:latest}"
  prev=""
  for a in "$@"; do
    [ "$prev" = "--image" ] && image="$a"
    prev="$a"
  done

  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "Docker is not installed or not running: https://docs.docker.com/get-docker/" >&2
    exit 1
  fi
  # The launcher asks questions; when this script is piped from curl, stdin is the script, not the terminal.
  if [ ! -t 0 ]; then
    if ( : </dev/tty ) 2>/dev/null; then exec </dev/tty; else echo "no terminal to ask questions on" >&2; exit 1; fi
  fi

  dir="$(mktemp -d)"
  trap 'rm -rf "$dir"' EXIT INT TERM
  docker run --rm "$image" tar -C /opt/kyverno -c launcher | tar -x -C "$dir"

  KYVERNO_IMAGE="$image" "$dir/launcher/kyverno" "$@"
}

main "$@"
