#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

python3 - <<'PY'
import sys

if sys.version_info[:2] != (3, 12):
    raise SystemExit("SKLegal requires Python 3.12")
PY

node_version=$(node --version)
case "$node_version" in
  v22.*) ;;
  *) echo "SKLegal requires Node 22, found $node_version" >&2; exit 1 ;;
esac

# shellcheck disable=SC1091
source requirements/bootstrap.lock
readonly UV_VERSION UV_ARCHIVE_NAME UV_ARCHIVE_SHA256 UV_ARCHIVE_URL
tools_bin="$repo_root/.tools/bin"

verify_uv_version() {
  local uv_binary=$1
  local uv_report
  if ! uv_report=$("$uv_binary" --version 2>&1); then
    echo "unable to execute pinned uv binary: $uv_binary" >&2
    return 1
  fi
  case "$uv_report" in
    "uv $UV_VERSION" | "uv $UV_VERSION "*) ;;
    *) echo "unexpected uv version: $uv_report" >&2; return 1 ;;
  esac
}

if [[ -x "$tools_bin/uv" ]]; then
  verify_uv_version "$tools_bin/uv"
else
  mkdir -p "$tools_bin"
  bootstrap_dir=$(mktemp -d "$repo_root/.tools/.uv-bootstrap-XXXXXXXX")
  cleanup_bootstrap() {
    rm -rf -- "$bootstrap_dir"
  }
  trap cleanup_bootstrap EXIT

  echo "bootstrapping uv $UV_VERSION from pinned official archive"
  timeout --kill-after=30 300 curl \
    --fail \
    --location \
    --proto '=https' \
    --show-error \
    --silent \
    --tlsv1.2 \
    --output "$bootstrap_dir/$UV_ARCHIVE_NAME" \
    "$UV_ARCHIVE_URL"
  if ! printf '%s  %s\n' \
    "$UV_ARCHIVE_SHA256" \
    "$bootstrap_dir/$UV_ARCHIVE_NAME" \
    | sha256sum --check --status; then
    echo "uv archive checksum validation failed" >&2
    exit 1
  fi
  timeout --kill-after=30 300 tar \
    --extract \
    --file "$bootstrap_dir/$UV_ARCHIVE_NAME" \
    --gzip \
    --directory "$bootstrap_dir" \
    --no-same-owner

  extracted="$bootstrap_dir/uv-x86_64-unknown-linux-gnu"
  if [[ ! -f "$extracted/uv" || ! -f "$extracted/uvx" ]]; then
    echo "uv archive does not contain the expected executables" >&2
    exit 1
  fi
  verify_uv_version "$extracted/uv"
  chmod 0755 "$extracted/uv" "$extracted/uvx"
  mv "$extracted/uv" "$tools_bin/.uv.new"
  mv "$extracted/uvx" "$tools_bin/.uvx.new"
  mv "$tools_bin/.uv.new" "$tools_bin/uv"
  mv "$tools_bin/.uvx.new" "$tools_bin/uvx"
  trap - EXIT
  cleanup_bootstrap
  verify_uv_version "$tools_bin/uv"
fi

export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/.tools/uv-cache}"
export UV_LINK_MODE=copy
timeout --kill-after=30 600 .tools/bin/uv sync --locked --all-packages

npm_digest=$(
  sha256sum package-lock.json package.json apps/web/package.json | sha256sum | cut -d' ' -f1
)
npm_stamp="$repo_root/.tools/npm-install.sha256"
if [[ -d node_modules && -f "$npm_stamp" ]] \
  && [[ "$(<"$npm_stamp")" == "$npm_digest" ]]; then
  npm ls --all --ignore-scripts >/dev/null
else
  timeout --kill-after=30 300 npm ci \
    --ignore-scripts \
    --no-audit \
    --no-fund \
    --prefer-offline
  npm ls --all --ignore-scripts >/dev/null
  printf '%s\n' "$npm_digest" > "$npm_stamp"
fi
