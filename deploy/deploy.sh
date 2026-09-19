#!/usr/bin/env bash
set -euo pipefail

branch=${1:?development or main required}
sha=${2:?commit SHA required}
[[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA' >&2; exit 2; }
[[ $branch == development || $branch == main ]] || { echo 'Invalid branch' >&2; exit 2; }

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
state_dir=${DEPLOY_STATE_DIR:-/home/forestlee/deploy/iinfo-dx-backend}
mkdir -p "$state_dir/env"
exec 9>"$state_dir/deploy.lock"
flock -x 9

actual_sha=$(git -C "$repo" rev-parse HEAD)
[[ $actual_sha == "$sha" ]] || { echo 'Checkout does not match requested SHA' >&2; exit 1; }

wait_healthy() {
  local container=$1 status
  for ((attempt=0; attempt<36; attempt++)); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    [[ $status == healthy ]] && return 0
    [[ $status == unhealthy || $status == exited ]] && break
    sleep 5
  done
  docker logs --tail 80 "$container" >&2 || true
  echo "$container failed its health check" >&2
  return 1
}

if [[ $branch == development ]]; then
  export DEPLOY_ENV_FILE="$state_dir/env/development.env"
  export NPM_CREDENTIALS="$state_dir/npm-credentials.json"
  export DEPLOY_SHA="$sha"
  [[ -s $DEPLOY_ENV_FILE ]] || { echo "Create $DEPLOY_ENV_FILE first" >&2; exit 1; }
  [[ -s $NPM_CREDENTIALS ]] || { echo "Create $NPM_CREDENTIALS first" >&2; exit 1; }
  python3 "$repo/deploy/check_env.py" "$DEPLOY_ENV_FILE" gooxuqvpxpzmofddcuow
  compose=(docker compose -f "$repo/deploy/development.compose.yaml")
  "${compose[@]}" up -d redis
  # Development intentionally has a stop/start window.
  "${compose[@]}" stop api || true
  "${compose[@]}" up -d --build --force-recreate api
  wait_healthy iinfo-dx-development-api
  python3 "$repo/deploy/npm_proxy.py" development
  headers=$(mktemp)
  route_healthy=false
  for ((attempt=0; attempt<10; attempt++)); do
    if curl --fail --silent --show-error --max-time 10 -D "$headers" \
        --noproxy '*' \
        --resolve iinfo-dx-api-dev.forestlee.me:443:127.0.0.1 \
        https://iinfo-dx-api-dev.forestlee.me/api/v1/health >/dev/null \
        && tr -d '\r' < "$headers" | grep -qi "^X-Deploy-Commit: $sha$"; then
      route_healthy=true
      break
    fi
    sleep 2
  done
  rm -f "$headers"
  [[ $route_healthy == true ]] || { echo 'Development NPM route health check failed' >&2; exit 1; }
  echo "Development deployed: $sha"
  exit 0
fi

export DEPLOY_ENV_FILE="$state_dir/env/production.env"
export NPM_CREDENTIALS="$state_dir/npm-credentials.json"
[[ -s $DEPLOY_ENV_FILE ]] || { echo "Create $DEPLOY_ENV_FILE first" >&2; exit 1; }
python3 "$repo/deploy/check_env.py" "$DEPLOY_ENV_FILE" byfyglcaoclsjugliphe
[[ -s $NPM_CREDENTIALS ]] || { echo "Create $NPM_CREDENTIALS first" >&2; exit 1; }
compose=(docker compose -f "$repo/deploy/production.compose.yaml")
"${compose[@]}" up -d redis

active_file="$state_dir/active-slot"
active=$(cat "$active_file" 2>/dev/null || true)
[[ -z $active || $active == blue || $active == green ]] || { echo 'Invalid active slot' >&2; exit 1; }
if [[ $active == blue ]]; then candidate=green; else candidate=blue; fi

image="iinfo-dx-backend:$sha"
docker build -t "$image" "$repo"
if [[ $candidate == blue ]]; then
  export BLUE_IMAGE=$image BLUE_SHA=$sha
else
  export GREEN_IMAGE=$image GREEN_SHA=$sha
fi
"${compose[@]}" up -d --no-deps --force-recreate "$candidate"
if ! wait_healthy "iinfo-dx-production-$candidate"; then
  "${compose[@]}" stop "$candidate"
  exit 1
fi

if ! python3 "$repo/deploy/npm_proxy.py" "$candidate"; then
  "${compose[@]}" stop "$candidate"
  exit 1
fi

headers=$(mktemp)
route_healthy=false
for ((attempt=0; attempt<10; attempt++)); do
  if curl --fail --silent --show-error --max-time 10 -D "$headers" \
      --noproxy '*' \
      --resolve iinfo-dx-api.forestlee.me:443:127.0.0.1 \
      https://iinfo-dx-api.forestlee.me/api/v1/health >/dev/null \
      && tr -d '\r' < "$headers" | grep -qi "^X-Deploy-Commit: $sha$"; then
    route_healthy=true
    break
  fi
  sleep 2
done
if [[ $route_healthy != true ]]; then
  rm -f "$headers"
  if [[ -n $active ]]; then
    if python3 "$repo/deploy/npm_proxy.py" "$active"; then
      "${compose[@]}" stop "$candidate"
    else
      echo 'Rollback failed; candidate left running to preserve service' >&2
    fi
  fi
  echo 'NPM route health check failed' >&2
  exit 1
fi
rm -f "$headers"

printf '%s\n' "$candidate" > "$active_file"
if [[ -n $active ]]; then
  sleep 30  # Drain requests from the previous Nginx workers.
  "${compose[@]}" stop -t 120 "$active"
fi
echo "Production deployed: $sha ($candidate)"
