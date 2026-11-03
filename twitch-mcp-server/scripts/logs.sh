#!/usr/bin/env bash
# Show server logs (JSON lines). Follows by default.
#   -n N          number of recent lines (default 100)
#   --no-follow   print and exit
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

lines=100
follow=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    -n)
      [ "$#" -ge 2 ] || die "-n needs a number"
      lines="$2"
      shift 2
      ;;
    --no-follow)
      follow=0
      shift
      ;;
    -h | --help)
      echo "usage: scripts/logs.sh [-n LINES] [--no-follow]"
      exit 0
      ;;
    *) die "unknown option: $1 (usage: scripts/logs.sh [-n LINES] [--no-follow])" ;;
  esac
done
[[ "${lines}" =~ ^[0-9]+$ ]] || die "-n must be a non-negative integer"

require_docker
[ "$(container_state)" != "not created" ] || die "container '${CONTAINER}' does not exist. Run scripts/start.sh first."

if [ "${follow}" -eq 1 ]; then
  exec docker logs --tail "${lines}" --follow "${CONTAINER}"
fi
exec docker logs --tail "${lines}" "${CONTAINER}"
