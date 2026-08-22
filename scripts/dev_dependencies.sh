#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
compose_file="$repo_root/deploy/chiap01/compose.dev.yml"
project_name="sklegal-dev"
# An isolated qualification run overrides these so its containers, volume, and
# host ports never collide with the shared development project.
postgres_port="15433"
temporal_port="17233"

usage() {
  cat >&2 <<'EOF'
usage: $0 {check|up|down} [--project NAME] [--postgres-port N] [--temporal-port N]
EOF
}

subcommand=""
while (($# > 0)); do
  case "$1" in
    check|up|down)
      subcommand="$1"
      shift
      ;;
    --project)
      (($# >= 2)) || { usage; exit 2; }
      project_name="$2"
      shift 2
      ;;
    --postgres-port)
      (($# >= 2)) || { usage; exit 2; }
      postgres_port="$2"
      shift 2
      ;;
    --temporal-port)
      (($# >= 2)) || { usage; exit 2; }
      temporal_port="$2"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

export SKLEGAL_DEV_POSTGRES_PORT="$postgres_port"
export SKLEGAL_DEV_TEMPORAL_PORT="$temporal_port"
export SKLEGAL_DEV_VOLUME_NAME="${project_name}-postgres-data"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for SKLegal development dependencies" >&2
  exit 1
fi

case "${subcommand:-}" in
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
    usage
    exit 2
    ;;
esac
