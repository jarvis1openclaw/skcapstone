#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
compose_file="$repo_root/deploy/chiap01/compose.dev.yml"
project_name="sklegal-dev"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for SKLegal development dependencies" >&2
  exit 1
fi

case "${1:-}" in
  check)
    docker compose --project-name "$project_name" --file "$compose_file" config --quiet
    docker compose --dry-run --project-name "$project_name" --file "$compose_file" \
      up --detach --wait >/dev/null
    ;;
  up)
    docker compose --project-name "$project_name" --file "$compose_file" config --quiet
    docker compose --project-name "$project_name" --file "$compose_file" up --detach --wait
    ;;
  down)
    docker compose --project-name "$project_name" --file "$compose_file" down --volumes --remove-orphans
    ;;
  *)
    echo "usage: $0 {check|up|down}" >&2
    exit 2
    ;;
esac
